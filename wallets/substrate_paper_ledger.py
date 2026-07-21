from __future__ import annotations

"""
wallets/substrate_paper_ledger.py
===============================================================================
SUBSTRATE PAPER LEDGER — FULL LIFECYCLE V3 (SUBSTRATE_REAL_PRICE_20260721)

V2 shipped an open path only: positions could never close, so the "strategy
laboratory" was three static rows with zero PnL. V3 adds the honest remainder
of the lifecycle and enforces the price-truth doctrine at the capital-adjacent
boundary (paper cash is still an accounting truth):

  * open_paper_position_from_opportunity — unchanged contract, plus:
      - refuses SEED_MOCK-priced opportunities (mock data can never become
        paper PnL or promotion evidence);
      - refuses entry prices older than SUBSTRATE_ENTRY_MAX_PRICE_AGE_SEC
        (default 900s) measured against the PROVIDER timestamp;
      - stamps strategy_id and entry_price_status onto the position.
  * close_paper_position — realises PnL from entry vs a supplied real mark,
    returns size+PnL to SUBSTRATE_PAPER_CASH_USD, writes the audit row,
    advances the opportunity to PAPER_CLOSED, journals best-effort, and
    updates substrate_strategy_scores (closes/wins/losses/realized_pnl) so
    every exit feeds strategy attribution.
"""

import json
import time
from typing import Any, Dict, Optional

from .substrate_wallet_schema import (
    connect, ensure_schema, cfg_float, cfg_int, cfg_get, cfg_set, _ensure_col,
)

DEFAULT_STRATEGY_ID = "SUBSTRATE_CORE_SPOT_V1"
ENTRY_MAX_PRICE_AGE_SEC_DEFAULT = 900.0


def _audit(con, allowed: bool, reason: str, opp: dict | None = None,
           source: str = "paper_ledger") -> None:
    opp = opp or {}
    con.execute(
        "INSERT INTO substrate_execution_audit(created_at,allowed,reason,source,"
        "asset_symbol,chain,confidence,raw_json) VALUES(?,?,?,?,?,?,?,?)",
        (
            time.time(), 1 if allowed else 0, reason, source,
            opp.get("asset_symbol", ""), opp.get("chain", ""),
            float(opp.get("confidence") or 0),
            json.dumps({"opportunity_id": opp.get("id"), "mode": "PAPER"},
                       sort_keys=True),
        ),
    )


def _ensure_lifecycle_cols(con) -> None:
    _ensure_col(con, "substrate_positions", "strategy_id", "TEXT")
    _ensure_col(con, "substrate_positions", "entry_price_status", "TEXT")
    _ensure_col(con, "substrate_positions", "mark_source", "TEXT")
    _ensure_col(con, "substrate_positions", "mark_status", "TEXT")
    _ensure_col(con, "substrate_positions", "marked_at", "REAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS substrate_strategy_scores("
        " strategy_id TEXT PRIMARY KEY,"
        " closes INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,"
        " losses INTEGER DEFAULT 0, realized_pnl REAL DEFAULT 0,"
        " last_close_at REAL, updated_at REAL)"
    )


