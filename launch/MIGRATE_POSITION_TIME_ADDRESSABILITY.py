"""
MIGRATE_POSITION_TIME_ADDRESSABILITY_20260804
=============================================
Restores time-addressability to paper_positions, and stamps the durable
operator-selection columns the mode contract reads.

Why
---
The 4 Aug 10:37-15:07 audit found paper_positions with window_rows = 0 in the
canonical database AND in the pre-retention backup, while every other trading
table showed in-window activity (65 parity rows, 65 live decisions, 128 paper
executions, 63 autopsies, 63 fill_provenance rows).

Cause: paper_positions.updated_at is declared REAL but never written. Across
services/ there are 41 `UPDATE paper_positions` statements and none of them
maintain updated_at. The outcome authority is therefore invisible to any
time-window query, which is precisely why realised PnL for the session could
not be recomputed from the canonical database.

This migration is additive and idempotent. It does not alter any realised PnL,
entry, exit, or funding_mode value. It backfills updated_at from the best
available existing timestamp so historical rows become addressable, and adds a
trigger so future writes maintain it without touching 41 call sites.

Run:
    python launch\\MIGRATE_POSITION_TIME_ADDRESSABILITY.py
    python launch\\MIGRATE_POSITION_TIME_ADDRESSABILITY.py --verify
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"

TRIGGER = "trg_paper_positions_updated_at"
INSERT_TRIGGER = "trg_paper_positions_insert_updated_at"

# Columns the mode contract needs durably recorded.
CONFIG_COLUMNS = (
    ("OPERATOR_SELECTION", "TEXT"),
    ("LIVE_MIRROR_POLICY", "TEXT"),
)


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def migrate(db_path: Path = DB) -> int:
    if not db_path.exists():
        print(f"[FAIL] database missing: {db_path}")
        return 3

    backup = db_path.with_name(
        f"{db_path.stem}.before_time_addressability_{int(time.time())}.db"
    )
    shutil.copy2(db_path, backup)
    print(f"[OK] backup: {backup.name}")

    con = sqlite3.connect(str(db_path), timeout=30)
    try:
        if not _table_exists(con, "paper_positions"):
            print("[FAIL] paper_positions table absent")
            return 4

        cols = _cols(con, "paper_positions")
        if "updated_at" not in cols:
            con.execute("ALTER TABLE paper_positions ADD COLUMN updated_at REAL")
            cols.add("updated_at")
            print("[OK] added paper_positions.updated_at")

        # Backfill from the most specific timestamp each row actually has.
        # Order matters: a closed position's truth is its close time.
        candidates = [c for c in ("closed_at", "opened_at", "created_at", "entry_time")
                      if c in cols]
        if candidates:
            expr = "COALESCE(" + ", ".join(candidates) + ")"
            n = con.execute(
                f"UPDATE paper_positions SET updated_at = {expr} "
                f"WHERE updated_at IS NULL AND {expr} IS NOT NULL"
            ).rowcount
            print(f"[OK] backfilled updated_at on {n} rows from {candidates}")
        else:
            print("[WARN] no source timestamp column found; backfill skipped")

        still_null = con.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE updated_at IS NULL"
        ).fetchone()[0]
        if still_null:
            print(f"[WARN] {still_null} rows remain without any timestamp")

        # Low-contention lifecycle timestamps. The former generic AFTER UPDATE
        # trigger doubled every paper_positions write, including high-frequency
        # MTM/peak/trail telemetry. That could amplify writer contention.
        # Timestamp only inserts and material lifecycle/settlement changes.
        con.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}")
        con.execute(f"DROP TRIGGER IF EXISTS {INSERT_TRIGGER}")

        con.execute(f"""
            CREATE TRIGGER {INSERT_TRIGGER}
            AFTER INSERT ON paper_positions
            FOR EACH ROW
            WHEN NEW.updated_at IS NULL
            BEGIN
                UPDATE paper_positions
                   SET updated_at = COALESCE(NEW.closed_at, NEW.opened_at,
                       (julianday('now') - 2440587.5) * 86400.0)
                 WHERE rowid = NEW.rowid;
            END
        """)

        lifecycle_candidates = [
            "status", "closed_at", "exit_price", "realized_pnl_usd",
            "exit_reason", "win_loss", "live_state", "sell_tx_sig",
            "chain_confirmed_at", "reconciled_at",
        ]
        lifecycle_cols = [c for c in lifecycle_candidates if c in cols]
        if lifecycle_cols:
            update_of = ", ".join(lifecycle_cols)
            con.execute(f"""
                CREATE TRIGGER {TRIGGER}
                AFTER UPDATE OF {update_of} ON paper_positions
                FOR EACH ROW
                WHEN NEW.updated_at IS OLD.updated_at
                BEGIN
                    UPDATE paper_positions
                       SET updated_at = (julianday('now') - 2440587.5) * 86400.0
                     WHERE rowid = NEW.rowid;
                END
            """)
        print(f"[OK] installed low-contention triggers {INSERT_TRIGGER}, {TRIGGER}")

        # Durable mode-contract columns.
        con.execute(
            "CREATE TABLE IF NOT EXISTS system_config "
            "(key TEXT PRIMARY KEY, value TEXT, description TEXT, updated_at REAL)"
        )
        for key, _t in CONFIG_COLUMNS:
            row = con.execute(
                "SELECT value FROM system_config WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO system_config(key,value,description,updated_at) "
                    "VALUES(?,?,?,?)",
                    (key, "", "stamped by launcher; read by core.mode_contract",
                     time.time()),
                )
                print(f"[OK] seeded system_config.{key}")

        con.commit()
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        print(f"[OK] quick_check = {qc}")
        return 0 if qc == "ok" else 5
    finally:
        con.close()


def verify(db_path: Path = DB) -> int:
    con = sqlite3.connect(str(db_path), timeout=15)
    try:
        ok = True
        cols = _cols(con, "paper_positions")
        if "updated_at" not in cols:
            print("[FAIL] paper_positions.updated_at absent")
            ok = False

        total = con.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
        nulls = con.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE updated_at IS NULL"
        ).fetchone()[0]
        print(f"[INFO] paper_positions rows={total} without_timestamp={nulls}")
        if total and nulls == total:
            print("[FAIL] no row is time-addressable")
            ok = False

        for trigger_name in (INSERT_TRIGGER, TRIGGER):
            trg = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger_name,),
            ).fetchone()
            if not trg:
                print(f"[FAIL] trigger {trigger_name} missing")
                ok = False
            else:
                print(f"[OK] trigger {trigger_name} present")
                if trigger_name == TRIGGER and "AFTER UPDATE ON paper_positions" in str(trg[0]):
                    print("[FAIL] broad every-update trigger still installed")
                    ok = False

        for key, _t in CONFIG_COLUMNS:
            r = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            print(f"[INFO] system_config.{key} = {r[0]!r}" if r
                  else f"[FAIL] system_config.{key} missing")
            if not r:
                ok = False

        print("[PASS] migration verified" if ok else "[FAIL] verification failed")
        return 0 if ok else 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(verify() if "--verify" in sys.argv else migrate())
