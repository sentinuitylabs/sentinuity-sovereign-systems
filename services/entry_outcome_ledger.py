"""
services/entry_outcome_ledger.py — SIGNOFF_ENTRY_OUTCOME_LEDGER_20260613
========================================================================
APPEND-ONLY, EVICTION-EXCLUDED outcome ledger.

This is the INSTRUMENT every profitability acceptance test depends on, and
the permanent training set for empirical resolver re-weighting later. It is
NOT cleanup — it goes in FIRST so the scorer fix and every veto can be
validated against durable evidence instead of the n=30 pruned market_snapshots.

Hard rules:
  - One row written at position open (ledger_open).
  - The SAME row updated at close (ledger_close).
  - NEVER deleted by rolling eviction / periodic refresh / cleanup.
    The table name is registered in LEDGER_PROTECTED_TABLES; cleaners must skip it.
  - Pure additive: a failure here can never block or alter a trade. Every call
    is wrapped so an exception is swallowed and logged at debug only.

Safe to import from execution_engine. No trading logic lives here.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

try:
    from core.schema import get_connection
except Exception:  # pragma: no cover - sandbox/import-order tolerance
    get_connection = None  # type: ignore

log = logging.getLogger("entry_outcome_ledger")

LEDGER_TABLE = "entry_outcome_ledger"

# Cleaners / eviction passes must consult this and SKIP these tables.
LEDGER_PROTECTED_TABLES = (LEDGER_TABLE,)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                 TEXT,
    snap_id              INTEGER,
    position_id          INTEGER,
    lane                 TEXT,
    mode                 TEXT,
    entry_source         TEXT,
    opened_at            REAL,
    closed_at            REAL,
    status               TEXT DEFAULT 'OPEN',
    confidence_raw       REAL,
    confidence_final     REAL,
    confidence_source    TEXT,
    feature_payload_json TEXT,
    feature_fill_json    TEXT,
    resolver_version     TEXT,
    code_vault_sha       TEXT,
    velocity_preentry_pct REAL,
    velocity_window_sec  REAL,
    liquidity_usd        REAL,
    curve_sol            REAL,
    market_cap_usd       REAL,
    volume_5m_usd        REAL,
    buy_pressure         REAL,
    sm_tier              TEXT,
    smart_wallet_count   INTEGER,
    copytrade_bonus      REAL,
    signal_age_sec       REAL,
    price_age_sec        REAL,
    latch_to_open_sec    REAL,
    provider_at_entry    TEXT,
    oracle_route         TEXT,
    first_tick_delay_sec REAL,
    post_entry_tick_count INTEGER,
    max_move_pct         REAL,
    max_move_time_sec    REAL,
    peak_price_seen      REAL,
    exit_reason          TEXT,
    expected_exit_reason TEXT,
    realized_pnl_usd     REAL,
    pnl_pct              REAL,
    notes                TEXT,
    created_at           REAL,
    updated_at           REAL
);
CREATE INDEX IF NOT EXISTS idx_eol_position ON {LEDGER_TABLE}(position_id);
CREATE INDEX IF NOT EXISTS idx_eol_mint     ON {LEDGER_TABLE}(mint);
CREATE INDEX IF NOT EXISTS idx_eol_opened   ON {LEDGER_TABLE}(opened_at);
"""


def ensure_schema(conn: Any = None) -> None:
    """Create the ledger table if absent. Idempotent, safe to call every launch."""
    try:
        if conn is not None:
            conn.executescript(_SCHEMA)
            return
        if get_connection is None:
            return
        with get_connection() as c:
            c.executescript(_SCHEMA)
            c.commit()
    except Exception as e:
        log.debug("ledger ensure_schema skipped: %s", e)


def _to_json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)[:60000]
    except Exception:
        return None


