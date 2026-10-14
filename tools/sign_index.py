#!/usr/bin/env python3
"""Fabrikanın taslak indeksini (staging/packages.json) denetleyip imzalı packages.json'a alır.

Günlük GitHub Actions fabrikası yeni .mind paketlerini yalnızca taslağa yazar. Kullanıcılara
ulaşmaları için bu betik geliştiricinin bilgisayarında çalıştırılır: her kayıt denetlenir (adres
yalnızca bu deponun Releases'ı, SHA256 var), değişiklikler özetlenir, packages.json ve system.json
sürüm anahtarıyla imzalanır. mindpkg/system/release kayıtları packages.json'daki haliyle kalır.

kullanım: sign_index.py [--yes] [--allow-removals]
Ardından packages.json, system.json ve .sig dosyaları commit ve push edilir.
"""
import argparse
import json
import re
import sys

from system_meta import INDEX, META_KEYS, ROOT, write_index

REPO = "MindOS-Official/packages"
STAGING = ROOT / "staging" / "packages.json"
URL_PREFIX = f"https://github.com/{REPO}/releases/download/"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")


def check_entry(p):
    if not NAME_RE.match(str(p.get("name", ""))):
        return "geçersiz ad"
    if not str(p.get("url", "")).startswith(URL_PREFIX):
        return f"adres bu deponun Releases'ı değil: {p.get('url')}"
    if not re.fullmatch(r"[0-9a-f]{64}", str(p.get("sha256", "")).lower()):
        return "SHA256 yok"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="onay sormadan imzala")
    ap.add_argument("--allow-removals", action="store_true",
                    help="taslakta paket sayısı belirgin azaldıysa yine de imzala")
    a = ap.parse_args()

    current = json.loads(INDEX.read_text(encoding="utf-8"))
    if not STAGING.exists():
        print("Taslak yok; yalnızca mevcut indeks yeniden imzalanıyor.")
        staged = current
    else:
        staged = json.loads(STAGING.read_text(encoding="utf-8"))

    bad = [(p.get("name"), err) for p in staged.get("packages", []) if (err := check_entry(p))]
    if bad:
        for name, err in bad[:20]:
            print(f"  ✗ {name}: {err}")
        raise SystemExit(f"{len(bad)} kayıt denetimden geçmedi; imzalanmadı.")

    old = {p["name"]: p for p in current.get("packages", [])}
    new = {p["name"]: p for p in staged.get("packages", [])}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(n for n in set(new) & set(old) if new[n].get("sha256") != old[n].get("sha256"))
    print(f"Eklenen: {len(added)}, güncellenen: {len(changed)}, kaldırılan: {len(removed)}, "
          f"toplam: {len(new)}")
    for label, names in (("eklenen", added), ("güncellenen", changed), ("kaldırılan", removed)):
        if names:
            print(f"  {label}: {', '.join(names[:15])}{' …' if len(names) > 15 else ''}")
    # 2026-10-11'de hatalı bir iş indeksi 32 kayda indirmişti; büyük kayıplar elle onay ister
    if len(removed) > max(20, len(old) // 50) and not a.allow_removals:
        raise SystemExit(f"{len(removed)} paket kayboluyor; bilerek yapıyorsanız --allow-removals ekleyin.")
    if not a.yes and input("İmzalansın mı? [e/H] ").strip().lower() not in ("e", "evet", "y", "yes"):
        raise SystemExit("İptal edildi.")

    merged = {k: v for k, v in staged.items() if k not in META_KEYS}
    merged.update({k: current[k] for k in META_KEYS if k in current})
    write_index(merged)
    print("✓ packages.json ve system.json imzalandı. Şimdi commit ve push edin.")


if __name__ == "__main__":
    sys.exit(main())
