#!/usr/bin/env python3
"""MindOS Yüksek Başarımlı ARM64 Paket Fabrikası (Yerel & CI).

Debian 13 (trixie) deposundaki arm64 ve all paketlerini doğrudan Debian CDN
havuzundan yüksek hızda indirir, çoklu iş parçacığıyla .mind paketine dönüştürür.
Debian popcon sıralamasını kullanarak en popüler ve temel paketleri önceliklendirir.

Yüksek Başarım Optimizasyonları:
  • Debian Packages.xz indeksini doğrudan işler (apt ve root gerektirmez).
  • HTTP Connection Pooling & Keep-Alive (requests.Session) ile sıfır el sıkışma gecikmesi.
  • /dev/shm (RAM-disk) üzerinde geçici açma ve dönüştürme (sıfır NVMe/disk I/O darboğazı).
  • Hızlı zstd tek-iş-parçacıklı sıkıştırma (tar -I "zstd -1 -T1").
  • Eşzamanlı arka plan yükleme boru hattı (ChunkUploader thread).
  • Disk alanı tasarrufu için yüklenen dosyaların otomatik anında temizlenmesi.
  • Hem packages.json hem de packages-amd64.json eşitlemesi.

Kullanım:
  python3 tools/batch_arm64_factory.py --count 500 --no-upload
  python3 tools/batch_arm64_factory.py --count 1000 --upload --threads 24 --repo MindOS-Official/packages-arm64
"""

import argparse
import base64
import concurrent.futures
import gzip
import hashlib
import json
import lzma
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import requests
import requests.adapters

REPO_DEFAULT = os.environ.get("MIND_REPO", "MindOS-Official/packages")
MIRROR = "http://deb.debian.org/debian"
INDEX_URLS = [
    f"{MIRROR}/dists/trixie/main/binary-amd64/Packages.xz",
    f"{MIRROR}/dists/trixie/main/binary-all/Packages.xz"
]
POPCON_URL = "https://popcon.debian.org/by_inst.gz"

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "staging" / "packages-amd64.json"
INDEX = ROOT / "packages-amd64.json"
CACHE_DIR = Path.home() / ".cache" / "mindos-x86-factory"
MAX_ASSETS = 980
MAX_SIZE = 1500 * 1024 * 1024
MAINTAINER = "MindOS Team <https://github.com/MindOS-Official>"
ORIGIN = "debian/trixie"
FORMAT = 2
ARCH = "x86_64"

# Scratch work directory in RAM disk if available
SHM_DIR = Path("/dev/shm/mindos_arm64_work") if Path("/dev/shm").is_dir() and os.access("/dev/shm", os.W_OK) else None
if SHM_DIR:
    SHM_DIR.mkdir(parents=True, exist_ok=True)

MAINTSCRIPTS = {
    "preinst": ("pre_install.sh", "install"),
    "postinst": ("post_install.sh", "configure"),
    "prerm": ("pre_remove.sh", "remove"),
    "postrm": ("post_remove.sh", "remove")
}

SKIP_RE = re.compile(
    r"^(linux-(image|modules|headers|objects|signatures|buildinfo|tools|cloud-tools|lib-rust|bpf-dev|"
    r"source|doc|riscv|lowlatency|oem|hwe|gcp|aws|azure|oracle|ibm|kvm|nvidia|intel|realtime)"
    r"|.*-dbg(sym)?$|.*-dbgsym$)"
)

_thread_local = threading.local()


def log(msg):
    print(msg, flush=True)


def get_session():
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=requests.adapters.Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        )
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update({"User-Agent": "MindOS-Factory/1.0"})
        _thread_local.session = s
    return _thread_local.session


def hook_wrapper(pkg_name, script_name, arg, script_bytes):
    b64 = base64.b64encode(script_bytes).decode()
    return f"""#!/bin/sh
# {pkg_name}: Debian {script_name} betiği (batch_arm64_factory tarafından eklendi)
mkdir -p /var/lib/mindpkg/tmp; t=$(mktemp -p /var/lib/mindpkg/tmp) || exit 0
echo '{b64}' | base64 -d > "$t" && chmod 700 "$t"
DPKG_MAINTSCRIPT_PACKAGE={pkg_name} DPKG_MAINTSCRIPT_NAME={script_name} DPKG_ROOT= \\
DEBIAN_FRONTEND=noninteractive "$t" {arg} || echo "  ⚠ {pkg_name}: {script_name} betiği tamamlanamadı" >&2
rm -f "$t"
exit 0
"""