def ledger_open(position_id: int, mint: str, snap_id: Optional[int],
                fields: dict, conn: Any = None) -> None:
    """Write one row at position open. `fields` is a flat dict; unknown keys ignored.
    Pure additive — never raises into the caller."""
    try:
        now = time.time()
        row = {
            "mint": mint, "snap_id": snap_id, "position_id": position_id,
            "lane": fields.get("lane"), "mode": fields.get("mode"),
            "entry_source": fields.get("entry_source"),
            "opened_at": fields.get("opened_at", now),
            "status": "OPEN",
            "confidence_raw": fields.get("confidence_raw"),
            "confidence_final": fields.get("confidence_final"),
            "confidence_source": fields.get("confidence_source"),
            "feature_payload_json": _to_json(fields.get("feature_payload")),
            "feature_fill_json": _to_json(fields.get("feature_fill")),
            "resolver_version": fields.get("resolver_version"),
            "code_vault_sha": fields.get("code_vault_sha"),
            "velocity_preentry_pct": fields.get("velocity_preentry_pct"),
            "velocity_window_sec": fields.get("velocity_window_sec"),
            "liquidity_usd": fields.get("liquidity_usd"),
            "curve_sol": fields.get("curve_sol"),
            "market_cap_usd": fields.get("market_cap_usd"),
            "volume_5m_usd": fields.get("volume_5m_usd"),
            "buy_pressure": fields.get("buy_pressure"),
            "sm_tier": fields.get("sm_tier"),
            "smart_wallet_count": fields.get("smart_wallet_count"),
            "copytrade_bonus": fields.get("copytrade_bonus"),
            "signal_age_sec": fields.get("signal_age_sec"),
            "price_age_sec": fields.get("price_age_sec"),
            "latch_to_open_sec": fields.get("latch_to_open_sec"),
            "provider_at_entry": fields.get("provider_at_entry"),
            "oracle_route": fields.get("oracle_route"),
            "created_at": now, "updated_at": now,
        }
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        vals = list(row.values())

        def _do(c):
            ensure_schema(c)
            c.execute(f"INSERT INTO {LEDGER_TABLE} ({cols}) VALUES ({ph})", vals)

        if conn is not None:
            _do(conn)
        elif get_connection is not None:
            with get_connection() as c:
                _do(c); c.commit()
    except Exception as e:
        log.debug("ledger_open skipped pid=%s: %s", position_id, e)


def ledger_close(position_id: int, fields: dict, conn: Any = None) -> None:
    """Update the same ledger row at close. Pure additive — never raises."""
    try:
        now = time.time()
        sets = {
            "closed_at": fields.get("closed_at", now),
            "status": "CLOSED",
            "exit_reason": fields.get("exit_reason"),
            "expected_exit_reason": fields.get("expected_exit_reason"),
            "realized_pnl_usd": fields.get("realized_pnl_usd"),
            "pnl_pct": fields.get("pnl_pct"),
            "max_move_pct": fields.get("max_move_pct"),
            "max_move_time_sec": fields.get("max_move_time_sec"),
            "peak_price_seen": fields.get("peak_price_seen"),
            "post_entry_tick_count": fields.get("post_entry_tick_count"),
            "first_tick_delay_sec": fields.get("first_tick_delay_sec"),
            "notes": fields.get("notes"),
            "updated_at": now,
        }
        sets = {k: v for k, v in sets.items() if v is not None}
        if not sets:
            return
        assign = ", ".join(f"{k}=?" for k in sets)
        vals = list(sets.values()) + [position_id]

        def _do(c):
            ensure_schema(c)
            c.execute(
                f"UPDATE {LEDGER_TABLE} SET {assign} WHERE position_id=? AND status='OPEN'",
                vals)

        if conn is not None:
            _do(conn)
        elif get_connection is not None:
            with get_connection() as c:
                _do(c); c.commit()
    except Exception as e:
        log.debug("ledger_close skipped pid=%s: %s", position_id, e)


def is_protected_table(name: str) -> bool:
    """Cleaners call this to know they must NOT evict/delete this table."""
    return str(name).strip().lower() in {t.lower() for t in LEDGER_PROTECTED_TABLES}


if __name__ == "__main__":
    ensure_schema()
    print(f"{LEDGER_TABLE} ensured. Protected tables: {LEDGER_PROTECTED_TABLES}")
