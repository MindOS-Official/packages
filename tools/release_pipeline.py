#!/usr/bin/env python3
# ==============================================================================
# MindOS Official Release Pipeline & Cloud Distributor
# Uploads all packages to GitHub Releases (v1.0-chunk-XX) with zero LFS quota
# ==============================================================================

import os
import sys
import json
import time
import math
import hashlib
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

REPO_ROOT = Path(__file__).resolve().parent.parent
PKGS_DIR = REPO_ROOT / "pkgs" / "x86_64"
INDEX_FILE = REPO_ROOT / "packages.json"
STATE_FILE = REPO_ROOT / "tools" / "release_state.json"
REPO_NAME = "MindOS-Official/packages"

TELEGRAM_BOT_TOKEN = "8939687136:AAHWElfdQfdXzglvARc2da6mCzmppEckKrk"
TELEGRAM_CHAT_ID = "8337158473"
CHUNK_SIZE = 500
BATCH_UPLOAD_SIZE = 25


def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


def calculate_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def extract_metadata(mind_file):
    for target in ["./metadata.json", "metadata.json"]:
        try:
            out = subprocess.check_output(
                ["tar", "--zstd", "-xf", str(mind_file), target, "-O"],
                stderr=subprocess.DEVNULL
            )
            meta = json.loads(out)
            meta["filename"] = mind_file.name
            meta["sha256"] = calculate_sha256(mind_file)
            meta["size"] = mind_file.stat().st_size
            return meta
        except Exception:
            continue
    # Minimal fallback
    return {
        "name": mind_file.name.replace("-x86_64.mind", "").rsplit("-", 2)[0],
        "version": "1.0",
        "release": "1",
        "architecture": "x86_64",
        "description": f"MindOS binary package {mind_file.name}",
        "filename": mind_file.name,
        "sha256": calculate_sha256(mind_file),
        "size": mind_file.stat().st_size
    }


