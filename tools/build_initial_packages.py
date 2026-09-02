#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Initial Official Package Suite Builder
# ==============================================================================

import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
sys.path.insert(0, str(REPO_ROOT / "tools"))
from mind_repo_builder import create_mind_package, update_repository_index

PACKAGES_TO_EXTRACT = [
    {
        "name": "neofetch",
        "version": "7.1.0",
        "release": "1",
        "description": "A fast, highly customizable system info script",
        "binaries": ["/usr/bin/neofetch"],
        "extra_dirs": ["/usr/share/neofetch"]
    },
    {
        "name": "tree",
        "version": "2.1.1",
        "release": "1",
        "description": "A recursive directory indentation listing tool",
        "binaries": ["/usr/bin/tree"],
        "extra_dirs": ["/usr/share/man/man1/tree.1.gz"]
    },
    {
        "name": "htop",
        "version": "3.3.0",
        "release": "1",
        "description": "Interactive process viewer and system monitor",
        "binaries": ["/usr/bin/htop"],
        "extra_dirs": ["/usr/share/applications/htop.desktop", "/usr/share/pixmaps/htop.png"]
    },
    {
        "name": "btop",
        "version": "1.3.2",
        "release": "1",
        "description": "Modern and beautiful resource monitor that shows usage and stats",
        "binaries": ["/usr/bin/btop"],
        "extra_dirs": ["/usr/share/btop", "/usr/share/applications/btop.desktop"]
    },
    {
        "name": "fastfetch",
        "version": "2.15.0",
        "release": "1",
        "description": "Like neofetch, but much faster because written in C",
        "binaries": ["/usr/bin/fastfetch"],
        "extra_dirs": ["/usr/share/fastfetch"]
    },
    {
        "name": "jq",
        "version": "1.7.1",
        "release": "1",
        "description": "Command-line JSON processor",
        "binaries": ["/usr/bin/jq"],
        "extra_dirs": []
    },
    {
        "name": "tmux",
        "version": "3.4",
        "release": "1",
        "description": "Terminal multiplexer for managing multiple terminal sessions",
        "binaries": ["/usr/bin/tmux"],
        "extra_dirs": []
    }
]


def build_all():
    PKGS_DIR.mkdir(parents=True, exist_ok=True)
    print("🚀 MindOS Sunrise Resmi Paketleri Derleniyor/Paketleniyor...")

    for pkg in PACKAGES_TO_EXTRACT:
        name = pkg["name"]
        ver = pkg["version"]
        rel = pkg["release"]
        desc = pkg["description"]
        out_file = PKGS_DIR / f"{name}-{ver}-{rel}-x86_64.mind"

        with tempfile.TemporaryDirectory() as tmpdir:
            rootfs = Path(tmpdir) / "rootfs"
            rootfs.mkdir(parents=True, exist_ok=True)

            found_any = False
            # Host sistemden veya LFS kökünden dosyaları topla
            for bin_path in pkg["binaries"]:
                src = Path(bin_path)
                if not src.exists():
                    # LFS mount altında ara
                    src = Path(f"/mnt/lfs{bin_path}")

                if src.exists():
                    dest = rootfs / src.relative_to("/")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    found_any = True

            for extra in pkg.get("extra_dirs", []):
                src = Path(extra)
                if not src.exists():
                    src = Path(f"/mnt/lfs{extra}")

                if src.exists():
                    dest = rootfs / src.relative_to("/")
                    if src.is_dir():
                        shutil.copytree(src, dest, dirs_exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
                    found_any = True

            if found_any:
                create_mind_package(name, ver, rel, desc, rootfs, out_file)
            else:
                print(f"  ⚠ {name} için dosyalar bulunamadı, atlanıyor...")

    update_repository_index()


if __name__ == "__main__":
    build_all()
