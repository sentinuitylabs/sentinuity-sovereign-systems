"""
trade_lifecycle.py
──────────────────────────────────────────────────────────────────────────────
SENTINUITY TRADE LIFECYCLE EVENT SPINE

Append-only event ledger for every trade. The single source of temporal truth
that all modules can read without disagreeing.

This is NOT a replacement for paper_positions or wallet_write_log.
It is an ADDITIONAL append-only record that makes the full trade story
queryable, Polaris-learnable, and Guardian-verifiable in one place.

DESIGN PRINCIPLES:
  - Append-only. No UPDATE. No DELETE. Ever.
  - One row per event. Cheap. Fast. WAL-safe.
  - Written inside existing DB transactions where possible.
  - Falls back silently — never blocks a trade or mark on write failure.
  - Four writers only: execution_engine (OPEN/MARK/CLOSE), ws_price_oracle (TICK).
  - All readers: sovereign_hub, polaris, system_guardian, replay_engine.

EVENT TYPES:
  TRADE_OPENED      - position committed to paper_positions
  PRICE_TICK        - oracle wrote a post-entry mtm_tick (sampled, not every tick)
  MARK_WRITTEN      - update_position_mark wrote live_exec_* columns
  EXIT_SIGNAL       - TP/SL/TIME_CUT condition evaluated (before close)
  TRADE_CLOSED      - close_position_canonical committed
  GUARDIAN_ACTION   - guardian closed or flagged a position
  COVERAGE_ALERT    - NO_POST_ENTRY_TICKS fired
  POLARIS_REVIEW    - polaris analyzed this trade

QUERY PATTERNS:
  -- Full trade story:
  SELECT * FROM trade_lifecycle_events WHERE position_id=? ORDER BY ts;

  -- Coverage gaps (trades with zero ticks):
  SELECT position_id, COUNT(*) events,
         SUM(CASE WHEN event_type='PRICE_TICK' THEN 1 ELSE 0 END) ticks
  FROM trade_lifecycle_events GROUP BY position_id HAVING ticks=0;

  -- Peak price seen during trade:
  SELECT position_id, MAX(price) max_price, MIN(price) min_price
  FROM trade_lifecycle_events WHERE event_type='PRICE_TICK' GROUP BY position_id;

  -- First tick delay:
  SELECT o.position_id,
         MIN(t.ts) - o.ts AS first_tick_delay_sec
  FROM trade_lifecycle_events o
  JOIN trade_lifecycle_events t ON t.position_id=o.position_id AND t.event_type='PRICE_TICK'
  WHERE o.event_type='TRADE_OPENED'
  GROUP BY o.position_id;

  -- Exit validity distribution:
  SELECT exit_validity, COUNT(*) FROM trade_lifecycle_events
  WHERE event_type='TRADE_CLOSED' GROUP BY exit_validity;

  -- Polaris-clean trades (coverage_score > 0.3, exit executable):
  SELECT * FROM trade_lifecycle_events
  WHERE event_type='TRADE_CLOSED'
    AND coverage_score > 0.3
    AND exit_validity='EXECUTABLE'
  ORDER BY ts DESC LIMIT 20;
"""

from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("trade_lifecycle")

# ── SCHEMA ────────────────────────────────────────────────────────────────────

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL,
    mint_address    TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,

    -- Price state at time of event
    price           REAL,
    pct_from_entry  REAL,
    age_sec         REAL,
    source          TEXT,
    can_execute     INTEGER,

    -- Coverage state at time of event
    tick_count      INTEGER,
    coverage_score  REAL,
    first_tick_delay_sec REAL,
    max_pct_seen    REAL,
    min_pct_seen    REAL,

    -- Exit/close metadata (only on TRADE_CLOSED)
    exit_reason     TEXT,
    exit_validity   TEXT,   -- EXECUTABLE | STALE | NO_COVERAGE | GUARDIAN | FALLBACK
    realized_pnl    REAL,
    hold_seconds    REAL,

    -- Free-form note for any event
    note            TEXT,

    ts              REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tle_pos_ts  ON trade_lifecycle_events(position_id, ts);
