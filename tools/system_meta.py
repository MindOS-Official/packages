"""packages.json'daki mindpkg / system / release kayıtlarını küçük system.json dosyasına yazar.

Mind Arc'ın "Sistem Güncellemesi" sayfası (mindpkg check) yetki istemeden yalnızca bu
birkaç KB'lık dosyayı indirir; ~9 MB'lık paket indeksine gerek kalmaz.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META_KEYS = ("mindpkg", "system", "release")


def write_system_json(index):
    meta = {k: index[k] for k in META_KEYS if k in index}
    (ROOT / "system.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_system_json(json.loads((ROOT / "packages.json").read_text(encoding="utf-8")))
    print("system.json yazıldı")
