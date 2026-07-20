
from __future__ import annotations
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

ENV_PATH = ROOT / ".env"
MATRIX_DB = ROOT / "sentinuity_matrix.db"
INTEL_DB = ROOT / "sentinuity_intelligence.db"

passed = 0
warnings = 0
failures = []

def ok(msg: str):
    global passed
    passed += 1
    print(f"  [OK] {msg}")

def warn(msg: str):
    global warnings
    warnings += 1
    print(f"  [WARN] {msg}")

def fail(msg: str):
    failures.append(msg)
    print(f"  [CRITICAL] {msg}")

def check_env():
    print("\n[1] .env structural integrity")
    if not ENV_PATH.exists():
        fail(f".env not found at root: {ENV_PATH}")
        return
    if ENV_PATH.stat().st_size <= 0:
        fail(f".env exists but is empty: {ENV_PATH}")
        return

    text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
    bad_markers = ["<<<<<<<", "=======", ">>>>>>>"]
    if any(m in text for m in bad_markers):
        fail(".env contains merge-conflict markers")
        return

    ok(f".env found at root: {ENV_PATH}")

def check_db(path: Path, label: str, required: bool = True):
    if not path.exists():
        if required:
            fail(f"{label} DB missing: {path}")
        else:
            warn(f"{label} DB missing: {path}")
        return

    try:
        conn = sqlite3.connect(str(path), timeout=5)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        conn.close()
        ok(f"{label} DB reachable: {path}")
    except Exception as e:
        if required:
            fail(f"{label} DB unreadable: {path} :: {e}")
        else:
            warn(f"{label} DB unreadable: {path} :: {e}")

def main() -> int:
    print("=" * 70)
    print("  SENTINUITY SOVEREIGN SECURITY PREFLIGHT")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"\nResolved project root: {ROOT}")

    os.chdir(str(ROOT))

    check_env()

    print("\n[2] Database reachability")
    check_db(MATRIX_DB, "sentinuity_matrix", required=True)
    check_db(INTEL_DB, "sentinuity_intelligence", required=True)

    print("\n" + "=" * 70)
    print(f"  Passed:   {passed}")
    print(f"  Warnings: {warnings}")
    print(f"  Failures: {len(failures)}")
    print("=" * 70)

    if failures:
        print("\n[SECURITY PREFLIGHT] FAIL - resolve blocking issues before launch")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("\n[SECURITY PREFLIGHT] PASS - root .env and DBs reachable")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
