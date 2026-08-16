#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def quick_check(path: Path) -> str:
    con = sqlite3.connect(path, timeout=120)
    try:
        con.execute("PRAGMA busy_timeout=120000")
        return str(con.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        con.close()


def backup_database(source: Path, target: Path) -> None:
    src = sqlite3.connect(source, timeout=120)
    dst = sqlite3.connect(target, timeout=120)
    try:
        src.execute("PRAGMA busy_timeout=120000")
        src.backup(dst)
        dst.commit()
        result = dst.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup quick_check={result}")
    finally:
        dst.close()
        src.close()


def final_vacuum(path: Path) -> str:
    con = sqlite3.connect(path, timeout=300)
    try:
        con.execute("PRAGMA busy_timeout=300000")
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
        con.execute("PRAGMA journal_mode=WAL")
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        return str(con.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        con.close()


def footprint(path: Path) -> tuple[float, dict[str, float]]:
    parts = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    sizes = {
        p.name: (p.stat().st_size / 1048576 if p.exists() else 0.0)
        for p in parts
    }
    return sum(sizes.values()), sizes



def rotate_named_backups(folder: Path, pattern: str, keep: int = 3) -> list[Path]:
    """Bound shutdown-created backups so successful pruning cannot grow disk forever."""
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(folder.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for stale in files[max(1, keep):]:
        try:
            stale.unlink()
            removed.append(stale)
        except OSError:
            pass
    return removed


def run_retention_engine(
    *, root: Path, db: Path, archive: Path, trim: Path, report: Path, log: Path,
    target_mb: float, max_safe_mb: float,
) -> int:
    cmd = [
        sys.executable, str(trim),
        "--db", str(db),
        "--archive", str(archive),
        "--apply", "--vacuum",
        "--target-mb", str(target_mb),
        "--max-safe-mb", str(max_safe_mb),
        "--heartbeat-grace-seconds", "12",
        "--keep-backups", "3",
        "--json", str(report),
    ]
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            fh.write(line)
        return proc.wait()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    db = root / "sentinuity_matrix.db"
    archive = root / "sentinuity_archive.db"
    price_db = root / "sentinuity_price_truth.db"
    price_archive = root / "sentinuity_price_truth_archive.db"
    trim = root / "launch" / "db_retention_trim.py"
    backup_dir = root / "db_backups"
    log_dir = root / "logs" / "db_retention"

    if not db.exists():
        print(f"[FAIL] Missing database: {db}")
        return 2
    if not trim.exists():
        print(f"[FAIL] Missing retention engine: {trim}")
        return 3

    backup_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"sentinuity_matrix.SHUTDOWN_before_retention_{stamp}.db"
    report = log_dir / f"matrix_shutdown_retention_{stamp}.json"
    log = log_dir / f"matrix_shutdown_retention_{stamp}.log"

    before_mb, before_sizes = footprint(db)
    print(f"[MATRIX BEFORE] footprint_mb={before_mb:.2f} sizes={before_sizes}")

    qc = quick_check(db)
    print(f"matrix_quick_check={qc}")
    if qc != "ok":
        print("[FAIL] Matrix pre-retention quick_check failed.")
        return 4

    backup_database(db, backup)
    print(f"[PASS] Verified matrix shutdown backup: {backup}")
    removed = rotate_named_backups(backup_dir, "sentinuity_matrix.SHUTDOWN_before_retention_*.db", 1)
    if removed:
        print(f"[BACKUP_RETENTION] removed {len(removed)} old shutdown matrix backup(s); keeping newest 1")

    rc = run_retention_engine(
        root=root, db=db, archive=archive, trim=trim, report=report, log=log,
        target_mb=15, max_safe_mb=20,
    )
    if rc != 0:
        print(f"[FAIL] Matrix retention engine exit={rc}")
        print(f"Log: {log}")
        print(f"Report: {report}")
        print(f"Backup: {backup}")
        return rc or 5

    # Dedicated price-truth DB was previously omitted from shutdown retention.
    # It now carries high-frequency snapshots/quotes/tape and can become the
    # apparent 'DB bulk' even while sentinuity_matrix.db remains healthy.
    price_report = log_dir / f"price_truth_shutdown_retention_{stamp}.json"
    price_log = log_dir / f"price_truth_shutdown_retention_{stamp}.log"
    price_before_mb = 0.0
    if price_db.exists():
        price_before_mb, price_before_sizes = footprint(price_db)
        print(f"[PRICE TRUTH BEFORE] footprint_mb={price_before_mb:.2f} sizes={price_before_sizes}")
        pqc = quick_check(price_db)
        print(f"price_truth_quick_check={pqc}")
        if pqc != "ok":
            print("[FAIL] Price-truth pre-retention quick_check failed.")
            return 8
        price_rc = run_retention_engine(
            root=root, db=price_db, archive=price_archive, trim=trim,
            report=price_report, log=price_log, target_mb=12, max_safe_mb=20,
        )
        if price_rc != 0:
            print(f"[FAIL] Price-truth retention engine exit={price_rc}")
            print(f"Log: {price_log}")
            print(f"Report: {price_report}")
            return price_rc or 9
    else:
        print("[INFO] sentinuity_price_truth.db absent; dedicated retention skipped.")

    post_qc = final_vacuum(db)
    after_mb, after_sizes = footprint(db)

    print(f"matrix_post_vacuum_quick_check={post_qc}")
    print("matrix_sizes_mb=" + json.dumps(
        {k: round(v, 2) for k, v in after_sizes.items()}, sort_keys=True,
    ))
    print(f"matrix_total_footprint_mb={after_mb:.2f}")
    print(f"matrix_reclaimed_mb={before_mb - after_mb:.2f}")

    if post_qc != "ok":
        print("[FAIL] Matrix post-retention quick_check failed.")
        return 6
    if after_mb > 20:
        print("[FAIL] Matrix footprint remains above signed-off 20 MB ceiling.")
        return 7

    if price_db.exists():
        price_post_qc = final_vacuum(price_db)
        price_after_mb, price_after_sizes = footprint(price_db)
        print(f"price_truth_post_vacuum_quick_check={price_post_qc}")
        print("price_truth_sizes_mb=" + json.dumps(
            {k: round(v, 2) for k, v in price_after_sizes.items()}, sort_keys=True,
        ))
        print(f"price_truth_total_footprint_mb={price_after_mb:.2f}")
        print(f"price_truth_reclaimed_mb={price_before_mb - price_after_mb:.2f}")
        if price_post_qc != "ok":
            print("[FAIL] Price-truth post-retention quick_check failed.")
            return 10
        if price_after_mb > 20:
            print("[FAIL] Price-truth footprint remains above signed-off 20 MB ceiling.")
            return 11

    print("[PASS] Shutdown retention complete: matrix target 10-20 MB band + bounded price-truth DB are healthy.")
    print(f"Matrix backup: {backup}")
    print(f"Matrix log: {log}")
    print(f"Matrix report: {report}")
    if price_db.exists():
        print(f"Price-truth log: {price_log}")
        print(f"Price-truth report: {price_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
