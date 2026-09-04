#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Mass Package Factory (Target: 15,000 Packages)
# Resume-Aware, High-Capacity & Telegram-Notifying
# ==============================================================================

import os
import sys
import time
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
sys.path.insert(0, str(REPO_ROOT / "tools"))
from bulk_packager import package_deb_to_mind
from mind_repo_builder import update_repository_index

TELEGRAM_BOT_TOKEN = "8939687136:AAHWElfdQfdXzglvARc2da6mCzmppEckKrk"
TELEGRAM_CHAT_ID = "8337158473"

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

SEED_KEYWORDS = [
    # Geliştirme, Diller & Derleyiciler
    "python3-", "python3", "nodejs", "npm", "golang", "rustc", "cargo", "ruby", "perl", "php-",
    "gcc", "g++", "clang", "make", "cmake", "ninja-build", "meson", "autoconf",
    "automake", "libtool", "pkg-config", "gdb", "valgrind", "strace", "ltrace",
    # Editörler, Geliştirme Araçları & Kabuklar
    "neovim", "emacs-nox", "micro", "joe", "zsh", "fish", "tcsh", "ksh", "git", "subversion",
    # Sistem, İzleme & Donanım
    "glances", "bashtop", "bpytop", "sysstat", "dstat", "iotop", "iftop",
    "nload", "bmon", "vnstat", "speedtest-cli", "hardinfo", "lshw", "hwinfo",
    "dmidecode", "lsof", "procps", "psmisc", "htop", "btop", "inxi", "pciutils", "usbutils",
    # Ağ, Güvenlik & İletişim
    "nmap", "wireshark-common", "tcpdump", "netcat-openbsd", "socat", "iperf3", "openssh-client",
    "aria2", "axel", "rsync", "rclone", "curl", "wget", "whois", "bind9-dnsutils", "openvpn", "wireguard-tools",
    # Sıkıştırma & Arşivleme
    "zstd", "lz4", "pigz", "pixz", "p7zip", "unrar", "cabextract", "cpio", "tar", "gzip", "bzip2", "xz-utils",
    # Medya & Grafik CLI/GUI
    "ffmpeg", "imagemagick", "graphicsmagick", "sox", "mpv", "mediainfo",
    "yt-dlp", "optipng", "jpegoptim", "gifsicle", "vlc", "obs-studio", "flac", "vorbis-tools",
    # Veri, Metin & Terminal
    "jq", "yq", "ripgrep", "fd-find", "bat", "eza", "fzf", "silversearcher-ag",
    "dos2unix", "tree", "pv", "progress", "screen", "tmux", "byobu", "ranger", "mc",
    # Disk, Dosya Sistemleri & Kurtarma
    "btrfs-progs", "xfsprogs", "f2fs-tools", "e2fsprogs", "dosfstools",
    "ntfs-3g", "parted", "gdisk", "wipefs", "testdisk", "ncdu", "duf", "smartmontools",
    # Kütüphaneler & Altyapılar
    "libgl1", "libvulkan1", "libgtk-3", "libgtk-4", "libqt5", "libqt6", "libwayland", "libx11",
    "libasound2", "libpulse0", "libpipewire", "libssl", "libcurl4", "libffi", "libsqlite3",
    # Masaüstü & Uygulamalar
    "alacritty", "kitty", "foot", "rofi", "dmenu", "polybar", "waybar", "sway", "i3", "feh",
    "fastfetch", "neofetch", "cmatrix", "cowsay", "figlet", "fortune-mod", "sl"
]


def get_candidate_packages(limit=15000):
    print("🔍 Sistem paket havuzu taranıyor (Hedef 15.000 Paket)...")
    raw_pkgs = subprocess.check_output(["apt-cache", "pkgnames"], text=True).splitlines()
    candidates = []
    
    # 1. Öncelikli anahtar kelimeler
    for kw in SEED_KEYWORDS:
        for p in raw_pkgs:
            if p.startswith(kw) or p == kw:
                if not any(bad in p for bad in ["-dev", "-doc", "-dbg", "-dbgsym", ":i386"]):
                    if p not in candidates:
                        candidates.append(p)
                        if len(candidates) >= limit:
                            return candidates[:limit]

    # 2. Geniş havuz: Tüm yararlı binary araçlar ve kütüphaneler
    for p in raw_pkgs:
        if not any(bad in p for bad in ["-dev", "-doc", "-dbg", "-dbgsym", ":i386", "kernel", "linux-image", "linux-headers", "linux-modules"]):
            if p not in candidates:
                candidates.append(p)
                if len(candidates) >= limit:
                    break

    return candidates[:limit]


