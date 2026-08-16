from __future__ import annotations

"""
launch/migrate_substrate_exposure_contract.py
===============================================================================
SUBSTRATE EXPOSURE CONTRACT MIGRATION (SUBSTRATE_EXPOSURE_REPAIR_20260802)

Idempotent. Additive only. Touches no live-trading flag, no Solana Mode B
setting, no `would_veto` state, no live sizing and no canary governor value.

What it does:
  1. Seeds SUBSTRATE_MAX_ASSET_EXPOSURE_USD if absent. The audit found this key
     is READ in one place and WRITTEN nowhere, so its value was whatever some
     earlier manual tuning happened to leave behind.
  2. Refuses to leave the system in the unopenable state where
     POSITION_SIZE > ASSET_CAP: it reports the conflict and, only with
     --fix-sizing, lowers POSITION_SIZE to the cap. It never silently raises a
     risk ceiling.
  3. Adds the exposure-identity and block-state columns.
  4. Backfills exposure_asset for existing positions from the canonical family
     map, reporting every native/wrapped merge before it happens.
  5. Records the schema version and the migration outcome.

A schema failure here is TERMINAL and reported. It never becomes a retry.

Usage:
    python launch/migrate_substrate_exposure_contract.py --dry-run
    python launch/migrate_substrate_exposure_contract.py
    python launch/migrate_substrate_exposure_contract.py --fix-sizing
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_TAG = "SUBSTRATE_EXPOSURE_REPAIR_20260802"
DEFAULT_ASSET_CAP = 100.0

POSITION_COLUMNS = {"exposure_asset": "TEXT"}
OPPORTUNITY_COLUMNS = {
    "blocked_reason": "TEXT",
    "blocked_fingerprint": "TEXT",
    "blocked_at": "REAL",
    "blocked_count": "INTEGER",
    "retry_not_before": "REAL",
    "liquidity_status": "TEXT",
    "volume_status": "TEXT",
}


def _columns(con, table) -> set:
    try:
        return {str(r[1]) for r in con.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


def _cfg(con, key, default=None):
    try:
        row = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def migrate(db_path: Path, dry_run: bool = False, fix_sizing: bool = False) -> dict:
    from core.asset_identity import canonical_asset, WRAPPED_ALIASES

    report = {"schema_version": SCHEMA_TAG, "db": str(db_path),
              "dry_run": dry_run, "actions": [], "warnings": [],
              "blocked": False}

    if not db_path.exists():
        report["blocked"] = True
        report["warnings"].append(f"database not found: {db_path}")
        return report

    con = sqlite3.connect(str(db_path), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=8000")
        present = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for required in ("system_config", "substrate_positions", "substrate_opportunities"):
            if required not in present:
                report["blocked"] = True
                report["warnings"].append(
                    f"TERMINAL: required table {required!r} absent — run the substrate "
                    "schema bootstrap first. This migration will not create trading tables.")
        if report["blocked"]:
            return report

        # ── 1/2. config contract ────────────────────────────────────────────
        cap_raw = _cfg(con, "SUBSTRATE_MAX_ASSET_EXPOSURE_USD")
        size = _as_float(_cfg(con, "SUBSTRATE_POSITION_SIZE_USD"), 25.0)
        cap = _as_float(cap_raw, None)

        if cap is None:
            cap = max(DEFAULT_ASSET_CAP, size)
            report["actions"].append(
                f"seed SUBSTRATE_MAX_ASSET_EXPOSURE_USD={cap:.2f} "
                "(was absent from system_config entirely)")
            if not dry_run:
                con.execute(
                    "INSERT INTO system_config(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("SUBSTRATE_MAX_ASSET_EXPOSURE_USD", f"{cap:.4f}"))

        if size > cap:
            message = (f"UNOPENABLE: SUBSTRATE_POSITION_SIZE_USD={size:.2f} exceeds "
                       f"SUBSTRATE_MAX_ASSET_EXPOSURE_USD={cap:.2f}. The first position "
                       "on an empty book is arithmetically impossible.")
            if fix_sizing:
                report["actions"].append(
                    f"{message} --fix-sizing given: lowering POSITION_SIZE to {cap:.2f}. "
                    "The risk ceiling is NOT raised.")
                if not dry_run:
                    con.execute(
                        "INSERT INTO system_config(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        ("SUBSTRATE_POSITION_SIZE_USD", f"{cap:.4f}"))
            else:
                report["warnings"].append(
                    message + " Re-run with --fix-sizing to lower the position size, "
                    "or set the ceiling deliberately yourself. This migration will not "
                    "raise a risk limit on your behalf.")

        # ── 3. columns ──────────────────────────────────────────────────────
        for table, spec in (("substrate_positions", POSITION_COLUMNS),
                            ("substrate_opportunities", OPPORTUNITY_COLUMNS)):
            existing = _columns(con, table)
            for column, ddl in spec.items():
                if column in existing:
                    continue
                report["actions"].append(f"add {table}.{column} {ddl}")
                if not dry_run:
                    try:
                        con.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {ddl}')
                    except sqlite3.Error as exc:
                        report["blocked"] = True
                        report["warnings"].append(
                            f"TERMINAL schema failure on {table}.{column}: {exc}")
                        return report

        # ── 4. backfill canonical identity ──────────────────────────────────
        merges = []
        # In --dry-run the ALTER above was not executed, so exposure_asset may
        # not exist yet. Select on the ticker alone in that case and report the
        # merges the real run would perform.
        has_column = "exposure_asset" in _columns(con, "substrate_positions")
        backfill_sql = (
            "SELECT id, asset_symbol FROM substrate_positions "
            "WHERE exposure_asset IS NULL OR exposure_asset=''"
            if has_column else
            "SELECT id, asset_symbol FROM substrate_positions")
        for row in con.execute(backfill_sql):
            symbol = str(row["asset_symbol"] or "")
            canonical = canonical_asset(symbol)
            if canonical and canonical != symbol.strip().upper():
                merges.append({"position_id": row["id"], "ticker": symbol,
                               "exposure_asset": canonical})
            if not dry_run and has_column:
                con.execute("UPDATE substrate_positions SET exposure_asset=? WHERE id=?",
                            (canonical, row["id"]))
        report["wrapped_merges"] = merges
        if merges:
            report["actions"].append(
                f"backfilled {len(merges)} wrapped position(s) onto their canonical "
                f"family: {[m['ticker'] + '->' + m['exposure_asset'] for m in merges]}")
        report["known_wrapped_aliases"] = WRAPPED_ALIASES

        # Post-backfill exposure picture, so the operator sees what the new
        # unified budget actually looks like before the supervisor next runs.
        exposure = {}
        family_expr = ("COALESCE(exposure_asset, UPPER(COALESCE(asset_symbol,'')))"
                       if has_column else "UPPER(COALESCE(asset_symbol,''))")
        for row in con.execute(
                f"SELECT {family_expr} fam, "
                "COALESCE(SUM(COALESCE(size_usd,0)),0) total, COUNT(*) n "
                "FROM substrate_positions WHERE mode='PAPER' AND state='OPEN' GROUP BY 1"):
            exposure[row["fam"]] = {"usd": round(float(row["total"] or 0), 4),
                                    "legs": row["n"]}
        report["unified_open_exposure"] = exposure
        for family, detail in exposure.items():
            if detail["usd"] > cap:
                report["warnings"].append(
                    f"{family} already holds ${detail['usd']:.2f} across {detail['legs']} "
                    f"leg(s), above the ${cap:.2f} ceiling. Unifying wrapped identities "
                    "revealed pre-existing over-exposure; it did not create it.")

        # ── 5. version stamp ────────────────────────────────────────────────
        if not dry_run:
            con.execute("CREATE TABLE IF NOT EXISTS substrate_schema_migrations("
                        "tag TEXT PRIMARY KEY, applied_at REAL, result TEXT)")
            con.execute(
                "INSERT INTO substrate_schema_migrations(tag,applied_at,result) "
                "VALUES(?,?,?) ON CONFLICT(tag) DO UPDATE SET "
                "applied_at=excluded.applied_at, result=excluded.result",
                (SCHEMA_TAG, time.time(),
                 json.dumps({"actions": len(report["actions"]),
                             "warnings": len(report["warnings"])})))
            con.commit()
        report["ok"] = not report["blocked"]
        return report
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "sentinuity_matrix.db"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fix-sizing", action="store_true",
                        help="lower POSITION_SIZE to the asset cap; never raises the cap")
    args = parser.parse_args()

    report = migrate(Path(args.db), dry_run=args.dry_run, fix_sizing=args.fix_sizing)
    print(json.dumps(report, indent=2, default=str))
    if report.get("blocked"):
        print("\nMIGRATION_BLOCKED — terminal, not retried.")
        return 2
    if report.get("warnings"):
        print("\nMIGRATION_APPLIED_WITH_WARNINGS")
        return 1
    print("\nMIGRATION_APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
