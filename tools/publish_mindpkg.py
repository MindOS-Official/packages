#!/usr/bin/env python3
"""tools/mindpkg'nin yeni sürümünü 'mindpkg upgrade' ile dağıtıma açar.

1. tools/mindpkg'yi güncelleyip commit ve push edin.
2. Bu betiği çalıştırın: packages.json'daki "mindpkg" kaydını o commit'e sabitlenmiş
   adres ve SHA256 ile günceller. Ardından packages.json'u commit ve push edin.

Adres commit'e sabitlendiği için içerik sonradan değişemez; mindpkg indirdiği dosyayı
SHA256 ile doğrulamadan kendini asla değiştirmez.
"""
import hashlib
import json
import re
import base64
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "mindpkg"
INDEX = ROOT / "packages.json"

code = SRC.read_bytes()
version = re.search(rb'^VERSION = "([^"]+)"', code, re.M).group(1).decode()
commit = subprocess.check_output(["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", "tools/mindpkg"],
                                 text=True).strip()
if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", "tools/mindpkg"]).returncode != 0:
    raise SystemExit("tools/mindpkg commit edilmemiş değişiklik içeriyor; önce commit edin.")
if subprocess.run(["git", "-C", str(ROOT), "branch", "-r", "--contains", commit],
                  capture_output=True, text=True).stdout.strip() == "":
    raise SystemExit(f"{commit[:7]} henüz GitHub'a gönderilmemiş; önce push edin.")

# Sürüm, yalnızca geliştiricinin bilgisayarındaki Ed25519 anahtarıyla imzalanır;
# mindpkg (1.5.1+) imzası tutmayan güncellemeyi kurmaz.
KEY = Path(os.environ.get("MINDPKG_SIGNING_KEY", Path.home() / ".config/mindos/mindpkg-release.key"))
if not KEY.exists():
    raise SystemExit(f"İmza anahtarı yok: {KEY}")
sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(KEY), "-rawin", "-in", str(SRC)],
                     capture_output=True, check=True).stdout

index = json.loads(INDEX.read_text(encoding="utf-8"))
index["mindpkg"] = {
    "version": version,
    "url": f"https://raw.githubusercontent.com/MindOS-Official/packages/{commit}/tools/mindpkg",
    "sha256": hashlib.sha256(code).hexdigest(),
    "sig": base64.b64encode(sig).decode(),
}
INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"packages.json: mindpkg {version} ({commit[:7]})")
