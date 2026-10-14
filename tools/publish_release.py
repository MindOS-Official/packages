#!/usr/bin/env python3
"""Yeni bir MindOS Paradise sürümünü (ör. 1.1) duyurur.

Sürüm, o anki sistem bileşenlerinin (kernel, mindos-apps ...) bir etiketidir: bileşenleri
depodaki sürümlere ulaşan sistem kendini bu sürüm olarak işaretler (/etc/mindos-release).
Mind Arc → Sistem Güncellemesi sayfası yeni sürümü adı ve notlarıyla gösterir.

kullanım:
  publish_release.py 1.1 --tr "Yeni özellik" --tr "Düzeltme" --en "New feature" --en "Fix"
Önce bileşenleri publish_system.py ile yayınlayın; ardından packages.json ve system.json'u
commit ve push edin.
"""
import argparse
import datetime
import json
import re

from system_meta import ROOT, write_index

ap = argparse.ArgumentParser()
ap.add_argument("version")
ap.add_argument("--tr", action="append", default=[], help="Türkçe sürüm notu (tekrarlanabilir)")
ap.add_argument("--en", action="append", default=[], help="English release note (repeatable)")
a = ap.parse_args()
if not re.fullmatch(r"[0-9][0-9A-Za-z.+~-]{0,31}", a.version):
    raise SystemExit(f"Geçersiz sürüm: {a.version}")

INDEX = ROOT / "packages.json"
index = json.loads(INDEX.read_text(encoding="utf-8"))
index["release"] = {
    "version": a.version,
    "name": f"MindOS Paradise {a.version}",
    "date": datetime.date.today().isoformat(),
    "notes": {"tr": a.tr, "en": a.en or a.tr},
}
write_index(index)
print(f"release: MindOS Paradise {a.version}")
