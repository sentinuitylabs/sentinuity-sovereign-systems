"""
services/trade_accounting_guard.py
----------------------------------
Schema-safe SIM/REAL funding-mode guard for Sentinuity trade rows.

This module only adds metadata and validates separation. It never changes:
  - wallet balances
  - position size
  - entry/exit price
  - realized/unrealized PnL
  - status/open/closed state

CLI:
  python -m services.trade_accounting_guard migrate
  python -m services.trade_accounting_guard backfill --dry-run
  python -m services.trade_accounting_guard apply
  python -m services.trade_accounting_guard validate
"""
from __future__ import annotations


import argparse
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "sentinuity_matrix.db"


def _connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def ensure_trade_accounting_schema(db_path: str | Path = DEFAULT_DB) -> dict[str, Any]:
    """Add metadata columns to paper_positions if missing. No defaults that hide legacy state."""
    added: list[str] = []
    with _connect(db_path) as conn:
        if not _table_exists(conn, "paper_positions"):
            return {"paper_positions": "missing", "added": added}
        cols = _cols(conn, "paper_positions")
        for col, ddl in [
            ("funding_mode", "TEXT"),
            ("execution_source", "TEXT"),
            ("money_source", "TEXT"),
            ("mode_inferred_at", "REAL"),
            ("mode_confidence", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} {ddl}")
                added.append(col)
        conn.commit()
    return {"added": added}


def derive_funding_mode(row: Any) -> dict[str, str]:
    """
    REAL only with explicit tx/live evidence.
    Legacy unknown defaults to SIM for safety.
    """
    entry = str(row_get(row, "entry_price_source", "") or "")
    exit_reason = str(row_get(row, "exit_reason", "") or "")
    existing_mode = str(row_get(row, "trade_mode", "") or row_get(row, "funding_mode", "") or "")
    tx_sig = str(row_get(row, "tx_signature", "") or row_get(row, "tx_sig", "") or "")

    evidence = " ".join([entry, exit_reason, existing_mode, tx_sig]).lower()
    is_real = (
        entry.upper().startswith("LIVE:")
        or entry.lower().startswith("live_tx:")
        or exit_reason.upper().startswith("LIVE:")
        or existing_mode.lower() in {"live", "real", "real_money"}
        or bool(tx_sig.strip())
    )

    if is_real:
        return {
            "funding_mode": "REAL",
            "execution_source": "REAL_TX",
            "money_source": "REAL_WALLET",
            "mode_confidence": "explicit",
        }

    if entry.upper().startswith("PAPER_ONLY"):
        return {
            "funding_mode": "SIM",
            "execution_source": "PAPER_FAILED",
            "money_source": "SIM_EQUITY",
            "mode_confidence": "explicit_paper_failed",
        }

    return {
        "funding_mode": "SIM",
        "execution_source": "PAPER_ENGINE",
        "money_source": "SIM_EQUITY",
        "mode_confidence": "legacy_default",
    }


def _select_trade_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cols = _cols(conn, "paper_positions")
    wanted = ["id", "status", "entry_price_source", "exit_reason", "funding_mode",
              "execution_source", "money_source", "mode_confidence", "mode_inferred_at",
              "trade_mode", "tx_signature", "tx_sig"]
    select_parts = []
    for c in wanted:
        if c in cols:
            select_parts.append(c)
        else:
            select_parts.append(f"NULL AS {c}")
    where = []
    if "mode_inferred_at" in cols:
        where.append("mode_inferred_at IS NULL")
    if "mode_confidence" in cols:
        where.append("COALESCE(mode_confidence,'') IN ('','inferred','legacy_default')")
    if "funding_mode" in cols:
        where.append("COALESCE(funding_mode,'') = ''")
    where_sql = " OR ".join(where) if where else "1=1"
    return conn.execute(
        f"SELECT {', '.join(select_parts)} FROM paper_positions WHERE {where_sql} ORDER BY id ASC"
    ).fetchall()


def backfill_trade_modes(db_path: str | Path = DEFAULT_DB, dry_run: bool = False) -> dict[str, int]:
    ensure_trade_accounting_schema(db_path)
    counts = {"rows_seen": 0, "rows_updated": 0, "real": 0, "sim": 0, "open_metadata_rows": 0}
    now = time.time()

    with _connect(db_path) as conn:
        if not _table_exists(conn, "paper_positions"):
            return counts
        rows = _select_trade_rows(conn)
        counts["rows_seen"] = len(rows)

        for r in rows:
            derived = derive_funding_mode(r)
            if derived["funding_mode"] == "REAL":
                counts["real"] += 1
            else:
                counts["sim"] += 1
            if str(row_get(r, "status", "")).upper() == "OPEN":
                counts["open_metadata_rows"] += 1

            if dry_run:
                continue

            conn.execute(
                """
                UPDATE paper_positions
                SET funding_mode=?,
                    execution_source=?,
                    money_source=?,
                    mode_inferred_at=?,
                    mode_confidence=?
                WHERE id=?
                """,
                (
                    derived["funding_mode"],
                    derived["execution_source"],
                    derived["money_source"],
                    now,
                    derived["mode_confidence"],
                    row_get(r, "id"),
                ),
            )
            counts["rows_updated"] += 1

        if not dry_run:
            conn.commit()

    return counts


def validate_accounting_separation(db_path: str | Path = DEFAULT_DB) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with _connect(db_path) as conn:
        if not _table_exists(conn, "paper_positions"):
            return {"paper_positions": "missing"}
        cols = _cols(conn, "paper_positions")
        out["has_funding_mode"] = "funding_mode" in cols
        out["has_execution_source"] = "execution_source" in cols
        out["has_money_source"] = "money_source" in cols
        if "funding_mode" in cols:
            out["unknown_modes"] = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE COALESCE(funding_mode,'') NOT IN ('SIM','REAL')"
            ).fetchone()[0]
            out["sim_trades"] = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE funding_mode='SIM'"
            ).fetchone()[0]
            out["real_trades"] = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE funding_mode='REAL'"
            ).fetchone()[0]
            out["recent_unknown"] = conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT funding_mode FROM paper_positions ORDER BY COALESCE(opened_at, closed_at, id) DESC LIMIT 20
                ) WHERE COALESCE(funding_mode,'') NOT IN ('SIM','REAL')
                """
            ).fetchone()[0]
        if _table_exists(conn, "system_state"):
            try:
                r = conn.execute("SELECT wallet_balance, initial_capital FROM system_state WHERE id=1").fetchone()
                if r:
                    out["system_wallet_balance"] = float(r["wallet_balance"] or 0)
                    out["initial_capital"] = float(r["initial_capital"] or 0)
            except Exception:
                pass
    return out


def format_trade_label(position_state: str, funding_mode: str) -> str:
    state = str(position_state or "UNKNOWN").upper()
    mode = str(funding_mode or "SIM").upper()
    if mode not in {"SIM", "REAL"}:
        mode = "SIM"
    if state not in {"OPEN", "CLOSED"}:
        state = "OPEN" if state in {"LIVE", "ACTIVE"} else state
    return f"{state} — {mode}"


def _print_result(title: str, result: dict[str, Any]) -> None:
    print("\n" + title)
    print("=" * len(title))
    for k, v in result.items():
        print(f"{k}: {v}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="validate",
                    choices=["migrate", "backfill", "apply", "validate"])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = Path(args.db)

    if args.command == "migrate":
        _print_result("TRADE ACCOUNTING MIGRATE", ensure_trade_accounting_schema(db))
        return 0
    if args.command == "backfill":
        _print_result("TRADE ACCOUNTING BACKFILL DRY RUN" if args.dry_run else "TRADE ACCOUNTING BACKFILL",
                      backfill_trade_modes(db, dry_run=args.dry_run))
        return 0
    if args.command == "apply":
        ensure_trade_accounting_schema(db)
        _print_result("TRADE ACCOUNTING APPLY", backfill_trade_modes(db, dry_run=False))
        _print_result("TRADE ACCOUNTING VALIDATE", validate_accounting_separation(db))
        return 0
    if args.command == "validate":
        _print_result("TRADE ACCOUNTING VALIDATE", validate_accounting_separation(db))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
