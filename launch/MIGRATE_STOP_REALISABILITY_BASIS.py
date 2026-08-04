#!/usr/bin/env python3
"""
STOP_BASIS_REPAIR_20260804 — migration.

Adds the new provenance/latency columns and marks the pre-repair rows as a
separate evidence cohort.

What this does NOT do:
  * does not modify any existing column value on any existing row;
  * does not delete rows;
  * does not recompute or invent a historical SOL/USD basis;
  * does not touch any config key, live flag, gate, threshold or sender.

Rows written before the repair could never compute executable_pct, so they are
not evidence about executable_pct. They are retained in full and marked
basis_version=1 / cohort_reason='LEGACY_NO_USD_BASIS'. readiness() scopes to
basis_version >= 2, which makes the gate strictly harder to satisfy.

    python launch\\MIGRATE_STOP_REALISABILITY_BASIS.py            # apply
    python launch\\MIGRATE_STOP_REALISABILITY_BASIS.py --dry-run  # report only
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "sentinuity_matrix.db").exists() else HERE.parent
DB = ROOT / "sentinuity_matrix.db"
INTEL = ROOT / "sentinuity_intelligence.db"
TABLE = "stop_realisability_ledger"
WSOL = "So11111111111111111111111111111111111111112"

DRY = "--dry-run" in sys.argv


def main() -> int:
    print("=" * 70)
    print("STOP REALISABILITY — USD BASIS COHORT MIGRATION")
    print("=" * 70)
    print("DB:", DB)
    if not DB.exists():
        print("[FAIL] matrix DB missing")
        return 2

    sys.path.insert(0, str(ROOT))
    try:
        from services.stop_realisability import (
            ensure_schema, BASIS_VERSION, LEGACY_BASIS_VERSION, LEGACY_COHORT_REASON)
    except Exception as exc:
        print(f"[FAIL] cannot import patched module: {type(exc).__name__}: {exc}")
        print("       Install services/stop_realisability.py first.")
        return 3

    if not DRY:
        backup = ROOT / f"sentinuity_matrix.backup_stop_basis_{int(time.time())}.db"
        try:
            shutil.copy2(DB, backup)
            print("[OK] backup:", backup.name)
        except Exception as exc:
            print(f"[FAIL] backup failed, refusing to migrate: {exc}")
            return 4

    con = sqlite3.connect(str(DB), timeout=30)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
        if not exists:
            print(f"[INFO] {TABLE} does not exist yet; schema will be created on first probe.")
            if not DRY:
                ensure_schema(con)
                con.commit()
                print("[OK] schema created")
            return 0

        before = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        cols_before = {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
        print(f"[INFO] existing rows: {before}")

        ensure_schema(con)
        cols_after = {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
        added = sorted(cols_after - cols_before)
        print(f"[OK] columns added: {added or '(none, already present)'}")

        untagged = con.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE basis_version IS NULL").fetchone()[0]
        with_exec = con.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE executable_pct IS NOT NULL").fetchone()[0]
        print(f"[INFO] rows needing cohort tag : {untagged}")
        print(f"[INFO] rows with executable_pct: {with_exec}")

        if DRY:
            print("\n[DRY-RUN] would tag "
                  f"{untagged} rows as basis_version={LEGACY_BASIS_VERSION} "
                  f"cohort_reason='{LEGACY_COHORT_REASON}'")
        else:
            # Legacy rows that DID measure executable_pct (if any) keep their
            # value and are still tagged legacy: their latency fields were
            # recorded under the old, mislabelled semantics.
            con.execute(
                f"UPDATE {TABLE} SET basis_version=?, cohort_reason=? "
                "WHERE basis_version IS NULL",
                (LEGACY_BASIS_VERSION, LEGACY_COHORT_REASON))
            con.commit()
            tagged = con.execute(
                f"SELECT COUNT(*) FROM {TABLE} WHERE basis_version=?",
                (LEGACY_BASIS_VERSION,)).fetchone()[0]
            print(f"[OK] tagged legacy cohort: {tagged} rows")

        after = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        print(f"[OK] row count unchanged: {before} -> {after}"
              if before == after else f"[FAIL] row count changed {before} -> {after}")

        # Report only: is deterministic historical recovery even possible?
        print("\n--- historical recovery feasibility (report only, nothing written) ---")
        if not INTEL.exists():
            print("  intelligence DB absent -> recovery NOT possible")
        else:
            ic = sqlite3.connect(str(INTEL), timeout=10)
            try:
                has = ic.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='mtm_ticks'").fetchone()
                if not has:
                    print("  mtm_ticks absent -> recovery NOT possible")
                else:
                    n, lo, hi = ic.execute(
                        "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM mtm_ticks "
                        "WHERE mint_address=?", (WSOL,)).fetchone()
                    print(f"  wSOL ticks retained: {n}")
                    if n:
                        print(f"  coverage: {time.strftime('%Y-%m-%d %H:%M', time.gmtime(lo/1000))}"
                              f" .. {time.strftime('%Y-%m-%d %H:%M', time.gmtime(hi/1000))} UTC")
                        t0 = con.execute(
                            f"SELECT MIN(trigger_ts) FROM {TABLE}").fetchone()[0]
                        if t0 and lo / 1000.0 <= t0:
                            print("  -> basis MAY be recoverable; decide explicitly before"
                                  " writing to any new versioned field")
                        else:
                            print("  -> probe history predates retained ticks;"
                                  " recovery NOT possible")
                    else:
                        print("  -> no wSOL ticks; recovery NOT possible")
            finally:
                ic.close()
        print("\nPreferred policy stands: leave legacy rows as-is and gather a fresh"
              "\nforward cohort. No basis was invented.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