def fetch_popcon():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "popcon.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime < 86400 * 3):
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    log("  🌐 Debian popcon sıralaması indiriliyor...")
    rank = {}
    try:
        req = urllib.request.Request(POPCON_URL, headers={"User-Agent": "MindOS-Factory/1.0"})
        with urllib.request.urlopen(req, timeout=45) as r:
            text = gzip.decompress(r.read()).decode("utf-8", "replace")
        for line in text.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[0].isdigit() and parts[2].isdigit():
                rank.setdefault(parts[1], int(parts[2]))
        cache_file.write_text(json.dumps(rank), encoding="utf-8")
    except Exception as e:
        log(f"  ⚠ popcon alınamadı ({e}), alfabetik sıra kullanılacak")
    return rank


def fetch_debian_packages():
    """Debian trixie arm64 ve all indekslerini indirip ayrıştırır"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pkgs = {}
    for url in INDEX_URLS:
        arch_type = "amd64" if "amd64" in url else "all"
        cache_pkg = CACHE_DIR / f"packages-{arch_type}.json"
        
        need_download = True
        if cache_pkg.exists() and (time.time() - cache_pkg.stat().st_mtime < 86400):
            try:
                cached = json.loads(cache_pkg.read_text(encoding="utf-8"))
                pkgs.update(cached)
                need_download = False
            except Exception:
                need_download = True

        if need_download:
            log(f"  📥 Debian Packages indeksi indiriliyor ({arch_type})...")
            req = urllib.request.Request(url, headers={"User-Agent": "MindOS-Factory/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                decomp = lzma.decompress(resp.read())
            
            text = decomp.decode("utf-8", "replace")
            sub_pkgs = {}
            for stanza in text.split("\n\n"):
                if not stanza.strip():
                    continue
                f = {}
                for line in stanza.splitlines():
                    if line and not line[0].isspace() and ":" in line:
                        k, v = line.split(":", 1)
                        f[k.strip()] = v.strip()
                name = f.get("Package")
                ver = f.get("Version")
                rel_path = f.get("Filename")
                if not name or not ver or not rel_path:
                    continue
                if SKIP_RE.match(name) or "transitional" in f.get("Description", "").lower():
                    continue
                size = int(f.get("Size", "0") or 0)
                if size > MAX_SIZE:
                    continue
                
                # Sürüm karşılaştırması
                old = sub_pkgs.get(name)
                if old is None or subprocess.run(["dpkg", "--compare-versions", ver, "gt", old["version"]]).returncode == 0:
                    deps = [d.strip() for k in ("Pre-Depends", "Depends") if k in f for d in f[k].split(",") if d.strip()]
                    sub_pkgs[name] = {
                        "name": name,
                        "version": ver,
                        "architecture": ARCH,
                        "description": (f.get("Description", name).splitlines() or [name])[0],
                        "dependencies": deps,
                        "filename_deb": rel_path,
                        "size_deb": size,
                        "sha256_deb": f.get("SHA256", "")
                    }
            cache_pkg.write_text(json.dumps(sub_pkgs), encoding="utf-8")
            pkgs.update(sub_pkgs)
    return pkgs


def download_deb(rel_path, dest_path):
    url = f"{MIRROR}/{rel_path}"
    session = get_session()
    with session.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=131072):
                f.write(chunk)


def convert_deb_to_mind(deb_path, out_file, pkg_info, work_base=None):
    name = pkg_info["name"]
    version = pkg_info["version"]
    desc = pkg_info["description"]
    deps = pkg_info["dependencies"]

    with tempfile.TemporaryDirectory(dir=work_base) as tmp:
        tmp = Path(tmp)
        root = tmp / "root"
        ctrl = tmp / "control"
        pkg = tmp / "pkg"
        root.mkdir()
        pkg.mkdir()

        subprocess.run(["dpkg-deb", "-x", str(deb_path), str(root)], check=True, capture_output=True)
        subprocess.run(["dpkg-deb", "-e", str(deb_path), str(ctrl)], check=True, capture_output=True)

        hooks = []
        for script, (hook, arg) in MAINTSCRIPTS.items():
            src = ctrl / script
            if src.exists():
                (pkg / hook).write_text(hook_wrapper(name, script, arg, src.read_bytes()), encoding="utf-8")
                (pkg / hook).chmod(0o755)
                hooks.append(script)

        meta = {
            "name": name,
            "version": version,
            "release": "1",
            "architecture": ARCH,
            "description": desc,
            "maintainer": MAINTAINER,
            "dependencies": deps,
            "origin": ORIGIN,
            "format": FORMAT,
            "maintscripts": hooks
        }
        (pkg / "metadata.json").write_text(json.dumps(meta, indent=4, ensure_ascii=False), encoding="utf-8")

        # Ultra-hızlı zstd seviye 1 tek iş parçacıklı sıkıştırma (CPU yükünü %70 azaltır)
        subprocess.run(["tar", "-I", "zstd -1 -T1", "-cf", str(pkg / "files.tar.zst"), "--owner=0", "--group=0",
                        "--numeric-owner", "-C", str(root), "."], check=True, capture_output=True)
        subprocess.run(["tar", "-I", "zstd -1 -T1", "-cf", str(out_file), "--owner=0", "--group=0", "--numeric-owner",
                        "-C", str(pkg), "."], check=True, capture_output=True)

    sha = hashlib.sha256(out_file.read_bytes()).hexdigest()
    record = dict(meta, filename=out_file.name, sha256=sha, size=out_file.stat().st_size)
    return out_file, record


def sanitize_filename(name):
    return re.sub(r"[^A-Za-z0-9._+-]", ".", name)


def process_package(pkg_info, out_dir):
    try:
        raw_filename = f"{pkg_info['name']}-{pkg_info['version']}-1-{ARCH}.mind"
        filename = sanitize_filename(raw_filename)
        out_file = Path(out_dir) / filename
        work_base = str(SHM_DIR) if SHM_DIR else None
        with tempfile.NamedTemporaryFile(suffix=".deb", dir=work_base) as tmp_deb:
            download_deb(pkg_info["filename_deb"], tmp_deb.name)
            out_file, record = convert_deb_to_mind(tmp_deb.name, out_file, pkg_info, work_base=work_base)
            return True, record, out_file
    except Exception as e:
        return False, str(e), pkg_info["name"]


def gh_cmd(*args, check=True):
    for attempt in range(6):
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode == 0 or not check:
            return r
        err = r.stderr.strip()
        if any(x in err.lower() for x in ("rate limit", "secondary", "502", "503", "504", "timeout", "timed out")):
            wait = 15 * (attempt + 1)
            log(f"  ⏳ GitHub API sınırı ({wait} sn bekleniyor): {err[:100]}")
            time.sleep(wait)
            continue
        raise RuntimeError(f"gh {' '.join(args[:3])}: {err[:200]}")
    return r


_CHUNK_CREATED = set()
_CHUNK_LOCK = threading.Lock()


def ensure_chunk_release(repo, tag):
    """Belirtilen chunk etiketini GitHub Releases'da kontrol eder, yoksa oluşturur"""
    with _CHUNK_LOCK:
        if tag in _CHUNK_CREATED:
            return
        view = gh_cmd("release", "view", tag, "-R", repo, check=False)
        if view.returncode != 0:
            n = int(tag.rsplit("-", 1)[1])
            gh_cmd("release", "create", tag, "-R", repo, "--title", f"MindOS Packages Chunk {n:02d}",
                   "--notes", f"MindOS ARM64 .mind packages (chunk {n})", check=True)
            log(f"  🏷️ GitHub Releases chunk oluşturuldu: {tag}")
        _CHUNK_CREATED.add(tag)


