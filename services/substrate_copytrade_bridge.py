from __future__ import annotations

import argparse
import json
import os
import random
import time

from wallets.substrate_wallet_schema import connect, ensure_schema, heartbeat


def ingest_copytrade_once() -> int:
    """Substrate copytrade bridge — SIGN-OFF SAFE (PHASE_SIGNOFF_20260621).

    By default this does NOT inject any trade-influencing signals. Real substrate
    copytrade requires real wallet/API sources (GMGN/Birdeye) which are not yet
    wired. Until then:
      - SUBSTRATE_COPYTRADE_PAPER_INFLUENCE defaults to 0 (OFF). No rows written.
      - Fake/sample signals ONLY appear if SUBSTRATE_COPYTRADE_DEMO_MODE=1 is
        explicitly set, and even then they are tagged SIMULATED and state='DEMO'
        so downstream allocation must never treat them as real wallet signals.
    This prevents random.choice() sample data from influencing paper allocation.
    """
    ensure_schema()

    _influence = os.getenv("SUBSTRATE_COPYTRADE_PAPER_INFLUENCE", "0") == "1"
    _demo = os.getenv("SUBSTRATE_COPYTRADE_DEMO_MODE", "0") == "1"

    # No real source wired yet. Without explicit demo opt-in, write nothing and
    # report honestly — do NOT manufacture trade-like signals.
    if not _demo:
        msg = ("awaiting real wallet source (GMGN/Birdeye not configured); "
               "influence=" + ("ON" if _influence else "OFF"))
        heartbeat("substrate_copytrade_bridge", "OK", msg, 0)
        return 0

    # DEMO MODE (opt-in only): emit a clearly-tagged simulated row for UI smoke
    # testing. These rows are SIMULATED and state='DEMO' — they must never be
    # promoted into real substrate allocation.
    now = int(time.time())
    samples = [
        ("solana", "SOL", "SMART_WALLET_CLUSTER_CORE"),
        ("base", "WETH", "ALT_MARKET_SPREAD_WALLET"),
        ("base", "cbBTC", "BTC_PROXY_WALLET_SMALL"),
    ]
    chain, symbol, wallet = random.choice(samples)

    con = connect()
    try:
        con.execute(
            """
            INSERT INTO substrate_copytrade_signals
            (wallet_address, chain, asset_symbol, asset_address, action, confidence, observed_size_usd, pnl_hint, state, raw_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                wallet, chain, symbol, "native" if symbol == "SOL" else "wrapped", "BUY",
                0.63 + random.random() * 0.08, 50 + random.random() * 100,
                "SIMULATED — demo mode only, not a real wallet signal",
                "DEMO", json.dumps({"phase": "copytrade_ingest", "simulated": True, "demo_mode": True, "influence_real": False}, sort_keys=True),
                now, now,
            ),
        )
        con.commit()
        heartbeat("substrate_copytrade_bridge", "OK", f"DEMO_SIMULATED_signal={chain}:{symbol} (not real)", 1)
        return 1
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
