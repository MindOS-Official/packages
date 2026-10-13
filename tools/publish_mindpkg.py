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

index = json.loads(INDEX.read_text(encoding="utf-8"))
index["mindpkg"] = {
    "version": version,
    "url": f"https://raw.githubusercontent.com/MindOS-Official/packages/{commit}/tools/mindpkg",
    "sha256": hashlib.sha256(code).hexdigest(),
}
INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"packages.json: mindpkg {version} ({commit[:7]})")
