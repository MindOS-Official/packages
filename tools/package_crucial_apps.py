#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Crucial Apps Packager (package_crucial_apps.py)
# Packages must-have desktop apps (Chrome, Discord, VSCode, Steam, Spotify, etc.)
# ==============================================================================

import os
import sys
import shutil
import tempfile
import subprocess
import urllib.request
from pathlib import Path

REPO_ROOT = Path("/media/mamish/Samsung/MindOS_packages_repo")
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
sys.path.insert(0, str(REPO_ROOT / "tools"))
from bulk_packager import package_deb_to_mind
from mind_repo_builder import update_repository_index

DIRECT_APPS = [
    {
        "name": "google-chrome-stable",
        "url": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
        "filename": "google-chrome-stable_current_amd64.deb"
    },
    {
        "name": "code",
        "url": "https://update.code.visualstudio.com/latest/linux-deb-x64/stable",
        "filename": "code_latest_amd64.deb"
    },
    {
        "name": "discord",
        "url": "https://discord.com/api/download?platform=linux&format=deb",
        "filename": "discord_latest_amd64.deb"
    },
    {
        "name": "steam",
        "url": "https://repo.steampowered.com/steam/archive/precise/steam_latest.deb",
        "filename": "steam_latest_amd64.deb"
    }
]

APT_APPS = [
    "telegram-desktop",
    "vlc",
    "mpv",
    "gimp",
    "obs-studio",
    "blender",
    "audacity",
    "kdenlive",
    "neovim",
    "btop",
    "lutris",
    "wine64",
    "flameshot",
    "qbittorrent",
    "geany",
    "filezilla"
]


def download_url(url, dest_path):
    print(f"  📥 İndiriliyor: {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out_file:
        shutil.copyfileobj(resp, out_file)
    print(f"  ✓ İndirildi: {dest_path.name} ({dest_path.stat().st_size // (1024*1024)} MB)")


def main():
    print("🚀 MindOS Crucial Apps Packager Başlatılıyor...")
    PKGS_DIR.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Direct web download apps
        for app in DIRECT_APPS:
            dest = tmp_path / app["filename"]
            try:
                download_url(app["url"], dest)
                out = package_deb_to_mind(dest, PKGS_DIR)
                if out:
                    print(f"  ✅ Paketlendi: {out.name}")
            except Exception as e:
                print(f"  ❌ Hata ({app['name']}): {e}")

        # 2. Apt packages
        for pkg in APT_APPS:
            print(f"\n📦 Apt üzerinden indiriliyor: {pkg}")
            try:
                subprocess.run(["apt-get", "download", pkg], cwd=tmp_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"  ⚠ apt-get download hatası ({pkg}): {e}")

        for deb in tmp_path.glob("*.deb"):
            if not any(deb.name == d["filename"] for d in DIRECT_APPS):
                out = package_deb_to_mind(deb, PKGS_DIR)
                if out:
                    print(f"  ✅ Paketlendi: {out.name}")

    # Index güncelle
    print("\n📑 Repo indeksi (packages.json) güncelleniyor...")
    update_repository_index()

    # Git LFS & push
    print("📡 GitHub'a pushlanıyor...")
    try:
        subprocess.run(["git", "add", "."], cwd=REPO_ROOT, check=True)
        subprocess.run(
            ["git", "commit", "-m", "feat(crucial): Add must-have popular desktop apps (Chrome, Discord, VSCode, Steam, Telegram, VLC, OBS, GIMP, Blender, etc.)"],
            cwd=REPO_ROOT,
            check=True
        )
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
        print("🎉 Crucial apps başarıyla GitHub'a yüklendi!")
    except Exception as e:
        print(f"⚠ Git commit/push uyarısı: {e}")


if __name__ == "__main__":
    main()
