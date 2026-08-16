from __future__ import annotations

import argparse
import json
import os
import random
import time

from wallets.substrate_wallet_schema import connect, ensure_schema, heartbeat


def ingest_copytrade_once() -> int:
    """Bridge REAL observed smart-wallet trades into Substrate corroboration.

    Only assets with a canonical, unambiguous mapping are eligible. At present
    that is native SOL/WSOL. Arbitrary Solana token mints are never relabelled as
    WETH/cbBTC, and demo rows remain explicit opt-in only.
    """
    ensure_schema()
    influence = os.getenv("SUBSTRATE_COPYTRADE_PAPER_INFLUENCE", "0") == "1"
    demo = os.getenv("SUBSTRATE_COPYTRADE_DEMO_MODE", "0") == "1"
    now = time.time()
    max_age = max(60, int(os.getenv("SUBSTRATE_COPYTRADE_MAX_AGE_SEC", "1800")))
    wsol = "So11111111111111111111111111111111111111112"

    con = connect()
    inserted = 0
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "smart_wallet_trades" in tables:
            cols = {r[1] for r in con.execute("PRAGMA table_info(smart_wallet_trades)")}
            wallet_col = next((c for c in ("wallet_address","wallet","address") if c in cols), None)
            mint_col = next((c for c in ("mint_address","mint","token_mint") if c in cols), None)
            side_col = next((c for c in ("side","action","trade_side") if c in cols), None)
            conf_col = next((c for c in ("confidence","wallet_score","quality_score") if c in cols), None)
            size_col = next((c for c in ("size_usd","amount_usd","observed_size_usd") if c in cols), None)
            time_col = next((c for c in ("block_time","observed_at","created_at","timestamp") if c in cols), None)
            if wallet_col and mint_col and side_col and time_col:
                q=lambda x:'"'+x.replace('"','""')+'"'
                rows=con.execute(
                    f"SELECT * FROM smart_wallet_trades WHERE {q(time_col)}>=? "
                    f"AND {q(mint_col)}=? ORDER BY {q(time_col)} DESC LIMIT 50",
                    (now-max_age, wsol),
                ).fetchall()
                for row in rows:
                    d=dict(row); side=str(d.get(side_col) or '').upper()
                    if side not in ('BUY','SELL'): continue
                    wallet=str(d.get(wallet_col) or '')
                    observed=float(d.get(time_col) or now)
                    raw=json.dumps({"source":"smart_wallet_trades","source_row":d.get("id"),
                                    "observed_at":observed,"real":True,
                                    "paper_influence_enabled":influence},sort_keys=True)
                    exists=con.execute(
                        "SELECT 1 FROM substrate_copytrade_signals WHERE wallet_address=? "
                        "AND asset_symbol='SOL' AND action=? AND created_at BETWEEN ? AND ? LIMIT 1",
                        (wallet,side,observed-2,observed+2)).fetchone()
                    if exists: continue
                    conf=float(d.get(conf_col) or 0.65) if conf_col else 0.65
                    size=float(d.get(size_col) or 0.0) if size_col else 0.0
                    con.execute("""INSERT INTO substrate_copytrade_signals
                        (wallet_address,chain,asset_symbol,asset_address,action,confidence,
                         observed_size_usd,pnl_hint,state,raw_json,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (wallet,"solana","SOL",wsol,side,max(0.0,min(0.95,conf)),size,
                         "REAL observed smart-wallet SOL/WSOL trade",
                         "NEW" if influence else "OBSERVE",raw,observed,now))
                    inserted += 1
        if inserted:
            con.commit()
            heartbeat("substrate_copytrade_bridge","OK",
                      f"real_smart_wallet_signals={inserted} influence={'ON' if influence else 'OBSERVE'}",inserted)
            return inserted
    finally:
        con.close()

    if not demo:
        heartbeat("substrate_copytrade_bridge","DEGRADED",
                  "no recent canonical SOL/WSOL smart-wallet trades; no synthetic signals",0)
        return 0

    # Explicit UI smoke-test only; downstream state DEMO is never actionable.
    con=connect()
    try:
        con.execute("""INSERT INTO substrate_copytrade_signals
            (wallet_address,chain,asset_symbol,asset_address,action,confidence,
             observed_size_usd,pnl_hint,state,raw_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SMART_WALLET_CLUSTER_CORE","solana","SOL","native","BUY",0.65,50.0,
             "SIMULATED — demo mode only","DEMO",
             json.dumps({"simulated":True,"influence_real":False},sort_keys=True),now,now))
        con.commit(); heartbeat("substrate_copytrade_bridge","OK","DEMO_SIMULATED SOL",1); return 1
    finally:
        con.close()


def run_forever(interval_sec: int | None = None) -> None:
    """Continuously publish truthful Substrate copytrade state.

    Without a configured real source this writes no signals and only reports a
    healthy waiting heartbeat. Demo signals remain explicit opt-in and can
    never be promoted as real wallet evidence.
    """
    interval = max(15, int(interval_sec or os.getenv("SUBSTRATE_COPYTRADE_INTERVAL_SEC", "60")))
    while True:
        try:
            ingest_copytrade_once()
        except Exception as exc:
            try:
                heartbeat("substrate_copytrade_bridge", "ERROR", repr(exc), 0)
            except Exception:
                pass
        time.sleep(interval)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Sentinuity Substrate copytrade bridge")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=None)
    args = parser.parse_args()
    if args.loop:
        run_forever(args.interval)
    else:
        print(f"copytrade_signals={ingest_copytrade_once()}")


if __name__ == "__main__":
    _main()