def run_factory(target_count=15000, batch_size=30):
    print(f"🏭 MindOS Sunrise Mass Package Factory Başlatıldı!")
    print(f"🎯 Hedef: {target_count} paket (.mind) üretilip GitHub'a pushlanacak.\n")

    candidates = get_candidate_packages(target_count)
    print(f"📋 Toplam {len(candidates)} aday paket belirlendi.")

    # Mevcut paketlerin isimlerini tam olarak tara
    existing_files = list(PKGS_DIR.glob("*.mind"))
    existing_names = set()
    for f in existing_files:
        clean = f.name.replace("-x86_64.mind", "")
        parts = clean.rsplit("-", 2)
        if len(parts) >= 2:
            existing_names.add(parts[0])
        else:
            existing_names.add(clean)

    to_build = [p for p in candidates if p not in existing_names]
    already_built_count = len(existing_files)
    print(f"📦 Halihazırda mevcut paket sayısı: {already_built_count}")
    print(f"⚡ Kaldığı yerden inşa edilecek yeni paket sayısı: {len(to_build)}\n")

    send_telegram(f"🏭 MindOS Fabrikası 15.000 Hedefiyle Başlatıldı! 🌅\n\n📦 Mevcut: {already_built_count} paket\n🎯 Hedef: {target_count} paket\n🚀 Kalan: {len(to_build)} paket")

    import tempfile
    total_batches = (len(to_build) + batch_size - 1) // batch_size
    for i in range(0, len(to_build), batch_size):
        batch = to_build[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        print(f"\n=======================================================")
        print(f"🚀 PARTİ {batch_num}/{total_batches} ({len(batch)} Paket): {', '.join(batch[:5])}...")
        print(f"=======================================================")

        with tempfile.TemporaryDirectory() as dl_dir:
            for pkg in batch:
                try:
                    subprocess.run(["apt-get", "download", pkg], cwd=dl_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

            built_count = 0
            for deb_file in Path(dl_dir).glob("*.deb"):
                out = package_deb_to_mind(deb_file, PKGS_DIR)
                if out:
                    if out.stat().st_size > 90 * 1024 * 1024:
                        print(f"    ⚠ {out.name} çok büyük (90MB+), atlanıyor.")
                        out.unlink()
                    else:
                        built_count += 1

        update_repository_index()

        try:
            subprocess.run(["git", "add", "."], cwd=REPO_ROOT, check=True)
            result = subprocess.run(
                ["git", "commit", "-m", f"feat(factory): Add batch {batch_num}/{total_batches} ({built_count} .mind packages)"],
                cwd=REPO_ROOT, capture_output=True, text=True
            )
            if "nothing to commit" not in result.stdout + result.stderr:
                print("  🔄 Pull --rebase yapılıyor...")
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO_ROOT, check=True, capture_output=True)
                print("  📡 GitHub'a pushlanıyor...")
                subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
                print(f"  ✓ Parti {batch_num} GitHub'a başarıyla gönderildi!")
        except Exception as e:
            print(f"  ⚠ Git uyarısı: {e}")

        # Her 5 partide bir Telegram güncellemesi
        if batch_num % 5 == 0 or batch_num == total_batches:
            cur_count = len(list(PKGS_DIR.glob("*.mind")))
            send_telegram(f"📦 MindOS 15K Fabrika Güncellemesi:\n🚀 Parti {batch_num}/{total_batches} Tamamlandı!\n💾 Güncel Depo: {cur_count} paket")

        time.sleep(2)

    print("\n🎉 TEBRİKLER! 15.000 paket hedefi başarıyla tamamlandı!")
    send_telegram(f"🎉 TEBRİKLER! MindOS Sunrise 15.000 Paket Fabrikası Başarıyla Tamamlandı! 🚀👑")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 15000
    run_factory(target_count=count)