def open_paper_position_from_opportunity(opportunity_id: int) -> Dict[str, Any]:
    ensure_schema()
    con = connect()
    try:
        _ensure_lifecycle_cols(con)
        opp_row = con.execute(
            "SELECT * FROM substrate_opportunities WHERE id=?",
            (int(opportunity_id),),
        ).fetchone()
        if not opp_row:
            return {"ok": False, "reason": "opportunity_not_found"}
        opp = dict(opp_row)
        state = str(opp.get("state") or "NEW").upper()
        if state not in ("NEW", "READY", "PROMOTED"):
            _audit(con, False, f"state_not_openable:{state}", opp)
            con.commit()
            return {"ok": False, "reason": f"state_not_openable:{state}"}

        # PRICE-TRUTH GATE (SUBSTRATE_REAL_PRICE_20260721): mock prices cannot
        # open positions and stale provider timestamps cannot open positions.
        price_status = str(opp.get("price_status") or "").upper()
        if price_status == "SEED_MOCK":
            _audit(con, False, "seed_mock_price_cannot_open", opp)
            con.commit()
            return {"ok": False, "reason": "seed_mock_price_cannot_open"}
        px = float(opp.get("price_usd") or 0)
        px_ts = float(opp.get("price_updated_at") or 0)
        max_age = cfg_float(con, "SUBSTRATE_ENTRY_MAX_PRICE_AGE_SEC",
                            ENTRY_MAX_PRICE_AGE_SEC_DEFAULT)
        if px <= 0:
            _audit(con, False, "no_price", opp)
            con.commit()
            return {"ok": False, "reason": "no_price"}
        if px_ts <= 0 or (time.time() - px_ts) > max_age:
            _audit(con, False,
                   f"entry_price_too_old:{time.time() - px_ts:.0f}s>{max_age:.0f}s",
                   opp)
            con.commit()
            return {"ok": False, "reason": "entry_price_too_old"}

        max_open = cfg_int(con, "SUBSTRATE_MAX_OPEN", 3)
        open_n = con.execute(
            "SELECT COUNT(*) c FROM substrate_positions "
            "WHERE mode='PAPER' AND state='OPEN'"
        ).fetchone()["c"]
        if int(open_n or 0) >= max_open:
            _audit(con, False, "paper_max_open_reached", opp)
            con.commit()
            return {"ok": False, "reason": "paper_max_open_reached"}
        size = min(cfg_float(con, "SUBSTRATE_POSITION_SIZE_USD", 25.0),
                   cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0))
        if size <= 0:
            _audit(con, False, "paper_cash_empty", opp)
            con.commit()
            return {"ok": False, "reason": "paper_cash_empty"}

        now = time.time()
        qty = size / px
        strategy_id = str(opp.get("strategy_id") or DEFAULT_STRATEGY_ID)
        cur = con.execute(
            "INSERT INTO substrate_positions(opportunity_id,mode,state,status,"
            "chain,asset_symbol,symbol,side,size_usd,position_size,"
            "entry_price_usd,entry_price,current_price,quantity,source,"
            "opened_at,updated_at,raw_json,strategy_id,entry_price_status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(opportunity_id), "PAPER", "OPEN", "OPEN", opp.get("chain"),
                opp.get("asset_symbol"), opp.get("asset_symbol"), "LONG",
                size, size, px, px, px, qty,
                opp.get("source") or "SUBSTRATE", now, now,
                json.dumps({"opportunity_id": opportunity_id,
                            "quote_asset": opp.get("quote_asset", "USDC"),
                            "entry_route_provider": opp.get("route_provider")},
                           sort_keys=True),
                strategy_id, (price_status or "UNRECORDED"),
            ),
        )
        position_id = int(cur.lastrowid)
        try:
            from services.substrate_position_persistence import (
                connect as _jconnect, journal_open,
            )
            jc = _jconnect("sentinuity_matrix.db")
            try:
                journal_open(
                    jc, str(position_id), str(opp.get("asset_symbol") or ""),
                    "LONG", px, size,
                    intended_hold_seconds=cfg_float(
                        con, "SUBSTRATE_MAX_HOLD_SEC", 86400.0),
                    thesis=(f"opportunity_id={opportunity_id};"
                            f"source={opp.get('source') or 'SUBSTRATE'};"
                            f"strategy={strategy_id}"),
                )
            finally:
                jc.close()
        except Exception:
            pass
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0) - size
        cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        con.execute(
            "UPDATE substrate_opportunities SET state='PAPER_OPENED', "
            "updated_at=? WHERE id=?", (now, int(opportunity_id)),
        )
        _audit(con, True, "paper_opened", opp)
        con.commit()
        return {"ok": True, "position_id": position_id,
                "position_size_usd": size, "price_usd": px,
                "strategy_id": strategy_id}
    finally:
        con.close()


