"""MindOS depo indeksini yazar ve imzalar.

packages.json (tüm paketler) ve system.json (yalnızca mindpkg / system / release kayıtları)
yazılır; ikisi de geliştiricinin Ed25519 sürüm anahtarıyla imzalanır (<dosya>.sig).
mindpkg 1.8+ imzası doğrulanmayan indeksi kabul etmez; GitHub hesabı ele geçirilse bile
indeks ve .mind paketleri değiştirilemez. Özel anahtar yalnızca geliştiricinin bilgisayarında durur.

Mind Arc'ın "Sistem Güncellemesi" sayfası (mindpkg check) yetki istemeden yalnızca birkaç KB'lık
system.json'u indirir; ~9 MB'lık paket indeksine gerek kalmaz.
"""
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "packages.json"
META_KEYS = ("mindpkg", "system", "release")
KEY = Path(os.environ.get("MINDPKG_SIGNING_KEY", Path.home() / ".config/mindos/mindpkg-release.key"))


def sign_file(path):
    """<dosya>.sig: {"sha256": ..., "sig": Ed25519(dosya baytları)}"""
    if not KEY.exists():
        raise SystemExit(f"İmza anahtarı yok: {KEY}")
    data = Path(path).read_bytes()
    sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(KEY), "-rawin", "-in", str(path)],
                         capture_output=True, check=True).stdout
    Path(f"{path}.sig").write_text(json.dumps({"sha256": hashlib.sha256(data).hexdigest(),
                                               "sig": base64.b64encode(sig).decode()}) + "\n", encoding="utf-8")


def write_system_json(index):
    meta = {k: index[k] for k in META_KEYS if k in index}
    path = ROOT / "system.json"
    path.write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sign_file(path)


def write_index(index, out_path=None):
    """packages.json (veya out_path) + system.json'u yazar ve ikisini de imzalar"""
    target = Path(out_path) if out_path else INDEX
    target.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sign_file(target)
    if target == INDEX:
        write_system_json(index)


if __name__ == "__main__":
    write_index(json.loads(INDEX.read_text(encoding="utf-8")))
    print("packages.json ve system.json yazıldı, imzalandı")
