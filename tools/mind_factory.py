#!/usr/bin/env python3
"""MindOS sürekli paket fabrikası.

Debian 13 (trixie) deposundaki paketleri .mind biçimine dönüştürür, GitHub Releases
chunk'larına (v1.0-chunk-NN, en fazla 990 dosya) yükler ve taslak indekse
(staging/packages.json) ekler. Taslak, geliştirici tools/sign_index.py ile imzalayınca
packages.json'a geçer; bu sayede GitHub'daki otomasyon imzalı indeksi değiştiremez.
Debian trixie konteynerinde çalışır (MindOS Paradise ile aynı taban).

Her çalıştırmada (en çok kullanılanlar önce, Debian popcon sıralaması):
  1. Yenileme (--updates adet): Debian'da yeni sürümü çıkmış paketler ile eski Ubuntu
     tabanlı (origin alanı olmayan) paketler Debian sürümüyle yeniden üretilir,
  2. Ekleme (--new adet): henüz depoda olmayan Debian paketleri eklenir.

kullanım: mind_factory.py [--new 400] [--updates 150] [--only paket1,paket2] [--dry-run]
GitHub Actions'ta .github/workflows/package-factory.yml tarafından her gün çalıştırılır.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = os.environ.get("MIND_REPO", "MindOS-Official/packages")
ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "packages.json"             # imzalı, kullanıcıların okuduğu indeks
STAGING = ROOT / "staging" / "packages.json"  # fabrikanın yazdığı, henüz imzalanmamış taslak
MAX_ASSETS = 990
BATCH = 40
MAX_SIZE = 1500 * 1024 * 1024  # GitHub asset sınırı 2 GB; büyük paketler atlanır
MAINTAINER = "MindOS Team <https://github.com/MindOS-Official>"
ORIGIN = os.environ.get("MIND_ORIGIN", "debian/trixie")
# 2: Debian kurulum/kaldırma betikleri pakete dahil (format 1 paketler yeniden üretilir)
FORMAT = 2

# Debian betiği -> .mind kancası, betiğe verilecek argüman
MAINTSCRIPTS = {"preinst": ("pre_install.sh", "install"), "postinst": ("post_install.sh", "configure"),
                "prerm": ("pre_remove.sh", "remove"), "postrm": ("post_remove.sh", "remove")}


def hook_wrapper(pkg_name, script_name, arg, script_bytes):
    """Debian betiğini .mind kancasına sarar. Betik hata verirse (ör. dpkg'siz sistemde
    deb-systemd-helper yoksa) kurulum durmaz, uyarı yazılır; kanca her zaman 0 döner."""
    b64 = base64.b64encode(script_bytes).decode()
    return f"""#!/bin/sh
