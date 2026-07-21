from __future__ import annotations

"""
services/substrate_portfolio_supervisor.py
===============================================================================
SUBSTRATE PORTFOLIO SUPERVISOR — REAL-MARK LIFECYCLE V3
(SUBSTRATE_REAL_PRICE_20260721)

V2 defects repaired here:
  * promote_copytrade_to_opportunity hard-coded SOL=150/WETH=3500/cbBTC=100000
    with route_provider='mock' — now priced by the canonical feed, and signals
    without an actionable price are honestly deferred (state stays NEW with a
    deferral note; nothing fabricated).
  * mark_open_positions marked from the opportunity's stored price — the same
    fantasy constant — so marks never moved. Now every open position is marked
    from a live feed observation; STALE/UNAVAILABLE observations update only
    mark_status and never touch price or timestamps.
  * No exit path existed at all. evaluate_exits() now closes positions on
    stop-loss / take-profit / max-hold expiry using REAL marks only, feeding
    realised PnL and strategy attribution through the paper ledger.

Exit doctrine:
  * stop/profit decisions require a FRESH or DEGRADED mark from a live
    provider — never a mock, never a stale echo.
  * expiry (SUBSTRATE_MAX_HOLD_SEC) also requires a real mark to settle at;
    an expired position with no actionable mark is flagged mark_status
    EXPIRED_UNPRICED and stays OPEN — an honest limbo beats an invented exit.
"""

import argparse
import os
import time
from typing import Dict, Optional

from wallets.substrate_wallet_schema import (
    connect, ensure_schema, heartbeat, cfg_bool, cfg_float, _ensure_col,
)
from wallets.substrate_wallet import refresh_wallet_state
from wallets.substrate_paper_ledger import (
    open_paper_position_from_opportunity, close_paper_position,
    _ensure_lifecycle_cols,
)
from wallets.substrate_live_guard import stage_live_order_from_opportunity
from services.substrate_price_feed import ACTIONABLE_STATUSES, get_prices

STOP_LOSS_PCT_DEFAULT = 8.0
TAKE_PROFIT_PCT_DEFAULT = 15.0
MAX_HOLD_SEC_DEFAULT = 259200.0  # 3 days