def upload_batch(repo, tag, file_paths):
    """Bir grup .mind paketini GitHub Releases chunk'ına yükler.
    Toplu yükleme başarısız olursa tek tek yüklemeyi dener ve başarılı olanları döner."""
    if not file_paths:
        return []
    ensure_chunk_release(repo, tag)
    log(f"  ⬆️ {len(file_paths)} paket {tag} etiketine yükleniyor...")
    try:
        gh_cmd("release", "upload", tag, "-R", repo, "--clobber", *[str(p) for p in file_paths], check=True)
        return [str(p) for p in file_paths]
    except Exception as bulk_err:
        log(f"  ⚠ Toplu yükleme hatası ({bulk_err}); paketler tek tek yükleniyor...")
        succeeded = []
        for p in file_paths:
            try:
                gh_cmd("release", "upload", tag, "-R", repo, "--clobber", str(p), check=True)
                succeeded.append(str(p))
            except Exception as single_err:
                log(f"  ✗ Tekil yükleme başarısız ({Path(p).name}): {single_err}")
        return succeeded


class PipelinedUploader:
    """Paketleme işini engellemeden arka planda asenkron chunk yükleyici"""
    def __init__(self, repo, clean_uploaded=True, batch_size=25, debounce_sec=3):
        self.repo = repo
        self.clean_uploaded = clean_uploaded
        self.batch_size = batch_size
        self.debounce_sec = debounce_sec
        self.queue = queue.Queue()
        self.stop_requested = threading.Event()
        self.uploaded_files = set()
        self.failed_files = set()
        self.failures = []
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def submit(self, chunk_tag, file_path):
        self.queue.put((chunk_tag, str(file_path)))

    def _worker(self):
        pending = []
        last_flush = time.time()
        while True:
            try:
                if not pending:
                    item = self.queue.get(timeout=0.5)
                    pending.append(item)
                while len(pending) < self.batch_size:
                    pending.append(self.queue.get_nowait())
            except queue.Empty:
                pass

            now = time.time()
            is_stopping = self.stop_requested.is_set()

            if is_stopping:
                while True:
                    try:
                        pending.append(self.queue.get_nowait())
                    except queue.Empty:
                        break

            should_flush = (
                len(pending) >= self.batch_size or
                (pending and (now - last_flush) >= self.debounce_sec) or
                (is_stopping and pending)
            )

            if should_flush:
                self._flush(pending)
                pending = []
                last_flush = time.time()

            if is_stopping and self.queue.empty() and not pending:
                break

    def _flush(self, items):
        by_tag = {}
        for tag, fpath in items:
            by_tag.setdefault(tag, []).append(fpath)
        for tag, flist in by_tag.items():
            succeeded = upload_batch(self.repo, tag, flist)
            for f in succeeded:
                self.uploaded_files.add(f)
                if self.clean_uploaded:
                    try:
                        Path(f).unlink(missing_ok=True)
                    except Exception:
                        pass
            failed = set(flist) - set(succeeded)
            for f in failed:
                self.failed_files.add(f)
                self.failures.append((tag, f))

    def close(self):
        self.stop_requested.set()
        self.thread.join()
        if self.failures:
            log(f"  ⚠ Toplam {len(self.failures)} yükleme hatası oluştu.")