def _update_strategy_score(con, strategy_id: str, realized: float,
                           now: float) -> None:
    con.execute(
        "INSERT INTO substrate_strategy_scores(strategy_id,closes,wins,losses,"
        "realized_pnl,last_close_at,updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(strategy_id) DO UPDATE SET "
        " closes=closes+1,"
        " wins=wins+excluded.wins,"
        " losses=losses+excluded.losses,"
        " realized_pnl=realized_pnl+excluded.realized_pnl,"
        " last_close_at=excluded.last_close_at,"
        " updated_at=excluded.updated_at",
        (strategy_id, 1, 1 if realized > 0 else 0, 1 if realized < 0 else 0,
         float(realized), now, now),
    )


def close_paper_position(position_id: int, exit_price: float, reason: str,
                         mark_source: str = "substrate_price_feed") -> Dict[str, Any]:
    """Close one OPEN paper position at a REAL mark. Idempotent: a second call
    for the same id is a no-op reporting already_closed."""
    ensure_schema()
    con = connect()
    try:
        _ensure_lifecycle_cols(con)
        row = con.execute(
            "SELECT * FROM substrate_positions WHERE id=?", (int(position_id),)
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "position_not_found"}
        pos = dict(row)
        if str(pos.get("state") or "").upper() != "OPEN":
            return {"ok": False, "reason": "already_closed"}
        entry = float(pos.get("entry_price_usd") or pos.get("entry_price") or 0)
        qty = float(pos.get("quantity") or 0)
        size = float(pos.get("size_usd") or pos.get("position_size") or 0)
        exit_price = float(exit_price or 0)
        if entry <= 0 or qty <= 0 or exit_price <= 0:
            _audit(con, False, f"close_rejected_bad_inputs:{reason}", pos,
                   source="paper_ledger.close")
            con.commit()
            return {"ok": False, "reason": "close_rejected_bad_inputs"}
        side = str(pos.get("side") or "LONG").upper()
        realized = ((exit_price - entry) * qty if side != "SHORT"
                    else (entry - exit_price) * qty)
        now = time.time()
        con.execute(
            "UPDATE substrate_positions SET state='CLOSED', status='CLOSED', "
            "closed_at=?, exit_price=?, exit_reason=?, realized_pnl=?, "
            "unrealized_pnl=0, current_price=?, updated_at=?, mark_source=?, "
            "mark_status='CLOSED', marked_at=? WHERE id=? AND state='OPEN'",
            (now, exit_price, str(reason)[:200], realized, exit_price, now,
             mark_source, now, int(position_id)),
        )
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0) + size + realized
        cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        opp_id = pos.get("opportunity_id")
        if opp_id:
            con.execute(
                "UPDATE substrate_opportunities SET state='PAPER_CLOSED', "
                "updated_at=? WHERE id=?", (now, int(opp_id)),
            )
        strategy_id = str(pos.get("strategy_id") or DEFAULT_STRATEGY_ID)
        _update_strategy_score(con, strategy_id, realized, now)
        _audit(con, True,
               f"paper_closed:{reason}:pnl={realized:+.4f}", pos,
               source="paper_ledger.close")
        try:
            from services import substrate_position_persistence as _spp
            _jclose = getattr(_spp, "journal_close", None)
            if callable(_jclose):
                jc = _spp.connect("sentinuity_matrix.db")
                try:
                    _jclose(jc, str(position_id), exit_price, realized, reason)
                finally:
                    jc.close()
        except Exception:
            pass
        con.commit()
        return {"ok": True, "position_id": int(position_id),
                "realized_pnl": round(realized, 6), "exit_price": exit_price,
                "strategy_id": strategy_id, "reason": reason}
    finally:
        con.close()