# {pkg_name}: Debian {script_name} betiği (mind_factory tarafından eklendi)
mkdir -p /var/lib/mindpkg/tmp; t=$(mktemp -p /var/lib/mindpkg/tmp) || exit 0
echo '{b64}' | base64 -d > "$t" && chmod 700 "$t"
DPKG_MAINTSCRIPT_PACKAGE={pkg_name} DPKG_MAINTSCRIPT_NAME={script_name} DPKG_ROOT= \
DEBIAN_FRONTEND=noninteractive "$t" {arg} || echo "  ⚠ {pkg_name}: {script_name} betiği tamamlanamadı (sistem kendi kendine çalışmaya devam eder)" >&2
rm -f "$t"
exit 0
"""

# .mind deposuna alınmayan paketler: kernel, hata ayıklama sembolleri, geçiş paketleri
SKIP_RE = re.compile(
    r"^(linux-(image|modules|headers|objects|signatures|buildinfo|tools|cloud-tools|lib-rust|bpf-dev|"
    r"source|doc|riscv|lowlatency|oem|hwe|gcp|aws|azure|oracle|ibm|kvm|nvidia|intel|realtime)"
    r"|.*-dbg(sym)?$|.*-dbgsym$)"
)


def log(msg):
    print(msg, flush=True)


def gh(*args, check=True):
    """gh CLI; ikincil hız sınırına takılırsa bekleyip yeniden dener"""
    for attempt in range(8):
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode == 0 or not check:
            return r
        err = r.stderr.strip()
        if any(x in err.lower() for x in ("rate limit", "secondary", "502", "503", "504", "timeout", "timed out")):
            wait = 60 * (attempt + 1)
            log(f"  ⏳ GitHub sınırı, {wait} sn bekleniyor: {err[:120]}")
            time.sleep(wait)
            continue
        raise RuntimeError(f"gh {' '.join(args[:3])}: {err[:400]}")
    raise RuntimeError("gh: çok fazla yeniden deneme")


def asset_name(filename):
    """GitHub, asset adlarındaki ':' '~' gibi karakterleri '.' yapar"""
    return re.sub(r"[^A-Za-z0-9._+-]", ".", filename)


def newer(a, b):
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def available_packages(deb_arch="amd64"):
    """apt deposundaki aday paketler: ad -> {version, size, arch}"""
    out = subprocess.run(["apt-cache", "dumpavail"], capture_output=True, text=True, check=True).stdout
    pkgs = {}
    for stanza in out.split("\n\n"):
        f = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ":" in line:
                k, v = line.split(":", 1)
                f[k] = v.strip()
        name, ver = f.get("Package"), f.get("Version")
        if not name or not ver or f.get("Architecture") not in (deb_arch, "all"):
            continue
        if SKIP_RE.match(name) or "transitional" in f.get("Description", "").lower():
            continue
        if int(f.get("Size", "0") or 0) > MAX_SIZE:
            continue
        old = pkgs.get(name)
        if old is None or newer(ver, old["version"]):
            pkgs[name] = {"version": ver, "arch": f.get("Architecture")}
    return pkgs


def popcon_rank():
    try:
        with urllib.request.urlopen("https://popcon.debian.org/by_inst.gz", timeout=60) as r:
            text = gzip.decompress(r.read()).decode("utf-8", "replace")
    except Exception as e:
        log(f"  ⚠ popcon alınamadı ({e}), alfabetik sıra kullanılacak")
        return {}
    rank = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) > 2 and parts[0].isdigit():
            rank.setdefault(parts[1], int(parts[0]))
    return rank


def deb_to_mind(deb, outdir, arch="x86_64"):
    """Bir .deb dosyasını .mind paketine dönüştürür; dosya sahipleri root olarak yazılır"""
    field = lambda k: subprocess.run(["dpkg-deb", "-f", str(deb), k], capture_output=True, text=True).stdout.strip()
    name, version = field("Package"), field("Version")
    desc = field("Description").splitlines()[0] if field("Description") else name
    # Debian bağımlılıkları olduğu gibi saklanır ("a (>= 1) | b"); mindpkg eksik olanları kurar
    deps = [d.strip() for k in ("Pre-Depends", "Depends") for d in field(k).split(",") if d.strip()]
    meta = {"name": name, "version": version, "release": "1", "architecture": arch,
            "description": desc, "maintainer": MAINTAINER, "dependencies": deps, "origin": ORIGIN,
            "format": FORMAT}
    filename = f"{name}-{version}-1-{arch}.mind"
    with tempfile.TemporaryDirectory(dir=outdir) as tmp:
        tmp = Path(tmp)
        root = tmp / "root"
        root.mkdir()
        subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
        pkg = tmp / "pkg"
        pkg.mkdir()
        ctrl = tmp / "control"
        subprocess.run(["dpkg-deb", "-e", str(deb), str(ctrl)], check=True)
        hooks = []
        for script, (hook, arg) in MAINTSCRIPTS.items():
            src = ctrl / script
            if src.exists():
                (pkg / hook).write_text(hook_wrapper(name, script, arg, src.read_bytes()), encoding="utf-8")
                (pkg / hook).chmod(0o755)
                hooks.append(script)
        meta["maintscripts"] = hooks
        (pkg / "metadata.json").write_text(json.dumps(meta, indent=4, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["tar", "--zstd", "-cf", str(pkg / "files.tar.zst"), "--owner=0", "--group=0",
                        "--numeric-owner", "-C", str(root), "."], check=True)
        out = Path(outdir) / filename
        subprocess.run(["tar", "--zstd", "-cf", str(out), "--owner=0", "--group=0", "--numeric-owner",
                        "-C", str(pkg), "."], check=True)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    return out, dict(meta, filename=filename, sha256=sha, size=out.stat().st_size)


def current_chunk(idx_path=None):
    """Yazılabilir son chunk: (etiket, asset sayısı)"""
    # İndeksteki bilinen en yüksek chunk etiketinden başla; tüm releases geçmişini sayfalamak HTTP 504 verir
    if idx_path is None:
        idx_path = STAGING if STAGING.exists() else INDEX
    known_chunks = []
    if idx_path.exists():
        try:
            raw = idx_path.read_text(encoding="utf-8")
            known_chunks = re.findall(r"v1\.0-chunk-\d+", raw)
        except Exception:
            pass
    if known_chunks:
        last = max(set(known_chunks), key=lambda t: int(t.rsplit("-", 1)[1]))
    else:
        try:
            tags = [t for t in gh("api", f"repos/{REPO}/releases?per_page=30",
                                  "--jq", ".[].tag_name", check=False).stdout.split() if re.fullmatch(r"v1\.0-chunk-\d+", t)]
        except Exception:
            tags = []
        if tags:
            last = max(tags, key=lambda t: int(t.rsplit("-", 1)[1]))
        else:
            last = "v1.0-chunk-01"
            gh("release", "create", last, "-R", REPO, "--title", "MindOS Packages Chunk 01",
               "--notes", "MindOS .mind packages (part 1)", check=False)
            return last, 0
    try:
        count = int(gh("api", f"repos/{REPO}/releases/tags/{last}", "--jq", ".assets | length").stdout.strip())
        if count >= 100:  # .assets ilk 100'ü döndürür; gerçek sayı ayrı sayfalı uçtan alınır
            rid = gh("api", f"repos/{REPO}/releases/tags/{last}", "--jq", ".id").stdout.strip()
            count = len(gh("api", f"repos/{REPO}/releases/{rid}/assets?per_page=100", "--paginate",
                           "--jq", ".[].id").stdout.split())
    except Exception:
        count = 0
    return last, count


def next_chunk(tag):
    n = int(tag.rsplit("-", 1)[1]) + 1
    new = f"v1.0-chunk-{n:02d}"
    gh("release", "create", new, "-R", REPO, "--title", f"MindOS Packages Chunk {n:02d}",
       "--notes", f"MindOS .mind packages (part {n})")
    log(f"  🏷️ Yeni chunk açıldı: {new}")
    return new


def release_urls(tag):
    """chunk'taki asset'ler: sha256 -> indirme adresi"""
    rid = gh("api", f"repos/{REPO}/releases/tags/{tag}", "--jq", ".id").stdout.strip()
    out = gh("api", f"repos/{REPO}/releases/{rid}/assets?per_page=100", "--paginate",
             "--jq", '.[] | [.digest, .browser_download_url] | @tsv').stdout
    return {d.replace("sha256:", ""): u for d, u in (l.split("\t") for l in out.splitlines() if "\t" in l)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=["x86_64", "arm64", "aarch64"],
                    default=os.environ.get("MIND_ARCH", "x86_64"),
                    help="hedef paket mimarisi (x86_64 veya arm64/aarch64)")
    ap.add_argument("--repo", default="", help="hedef GitHub deposu (ör: MindOS-Official/packages-arm64)")
    ap.add_argument("--new", type=int, default=400)
    ap.add_argument("--updates", type=int, default=150)
    ap.add_argument("--only", default="", help="yalnızca bu paketler (virgül/boşlukla ayrılmış)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    arch_name = "aarch64" if args.arch in ("arm64", "aarch64") else "x86_64"
    deb_arch = "arm64" if arch_name == "aarch64" else "amd64"
    repo = args.repo or os.environ.get("MIND_REPO") or (
        "MindOS-Official/packages-arm64" if arch_name == "aarch64" else "MindOS-Official/packages"
    )
    global REPO
    REPO = repo
    log(f"🚀 MindOS Paket Fabrikası: Mimari={arch_name} (Debian: {deb_arch}), Depo={REPO}")

    # Mimariye göre indeks ve taslak dosya yolları
    if arch_name == "aarch64":
        is_dedicated = False
        if INDEX.exists():
            try:
                data = json.loads(INDEX.read_text(encoding="utf-8"))
                if data.get("architecture") == "aarch64":
                    is_dedicated = True
            except Exception:
                pass
        if is_dedicated:
            idx_file, stg_file = INDEX, STAGING
        else:
            idx_file, stg_file = ROOT / "packages-arm64.json", ROOT / "staging" / "packages-arm64.json"
    else:
        idx_file, stg_file = INDEX, STAGING

    cur_idx_path = stg_file if stg_file.exists() else idx_file
    index = json.loads(cur_idx_path.read_text(encoding="utf-8")) if cur_idx_path.exists() else {
        "repository": f"MindOS Sunrise Official Package Repository ({arch_name})",
        "architecture": arch_name,
        "version": "1.0",
        "total_packages": 0,
        "packages": []
    }
    by_name = {p["name"]: p for p in index.get("packages", [])}
    avail = available_packages(deb_arch)
    rank = popcon_rank()
    order = lambda n: (rank.get(n, 10 ** 9), n)

    def needs_update(n):
        old = by_name[n]
        if old.get("origin") != ORIGIN:  # eski Ubuntu tabanlı paket: Debian sürümüyle değiştirilir
            return True
        if int(old.get("format", 1)) < FORMAT:  # betikleri olmayan eski biçim
            return True
        return newer(avail[n]["version"], str(old.get("version", "0")))

    updates = sorted((n for n in avail if n in by_name and needs_update(n)), key=order)[:args.updates]
    fresh = sorted((n for n in avail if n not in by_name), key=order)[:args.new]
    if args.only.strip():  # istenen paketler: depoda olsa da olmasa da yeniden üretilir
        only = [n for n in re.split(r"[\s,]+", args.only.strip()) if n]
        missing = [n for n in only if n not in avail]
        if missing:
            log(f"  ⚠ Debian'da bulunamadı: {' '.join(missing)}")
        updates = [n for n in only if n in avail and n in by_name]
        fresh = [n for n in only if n in avail and n not in by_name]
    log(f"📦 Depoda {len(by_name)} paket, apt'de {len(avail)} aday. "
        f"Yenilenecek: {len(updates)}, eklenecek: {len(fresh)}")
    if args.dry_run:
        log("Yenilenecek: " + " ".join(updates[:30]))
        log("Eklenecek:   " + " ".join(fresh[:60]))
        return

    tag, count = current_chunk(idx_path=cur_idx_path)
    log(f"  Son chunk: {tag} ({count} dosya)")
    work = Path(tempfile.mkdtemp(prefix="mind-factory-"))
    added = updated = failed = 0
    todo = [(n, True) for n in updates] + [(n, False) for n in fresh]
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        dl = work / "deb"
        out = work / "mind"
        shutil.rmtree(dl, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        dl.mkdir()
        out.mkdir()
        built = []
        for name, is_update in batch:
            dl_spec = f"{name}:{deb_arch}={avail[name]['version']}" if deb_arch != "amd64" else f"{name}={avail[name]['version']}"
            r = subprocess.run(["apt-get", "download", "-q", dl_spec],
                               cwd=dl, capture_output=True, text=True)
            if r.returncode != 0:
                r = subprocess.run(["apt-get", "download", "-q", f"{name}={avail[name]['version']}"],
                                   cwd=dl, capture_output=True, text=True)
            debs = list(dl.glob(f"{name}_*.deb"))
            if r.returncode != 0 or not debs:
                failed += 1
                continue
            try:
                path, meta = deb_to_mind(debs[0], out, arch=arch_name)
                built.append((path, meta, is_update))
            except Exception as e:
                log(f"  ⚠ {name}: {e}")
                failed += 1
            for d in debs:
                d.unlink()
        if not built:
            continue
        # chunk dolarsa yenisine geç
        if count + len(built) > MAX_ASSETS:
            tag, count = next_chunk(tag), 0
        gh("release", "upload", tag, "-R", REPO, "--clobber", *[str(p) for p, _, _ in built])
        count += len(built)
        urls = release_urls(tag)
        for path, meta, is_update in built:
            url = urls.get(meta["sha256"])
            if not url:
                log(f"  ⚠ {meta['name']}: yüklenen dosya bulunamadı")
                failed += 1
                continue
            by_name[meta["name"]] = dict(meta, url=url, chunk_tag=tag)
            updated += is_update
            added += not is_update
        log(f"  ✓ {min(i + BATCH, len(todo))}/{len(todo)} işlendi → {tag} ({count} dosya)")
        # her partiden sonra indeksi yaz: iş yarıda kesilse bile yüklenenler kaybolmaz
        index["packages"] = sorted(by_name.values(), key=lambda p: p["name"])
        index["total_packages"] = len(index["packages"])
        index["total_chunks"] = int(tag.rsplit("-", 1)[1])
        stg_file.parent.mkdir(exist_ok=True)
        stg_file.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")

    shutil.rmtree(work, ignore_errors=True)
    summary = (f"Eklenen: {added}, yenilenen: {updated}, başarısız: {failed}. "
               f"Depoda toplam {len(by_name)} paket, son chunk {tag}.")
    log("✅ " + summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### MindOS paket fabrikası\n\n{summary}\n")


if __name__ == "__main__":
    sys.exit(main())