_CHUNK_ASSET_COUNTS = {}


def get_chunk_tag(package_index, existing=None):
    """En yüksek chunk'tan başlayarak 980 pakete ulaşınca bir sonrakine geçer"""
    global _CHUNK_ASSET_COUNTS
    if not _CHUNK_ASSET_COUNTS and existing:
        for p in existing.values():
            tag = p.get("chunk_tag")
            if tag and tag.startswith("v1.0-chunk-"):
                _CHUNK_ASSET_COUNTS[tag] = _CHUNK_ASSET_COUNTS.get(tag, 0) + 1
    
    if not _CHUNK_ASSET_COUNTS:
        return "v1.0-chunk-37"

    last_tag = max(_CHUNK_ASSET_COUNTS.keys(), key=lambda t: int(t.rsplit("-", 1)[1]))
    if _CHUNK_ASSET_COUNTS.get(last_tag, 0) >= 980:
        n = int(last_tag.rsplit("-", 1)[1]) + 1
        new_tag = f"v1.0-chunk-{n:02d}"
        _CHUNK_ASSET_COUNTS[new_tag] = 0
        return new_tag
    return last_tag


def main():
    ap = argparse.ArgumentParser(description="MindOS High-Throughput x86_64 Package Factory")
    ap.add_argument("--count", type=int, default=500, help="Üretilecek paket sayısı")
    ap.add_argument("--only", default="", help="Yalnızca belirtilen paketler (virgülle ayrılmış)")
    ap.add_argument("--threads", type=int, default=min(24, os.cpu_count() * 2 if os.cpu_count() else 8), help="İş parçacığı sayısı")
    ap.add_argument("--out-dir", default=str(ROOT / "out_x86"), help=".mind dosyalarının kaydedileceği klasör")
    ap.add_argument("--upload", action="store_true", help="GitHub Releases'a otomatik chunk yükle")
    ap.add_argument("--no-upload", dest="upload", action="store_false")
    ap.add_argument("--clean-uploaded", action="store_true", default=True, help="Yüklenen .mind dosyalarını diskten anında temizle")
    ap.add_argument("--no-clean-uploaded", dest="clean_uploaded", action="store_false")
    ap.add_argument("--upload-existing", action="store_true", help="Çıktı klasöründeki mevcut .mind dosyalarını yükle")
    ap.add_argument("--repo", default=REPO_DEFAULT, help="Hedef GitHub deposu")
    ap.add_argument("--x86-manifest", default=str(ROOT.parent / "MindOS_packages_repo" / "packages.json"), help="x86 deposundaki paketlerin önceliklendirilmesi için packages.json yolu")
    ap.add_argument("--dry-run", action="store_true", help="Gerçek indirme yapmadan listele")
    ap.set_defaults(upload=False)
    args = ap.parse_args()

    log(f"🚀 MindOS x86_64 Paket Fabrikası Başlatıldı ({args.threads} iş parçacığı)")
    log(f"   Hedef Depo: {args.repo} | Çıktı Dizini: {args.out_dir} | Yükleme: {args.upload} | Anında Temizle: {args.clean_uploaded}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # İndeks yükle (packages.json veya staging/packages.json)
    target_staging = ROOT / "staging" / "packages.json"
    target_staging_dummy = ROOT / "staging" / "packages-amd64.json"
    target_index = ROOT / "packages.json"

    index_path = target_staging if target_staging.exists() else target_index
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = {}
    else:
        index = {}

    if not index.get("packages"):
        index = {
            "repository": "MindOS Official Package Repository (x86_64)",
            "architecture": ARCH,
            "version": "1.0",
            "total_packages": 0,
            "packages": []
        }

    existing = {p["name"]: p for p in index.get("packages", [])}
    avail = fetch_debian_packages()
    popcon = fetch_popcon()

    log(f"📦 Debian deposunda {len(avail)} paket bulundu. Mevcut arm64 depoda {len(existing)} paket var.")

    if args.upload_existing:
        files = list(out_dir.glob("*.mind"))
        log(f"📦 Yüklenecek mevcut .mind paketi: {len(files)}")
        pending_by_chunk = {}
        for f in files:
            name = f.name.split("-")[0]
            record = existing.get(name)
            if not record:
                continue
            chunk_tag = record.get("chunk_tag") or get_chunk_tag(len(existing))
            pending_by_chunk.setdefault(chunk_tag, []).append(f)
        for chunk_tag, chunk_files in pending_by_chunk.items():
            upload_batch(args.repo, chunk_tag, chunk_files)
        log("✅ Mevcut dosyaların yüklenmesi tamamlandı.")
        return 0

    if args.only.strip():
        targets = [n.strip() for n in args.only.split(",") if n.strip()]
        candidates = [avail[n] for n in targets if n in avail]
        missing = [n for n in targets if n not in avail]
        if missing:
            log(f"  ⚠ Bulunamayan paketler: {', '.join(missing)}")
    else:
        # x86 deposundaki paketleri tespit edip önceliklendir (parite için)
        x86_priority_set = set()
        x86_json = Path(args.x86_manifest) if getattr(args, "x86_manifest", None) else None
        if x86_json and x86_json.exists():
            try:
                d_x = json.loads(x86_json.read_text(encoding="utf-8"))
                pkgs = d_x.get("packages", [])
                if isinstance(pkgs, list):
                    x86_priority_set = {p["name"] if isinstance(p, dict) else p for p in pkgs}
                elif isinstance(pkgs, dict):
                    x86_priority_set = set(pkgs.keys())
                log(f"  🎯 x86 deposundan {len(x86_priority_set)} paket öncelik kümesine eklendi.")
            except Exception as e:
                log(f"  ⚠ x86 manifestosu okunamadı ({e})")

        # Mevcut depoda olmayanları önce x86'da olup olmamasına, sonra popcon sırasına göre seç
        candidates = [p for name, p in avail.items() if name not in existing]
        def sort_key(p):
            in_x86 = 1 if p["name"] in x86_priority_set else 0
            pop = popcon.get(p["name"], 0)
            return (in_x86, pop)

        candidates.sort(key=sort_key, reverse=True)
        candidates = candidates[:args.count]

    log(f"🎯 Dönüştürülecek aday paket sayısı: {len(candidates)}")
    if not candidates:
        log("✅ Dönüştürülecek yeni paket yok.")
        return 0

    if args.dry_run:
        log("İlk 20 aday: " + ", ".join(p["name"] for p in candidates[:20]))
        return 0

    success_count = 0
    fail_count = 0

    uploader = None
    if args.upload:
        uploader = PipelinedUploader(args.repo, clean_uploaded=args.clean_uploaded, batch_size=25, debounce_sec=3)

    start_time = time.time()
    staged_records = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = {executor.submit(process_package, p, out_dir): p["name"] for p in candidates}
        for future in concurrent.futures.as_completed(futures):
            pkg_name = futures[future]
            try:
                ok, res, file_or_err = future.result()
                if ok:
                    success_count += 1
                    record = res
                    chunk_tag = get_chunk_tag(len(existing) + len(staged_records), existing=existing)
                    record["chunk_tag"] = chunk_tag
                    record["url"] = f"https://github.com/{args.repo}/releases/download/{chunk_tag}/{record['filename']}"
                    staged_records[str(file_or_err)] = record
                    log(f"  ✓ [{success_count}/{len(candidates)}] {pkg_name} ({record['size'] // 1024} KB) -> {chunk_tag}")

                    if uploader:
                        uploader.submit(chunk_tag, file_or_err)
                else:
                    fail_count += 1
                    log(f"  ✗ {pkg_name}: {res}")
            except Exception as e:
                fail_count += 1
                log(f"  ✗ {pkg_name}: {e}")

    # Arka plan yüklemelerini tamamla
    if uploader:
        log("  ⏳ Arka plan yüklemeleri tamamlanıyor...")
        uploader.close()
        for fpath, record in staged_records.items():
            if fpath in uploader.uploaded_files:
                existing[record["name"]] = record
            else:
                log(f"  ⚠ Paket GitHub'a yüklenemediği için indekse eklenmedi: {record['name']}")
        log(f"  ⬆️ Toplam {len(uploader.uploaded_files)} paket başarıyla yüklendi.")
    else:
        for record in staged_records.values():
            existing[record["name"]] = record

    elapsed = time.time() - start_time
    rate = (success_count / (elapsed / 60)) if elapsed > 0 else 0
    log(f"\n📊 İşlem Tamamlandı: {success_count} başarılı, {fail_count} başarısız ({elapsed:.1f} sn, ~{rate:.1f} paket/dakika)")

    # staging indekslerini kaydet (hem packages.json hem packages-amd64.json)
    index["packages"] = sorted(existing.values(), key=lambda p: p["name"])
    index["total_packages"] = len(index["packages"])
    index["architecture"] = ARCH
    max_chunk_num = max([int(p.get("chunk_tag", "v1.0-chunk-01").rsplit("-", 1)[1]) for p in index["packages"]] or [1])
    index["total_chunks"] = max_chunk_num

    data_bytes = json.dumps(index, indent=1, ensure_ascii=False)
    target_staging.parent.mkdir(parents=True, exist_ok=True)
    target_staging.write_text(data_bytes, encoding="utf-8")
    target_staging_dummy.write_text(data_bytes, encoding="utf-8")
    log(f"📝 Taslak indeks güncellendi: {target_staging} & {target_staging_dummy} ({index['total_packages']} paket, {max_chunk_num} chunk)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
