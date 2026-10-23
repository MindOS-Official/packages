#!/usr/bin/env python3
"""MindOS Sürekli Yüksek Başarımlı x86_64 Paket Fabrikası Döngüsü.

Debian trixie arm64 paketlerini parçalar (chunk) halinde üretir, GitHub Releases'a
yükler, indeksleri sürüm anahtarıyla imzalar, disk dolmasını engellemek için
yüklenen dosyaları temizler ve git reposuna commit/push eder.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FACTORY_PY = TOOLS / "batch_x86_factory.py"
SIGN_PY = TOOLS / "sign_index.py"
OUT_DIR = ROOT / "out_x86"
INDEX_FILE = ROOT / "packages.json"
STAGING_FILE = ROOT / "staging" / "packages.json"
SHM_DIR = Path("/dev/shm/mindos_x86_work")


def log(msg):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def get_current_count():
    if INDEX_FILE.exists():
        try:
            d = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            return len(d.get("packages", []))
        except Exception:
            return 0
    return 0


def run_batch(batch_size, threads, repo, upload, clean_uploaded):
    cmd = [
        sys.executable,
        str(FACTORY_PY),
        "--count", str(batch_size),
        "--threads", str(threads),
        "--repo", repo,
        "--out-dir", str(OUT_DIR)
    ]
    if upload:
        cmd.append("--upload")
    else:
        cmd.append("--no-upload")

    if clean_uploaded:
        cmd.append("--clean-uploaded")
    else:
        cmd.append("--no-clean-uploaded")

    log(f"▶ batch_x86_factory çalıştırılıyor (hedef: {batch_size} paket, {threads} iş parçacığı)...")
    res = subprocess.run(cmd, cwd=str(ROOT))
    return res.returncode == 0


def sign_index(repo):
    cmd = [sys.executable, str(SIGN_PY), "--arch", "x86_64", "--yes", "--repo", repo, "--out", str(INDEX_FILE)]
    log("▶ Taslak indeks denetlenip imzalanıyor (packages.json)...")
    res = subprocess.run(cmd, cwd=str(ROOT))
    return res.returncode == 0


def commit_and_push(new_count):
    log("▶ Git deposuna commit ve push ediliyor...")
    files_to_add = [
        "packages.json", "packages.json.sig",
        "system.json", "system.json.sig",
        "staging/packages.json"
    ]
    subprocess.run(["git", "add", *files_to_add], cwd=str(ROOT), check=True)
    msg = f"chore(x86_64): sync packages index (total {new_count} packages)"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True)
    if "packages.json" in status.stdout:
        subprocess.run(["git", "commit", "-m", msg], cwd=str(ROOT), check=True)
        # Rebase & Push
        pushed = False
        for attempt in range(5):
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(ROOT))
            push_res = subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT))
            if push_res.returncode == 0:
                log(f"✓ Git push tamamlandı (toplam {new_count} paket)")
                pushed = True
                break
            log(f"  ⚠ Git push başarısız (deneme {attempt+1}/5), 5 sn sonra yeniden denenecek...")
            time.sleep(5)
        if not pushed:
            log("  ✗ Git push tüm denemelere rağmen başarısız oldu!")
        return pushed
    else:
        log("ℹ İndekste commit edilecek yeni değişiklik yok.")
        return True


def clean_uploaded_files():
    if OUT_DIR.exists():
        files = list(OUT_DIR.glob("*.mind"))
        if files:
            known_filenames = set()
            for idx_f in (INDEX_FILE, STAGING_FILE):
                if idx_f.exists():
                    try:
                        data = json.loads(idx_f.read_text(encoding="utf-8"))
                        for p in data.get("packages", []):
                            if p.get("filename"):
                                known_filenames.add(p["filename"])
                    except Exception:
                        pass
            to_delete = [f for f in files if f.name in known_filenames or not known_filenames]
            if to_delete:
                log(f"🧹 Disk alanını korumak için {len(to_delete)} adet işlenmiş .mind dosyası siliniyor...")
                for f in to_delete:
                    try:
                        f.unlink()
                    except OSError:
                        pass

    # RAM disk geçici dosyalarını da temizle
    if SHM_DIR.exists():
        for leftover in SHM_DIR.glob("*"):
            try:
                if leftover.is_file():
                    leftover.unlink()
                elif leftover.is_dir():
                    shutil.rmtree(leftover, ignore_errors=True)
            except Exception:
                pass


def get_target_arm_count(manifest_path):
    p = Path(manifest_path) if manifest_path else None
    if not p or not p.exists():
        p = ROOT.parent / "MindOS_packages_arm64_repo" / "packages.json"
    if p and p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return len(d.get("packages", []))
        except Exception:
            return 0
    return 0


def main():
    ap = argparse.ArgumentParser(description="MindOS Continuous x86_64 Factory Runner")
    ap.add_argument("--batch-size", type=int, default=120, help="Her turda işlenecek paket sayısı")
    ap.add_argument("--max-total", type=int, default=0, help="Ulaşılacak toplam paket sayısı (0 = sınırsız)")
    ap.add_argument("--max-batches", type=int, default=0, help="Çalıştırılacak azami tur sayısı (0 = sınırsız)")
    ap.add_argument("--threads", type=int, default=24, help="İş parçacığı sayısı (azami verim için 24)")
    ap.add_argument("--repo", default="MindOS-Official/packages", help="Hedef GitHub deposu")
    ap.add_argument("--parity-target", default=str(ROOT.parent / "MindOS_packages_arm64_repo" / "packages-arm64.json"),
                    help="ARM64 paket sayısı ile eşitlenene kadar çalışmayı denetlemek için ARM64 manifestosu")
    ap.add_argument("--upload", action="store_true", default=True, help="GitHub Releases'a yükle")
    ap.add_argument("--no-upload", dest="upload", action="store_false")
    ap.add_argument("--clean-out", action="store_true", default=True, help="Disk dolmasını önlemek için out_x86'ü temizle")
    ap.add_argument("--no-clean", dest="clean-out", action="store_false")
    ap.add_argument("--auto-push", action="store_true", default=True, help="İmzalanan indeksi git ile commit/push et")
    ap.add_argument("--no-push", dest="auto-push", action="store_false")
    ap.add_argument("--cooldown", type=int, default=2, help="Turlar arası bekleme süresi (sn)")
    args = ap.parse_args()

    start_count = get_current_count()
    arm_target = get_target_arm_count(args.parity_target) if args.parity_target else 0

    log("=================================================================")
    log("🚀 MindOS Kesintisiz Yüksek Başarımlı x86_64 Fabrikası Başlatıldı")
    log(f"   Başlangıç Paket Sayısı : {start_count}")
    if arm_target > 0:
        log(f"   ARM64 Parite Hedefi    : {arm_target} paket (Fark: {max(0, arm_target - start_count)})")
    log(f"   Tur Başına Paket       : {args.batch_size}")
    log(f"   İş Parçacığı           : {args.threads}")
    log(f"   Hedef Depo             : {args.repo}")
    log(f"   Yükleme & İmzalı Push  : {args.upload} / {args.auto_push}")
    log("=================================================================")

    batch_idx = 0
    start_time = time.time()

    while True:
        curr_count = get_current_count()
        arm_target = get_target_arm_count(args.parity_target) if args.parity_target else 0

        if arm_target > 0 and curr_count >= arm_target:
            log(f"🎉 TEBRİKLER! x86_64 paket sayısı ARM64 paket sayısına ulaştı / geçti: {curr_count} >= {arm_target}!")
            break

        if args.max_total > 0 and curr_count >= args.max_total:
            log(f"🎯 Hedef paket sayısına ulaşıldı: {curr_count} >= {args.max_total}")
            break

        if args.max_batches > 0 and batch_idx >= args.max_batches:
            log(f"🏁 Azami tur sayısına ulaşıldı: {batch_idx} tur")
            break

        batch_idx += 1
        rem_str = f" | Pariteye kalan: {arm_target - curr_count}" if arm_target > 0 else ""
        log(f"\n--- [TUR {batch_idx}] Başlatılıyor (Şu anki paket: {curr_count}{rem_str}) ---")
        batch_start = time.time()

        success = run_batch(args.batch_size, args.threads, args.repo, args.upload, clean_uploaded=args.clean_out)
        if not success:
            log(f"⚠ Tur {batch_idx} hata ile sonuçlandı. {args.cooldown * 2} sn beklenip tekrar denenecek...")
            time.sleep(args.cooldown * 2)
            continue

        if sign_index(args.repo):
            new_count = get_current_count()
            if args.auto_push:
                commit_and_push(new_count)
            if args.clean_out:
                clean_uploaded_files()
        else:
            log("⚠ İndeks imzalama başarısız oldu!")

        batch_elapsed = time.time() - batch_start
        total_elapsed = time.time() - start_time
        net_added = get_current_count() - start_count
        rate_per_min = (net_added / (total_elapsed / 60)) if total_elapsed > 0 else 0

        log(f"✓ Tur {batch_idx} bitti ({batch_elapsed:.1f} sn). Toplam eklenen: {net_added} paket (~{rate_per_min:.1f} paket/dakika)")

        if args.cooldown > 0:
            log(f"⏳ Hız sınırını korumak için {args.cooldown} sn bekleniyor...")
            time.sleep(args.cooldown)

    log("\n🎉 Sürekli üretim döngüsü tamamlandı.")


if __name__ == "__main__":
    main()
