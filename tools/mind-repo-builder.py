#!/usr/bin/env python3
# ==============================================================================
# MindOS Sunrise Repository & Package Generator (mind-repo-builder)
# ==============================================================================

import os
import sys
import json
import hashlib
import shutil
import subprocess
import urllib.request
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
INDEX_FILE = REPO_ROOT / "packages.json"
BASE_URL = "https://raw.githubusercontent.com/MindOS-Official/packages/main/pkgs/x86_64"


def calculate_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def create_mind_package(name, version, release, description, rootfs_dir, output_path, maintainer="MindOS Team <core@mindos.org>", deps=None):
    """Verilen kök dizinden optimize edilmiş .mind paketi üretir"""
    if deps is None:
        deps = []

    meta = {
        "name": name,
        "version": version,
        "release": release,
        "architecture": "x86_64",
        "description": description,
        "maintainer": maintainer,
        "dependencies": deps
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with open(tmp_path / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4)

        files_archive = tmp_path / "files.tar.zst"
        if Path(rootfs_dir).exists():
            subprocess.run(
                ["tar", "--zstd", "-cf", str(files_archive), "-C", str(rootfs_dir), "."],
                check=True
            )
        else:
            subprocess.run(
                ["tar", "--zstd", "-cf", str(files_archive), "--files-from", "/dev/null"],
                check=True
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["tar", "--zstd", "-cf", str(output_path), "-C", str(tmp_path), "."],
            check=True
        )

    print(f"  ✓ Paket üretildi: {output_path.name} ({output_path.stat().st_size:,} bytes)")


def update_repository_index():
    """pkgs/x86_64 altındaki tüm .mind paketlerini tarayıp packages.json oluşturur"""
    PKGS_DIR.mkdir(parents=True, exist_ok=True)
    packages_list = []

    print("\n🔍 .mind paketleri taranıyor...")
    for mind_file in sorted(PKGS_DIR.glob("*.mind")):
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                subprocess.run(
                    ["tar", "--zstd", "-xf", str(mind_file), "-C", tmpdir],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                meta_file = Path(tmpdir) / "metadata.json"
                if meta_file.exists():
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    
                    meta["filename"] = mind_file.name
                    meta["sha256"] = calculate_sha256(mind_file)
                    meta["size"] = mind_file.stat().st_size
                    meta["url"] = f"{BASE_URL}/{mind_file.name}"
                    packages_list.append(meta)
                    print(f"  • {meta['name']} v{meta['version']}-{meta.get('release', '1')} (SHA256: {meta['sha256'][:8]}...)")
            except Exception as e:
                print(f"  ⚠ {mind_file.name} okunamadı: {e}")

    repo_index = {
        "repository": "MindOS Sunrise Official Package Repository",
        "architecture": "x86_64",
        "version": "1.0",
        "total_packages": len(packages_list),
        "packages": packages_list
    }

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(repo_index, f, indent=4)

    print(f"\n✅ packages.json başarıyla güncellendi! (Toplam {len(packages_list)} paket)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        update_repository_index()
    else:
        update_repository_index()
