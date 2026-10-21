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
import os
from pathlib import Path
import re
import sys

from system_meta import INDEX, META_KEYS, ROOT, write_index

REPO = os.environ.get("MIND_REPO", "MindOS-Official/packages")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")


def check_entry(p, url_prefix):
    if not NAME_RE.match(str(p.get("name", ""))):
        return "geçersiz ad"
    if not str(p.get("url", "")).startswith(url_prefix):
        return f"adres bu deponun Releases'ı değil: {p.get('url')}"
    if not re.fullmatch(r"[0-9a-f]{64}", str(p.get("sha256", "")).lower()):
        return "SHA256 yok"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=["x86_64", "arm64", "aarch64"], default="",
                    help="hedef mimari (x86_64 veya arm64/aarch64)")
    ap.add_argument("--yes", action="store_true", help="onay sormadan imzala")
    ap.add_argument("--repo", default="", help="GitHub deposu (ör: MindOS-Official/packages-arm64)")
    ap.add_argument("--staging", default="", help="taslak indeks dosya yolu")
    ap.add_argument("--out", default="", help="imzalanacak hedef indeks dosya yolu")
    ap.add_argument("--allow-removals", action="store_true",
                    help="taslakta paket sayısı belirgin azaldıysa yine de imzala")
    a = ap.parse_args()

    repo_is_arm = "packages-arm64" in str(ROOT) or "packages_arm64" in str(ROOT)
    if not repo_is_arm and INDEX.exists():
        try:
            data = json.loads(INDEX.read_text(encoding="utf-8"))
            if data.get("architecture") in ("aarch64", "arm64"):
                repo_is_arm = True
        except Exception:
            pass

    is_arm = a.arch in ("arm64", "aarch64") or "packages-arm64" in a.repo or (not a.arch and not a.repo and repo_is_arm)
    repo = a.repo or ("MindOS-Official/packages-arm64" if is_arm else REPO)

    if a.staging:
        staging_path = Path(a.staging)
    elif is_arm and not repo_is_arm and (ROOT / "staging" / "packages-arm64.json").exists():
        staging_path = ROOT / "staging" / "packages-arm64.json"
    else:
        staging_path = ROOT / "staging" / "packages.json"

    if a.out:
        out_path = Path(a.out)
    elif is_arm:
        if repo_is_arm:
            out_path = INDEX
        elif INDEX.exists():
            try:
                data = json.loads(INDEX.read_text(encoding="utf-8"))
                if data.get("architecture") == "aarch64":
                    out_path = INDEX
                else:
                    out_path = ROOT / "packages-arm64.json"
            except Exception:
                out_path = ROOT / "packages-arm64.json"
        else:
            out_path = ROOT / "packages-arm64.json"
    else:
        out_path = INDEX

    url_prefix = f"https://github.com/{repo}/releases/download/"
    current = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {"packages": []}
    if not staging_path.exists():
        print(f"Taslak ({staging_path}) yok; yalnızca mevcut indeks ({out_path}) yeniden imzalanıyor.")
        staged = current
    else:
        print(f"Taslak okunuyor: {staging_path} -> Hedef: {out_path} (Depo: {repo})")
        staged = json.loads(staging_path.read_text(encoding="utf-8"))

    bad = [(p.get("name"), err) for p in staged.get("packages", []) if (err := check_entry(p, url_prefix))]
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
    for k in META_KEYS:
        if k in current:
            merged[k] = current[k]
        elif k in staged:
            merged[k] = staged[k]
    write_index(merged, out_path=out_path)
    print(f"✓ {out_path.name} imzalandı ({out_path}.sig). Şimdi commit ve push edin.")


if __name__ == "__main__":
    sys.exit(main())
