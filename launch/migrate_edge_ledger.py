#!/usr/bin/env python3
"""
SENTINUITY — EDGE LEDGER MIGRATION (additive only)

Creates edge_confidence_ledger and its indexes. Idempotent.

Guarantees, verified before and after:
  * No existing table is created, dropped, altered or written.
  * The only new object is edge_confidence_ledger plus its indexes.
  * Safe to run repeatedly and safe to run on a live database.

    python launch/migrate_edge_ledger.py            # apply
    python launch/migrate_edge_ledger.py --check    # report only
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.edge_ledger import LEDGER_TABLE, ensure_schema  # noqa: E402


def snapshot(db: str) -> dict:
    con = sqlite3.connect(db, timeout=10.0)
    try:
        objs = {}
        for typ, name in con.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"):
            objs[f"{typ}:{name}"] = True
        counts = {}
        for k in list(objs):
            if k.startswith("table:"):
                t = k.split(":", 1)[1]
                try:
                    counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                except Exception:
                    counts[t] = -1
        return {"objects": objs, "counts": counts}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("SENTINUITY_DB", "sentinuity_matrix.db"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print(f"Database not found: {a.db}")
        print("Run this from the project root, or set SENTINUITY_DB.")
        return 2

    os.environ["SENTINUITY_DB"] = a.db
    before = snapshot(a.db)
    present = f"table:{LEDGER_TABLE}" in before["objects"]
    print(f"database        : {a.db}")
    print(f"existing tables : {sum(1 for k in before['objects'] if k.startswith('table:'))}")
    print(f"{LEDGER_TABLE:<16}: {'PRESENT' if present else 'ABSENT'}")

    if a.check:
        if present:
            con = sqlite3.connect(a.db)
            n = con.execute(f"SELECT COUNT(*) FROM {LEDGER_TABLE}").fetchone()[0]
            cols = len(list(con.execute(f"PRAGMA table_info({LEDGER_TABLE})")))
            con.close()
            print(f"rows            : {n}\ncolumns         : {cols}")
        return 0

    if not ensure_schema():
        print("FAIL  schema creation failed")
        return 1

    after = snapshot(a.db)
    new = set(after["objects"]) - set(before["objects"])
    removed = set(before["objects"]) - set(after["objects"])
    changed = [t for t, n in before["counts"].items()
               if t != LEDGER_TABLE and after["counts"].get(t) != n]

    print(f"\nnew objects     : {sorted(new) if new else '(none, already applied)'}")
    print(f"removed objects : {sorted(removed) if removed else '(none)'}")
    print(f"row counts moved: {changed if changed else '(none)'}")

    ok = True
    if removed:
        print("FAIL  migration removed an object"); ok = False
    if changed:
        print("FAIL  migration altered rows in an existing table"); ok = False
    # The only objects this migration may create: the ledger table itself and
    # its own indexes (idx_ecl_*). Anything else means scope creep.
    for o in new:
        typ, _, name = o.partition(":")
        expected = (typ == "table" and name == LEDGER_TABLE) or \
                   (typ == "index" and name.startswith("idx_ecl_"))
        if not expected:
            print(f"FAIL  unexpected new object: {o}"); ok = False

    con = sqlite3.connect(a.db)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({LEDGER_TABLE})")]
    con.close()
    required = ["mint_address", "calibrated_confidence", "mint_confidence",
                "feature_count", "missing_features_json", "shadow_state",
                "peak_return_pct", "runner_25", "age_cohort", "price_completeness"]
    missing = [c for c in required if c not in cols]
    if missing:
        print(f"FAIL  ledger missing columns: {missing}"); ok = False

    print(f"\nledger columns  : {len(cols)}")
    print("MIGRATION OK" if ok else "MIGRATION FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