def build_complete_index():
    print("📋 MindOS Depo İndeksi Hazırlanıyor...")
    cached_packages = {}
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                for p in d.get("packages", []):
                    if "filename" in p:
                        cached_packages[p["filename"]] = p
        except Exception as e:
            print("Eski indeks okunurken hata:", e)

    all_mind_files = sorted(PKGS_DIR.glob("*.mind"))
    total_count = len(all_mind_files)
    print(f"📦 Toplam .mind dosyası: {total_count}")
    print(f"💾 Önbellekte bulunan paket sayısı: {len(cached_packages)}")

    missing_files = [f for f in all_mind_files if f.name not in cached_packages]
    if missing_files:
        print(f"⚡ Eksik {len(missing_files)} paket ayrıştırılıyor...")
        with ThreadPoolExecutor(max_workers=16) as ex:
            parsed = list(ex.map(extract_metadata, missing_files))
        for p in parsed:
            if p:
                cached_packages[p["filename"]] = p

    total_chunks = math.ceil(total_count / CHUNK_SIZE)
    packages_list = []

    for idx, mind_file in enumerate(all_mind_files):
        chunk_num = (idx // CHUNK_SIZE) + 1
        chunk_tag = f"v1.0-chunk-{chunk_num:02d}"
        
        meta = cached_packages.get(mind_file.name)
        if not meta:
            meta = extract_metadata(mind_file)
            cached_packages[mind_file.name] = meta

        meta["url"] = f"https://github.com/{REPO_NAME}/releases/download/{chunk_tag}/{mind_file.name}"
        meta["chunk_tag"] = chunk_tag
        packages_list.append(meta)

    # Aliases
    aliases = {}
    for pkg in packages_list:
        if pkg["name"] == "steam-launcher":
            aliases["steam"] = dict(pkg, name="steam", description="Steam Installer (Valve Gaming Platform)")
        elif pkg["name"] == "google-chrome-stable":
            aliases["chrome"] = dict(pkg, name="chrome", description="Google Chrome Web Browser")
        elif pkg["name"] == "code":
            aliases["vscode"] = dict(pkg, name="vscode", description="Visual Studio Code Editor")

    for a_name, a_pkg in aliases.items():
        if not any(p["name"] == a_name for p in packages_list):
            packages_list.append(a_pkg)

    repo_data = {
        "repository": "MindOS Sunrise Official Package Repository",
        "architecture": "x86_64",
        "version": "1.0",
        "total_packages": len(packages_list),
        "total_chunks": total_chunks,
        "packages": packages_list
    }

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(repo_data, f, indent=4, ensure_ascii=False)

    print(f"✓ packages.json başarıyla güncellendi! ({len(packages_list)} paket, {total_chunks} chunk)")
    return packages_list, total_chunks


def get_remote_release_assets(tag):
    try:
        out = subprocess.check_output(
            ["gh", "release", "view", tag, "-R", REPO_NAME, "--json", "assets", "--jq", ".assets[].name"],
            text=True, stderr=subprocess.DEVNULL
        )
        return set(out.splitlines())
    except Exception:
        return None


def ensure_release_exists(tag, chunk_num, total_chunks):
    assets = get_remote_release_assets(tag)
    if assets is not None:
        return assets
    
    print(f"  🏷️ Release oluşturuluyor: {tag} ({chunk_num}/{total_chunks})...")
    title = f"MindOS Packages Chunk {chunk_num:02d} ({chunk_num}/{total_chunks})"
    notes = f"Official binary package releases for MindOS Sunrise Linux (Chunk {chunk_num} of {total_chunks})."
    subprocess.run(
        ["gh", "release", "create", tag, "-R", REPO_NAME, "--title", title, "--notes", notes],
        check=True
    )
    return set()


def upload_chunk(chunk_tag, chunk_num, total_chunks, chunk_files, total_all_files, cum_uploaded_start):
    print(f"\n=======================================================")
    print(f"🚀 CHUNK {chunk_num}/{total_chunks} ({chunk_tag}): Toplam {len(chunk_files)} Dosya")
    print(f"=======================================================")

    remote_assets = ensure_release_exists(chunk_tag, chunk_num, total_chunks)
    pending_files = [f for f in chunk_files if f.name not in remote_assets]

    print(f"  ✓ Zaten yüklü: {len(remote_assets)} dosya")
    print(f"  ⏳ Yüklenecek: {len(pending_files)} dosya")

    if not pending_files:
        print(f"  ✨ Bu chunk tamamen güncel, atlanıyor.")
        return len(chunk_files)

    uploaded_in_chunk = len(chunk_files) - len(pending_files)

    for i in range(0, len(pending_files), BATCH_UPLOAD_SIZE):
        batch = pending_files[i:i + BATCH_UPLOAD_SIZE]
        batch_paths = [str(p) for p in batch]
        print(f"  📤 Yükleniyor: {batch[0].name} ... (+{len(batch)-1} dosya)")

        uploaded_success = False
        while not uploaded_success:
            try:
                cmd = ["gh", "release", "upload", chunk_tag] + batch_paths + ["-R", REPO_NAME, "--clobber"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    uploaded_in_chunk += len(batch)
                    uploaded_success = True
                    time.sleep(1.5)  # Nazik bekleme: burst limitini tetiklememek için
                else:
                    err_msg = (res.stderr + res.stdout).strip()
                    if "rate limit" in err_msg.lower() or "403" in err_msg:
                        print(f"    ⏳ GitHub Yükleme Kotası/Rate Limit tespit edildi! 120 saniye bekleniyor...")
                        time.sleep(120)
                    else:
                        print(f"    ⚠ Yükleme hatası: {err_msg[:200]} - 10s sonra tekrar deneniyor...")
                        time.sleep(10)
            except Exception as e:
                print(f"    ⚠ Beklenmedik hata: {e} - 10s sonra tekrar deneniyor...")
                time.sleep(10)

        cur_total_uploaded = cum_uploaded_start + uploaded_in_chunk
        pct = (cur_total_uploaded / total_all_files) * 100
        print(f"    📊 İlerleme: {cur_total_uploaded}/{total_all_files} (%{pct:.1f})")

    return uploaded_in_chunk


def git_push_clean_index():
    print("\n🔄 packages.json ve araçlar GitHub'a pushlanıyor...")
    try:
        subprocess.run(["git", "add", "packages.json", ".gitignore", ".gitattributes", "tools/"], cwd=REPO_ROOT, check=True)
        res = subprocess.run(
            ["git", "commit", "-m", "feat: Update packages.json index pointing to GitHub Releases"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        if "nothing to commit" not in res.stdout + res.stderr:
            subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
            print("✓ packages.json başarıyla GitHub'a pushlandı!")
    except Exception as e:
        print(f"⚠ Git push uyarısı: {e}")


def main():
    print("🌅 MindOS Sunrise Release Uploader Başlatıldı!")
    packages_list, total_chunks = build_complete_index()
    total_files = len(list(PKGS_DIR.glob("*.mind")))

    git_push_clean_index()

    send_telegram(
        f"🚀 *MindOS GitHub Releases Aktarımı Başlatıldı!*\n\n"
        f"📦 *Toplam Paket:* `{total_files}` adet (~41 GB)\n"
        f"🏷️ *Hedef Release:* `20 Chunk` (`v1.0-chunk-01` -> `v1.0-chunk-20`)\n"
        f"🌐 *Dağıtım:* Fastly CDN & GitHub Releases (Sıfır LFS Kotası)\n"
        f"⚡ İşlem arka planda aralıksız çalışıyor."
    )

    all_mind_files = sorted(PKGS_DIR.glob("*.mind"))
    total_all_files = len(all_mind_files)
    total_chunks = math.ceil(total_all_files / CHUNK_SIZE)

    cum_uploaded = 0
    t_start = time.time()

    for chunk_num in range(1, total_chunks + 1):
        chunk_tag = f"v1.0-chunk-{chunk_num:02d}"
        chunk_files = all_mind_files[(chunk_num - 1) * CHUNK_SIZE: chunk_num * CHUNK_SIZE]

        up_count = upload_chunk(
            chunk_tag, chunk_num, total_chunks, chunk_files, total_all_files, cum_uploaded
        )
        cum_uploaded += up_count

        pct = (cum_uploaded / total_all_files) * 100
        elapsed_min = (time.time() - t_start) / 60

        send_telegram(
            f"📦 *MindOS Release Güncellemesi:*\n"
            f"🚀 *Chunk {chunk_num}/{total_chunks}* (`{chunk_tag}`) Tamamlandı!\n"
            f"📊 *Yüklenen:* `{cum_uploaded}/{total_all_files}` paket (%{pct:.1f})\n"
            f"⏱️ *Geçen Süre:* `{elapsed_min:.1f} dakika`"
        )

        time.sleep(1)

    print("\n🎉 TEBRİKLER! TÜM PAKETLER GITHUB RELEASES'A AKTARILDI!")
    send_telegram(
        f"🎉 *TEBRİKLER! TÜM MİNDOS PAKETLERİ GITHUB RELEASES'A AKTARILDI!* 👑🚀\n\n"
        f"✅ Toplam `{total_all_files}` paket başarıyla yüklendi.\n"
        f"🌍 Tüm paketler dünyaya Fastly CDN üzerinden 7/24 açık!\n"
        f"💾 Artık Samsung HDD'deki dosyaları gönül rahatlığıyla silebilirsiniz!"
    )


if __name__ == "__main__":
    main()
