#!/usr/bin/env python3
"""MindOS Yüksek Başarımlı ARM64 Paket Fabrikası (Yerel & CI).

Debian 13 (trixie) deposundaki arm64 ve all paketlerini doğrudan Debian CDN
havuzundan yüksek hızda indirir, çoklu iş parçacığıyla .mind paketine dönüştürür.
Debian popcon sıralamasını kullanarak en popüler ve temel paketleri önceliklendirir.

Özellikler:
  • Debian Packages.xz indeksini doğrudan işler (apt bağımlılığı ve root gerektirmez).
  • Eşzamanlı indirme ve dönüştürme (ThreadPoolExecutor / nproc paralelleştirme).
  • Yerel paketleme (--no-upload) veya GitHub Releases'a otomatik chunk yükleme (--upload).
  • staging/packages.json taslağını oluşturur ve günceller.

Kullanım:
  python3 tools/batch_arm64_factory.py --count 500 --no-upload
  python3 tools/batch_arm64_factory.py --count 2000 --upload --repo MindOS-Official/packages-arm64
  python3 tools/batch_arm64_factory.py --only "htop,curl,git,fastfetch,nano,tmux" --no-upload
"""

import argparse
import base64
import concurrent.futures
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_DEFAULT = os.environ.get("MIND_REPO_ARM64", "MindOS-Official/packages-arm64")
MIRROR = "http://deb.debian.org/debian"
INDEX_URLS = [
    f"{MIRROR}/dists/trixie/main/binary-arm64/Packages.xz",
    f"{MIRROR}/dists/trixie/main/binary-all/Packages.xz"
]
POPCON_URL = "https://popcon.debian.org/by_inst.gz"

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "staging" / "packages-arm64.json"
INDEX = ROOT / "packages-arm64.json"
CACHE_DIR = Path.home() / ".cache" / "mindos-arm64-factory"
MAX_ASSETS = 990
MAX_SIZE = 1500 * 1024 * 1024
MAINTAINER = "MindOS Team <https://github.com/MindOS-Official>"
ORIGIN = "debian/trixie"
FORMAT = 2
ARCH = "aarch64"

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


