#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Crucial Suite 2 (package_crucial_suite2.py)
# Additional critical apps: Firefox, LibreOffice, Inkscape, Telegram, Docker, etc.
# ==============================================================================

import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

REPO_ROOT = Path("/media/mamish/Samsung/MindOS_packages_repo")
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
sys.path.insert(0, str(REPO_ROOT / "tools"))
from bulk_packager import package_deb_to_mind
from mind_repo_builder import update_repository_index

CRUCIAL_LIST_2 = [
    # Tarayıcılar & İnternet
    "firefox", "chromium-browser", "thunderbird", "telegram-desktop", "hexchat", "irssi",
    # Ofis & Grafik
    "inkscape", "scribus", "rawtherapee", "darktable", "critcl", "dia",
    "libreoffice-writer", "libreoffice-calc", "libreoffice-impress", "libreoffice-draw",
    # Medya & Çalma
    "strawberry", "clementine", "rhythmbox", "handbrake", "soundconverter", "mkvtoolnix",
    # Geliştirme & DevOps
    "docker.io", "docker-compose", "git-lfs", "shellcheck", "valgrind", "sqlite3",
    "postgresql-client", "mysql-client", "redis-tools", "meld", "d-feet",
    # Oyun & Sistem
    "gamemode", "mangohud", "retroarch", "dosbox", "minetest", "supertuxkart",
    # Yardımcı & Güvenlik
    "keepassxc", "gparted", "bleachbit", "baobab", "timeshift", "hardinfo"
]

def main():
    print("🚀 Crucial Suite 2 Paketleme Başlatılıyor...")
    PKGS_DIR.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        for pkg in CRUCIAL_LIST_2:
            print(f"📦 Apt indiriliyor: {pkg}")
            try:
                subprocess.run(
                    ["apt-get", "download", pkg],
                    cwd=tmp_dir, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"  ⚠ Atlandı ({pkg}): {e}")

        for deb in tmp_path.glob("*.deb"):
            out = package_deb_to_mind(deb, PKGS_DIR)
            if out:
                print(f"  ✅ Paketlendi: {out.name}")

    print("\n📑 Repo indeksi güncelleniyor...")
    update_repository_index()

    print("📡 GitHub'a pushlanıyor...")
    try:
        subprocess.run(["git", "add", "."], cwd=REPO_ROOT, check=True)
        subprocess.run(
            ["git", "commit", "-m", "feat(crucial-2): Add extended crucial desktop & dev apps (Firefox, LibreOffice, Inkscape, Docker, KeePassXC, etc.)"],
            cwd=REPO_ROOT,
            check=True
        )
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
        print("🎉 Crucial Suite 2 başarıyla GitHub'a yüklendi!")
    except Exception as e:
        print(f"⚠ Push uyarısı: {e}")

if __name__ == "__main__":
    main()
