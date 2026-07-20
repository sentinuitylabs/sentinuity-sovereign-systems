from __future__ import annotations

import argparse
import json
import os
import time
from typing import List, Dict

from wallets.substrate_wallet_schema import connect, ensure_schema, heartbeat
from wallets.substrate_allocation_engine import propose_allocation


def _seed_opportunities() -> List[Dict]:
    now = int(time.time())
    # Paper-safe default universe. Replace/extend with Birdeye, DexScreener, GMGN,
    # Coinbase, 0x, Jupiter, or your own feeds after keys are configured.
    return [
        {
            "source": "COUNCIL_RESEARCH",
            "chain": "solana",
            "asset_symbol": "SOL",
            "asset_address": "native",
            "asset_type": "spot",
            "native_or_wrapped": "native",
            "confidence": 0.72,
            "expected_edge": 0.06,
            "liquidity_usd": 100000000,
            "volume_5m_usd": 1000000,
            "price_usd": 150.0,
            "price_updated_at": now,
            "risk_score": 0.28,
            "route_provider": "paper_reference",
            "raw_json": {"phase": "council_fetch", "note": "core low-fee Solana exposure"},
        },
        {
            "source": "COUNCIL_RESEARCH",
            "chain": "base",
            "asset_symbol": "WETH",
            "asset_address": "wrapped",
            "asset_type": "spot",
            "native_or_wrapped": "wrapped",
            "confidence": 0.66,
            "expected_edge": 0.035,
            "liquidity_usd": 50000000,
            "volume_5m_usd": 500000,
            "price_usd": 3500.0,
            "price_updated_at": now,
            "risk_score": 0.42,
            "route_provider": "paper_reference",
            "raw_json": {"phase": "wrapped_exposure", "note": "ETH exposure on cheaper chain, not native ETH"},
        },
        {
            "source": "COUNCIL_RESEARCH",
            "chain": "base",
            "asset_symbol": "cbBTC",
            "asset_address": "wrapped",
            "asset_type": "spot",
            "native_or_wrapped": "wrapped",
            "confidence": 0.64,
            "expected_edge": 0.025,
            "liquidity_usd": 35000000,
            "volume_5m_usd": 250000,
            "price_usd": 100000.0,
            "price_updated_at": now,
            "risk_score": 0.48,
            "route_provider": "paper_reference",
            "raw_json": {"phase": "wrapped_exposure", "note": "BTC exposure via tokenized asset; small allocation only"},
        },
    ]


def scan_once() -> int:
    ensure_schema()
    propose_allocation()
    rows = _seed_opportunities()
    now = int(time.time())
    con = connect()
    inserted = 0
    try:
        for row in rows:
            existing = con.execute("""SELECT 1 FROM substrate_opportunities
                WHERE chain=? AND asset_symbol=? AND state IN ('NEW','OPEN')
                  AND created_at>=? LIMIT 1""", (row["chain"], row["asset_symbol"], now-900)).fetchone()
            if existing:
                continue
            con.execute(
                """
                INSERT INTO substrate_opportunities
                (source, chain, asset_symbol, asset_address, asset_type, native_or_wrapped, quote_asset,
                 confidence, expected_edge, liquidity_usd, volume_5m_usd, price_usd, price_updated_at,
                 risk_score, route_provider, raw_json, state, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["source"], row["chain"], row["asset_symbol"], row["asset_address"], row["asset_type"],
                    row["native_or_wrapped"], "USDC", row["confidence"], row["expected_edge"], row["liquidity_usd"],
                    row["volume_5m_usd"], row["price_usd"], row["price_updated_at"], row["risk_score"],
                    row["route_provider"], json.dumps(row["raw_json"], sort_keys=True), "NEW", now, now,
                ),
            )
            inserted += 1
        con.commit()
        heartbeat("substrate_opportunity_scanner", "OK", f"inserted={inserted} council_votes_written=1", inserted)
        return inserted
    finally:
        con.close()


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_SCANNER_INTERVAL_SEC", "45"))
    while True:
        try:
            scan_once()
        except Exception as exc:
            heartbeat("substrate_opportunity_scanner", "ERROR", repr(exc), 0)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one scan and exit")
    args = parser.parse_args()
    if args.once or os.getenv("SUBSTRATE_RUN_FOREVER", "1") == "0":
        print(f"inserted={scan_once()}")
    else:
        run_forever()