def log(msg):
    print(msg, flush=True)


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
        arch_type = "arm64" if "arm64" in url else "all"
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
    req = urllib.request.Request(url, headers={"User-Agent": "MindOS-Factory/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest_path, "wb") as f:
        shutil.copyfileobj(resp, f)


def convert_deb_to_mind(deb_path, out_dir, pkg_info):
    name = pkg_info["name"]
    version = pkg_info["version"]
    desc = pkg_info["description"]
    deps = pkg_info["dependencies"]

    filename = f"{name}-{version}-1-{ARCH}.mind"
    out_file = Path(out_dir) / filename

    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
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

        subprocess.run(["tar", "--zstd", "-cf", str(pkg / "files.tar.zst"), "--owner=0", "--group=0",
                        "--numeric-owner", "-C", str(root), "."], check=True, capture_output=True)
        subprocess.run(["tar", "--zstd", "-cf", str(out_file), "--owner=0", "--group=0", "--numeric-owner",
                        "-C", str(pkg), "."], check=True, capture_output=True)

    sha = hashlib.sha256(out_file.read_bytes()).hexdigest()
    record = dict(meta, filename=filename, sha256=sha, size=out_file.stat().st_size)
    return out_file, record


def process_package(pkg_info, out_dir):
    try:
        with tempfile.NamedTemporaryFile(suffix=".deb") as tmp_deb:
            download_deb(pkg_info["filename_deb"], tmp_deb.name)
            out_file, record = convert_deb_to_mind(tmp_deb.name, out_dir, pkg_info)
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
            wait = 30 * (attempt + 1)
            log(f"  ⏳ GitHub API sınırı ({wait} sn bekleniyor): {err[:100]}")
            time.sleep(wait)
            continue
        raise RuntimeError(f"gh {' '.join(args[:3])}: {err[:200]}")
    return r


def ensure_chunk_release(repo, tag):
    """Belirtilen chunk etiketini GitHub Releases'da kontrol eder, yoksa oluşturur"""
    view = gh_cmd("release", "view", tag, "-R", repo, check=False)
    if view.returncode != 0:
        n = int(tag.rsplit("-", 1)[1])
        gh_cmd("release", "create", tag, "-R", repo, "--title", f"MindOS ARM64 Packages Chunk {n:02d}",
               "--notes", f"MindOS ARM64 .mind packages (chunk {n})", check=True)
        log(f"  🏷️ GitHub Releases chunk oluşturuldu: {tag}")


def upload_batch(repo, tag, file_paths):
    """Bir grup .mind paketini GitHub Releases chunk'ına yükler"""
    if not file_paths:
        return
    ensure_chunk_release(repo, tag)
    log(f"  ⬆️ {len(file_paths)} paket {tag} etiketine yükleniyor...")
    gh_cmd("release", "upload", tag, "-R", repo, "--clobber", *[str(p) for p in file_paths], check=True)


def get_chunk_tag(package_index):
    chunk_num = (package_index // MAX_ASSETS) + 1
    return f"v1.0-chunk-{chunk_num:02d}"


def main():
    ap = argparse.ArgumentParser(description="MindOS High-Throughput ARM64 Package Factory")
    ap.add_argument("--count", type=int, default=500, help="Üretilecek paket sayısı")
    ap.add_argument("--only", default="", help="Yalnızca belirtilen paketler (virgülle ayrılmış)")
    ap.add_argument("--threads", type=int, default=min(16, os.cpu_count() or 4), help="İş parçacığı sayısı")
    ap.add_argument("--out-dir", default=str(ROOT / "out_arm64"), help=".mind dosyalarının kaydedileceği klasör")
    ap.add_argument("--upload", action="store_true", help="GitHub Releases'a otomatik chunk yükle")
    ap.add_argument("--no-upload", dest="upload", action="store_false")
    ap.add_argument("--upload-existing", action="store_true", help="Çıktı klasöründeki mevcut .mind dosyalarını yükle")
    ap.add_argument("--repo", default=REPO_DEFAULT, help="Hedef GitHub deposu")
    ap.add_argument("--dry-run", action="store_true", help="Gerçek indirme yapmadan listele")
    ap.set_defaults(upload=False)
    args = ap.parse_args()

    log(f"🚀 MindOS ARM64 Paket Fabrikası Başlatıldı ({args.threads} iş parçacığı)")
    log(f"   Hedef Depo: {args.repo} | Çıktı Dizini: {args.out_dir} | Yükleme: {args.upload}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # İndeks yükle veya oluştur (ayrı repo veya ana repo arm64 taslağı)
    main_packages = ROOT / "packages.json"
    is_dedicated = False
    if main_packages.exists():
        try:
            data = json.loads(main_packages.read_text(encoding="utf-8"))
            if data.get("architecture") in ("aarch64", "arm64"):
                is_dedicated = True
        except Exception:
            pass
    elif "packages-arm64" in ROOT.name:
        is_dedicated = True

    target_staging = ROOT / "staging" / "packages.json" if is_dedicated else (ROOT / "staging" / "packages-arm64.json")
    target_index = ROOT / "packages.json" if is_dedicated else (ROOT / "packages-arm64.json")

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
            "repository": "MindOS Sunrise Official Package Repository (aarch64)",
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
        # Mevcut depoda olmayanları popcon sırasına göre seç
        candidates = [p for name, p in avail.items() if name not in existing]
        candidates.sort(key=lambda p: popcon.get(p["name"], 0), reverse=True)
        candidates = candidates[:args.count]

    log(f"🎯 Dönüştürülecek paket sayısı: {len(candidates)}")
    if not candidates:
        log("✅ Dönüştürülecek yeni paket yok.")
        return 0

    if args.dry_run:
        log("İlk 20 aday: " + ", ".join(p["name"] for p in candidates[:20]))
        return 0

    success_count = 0
    fail_count = 0
    pending_uploads = []

    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = {executor.submit(process_package, p, out_dir): p["name"] for p in candidates}
        for future in concurrent.futures.as_completed(futures):
            pkg_name = futures[future]
            try:
                ok, res, file_or_err = future.result()
                if ok:
                    success_count += 1
                    record = res
                    chunk_tag = get_chunk_tag(len(existing))
                    record["chunk_tag"] = chunk_tag
                    record["url"] = f"https://github.com/{args.repo}/releases/download/{chunk_tag}/{record['filename']}"
                    existing[record["name"]] = record
                    log(f"  ✓ [{success_count}/{len(candidates)}] {pkg_name} ({record['size'] // 1024} KB) -> {chunk_tag}")

                    if args.upload:
                        pending_uploads.append((chunk_tag, file_or_err))
                        if len(pending_uploads) >= 20:
                            # Grup olarak yükle
                            by_tag = {}
                            for t, f in pending_uploads:
                                by_tag.setdefault(t, []).append(f)
                            for t, flist in by_tag.items():
                                upload_batch(args.repo, t, flist)
                            pending_uploads = []
                else:
                    fail_count += 1
                    log(f"  ✗ {pkg_name}: {res}")
            except Exception as e:
                fail_count += 1
                log(f"  ✗ {pkg_name}: {e}")

    # Kalan yüklemeleri tamamla
    if args.upload and pending_uploads:
        by_tag = {}
        for t, f in pending_uploads:
            by_tag.setdefault(t, []).append(f)
        for t, flist in by_tag.items():
            upload_batch(args.repo, t, flist)

    elapsed = time.time() - start_time
    log(f"\n📊 İşlem Tamamlandı: {success_count} başarılı, {fail_count} başarısız ({elapsed:.1f} sn)")

    # staging indeksini kaydet
    index["packages"] = sorted(existing.values(), key=lambda p: p["name"])
    index["total_packages"] = len(index["packages"])
    index["architecture"] = ARCH
    max_chunk_num = max([int(p.get("chunk_tag", "v1.0-chunk-01").rsplit("-", 1)[1]) for p in index["packages"]] or [1])
    index["total_chunks"] = max_chunk_num

    target_staging.parent.mkdir(parents=True, exist_ok=True)
    target_staging.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"📝 Taslak indeks güncellendi: {target_staging} ({index['total_packages']} paket, {max_chunk_num} chunk)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
