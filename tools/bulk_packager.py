#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Bulk Package Builder (bulk_packager.py)
# Converts upstream packages to native .mind containers automatically
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

DEFAULT_PACKAGES = [
    "nano", "vim-tiny", "git", "curl", "wget", "tmux", "zsh", "fish",
    "cmatrix", "p7zip-full", "unzip", "zip", "ripgrep", "ncdu", "inxi",
    "pciutils", "usbutils", "smartmontools", "ethtool", "net-tools",
    "iputils-ping", "traceroute", "dnsutils", "tree", "htop", "fastfetch",
    "jq", "ranger", "gawk", "sed", "tar", "gzip", "bzip2", "xz-utils"
]


def package_deb_to_mind(deb_path, output_dir):
    """Bir .deb paketini doğrudan optimize .mind paketine dönüştürür"""
    deb_file = Path(deb_path).resolve()
    if not deb_file.exists():
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        try:
            subprocess.run(["ar", "x", str(deb_file)], cwd=tmpdir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None

        control_tar = None
        data_tar = None
        for f in os.listdir(tmpdir):
            if f.startswith("control.tar"):
                control_tar = tmp_path / f
            elif f.startswith("data.tar"):
                data_tar = tmp_path / f

        if not data_tar:
            return None

        ctrl_dir = tmp_path / "control"
        ctrl_dir.mkdir(parents=True, exist_ok=True)
        if control_tar:
            subprocess.run(["tar", "-xf", str(control_tar), "-C", str(ctrl_dir)], check=True)

        pkg_name = deb_file.stem.split("_")[0]
        pkg_ver = "1.0"
        pkg_desc = "Official MindOS Sunrise package"
        c_file = ctrl_dir / "control"
        if c_file.exists():
            for line in c_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Package:"):
                    pkg_name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    pkg_ver = line.split(":", 1)[1].strip().split("+")[0].split("-")[0]
                elif line.startswith("Description:"):
                    pkg_desc = line.split(":", 1)[1].strip()

        # Rootfs çıkart
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-xf", str(data_tar), "-C", str(rootfs)], check=True)

        out_name = f"{pkg_name}-{pkg_ver}-1-x86_64.mind"
        out_path = Path(output_dir) / out_name
        create_mind_package(
            name=pkg_name,
            version=pkg_ver,
            release="1",
            description=pkg_desc,
            rootfs_dir=rootfs,
            output_path=out_path,
            maintainer="MindOS Team <https://github.com/MindOS-Official>"
        )
        return out_path


def build_packages_from_list(packages_list):
    PKGS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📦 Toplam {len(packages_list)} adet paket .mind formatına dönüştürülüyor...")

    with tempfile.TemporaryDirectory() as dl_dir:
        # apt-get download ile paketleri çek
        for pkg in packages_list:
            pkg = pkg.strip()
            if not pkg:
                continue
            print(f"\n🔍 İndiriliyor & Paketleniyor: {pkg} ...")
            try:
                subprocess.run(["apt-get", "download", pkg], cwd=dl_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"  ⚠ {pkg} apt üzerinden indirilemedi, atlanıyor...")
                continue

        # İndirilen tüm .deb dosyalarını .mind formatına dönüştür
        for deb_file in Path(dl_dir).glob("*.deb"):
            package_deb_to_mind(deb_file, PKGS_DIR)

    update_repository_index()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].strip():
        pkgs = sys.argv[1].split()
    else:
        pkgs = DEFAULT_PACKAGES

    build_packages_from_list(pkgs)
