#!/usr/bin/env python3
"""MindOS sistem bileşenlerini (kernel, mindos-apps ...) yayınlar.

.deb dosyalarını "mindos-system" release'ine yükler, geliştiricinin Ed25519 anahtarıyla
imzalar ve packages.json'daki "system" listesine ekler. MindOS Paradise'ta
"sudo mindpkg upgrade" bu listeden daha yeni olanları imzayı doğrulayarak kurar.

kullanım: publish_system.py <paket.deb> [<paket.deb> ...]
Ardından packages.json ve system.json commit ve push edilir.
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from system_meta import write_system_json

REPO = "MindOS-Official/packages"
TAG = "mindos-system"
ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "packages.json"
KEY = Path(os.environ.get("MINDPKG_SIGNING_KEY", Path.home() / ".config/mindos/mindpkg-release.key"))


def field(deb, name):
    return subprocess.run(["dpkg-deb", "-f", str(deb), name], capture_output=True, text=True,
                          check=True).stdout.strip()


def main():
    debs = [Path(a).resolve() for a in sys.argv[1:]]
    if not debs:
        raise SystemExit(__doc__)
    if not KEY.exists():
        raise SystemExit(f"İmza anahtarı yok: {KEY}")
    if subprocess.run(["gh", "release", "view", TAG, "-R", REPO], capture_output=True).returncode != 0:
        subprocess.run(["gh", "release", "create", TAG, "-R", REPO, "--title", "MindOS system components",
                        "--notes", "Signed kernel and MindOS application packages used by `mindpkg upgrade`."],
                       check=True)
    subprocess.run(["gh", "release", "upload", TAG, "-R", REPO, "--clobber", *map(str, debs)], check=True)
    assets = json.loads(subprocess.run(["gh", "release", "view", TAG, "-R", REPO, "--json", "assets"],
                                       capture_output=True, text=True, check=True).stdout)["assets"]
    urls = {a["name"]: a["url"] for a in assets}

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    system = [e for e in index.get("system", []) if isinstance(e, dict)]
    for deb in debs:
        name, version = field(deb, "Package"), field(deb, "Version")
        if deb.name not in urls:
            raise SystemExit(f"Yüklenen dosya release'te bulunamadı: {deb.name}")
        sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(KEY), "-rawin", "-in", str(deb)],
                             capture_output=True, check=True).stdout
        entry = {"name": name, "version": version, "url": urls[deb.name],
                 "sha256": hashlib.sha256(deb.read_bytes()).hexdigest(), "size": deb.stat().st_size,
                 "sig": base64.b64encode(sig).decode()}
        system = [e for e in system if e.get("name") != name] + [entry]
        print(f"system: {name} {version}")
    index["system"] = sorted(system, key=lambda e: e["name"])
    INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    write_system_json(index)


if __name__ == "__main__":
    main()
