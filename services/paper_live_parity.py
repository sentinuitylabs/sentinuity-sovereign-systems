#!/usr/bin/env python3
"""
SENTINUITY — PAPER/LIVE PARITY LEDGER

One canonical row per decision identity, from paper admission through to a
terminal state. Its purpose is to make orphaned live handoffs impossible to
miss: the six-hour window produced 16 FIRE_PATH_OPEN decisions and zero settled
live positions, and nothing in the tree recorded where those 16 ended.

Every FIRE_PATH_OPEN must reach a terminal state. A handoff that stops
progressing is terminalised as LIVE_SELL_UNRESOLVED or LIVE_EXCEPTION after a
declared timeout, never left silently open.

Hard contracts:
  * Telemetry only. Writes exactly one table: paper_live_parity.
  * Never creates, sizes, approves or blocks a trade. A parity write failure
    must never approve a live trade — callers ignore the return value.
  * Never raises into the execution path.
  * Never touches live flags, sizing, Mode B or would_veto.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

SERVICE = "paper_live_parity"
PARITY_TABLE = "paper_live_parity"

# A live handoff with no progress for this long is terminalised, not ignored.
ORPHAN_TIMEOUT_SEC = float(os.environ.get("PARITY_ORPHAN_TIMEOUT_SEC", "900"))

# Terminal states (directive section 2).
PAPER_ONLY_ADMITTED = "PAPER_ONLY_ADMITTED"
LIVE_REFUSED = "LIVE_REFUSED"
LIVE_EXCEPTION = "LIVE_EXCEPTION"
LIVE_SUBMITTED = "LIVE_SUBMITTED"
LIVE_BUY_SETTLED = "LIVE_BUY_SETTLED"
DUAL_OPEN = "DUAL_OPEN"
DUAL_SETTLED = "DUAL_SETTLED"
LIVE_SELL_UNRESOLVED = "LIVE_SELL_UNRESOLVED"
RECONCILIATION_CONTRADICTION = "RECONCILIATION_CONTRADICTION"
TERMINAL_COMPLETE = "TERMINAL_COMPLETE"

TERMINAL_STATES = frozenset({
    LIVE_REFUSED, LIVE_EXCEPTION, DUAL_SETTLED,
    LIVE_SELL_UNRESOLVED, RECONCILIATION_CONTRADICTION, TERMINAL_COMPLETE,
})
# States that represent an in-flight live handoff and must not persist.
IN_FLIGHT_STATES = frozenset({PAPER_ONLY_ADMITTED, LIVE_SUBMITTED, LIVE_BUY_SETTLED, DUAL_OPEN})

_DDL = f"""
CREATE TABLE IF NOT EXISTS {PARITY_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    candidate_id INTEGER,
    snapshot_id INTEGER,
    mint_address TEXT NOT NULL,
    token_name TEXT,
    paper_position_id INTEGER,
    live_position_id INTEGER,
    live_decision_id INTEGER,
    edge_ledger_id INTEGER,
    feature_snapshot_hash TEXT,

    paper_admitted INTEGER DEFAULT 0,
    paper_admitted_at REAL,
    decision_at REAL,
    paper_intent_at REAL,
    paper_entry_price REAL,

    live_eligible INTEGER,
    live_verdict TEXT,
    live_refusal_reason TEXT,
    gate_state_json TEXT,
    executability_state TEXT,
    quote_evidence_json TEXT,
    selected_size_usd REAL,
    pattern_stage TEXT,
    confidence REAL,
    freshness_state TEXT,

    live_intent_at REAL,
    route_requested_at REAL,
    route_received_at REAL,
    signing_started_at REAL,
    live_submit_at REAL,
    confirmed_local_at REAL,
    chain_block_time REAL,
    reconciled_at REAL,
    decision_to_submit_ms REAL,
    paper_to_live_intent_ms REAL,
    entry_slippage_pct REAL,
    buy_signature TEXT,
    chain_fill_at REAL,
    live_fill_price REAL,
    raw_token_quantity INTEGER,
    sell_signature TEXT,
    settled_exit_price REAL,
    settlement_pnl_usd REAL,
    reconciliation_status TEXT,

    paper_exit_at REAL,
    paper_exit_price REAL,
    paper_credited_pnl_usd REAL,
    paper_market_true_pnl_usd REAL,

    parity_state TEXT NOT NULL,
    state_updated_at REAL NOT NULL,
    terminal INTEGER DEFAULT 0,
    terminal_reason TEXT,
    created_at REAL NOT NULL
);
"""

_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_plp_mint ON {PARITY_TABLE}(mint_address)",
    f"CREATE INDEX IF NOT EXISTS idx_plp_paper ON {PARITY_TABLE}(paper_position_id)",
    f"CREATE INDEX IF NOT EXISTS idx_plp_state ON {PARITY_TABLE}(parity_state, terminal)",
    f"CREATE INDEX IF NOT EXISTS idx_plp_created ON {PARITY_TABLE}(created_at)",
)


def ensure_schema(conn) -> bool:
    try:
        conn.execute(_DDL)
        for ix in _INDEXES:
            conn.execute(ix)
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({PARITY_TABLE})")}
        for name, typ in (
            ("decision_at", "REAL"), ("paper_intent_at", "REAL"),
            ("live_intent_at", "REAL"), ("route_requested_at", "REAL"),
            ("route_received_at", "REAL"), ("signing_started_at", "REAL"),
            ("confirmed_local_at", "REAL"), ("chain_block_time", "REAL"),
            ("reconciled_at", "REAL"), ("decision_to_submit_ms", "REAL"),
            ("paper_to_live_intent_ms", "REAL"), ("entry_slippage_pct", "REAL"),
        ):
            if name not in existing:
                conn.execute(f"ALTER TABLE {PARITY_TABLE} ADD COLUMN {name} {typ}")
        return True
    except Exception:
        return False


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _row_for(conn, *, mint: str, paper_position_id: Optional[int] = None,
             candidate_id: Optional[int] = None):
    """Most specific existing row for this identity, or None."""
    try:
        if paper_position_id:
            r = conn.execute(
                f"SELECT id FROM {PARITY_TABLE} WHERE paper_position_id=? "
                "ORDER BY id DESC LIMIT 1", (int(paper_position_id),)).fetchone()
            if r:
                return int(r[0])
        if candidate_id:
            r = conn.execute(
                f"SELECT id FROM {PARITY_TABLE} WHERE candidate_id=? "
                "ORDER BY id DESC LIMIT 1", (int(candidate_id),)).fetchone()
            if r:
                return int(r[0])
        if mint:
            r = conn.execute(
                f"SELECT id FROM {PARITY_TABLE} WHERE mint_address=? AND terminal=0 "
                "ORDER BY id DESC LIMIT 1", (str(mint),)).fetchone()
            if r:
                return int(r[0])
    except Exception:
        pass
    return None


def record(conn, *, mint: str, state: str,
           paper_position_id: Optional[int] = None,
           candidate_id: Optional[int] = None,
           terminal_reason: str = "", **fields) -> Optional[int]:
    """
    Create or advance the parity row for one decision identity.

    Telemetry only. Never raises. Callers MUST ignore the return value for
    control-flow purposes: a parity failure can never approve a live trade.
    """
    try:
        ensure_schema(conn)
        now = time.time()
        rid = _row_for(conn, mint=mint, paper_position_id=paper_position_id,
                       candidate_id=candidate_id)

        payload: Dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
        for jk in ("gate_state_json", "quote_evidence_json"):
            if jk in payload and not isinstance(payload[jk], str):
                try:
                    payload[jk] = json.dumps(payload[jk], default=str)[:4000]
                except Exception:
                    payload[jk] = ""
        payload["parity_state"] = str(state)[:48]
        payload["state_updated_at"] = now
        payload["terminal"] = 1 if state in TERMINAL_STATES else 0
        if terminal_reason:
            payload["terminal_reason"] = str(terminal_reason)[:300]
        if paper_position_id is not None:
            payload["paper_position_id"] = int(paper_position_id)
        if candidate_id is not None:
            payload["candidate_id"] = int(candidate_id)

        valid = {r[1] for r in conn.execute(f"PRAGMA table_info({PARITY_TABLE})")}
        payload = {k: v for k, v in payload.items() if k in valid}

        if rid is None:
            payload["mint_address"] = str(mint or "")[:64]
            payload["created_at"] = now
            cols = ", ".join(payload.keys())
            qs = ", ".join("?" for _ in payload)
            cur = conn.execute(
                f"INSERT INTO {PARITY_TABLE} ({cols}) VALUES ({qs})",
                list(payload.values()))
            return int(cur.lastrowid)

        # Never regress a terminal row back to an in-flight state.
        cur_state = conn.execute(
            f"SELECT parity_state, terminal FROM {PARITY_TABLE} WHERE id=?",
            (rid,)).fetchone()
        if cur_state and int(cur_state[1] or 0) == 1 and state in IN_FLIGHT_STATES:
            return rid

        sets = ", ".join(f"{k}=?" for k in payload)
        conn.execute(f"UPDATE {PARITY_TABLE} SET {sets} WHERE id=?",
                     list(payload.values()) + [rid])
        return rid
    except Exception:
        return None


def terminalise_orphans(conn, *, timeout_sec: float = None) -> int:
    """
    Force a terminal state on live handoffs that stopped progressing.

    An orphan is not a neutral absence of data: it is a live decision whose
    outcome nobody recorded. Every one becomes LIVE_SELL_UNRESOLVED or
    LIVE_EXCEPTION with an explicit reason.
    """
    try:
        ensure_schema(conn)
        t = float(timeout_sec if timeout_sec is not None else ORPHAN_TIMEOUT_SEC)
        cut = time.time() - t
        rows = conn.execute(
            f"SELECT id, parity_state FROM {PARITY_TABLE} "
            "WHERE terminal=0 AND state_updated_at < ?", (cut,)).fetchall()
        n = 0
        for r in rows:
            st = str(r[1] or "")
            if st in (LIVE_BUY_SETTLED, DUAL_OPEN):
                new, why = LIVE_SELL_UNRESOLVED, f"no progress for {t:.0f}s after buy"
            elif st == LIVE_SUBMITTED:
                new, why = LIVE_EXCEPTION, f"submit never settled within {t:.0f}s"
            else:
                new, why = LIVE_EXCEPTION, f"handoff abandoned in state {st or 'UNKNOWN'}"
            conn.execute(
                f"UPDATE {PARITY_TABLE} SET parity_state=?, terminal=1, "
                "terminal_reason=?, state_updated_at=? WHERE id=?",
                (new, why, time.time(), int(r[0])))
            n += 1
        return n
    except Exception:
        return 0


def coverage(conn) -> Dict[str, Any]:
    """Parity terminal coverage and orphan count. Read-only."""
    out = {"n": 0, "terminal": 0, "terminal_coverage_pct": 0.0,
           "orphan_fire_path_open": 0, "by_state": {}}
    try:
        ensure_schema(conn)
        out["n"] = int(conn.execute(
            f"SELECT COUNT(*) FROM {PARITY_TABLE}").fetchone()[0] or 0)
        out["terminal"] = int(conn.execute(
            f"SELECT COUNT(*) FROM {PARITY_TABLE} WHERE terminal=1").fetchone()[0] or 0)
        if out["n"]:
            out["terminal_coverage_pct"] = 100.0 * out["terminal"] / out["n"]
        out["orphan_fire_path_open"] = int(conn.execute(
            f"SELECT COUNT(*) FROM {PARITY_TABLE} "
            "WHERE terminal=0 AND UPPER(COALESCE(live_verdict,''))='FIRE_PATH_OPEN'"
        ).fetchone()[0] or 0)
        for r in conn.execute(
                f"SELECT parity_state, COUNT(*) FROM {PARITY_TABLE} "
                "GROUP BY parity_state ORDER BY 2 DESC"):
            out["by_state"][str(r[0])] = int(r[1])
    except Exception:
        pass
    return out
