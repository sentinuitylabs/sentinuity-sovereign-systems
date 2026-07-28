from __future__ import annotations

"""
services/substrate_opportunity_scanner.py
===============================================================================
SUBSTRATE OPPORTUNITY SCANNER — REAL-PRICE V3 (SUBSTRATE_REAL_PRICE_20260721)

V2 defect (review blocker 6): this file hard-coded SOL=150 / WETH=3500 /
cbBTC=100000 and stamped price_updated_at=now on every scan — fantasy prices
wearing perpetually fresh timestamps, labelled COUNCIL_RESEARCH.

V3 behaviour:
  * Seed templates carry NO price. Every inserted opportunity's price comes
    from services/substrate_price_feed.get_prices() — a real provider with a
    real provider timestamp. price_updated_at is the provider's source_ts,
    never now.
  * A guarded price_status column records FRESH/DEGRADED per row.
  * When no actionable price exists the opportunity is NOT inserted; the
    heartbeat reports exactly how many assets were price-blocked and why.
  * SEED_MOCK insertion exists only behind SUBSTRATE_ALLOW_SEED_MOCK
    (default off): price_status='SEED_MOCK', price_updated_at=0,
    route_provider='seed_mock'. The paper ledger refuses to open positions
    from such rows, so mock data can never become fake PnL or promotion
    evidence.
  * A guarded strategy_id column attributes every opportunity to a strategy
    so closes can update substrate_strategy_scores.
"""

import argparse
import json
import os
import time
from typing import Dict, List, Optional

from wallets.substrate_wallet_schema import (
    connect, ensure_schema, heartbeat, cfg_bool, _ensure_col,
)
from wallets.substrate_allocation_engine import propose_allocation
from services.substrate_price_feed import (
    ACTIONABLE_STATUSES, STATUS_SEED_MOCK, get_prices, seed_mock_contract,
)

DEFAULT_STRATEGY_ID = "SUBSTRATE_CORE_SPOT_V1"


def _seed_templates() -> List[Dict]:
    """Approved spot universe. NO prices here — prices come from the feed."""
    return [
        {
            "source": "COUNCIL_RESEARCH", "chain": "solana",
            "asset_symbol": "SOL", "asset_address": "native",
            "asset_type": "spot", "native_or_wrapped": "native",
            "confidence": 0.72, "expected_edge": 0.06,
            "liquidity_usd": 100000000, "volume_5m_usd": 1000000,
            "risk_score": 0.28, "strategy_id": DEFAULT_STRATEGY_ID,
            "raw_json": {"phase": "council_fetch",
                         "note": "core low-fee Solana exposure"},
        },
        {
            "source": "COUNCIL_RESEARCH", "chain": "base",
            "asset_symbol": "WETH", "asset_address": "wrapped",
            "asset_type": "spot", "native_or_wrapped": "wrapped",
            "confidence": 0.66, "expected_edge": 0.035,
            "liquidity_usd": 50000000, "volume_5m_usd": 500000,
            "risk_score": 0.42, "strategy_id": DEFAULT_STRATEGY_ID,
            "raw_json": {"phase": "wrapped_exposure",
                         "note": "ETH exposure on cheaper chain, not native ETH"},
        },
        {
            "source": "COUNCIL_RESEARCH", "chain": "base",
            "asset_symbol": "cbBTC", "asset_address": "wrapped",
            "asset_type": "spot", "native_or_wrapped": "wrapped",
            "confidence": 0.64, "expected_edge": 0.025,
            "liquidity_usd": 35000000, "volume_5m_usd": 250000,
            "risk_score": 0.48, "strategy_id": DEFAULT_STRATEGY_ID,
            "raw_json": {"phase": "wrapped_exposure",
                         "note": "BTC exposure via tokenized asset; small allocation only"},
        },
    ]


def _ensure_scanner_cols(con) -> None:
    _ensure_col(con, "substrate_opportunities", "price_status", "TEXT")
    _ensure_col(con, "substrate_opportunities", "strategy_id", "TEXT")


def scan_once(fetch_json=None) -> int:
    ensure_schema()
    propose_allocation()
    templates = _seed_templates()
    now = int(time.time())
    con = connect()
    inserted = 0
    price_blocked: List[str] = []
    mock_inserted = 0
    try:
        _ensure_scanner_cols(con)
        allow_mock = cfg_bool(con, "SUBSTRATE_ALLOW_SEED_MOCK", False) or (
            str(os.getenv("SUBSTRATE_ALLOW_SEED_MOCK", "")).strip().lower()
            in {"1", "true", "yes", "on"}
        )
        prices = get_prices([t["asset_symbol"] for t in templates],
                            fetch_json=fetch_json, con=con, persist=True)
        for row in templates:
            symbol = row["asset_symbol"]
            existing = con.execute(
                """SELECT 1 FROM substrate_opportunities
                   WHERE chain=? AND asset_symbol=? AND state IN ('NEW','OPEN')
                     AND created_at>=? LIMIT 1""",
                (row["chain"], symbol, now - 900),
            ).fetchone()
            if existing:
                continue

            px = prices.get(symbol) or {}
            status = str(px.get("status") or "UNAVAILABLE")
            if status in ACTIONABLE_STATUSES:
                price_usd = float(px["price"])
                price_ts = float(px["source_ts"])       # provider truth, never now
                price_status = status
                route_provider = str(px.get("source") or "unknown")
                raw = dict(row["raw_json"])
                raw["price_contract"] = {k: px.get(k) for k in
                                         ("source", "source_ts", "age_sec",
                                          "confidence", "status")}
            elif allow_mock:
                mock = seed_mock_contract(symbol)
                if mock.get("price") is None:
                    price_blocked.append(f"{symbol}:{status}")
                    continue
                price_usd = float(mock["price"])
                price_ts = 0.0                          # explicitly not a market ts
                price_status = STATUS_SEED_MOCK
                route_provider = "seed_mock"
                raw = dict(row["raw_json"])
                raw["price_contract"] = {"status": STATUS_SEED_MOCK,
                                         "note": mock["error"]}
                mock_inserted += 1
            else:
                price_blocked.append(f"{symbol}:{status}:"
                                     f"{str(px.get('error') or '')[:60]}")
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
                (row["source"], row["chain"], symbol, row["asset_address"],
                 row["asset_type"], row["native_or_wrapped"], "USDC",
                 row["confidence"], row["expected_edge"], row["liquidity_usd"],
                 row["volume_5m_usd"], price_usd, price_ts, row["risk_score"],
                 route_provider, json.dumps(raw, sort_keys=True), "NEW",
                 now, now, price_status, row["strategy_id"]),
            )
            inserted += 1
        con.commit()
        note = f"inserted={inserted}"
        if mock_inserted:
            note += f" seed_mock={mock_inserted}"
        if price_blocked:
            note += " price_blocked=" + ";".join(price_blocked[:3])
        heartbeat("substrate_opportunity_scanner",
                  "OK" if not price_blocked else "DEGRADED", note, inserted)
        return inserted
    finally:
        con.close()


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_SCANNER_INTERVAL_SEC", "45"))
    while True:
        try:
            scan_once()
        except Exception as exc:  # noqa: BLE001
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
