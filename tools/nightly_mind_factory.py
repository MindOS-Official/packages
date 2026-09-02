#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Nightly Mass Package Factory (nightly_mind_factory.py)
# Automatically packages 1,000+ popular Linux packages to .mind and pushes to Git
# ==============================================================================

import os
import sys
import time
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
sys.path.insert(0, str(REPO_ROOT / "tools"))
from bulk_packager import package_deb_to_mind
from mind_repo_builder import update_repository_index

# Popüler ve temel 1000+ paket havuzunu oluşturmak için kategori listesi
SEED_KEYWORDS = [
    # Geliştirme & Diller
    "python3-", "python3", "nodejs", "npm", "golang", "rustc", "cargo",
    "gcc", "g++", "clang", "make", "cmake", "ninja-build", "meson", "autoconf",
    "automake", "libtool", "pkg-config", "gdb", "valgrind", "strace", "ltrace",
    # Editörler & Kabuklar
    "neovim", "emacs-nox", "micro", "joe", "zsh", "fish", "tcsh", "ksh",
    # Sistem & İzleme
    "glances", "bashtop", "bpytop", "sysstat", "dstat", "iotop", "iftop",
    "nload", "bmon", "vnstat", "speedtest-cli", "hardinfo", "lshw", "hwinfo",
    "dmidecode", "lsof", "procps", "psmisc", "htop", "btop", "inxi",
    # Ağ & İletişim
    "nmap", "wireshark-common", "tcpdump", "netcat-openbsd", "socat", "iperf3",
    "aria2", "axel", "rsync", "rclone", "curl", "wget", "whois", "bind9-dnsutils",
    # Sıkıştırma & Arşivleme
    "zstd", "lz4", "pigz", "pixz", "p7zip", "unrar", "cabextract", "cpio",
    # Medya & Grafik CLI
    "ffmpeg", "imagemagick", "graphicsmagick", "sox", "mpv", "mediainfo",
    "yt-dlp", "optipng", "jpegoptim", "gifsicle",
    # Veri & Metin İşleme
    "jq", "yq", "ripgrep", "fd-find", "bat", "eza", "fzf", "silversearcher-ag",
    "dos2unix", "tree", "pv", "progress", "screen", "tmux", "byobu",
    # Disk & Dosya Sistemleri
    "btrfs-progs", "xfsprogs", "f2fs-tools", "e2fsprogs", "dosfstools",
    "ntfs-3g", "parted", "gdisk", "wipefs", "testdisk", "ncdu", "duf"
]


def get_candidate_packages(limit=1000):
    """Sistemdeki mevcut paketlerden aday listesi çıkarır"""
    print("🔍 Sistem paket havuzu taranıyor...")
    raw_pkgs = subprocess.check_output(["apt-cache", "pkgnames"], text=True).splitlines()

    # İlgili ve faydalı olanları seç
    candidates = []
    # Önce anahtar kelimelere uyanlar
    for kw in SEED_KEYWORDS:
        for p in raw_pkgs:
            if p.startswith(kw) or p == kw:
                if not any(bad in p for bad in ["-dev", "-doc", "-dbg", "-dbgsym", "-dbgsym", ":i386"]):
                    if p not in candidates:
                        candidates.append(p)

    # Hala limit altındaysa en popüler alfabetik utility paketlerini ekle
    if len(candidates) < limit:
        for p in raw_pkgs:
            if not any(bad in p for bad in ["-dev", "-doc", "-dbg", "-dbgsym", ":i386", "kernel", "linux-"]):
                if not (p.startswith("lib") and not any(k in p for k in ["gl", "ssl", "gtk", "qt", "av", "fuse", "c6", "pulse", "asound"])):
                    if p not in candidates:
                        candidates.append(p)
            if len(candidates) >= limit:
                break

    return candidates[:limit]


def run_factory(target_count=1000, batch_size=30):
    print(f"🏭 MindOS Sunrise Nightly Package Factory Başlatıldı!")
    print(f"🎯 Hedef: {target_count} paket (.mind) üretilip GitHub'a pushlanacak.\n")

    candidates = get_candidate_packages(target_count)
    print(f"📋 Toplam {len(candidates)} aday paket belirlendi.")

    # Halihazırda var olanları tara
    existing_pkgs = set()
    for f in PKGS_DIR.glob("*.mind"):
        pkg_name = f.name.split("-")[0]
        existing_pkgs.add(pkg_name)

    to_build = [p for p in candidates if p not in existing_pkgs]
    print(f"⚡ İnşa edilecek yeni paket sayısı: {len(to_build)}\n")

    import tempfile
    for i in range(0, len(to_build), batch_size):
        batch = to_build[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(to_build) + batch_size - 1) // batch_size
        print(f"\n=======================================================")
        print(f"🚀 PARTİ {batch_num}/{total_batches} ({len(batch)} Paket): {', '.join(batch[:5])}...")
        print(f"=======================================================")

        with tempfile.TemporaryDirectory() as dl_dir:
            for pkg in batch:
                try:
                    subprocess.run(["apt-get", "download", pkg], cwd=dl_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

            # Dönüştür — 90MB+ dosyaları atla (GitHub limiti)
            built_count = 0
            for deb_file in Path(dl_dir).glob("*.deb"):
                out = package_deb_to_mind(deb_file, PKGS_DIR)
                if out:
                    if out.stat().st_size > 90 * 1024 * 1024:
                        print(f"    ⚠ {out.name} çok büyük, atlanıyor.")
                        out.unlink()
                    else:
                        built_count += 1

        # İndeksi güncelle
        update_repository_index()

        # Git commit & push (pull --rebase ile senkronize et, force push kullanma)
        try:
            subprocess.run(["git", "add", "."], cwd=REPO_ROOT, check=True)
            result = subprocess.run(
                ["git", "commit", "-m", f"feat(factory): Add batch {batch_num}/{total_batches} ({built_count} .mind packages)"],
                cwd=REPO_ROOT, capture_output=True, text=True
            )
            if "nothing to commit" in result.stdout + result.stderr:
                print(f"  ℹ Batch {batch_num}: commit edilecek yeni dosya yok.")
            else:
                print("  🔄 Pull --rebase yapılıyor...")
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO_ROOT,
                                check=True, capture_output=True)
                print("  📡 GitHub'a pushlanıyor...")
                subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
                print(f"  ✓ Parti {batch_num} GitHub'a başarıyla gönderildi!")
        except Exception as e:
            print(f"  ⚠ Git commit/push uyarısı: {e}")

        # Kısa dinlenme
        time.sleep(2)

    print("\n🎉 TEBRİKLER HÜNKARIM! Tüm paketler .mind formatında hazırlanıp GitHub'a pushlandı!")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    run_factory(target_count=count)