def promote_copytrade_to_opportunity(fetch_json=None) -> int:
    ensure_schema()
    now = int(time.time())
    con = connect()
    count = 0
    deferred = 0
    try:
        _ensure_col(con, "substrate_opportunities", "price_status", "TEXT")
        _ensure_col(con, "substrate_opportunities", "strategy_id", "TEXT")
        signals = con.execute(
            """SELECT * FROM substrate_copytrade_signals
               WHERE state='NEW' ORDER BY created_at DESC LIMIT 5"""
        ).fetchall()
        if not signals:
            return 0
        symbols = sorted({str(s["asset_symbol"]).upper() for s in signals})
        prices = get_prices(symbols, fetch_json=fetch_json, con=con,
                            persist=True)
        for s in signals:
            symbol = str(s["asset_symbol"]).upper()
            px = prices.get(symbol) or {}
            if str(px.get("status")) not in ACTIONABLE_STATUSES:
                deferred += 1
                con.execute(
                    "UPDATE substrate_copytrade_signals SET updated_at=?, "
                    "raw_json=COALESCE(raw_json,'') || ? WHERE id=?",
                    (now,
                     f"|price_deferred:{px.get('status')}:"
                     f"{str(px.get('error') or '')[:60]}",
                     s["id"]),
                )
                continue
            con.execute(
                """
                INSERT INTO substrate_opportunities
                (source, chain, asset_symbol, asset_address, asset_type,
                 native_or_wrapped, quote_asset, confidence, expected_edge,
                 liquidity_usd, volume_5m_usd, price_usd, price_updated_at,
                 risk_score, route_provider, raw_json, state, created_at,
                 updated_at, price_status, strategy_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "COPYTRADE_BRIDGE", s["chain"], s["asset_symbol"],
                    s["asset_address"], "spot",
                    "native" if symbol == "SOL" else "wrapped", "USDC",
                    s["confidence"], 0.04, 25000000, 200000,
                    float(px["price"]), float(px["source_ts"]),
                    0.38, str(px.get("source") or "unknown"),
                    s["raw_json"], "NEW", now, now,
                    str(px["status"]), "SUBSTRATE_COPYTRADE_V1",
                ),
            )
            con.execute(
                "UPDATE substrate_copytrade_signals SET state='PROMOTED', "
                "updated_at=? WHERE id=?", (now, s["id"]),
            )
            count += 1
        con.commit()
        return count
    finally:
        con.close()


def mark_open_positions(fetch_json=None) -> dict:
    """Mark every open paper position from the canonical price feed.

    Only FRESH/DEGRADED observations update prices. STALE/UNAVAILABLE update
    mark_status alone: the last real price and its timestamp remain untouched
    — stale data is never dressed as healthy data.
    """
    ensure_schema()
    con = connect()
    marked = 0
    stale = 0
    total_upnl = 0.0
    now = time.time()
    marks: Dict[str, dict] = {}
    try:
        _ensure_lifecycle_cols(con)
        rows = con.execute(
            "SELECT * FROM substrate_positions WHERE mode='PAPER' AND state='OPEN'"
        ).fetchall()
        if not rows:
            return {"marked": 0, "stale": 0, "unrealized_pnl": 0.0, "marks": {}}
        symbols = sorted({str(dict(r).get("asset_symbol") or "").upper()
                          for r in rows})
        marks = get_prices(symbols, fetch_json=fetch_json, con=con, persist=True)
        for row in rows:
            d = dict(row)
            symbol = str(d.get("asset_symbol") or "").upper()
            px = marks.get(symbol) or {}
            entry = float(d.get("entry_price_usd") or d.get("entry_price") or 0.0)
            qty = float(d.get("quantity") or 0.0)
            if str(px.get("status")) not in ACTIONABLE_STATUSES:
                stale += 1
                con.execute(
                    "UPDATE substrate_positions SET mark_status=?, updated_at=? "
                    "WHERE id=?",
                    (str(px.get("status") or "UNAVAILABLE"), now, int(d["id"])),
                )
                continue
            price = float(px["price"])
            if entry <= 0 or qty <= 0 or price <= 0:
                stale += 1
                continue
            side = str(d.get("side") or "LONG").upper()
            upnl = ((price - entry) * qty if side != "SHORT"
                    else (entry - price) * qty)
            con.execute(
                "UPDATE substrate_positions SET current_price=?, "
                "unrealized_pnl=?, updated_at=?, marked_at=?, mark_source=?, "
                "mark_status=? WHERE id=?",
                (price, upnl, now, float(px["observed_ts"]),
                 str(px.get("source") or ""), str(px["status"]), int(d["id"])),
            )
            marked += 1
            total_upnl += upnl
            try:
                from services.substrate_position_persistence import (
                    connect as _jconnect, journal_mark,
                )
                jc = _jconnect("sentinuity_matrix.db")
                try:
                    journal_mark(jc, str(d["id"]), price, upnl)
                finally:
                    jc.close()
            except Exception:
                pass
        con.commit()
        return {"marked": marked, "stale": stale,
                "unrealized_pnl": round(total_upnl, 6), "marks": marks}
    finally:
        con.close()


def evaluate_exits(marks: Optional[Dict[str, dict]] = None,
                   fetch_json=None) -> dict:
    """Stop-loss / take-profit / max-hold exits from REAL marks only."""
    ensure_schema()
    con = connect()
    closed = 0
    expired_unpriced = 0
    realized_total = 0.0
    now = time.time()
    try:
        _ensure_lifecycle_cols(con)
        stop_pct = cfg_float(con, "SUBSTRATE_STOP_LOSS_PCT", STOP_LOSS_PCT_DEFAULT)
        tp_pct = cfg_float(con, "SUBSTRATE_TAKE_PROFIT_PCT", TAKE_PROFIT_PCT_DEFAULT)
        max_hold = cfg_float(con, "SUBSTRATE_MAX_HOLD_SEC", MAX_HOLD_SEC_DEFAULT)
        rows = con.execute(
            "SELECT * FROM substrate_positions WHERE mode='PAPER' AND state='OPEN'"
        ).fetchall()
        if not rows:
            return {"closed": 0, "expired_unpriced": 0, "realized_pnl": 0.0}
        if marks is None:
            symbols = sorted({str(dict(r).get("asset_symbol") or "").upper()
                              for r in rows})
            marks = get_prices(symbols, fetch_json=fetch_json, con=con,
                               persist=False)
    finally:
        con.close()

    for row in rows:
        d = dict(row)
        symbol = str(d.get("asset_symbol") or "").upper()
        px = (marks or {}).get(symbol) or {}
        entry = float(d.get("entry_price_usd") or d.get("entry_price") or 0.0)
        opened_at = float(d.get("opened_at") or 0.0)
        actionable = (str(px.get("status")) in ACTIONABLE_STATUSES
                      and entry > 0 and float(px.get("price") or 0) > 0)
        expired = opened_at > 0 and (now - opened_at) >= max_hold
        if not actionable:
            if expired:
                expired_unpriced += 1
                c2 = connect()
                try:
                    c2.execute(
                        "UPDATE substrate_positions SET mark_status="
                        "'EXPIRED_UNPRICED', updated_at=? WHERE id=? "
                        "AND state='OPEN'", (now, int(d["id"])),
                    )
                    c2.commit()
                finally:
                    c2.close()
            continue
        price = float(px["price"])
        side = str(d.get("side") or "LONG").upper()
        pnl_pct = (((price - entry) / entry) * 100.0 if side != "SHORT"
                   else ((entry - price) / entry) * 100.0)
        reason = None
        if pnl_pct <= -abs(stop_pct):
            reason = f"STOP_LOSS:{pnl_pct:.2f}%<=-{abs(stop_pct):.2f}%"
        elif pnl_pct >= abs(tp_pct):
            reason = f"TAKE_PROFIT:{pnl_pct:.2f}%>=+{abs(tp_pct):.2f}%"
        elif expired:
            reason = f"MAX_HOLD_EXPIRY:{(now - opened_at) / 3600.0:.1f}h"
        if reason:
            res = close_paper_position(
                int(d["id"]), price, reason,
                mark_source=str(px.get("source") or "substrate_price_feed"),
            )
            if res.get("ok"):
                closed += 1
                realized_total += float(res.get("realized_pnl") or 0.0)
    return {"closed": closed, "expired_unpriced": expired_unpriced,
            "realized_pnl": round(realized_total, 6)}


def supervise_once(fetch_json=None) -> dict:
    ensure_schema()
    state = refresh_wallet_state()
    mark_state = mark_open_positions(fetch_json=fetch_json)
    exit_state = evaluate_exits(marks=mark_state.get("marks"),
                                fetch_json=fetch_json)
    promoted = promote_copytrade_to_opportunity(fetch_json=fetch_json)

    con = connect()
    opened = 0
    rejected_seen = 0
    try:
        max_open = int(os.getenv("SUBSTRATE_MAX_OPEN_PAPER_POSITIONS", "3"))
        open_count = con.execute(
            "SELECT COUNT(*) c FROM substrate_positions "
            "WHERE mode='PAPER' AND state='OPEN'"
        ).fetchone()["c"]
        slots = max(0, max_open - int(open_count))
        opps = con.execute(
            """SELECT * FROM substrate_opportunities WHERE state='NEW'
               ORDER BY confidence DESC, created_at DESC LIMIT ?""",
            (slots or 1,),
        ).fetchall()
    finally:
        con.close()

    staged_live = 0
    live_blocked = 0
    for opp in opps[:slots]:
        res = open_paper_position_from_opportunity(int(opp["id"]))
        if res.get("ok"):
            opened += 1
        else:
            rejected_seen += 1

        # Live path is manual-sign only. This creates a READY_FOR_MANUAL_SIGN
        # row after guard checks, never an auto-sent transaction.
        try:
            con2 = connect()
            try:
                live_enabled = cfg_bool(con2, "SUBSTRATE_LIVE_ENABLED", False)
            finally:
                con2.close()
            if not live_enabled:
                live_blocked += 1
                continue
            gate = stage_live_order_from_opportunity(int(opp["id"]))
            if gate.get("ok"):
                staged_live += 1
            else:
                live_blocked += 1
        except Exception:
            live_blocked += 1

    heartbeat(
        "substrate_portfolio_supervisor",
        "OK" if not mark_state.get("stale") else "DEGRADED",
        (f"mode={state['mode']} marked={mark_state['marked']} "
         f"stale={mark_state['stale']} "
         f"upnl=${mark_state['unrealized_pnl']:+.2f} "
         f"closed={exit_state['closed']} "
         f"realized=${exit_state['realized_pnl']:+.2f} "
         f"promoted={promoted} opened={opened} rejected={rejected_seen} "
         f"live_staged={staged_live} live_blocked={live_blocked}"),
        opened + promoted + staged_live + exit_state["closed"],
    )
    return {"state": state, "marks": mark_state, "exits": exit_state,
            "promoted": promoted, "opened": opened, "rejected": rejected_seen,
            "live_staged": staged_live, "live_blocked": live_blocked}


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_SUPERVISOR_INTERVAL_SEC", "30"))
    while True:
        try:
            supervise_once()
        except Exception as exc:  # noqa: BLE001
            heartbeat("substrate_portfolio_supervisor", "ERROR", repr(exc), 0)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="run one supervision cycle and exit")
    args = parser.parse_args()
    if args.once or os.getenv("SUBSTRATE_RUN_FOREVER", "1") == "0":
        print(supervise_once())
    else:
        run_forever()