CREATE INDEX IF NOT EXISTS tle_mint_ts ON trade_lifecycle_events(mint_address, ts);
CREATE INDEX IF NOT EXISTS tle_type    ON trade_lifecycle_events(event_type, ts);
"""

def ensure_schema(conn) -> None:
    """Create the table if absent. Safe to call on every startup."""
    for stmt in CREATE_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass


# ── WRITER ────────────────────────────────────────────────────────────────────

def _write_event(
    conn,
    *,
    position_id: int,
    mint_address: str,
    event_type: str,
    price: Optional[float] = None,
    pct_from_entry: Optional[float] = None,
    age_sec: Optional[float] = None,
    source: Optional[str] = None,
    can_execute: Optional[int] = None,
    tick_count: Optional[int] = None,
    coverage_score: Optional[float] = None,
    first_tick_delay_sec: Optional[float] = None,
    max_pct_seen: Optional[float] = None,
    min_pct_seen: Optional[float] = None,
    exit_reason: Optional[str] = None,
    exit_validity: Optional[str] = None,
    realized_pnl: Optional[float] = None,
    hold_seconds: Optional[float] = None,
    note: Optional[str] = None,
) -> None:
    """
    Write one event row. Designed to be called inside an existing transaction.
    Never raises — any failure is logged and swallowed.
    """
    try:
        conn.execute(
            """
            INSERT INTO trade_lifecycle_events (
                position_id, mint_address, event_type,
                price, pct_from_entry, age_sec, source, can_execute,
                tick_count, coverage_score, first_tick_delay_sec,
                max_pct_seen, min_pct_seen,
                exit_reason, exit_validity, realized_pnl, hold_seconds,
                note, ts
            ) VALUES (
                ?,?,?,  ?,?,?,?,?,  ?,?,?,  ?,?,  ?,?,?,?,  ?,?
            )
            """,
            (
                position_id, mint_address, event_type,
                price, pct_from_entry, age_sec, source, can_execute,
                tick_count, coverage_score, first_tick_delay_sec,
                max_pct_seen, min_pct_seen,
                exit_reason, exit_validity, realized_pnl, hold_seconds,
                note, time.time(),
            ),
        )
    except Exception as e:
        log.debug("trade_lifecycle._write_event failed type=%s pos=%s: %s",
                  event_type, position_id, e)


# ── COVERAGE HELPER ───────────────────────────────────────────────────────────

def get_trade_coverage(
    mint_address: str,
    opened_at: float,
    closed_at: Optional[float] = None,
) -> dict:
    """
    Read coverage metrics from sentinuity_intelligence.db for a trade window.
    Returns dict with tick_count, first_tick_delay, max_pct, min_pct, coverage_score.
    Safe — returns zeros on any error.
    """
    result = {
        "tick_count": 0,
        "first_tick_delay_sec": None,
        "max_pct_seen": None,
        "min_pct_seen": None,
        "coverage_score": 0.0,
        "entry_price": None,
    }
    try:
        from core.schema import get_intel_connection
        end_ms = (closed_at or time.time()) * 1000
        iconn = get_intel_connection()
        row = iconn.execute(
            """
            SELECT COUNT(*) n,
                   MIN(ts_ms)/1000.0 first_ts,
                   MAX(price_usd) mx,
                   MIN(price_usd) mn
            FROM mtm_ticks
            WHERE mint_address=? AND ts_ms>=? AND ts_ms<=? AND price_usd>0
            """,
            (mint_address, opened_at * 1000, end_ms),
        ).fetchone()
        iconn.close()
        if row and row[0]:
            n = int(row[0])
            result["tick_count"] = n
            if row[1]:
                result["first_tick_delay_sec"] = round(float(row[1]) - opened_at, 2)
            result["max_price"] = float(row[2]) if row[2] else None
            result["min_price"] = float(row[3]) if row[3] else None
            hold = (closed_at or time.time()) - opened_at
            # Coverage score: ticks seen vs expected at 1 tick/2s
            result["coverage_score"] = round(min(1.0, n / max(1.0, hold / 2.0)), 3)
    except Exception:
        pass
    return result


def compute_pct(price: Optional[float], entry_price: float) -> Optional[float]:
    if price and entry_price > 0:
        return round((price - entry_price) / entry_price * 100, 4)
    return None


# ── EXIT VALIDITY CLASSIFIER ──────────────────────────────────────────────────

def classify_exit_validity(
    exit_price: float,
    entry_price: float,
    exit_reason: str,
    coverage: dict,
    router_can_execute: bool,
) -> str:
    """
    Returns one of:
      EXECUTABLE    - router confirmed, coverage healthy
      STALE         - router price was stale but present
      NO_COVERAGE   - zero post-entry ticks (oracle was blind)
      GUARDIAN      - guardian closed (not execution engine decision)
      FALLBACK      - used last_known/unscoped price, not router
    """
    reason_upper = str(exit_reason or "").upper()
    if "GUARDIAN" in reason_upper:
        return "GUARDIAN"
    if coverage.get("tick_count", 0) == 0:
        return "NO_COVERAGE"
    if router_can_execute:
        return "EXECUTABLE"
    if exit_price != entry_price:
        return "STALE"
    return "FALLBACK"


# ── CONVENIENCE WRAPPERS (called from instrumented files) ─────────────────────

def emit_trade_opened(conn, *, position_id: int, mint: str,
                      entry_price: float, pos_size_usd: float,
                      source: str, note: str = "") -> None:
    _write_event(
        conn,
        position_id=position_id,
        mint_address=mint,
        event_type="TRADE_OPENED",
        price=entry_price,
        pct_from_entry=0.0,
        source=source,
        can_execute=1,
        tick_count=0,
        coverage_score=0.0,
        note=note or f"size=${pos_size_usd:.2f}",
    )


def emit_mark_written(conn, *, position_id: int, mint: str,
                      entry_price: float, router_result: dict,
                      pct: float) -> None:
    _write_event(
        conn,
        position_id=position_id,
        mint_address=mint,
        event_type="MARK_WRITTEN",
        price=router_result.get("price"),
        pct_from_entry=pct,
        age_sec=router_result.get("age_sec"),
        source=router_result.get("source"),
        can_execute=1 if router_result.get("can_execute_exit") else 0,
    )


def emit_trade_closed(conn, *, position_id: int, mint: str,
                      entry_price: float, exit_price: float,
                      realized_pnl: float, exit_reason: str,
                      hold_seconds: float, opened_at: float,
                      router_can_execute: bool = False) -> None:
    coverage = get_trade_coverage(mint, opened_at, time.time())
    validity = classify_exit_validity(
        exit_price, entry_price, exit_reason, coverage, router_can_execute
    )
    _write_event(
        conn,
        position_id=position_id,
        mint_address=mint,
        event_type="TRADE_CLOSED",
        price=exit_price,
        pct_from_entry=compute_pct(exit_price, entry_price),
        tick_count=coverage["tick_count"],
        coverage_score=coverage["coverage_score"],
        first_tick_delay_sec=coverage.get("first_tick_delay_sec"),
        max_pct_seen=compute_pct(coverage.get("max_price"), entry_price),
        min_pct_seen=compute_pct(coverage.get("min_price"), entry_price),
        exit_reason=exit_reason,
        exit_validity=validity,
        realized_pnl=realized_pnl,
        hold_seconds=hold_seconds,
        source="execution_engine",
        can_execute=1 if router_can_execute else 0,
    )


def emit_coverage_alert(conn, *, position_id: int, mint: str,
                        age_sec: float) -> None:
    _write_event(
        conn,
        position_id=position_id,
        mint_address=mint,
        event_type="COVERAGE_ALERT",
        tick_count=0,
        coverage_score=0.0,
        note=f"NO_POST_ENTRY_TICKS age={age_sec:.0f}s",
    )


def emit_oracle_tick(conn, *, mint: str, price: float,
                     source: str = "oracle") -> None:
    """
    Called from _write_mtm for open-position mints only.
    Does NOT need a position_id — written against mint, joined later.
    Uses position_id=-1 as sentinel for mint-level events.
    Throttled externally — not every tick, just first tick per trade window.
    """
    _write_event(
        conn,
        position_id=-1,   # sentinel: mint-level, join via mint_address + ts range
        mint_address=mint,
        event_type="PRICE_TICK",
        price=price,
        source=source,
        can_execute=1,
    )


# ── READER HELPERS (for sovereign_hub and polaris) ────────────────────────────

def get_trade_story(position_id: int) -> list:
    """Return all events for a position in chronological order."""
    try:
        from core.schema import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lifecycle_events "
                "WHERE position_id=? ORDER BY ts ASC",
                (position_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_closed_trade_validity_stats() -> dict:
    """Summary of exit validity for Polaris — filters ghost trades."""
    try:
        from core.schema import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT exit_validity, COUNT(*) n,
                       AVG(realized_pnl) avg_pnl,
                       AVG(coverage_score) avg_cov,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) wins
                FROM trade_lifecycle_events
                WHERE event_type='TRADE_CLOSED'
                GROUP BY exit_validity
                """
            ).fetchall()
        return {r["exit_validity"]: dict(r) for r in rows}
    except Exception:
        return {}


def get_polaris_clean_trades(min_coverage: float = 0.3, limit: int = 30) -> list:
    """
    Trades Polaris should actually learn from.
    Filters out NO_COVERAGE, GUARDIAN, FALLBACK exits.
    """
    try:
        from core.schema import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT position_id, mint_address, pct_from_entry,
                       realized_pnl, hold_seconds, tick_count,
                       coverage_score, first_tick_delay_sec,
                       max_pct_seen, min_pct_seen, exit_validity, ts
                FROM trade_lifecycle_events
                WHERE event_type='TRADE_CLOSED'
                  AND exit_validity IN ('EXECUTABLE','STALE')
                  AND coverage_score >= ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (min_coverage, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
