from __future__ import annotations

import argparse
import os
import time

from wallets.substrate_wallet_schema import connect, ensure_schema, heartbeat, cfg_bool
from wallets.substrate_wallet import refresh_wallet_state
from wallets.substrate_paper_ledger import open_paper_position_from_opportunity
from wallets.substrate_live_guard import stage_live_order_from_opportunity


def promote_copytrade_to_opportunity() -> int:
    ensure_schema()
    now = int(time.time())
    con = connect()
    count = 0
    try:
        signals = con.execute(
            """
            SELECT * FROM substrate_copytrade_signals
            WHERE state='NEW'
            ORDER BY created_at DESC
            LIMIT 5
            """
        ).fetchall()
        for s in signals:
            price = 150.0 if s["asset_symbol"].upper() == "SOL" else 3500.0 if s["asset_symbol"].upper() == "WETH" else 100000.0
            con.execute(
                """
                INSERT INTO substrate_opportunities
                (source, chain, asset_symbol, asset_address, asset_type, native_or_wrapped, quote_asset,
                 confidence, expected_edge, liquidity_usd, volume_5m_usd, price_usd, price_updated_at,
                 risk_score, route_provider, raw_json, state, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "COPYTRADE_BRIDGE", s["chain"], s["asset_symbol"], s["asset_address"], "spot",
                    "native" if s["asset_symbol"].upper() == "SOL" else "wrapped", "USDC",
                    s["confidence"], 0.04, 25000000, 200000, price, now, 0.38, "mock",
                    s["raw_json"], "NEW", now, now,
                ),
            )
            con.execute("UPDATE substrate_copytrade_signals SET state='PROMOTED', updated_at=? WHERE id=?", (now, s["id"]))
            count += 1
        con.commit()
        return count
    finally:
        con.close()



def mark_open_positions() -> dict:
    """Persist current marks and unrealised economics for every open paper position.

    Uses the latest price on the originating opportunity.  It never invents a
    close and never mixes Substrate PnL into the Solana paper ledger.
    """
    ensure_schema()
    con = connect()
    marked = 0
    stale = 0
    total_upnl = 0.0
    now = time.time()
    try:
        rows = con.execute(
            """SELECT p.*, o.price_usd AS opportunity_price, o.price_updated_at
                 FROM substrate_positions p
                 LEFT JOIN substrate_opportunities o ON o.id=p.opportunity_id
                WHERE p.mode='PAPER' AND p.state='OPEN'"""
        ).fetchall()
        for row in rows:
            d = dict(row)
            px = float(d.get("opportunity_price") or d.get("current_price") or d.get("entry_price_usd") or d.get("entry_price") or 0.0)
            entry = float(d.get("entry_price_usd") or d.get("entry_price") or 0.0)
            qty = float(d.get("quantity") or 0.0)
            if px <= 0 or entry <= 0 or qty <= 0:
                stale += 1
                continue
            side = str(d.get("side") or "LONG").upper()
            upnl = (px - entry) * qty if side != "SHORT" else (entry - px) * qty
            con.execute(
                "UPDATE substrate_positions SET current_price=?, unrealized_pnl=?, updated_at=? WHERE id=?",
                (px, upnl, now, int(d["id"])),
            )
            marked += 1
            total_upnl += upnl
            try:
                from services.substrate_position_persistence import connect as _jconnect, journal_mark
                jc = _jconnect("sentinuity_matrix.db")
                try:
                    journal_mark(jc, str(d["id"]), px, upnl)
                finally:
                    jc.close()
            except Exception:
                pass
        con.commit()
        return {"marked": marked, "stale": stale, "unrealized_pnl": round(total_upnl, 6)}
    finally:
        con.close()

def supervise_once() -> dict:
    ensure_schema()
    state = refresh_wallet_state()
    mark_state = mark_open_positions()
    promoted = promote_copytrade_to_opportunity()

    con = connect()
    opened = 0
    rejected_seen = 0
    try:
        max_open = int(os.getenv("SUBSTRATE_MAX_OPEN_PAPER_POSITIONS", "3"))
        open_count = con.execute("SELECT COUNT(*) c FROM substrate_positions WHERE mode='PAPER' AND state='OPEN'").fetchone()["c"]
        slots = max(0, max_open - int(open_count))
        opps = con.execute(
            """
            SELECT * FROM substrate_opportunities
            WHERE state='NEW'
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
            """,
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

        # Live path is manual-sign only. This creates a READY_FOR_MANUAL_SIGN row
        # after guard checks, never an auto-sent transaction. It lets tiny $10-$25
        # live tests prove the wallet/route/audit path without storing keys here.
        try:
            if not cfg_bool("SUBSTRATE_LIVE_ENABLED", False):
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
        "OK",
        f"mode={state['mode']} marked={mark_state['marked']} upnl=${mark_state['unrealized_pnl']:+.2f} promoted={promoted} opened={opened} rejected={rejected_seen} live_staged={staged_live} live_blocked={live_blocked}",
        opened + promoted + staged_live,
    )
    return {"state": state, "marks": mark_state, "promoted": promoted, "opened": opened, "rejected": rejected_seen, "live_staged": staged_live, "live_blocked": live_blocked}


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_SUPERVISOR_INTERVAL_SEC", "30"))
    while True:
        try:
            supervise_once()
        except Exception as exc:
            heartbeat("substrate_portfolio_supervisor", "ERROR", repr(exc), 0)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one supervision cycle and exit")
    args = parser.parse_args()
    if args.once or os.getenv("SUBSTRATE_RUN_FOREVER", "1") == "0":
        print(supervise_once())
    else:
        run_forever()
