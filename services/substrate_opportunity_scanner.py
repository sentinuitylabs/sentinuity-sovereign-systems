from __future__ import annotations

"""Evidence-driven Substrate opportunity scanner (paper research lane).

This scanner replaces the former hard-coded three-row basket. It collects real,
provider-timestamped marks from the canonical price feed and only emits an
opportunity after sufficient price history exists to calculate a reproducible
signal. No literal confidence or expected-edge values are used.
"""

import argparse
import json
import math
import os
import statistics
import time
from typing import Dict, List

from wallets.substrate_wallet_schema import connect, ensure_schema, heartbeat, cfg_float, cfg_int, _ensure_col
from services.substrate_price_feed import ASSETS, ACTIONABLE_STATUSES, get_prices

STRATEGY_ID = "SUBSTRATE_EVIDENCE_MOMENTUM_V1"


def _ensure_cols(con) -> None:
    for name, ddl in (
        ("price_status", "TEXT"), ("strategy_id", "TEXT"),
        ("score_json", "TEXT"), ("discovery_at", "REAL"),
        ("timeframe_or_regime", "TEXT"),
        # Evidence-presence flags. MISSING is a first-class value: it must
        # never be collapsed into 0 by a reader (audit finding A3).
        ("liquidity_status", "TEXT"), ("volume_status", "TEXT"),
    ):
        _ensure_col(con, "substrate_opportunities", name, ddl)


def _history(con, symbol: str, limit: int) -> List[dict]:
    rows = con.execute(
        "SELECT price,source_ts,observed_ts,status,confidence,source "
        "FROM substrate_price_marks WHERE asset=? AND price>0 "
        "AND status IN ('FRESH','DEGRADED') ORDER BY observed_ts DESC LIMIT ?",
        (symbol, int(limit)),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _signal_from_history(rows: List[dict], round_trip_cost_pct: float) -> Dict:
    prices = [float(r["price"]) for r in rows if float(r.get("price") or 0) > 0]
    if len(prices) < 4:
        return {"actionable": False, "reason": "insufficient_history", "samples": len(prices)}
    rets = [(prices[i] / prices[i - 1] - 1.0) * 100.0 for i in range(1, len(prices))]
    short_n = min(3, len(rets))
    short_mom = sum(rets[-short_n:])
    long_mom = (prices[-1] / prices[0] - 1.0) * 100.0
    volatility = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    positive_ratio = sum(1 for r in rets if r > 0) / len(rets)
    trend_consistency = abs(positive_ratio - 0.5) * 2.0
    drawdown = (prices[-1] / max(prices) - 1.0) * 100.0

    if long_mom > 0 and short_mom > 0:
        regime = "TREND_UP"
    elif long_mom < 0 and short_mom < 0:
        regime = "TREND_DOWN"
    else:
        regime = "MIXED"

    gross_edge = max(0.0, 0.55 * short_mom + 0.25 * long_mom)
    risk_penalty = 0.65 * volatility + max(0.0, -drawdown) * 0.12
    net_edge_pct = gross_edge - risk_penalty - round_trip_cost_pct
    sample_factor = min(1.0, len(prices) / 12.0)
    confidence = max(0.0, min(0.95,
        0.35 + 0.25 * sample_factor + 0.25 * trend_consistency
        + 0.10 * min(1.0, max(0.0, net_edge_pct) / 3.0)
        - 0.08 * min(1.0, volatility / 4.0)
    ))
    return {
        "actionable": regime == "TREND_UP" and net_edge_pct > 0,
        "samples": len(prices), "regime": regime,
        "short_momentum_pct": round(short_mom, 6),
        "long_momentum_pct": round(long_mom, 6),
        "volatility_pct": round(volatility, 6),
        "trend_consistency": round(trend_consistency, 6),
        "drawdown_from_peak_pct": round(drawdown, 6),
        "estimated_round_trip_cost_pct": round(round_trip_cost_pct, 6),
        "net_expected_edge_pct": round(net_edge_pct, 6),
        "confidence": round(confidence, 6),
    }


def scan_once(fetch_json=None) -> int:
    ensure_schema()
    now = time.time()
    con = connect()
    inserted = 0
    blocked: List[str] = []
    try:
        _ensure_cols(con)
        symbols = [s.strip() for s in os.getenv("SUBSTRATE_UNIVERSE", ",".join(ASSETS)).split(",") if s.strip() in ASSETS]
        prices = get_prices(symbols, fetch_json=fetch_json, con=con, persist=True)
        min_samples = cfg_int(con, "SUBSTRATE_SIGNAL_MIN_SAMPLES", 6)
        history_n = cfg_int(con, "SUBSTRATE_SIGNAL_HISTORY_SAMPLES", 18)
        min_conf = cfg_float(con, "SUBSTRATE_SIGNAL_MIN_CONFIDENCE", 0.62)
        min_edge_pct = cfg_float(con, "SUBSTRATE_SIGNAL_MIN_NET_EDGE_PCT", 0.35)
        cost_pct = cfg_float(con, "SUBSTRATE_EST_ROUND_TRIP_COST_PCT", 0.20)
        cooldown = cfg_int(con, "SUBSTRATE_SIGNAL_COOLDOWN_SEC", 900)

        for symbol in symbols:
            px = prices.get(symbol) or {}
            status = str(px.get("status") or "UNAVAILABLE")
            if status not in ACTIONABLE_STATUSES:
                blocked.append(f"{symbol}:{status}")
                continue
            rows = _history(con, symbol, history_n)
            signal = _signal_from_history(rows, cost_pct)
            if int(signal.get("samples") or 0) < min_samples:
                blocked.append(f"{symbol}:history={signal.get('samples', 0)}/{min_samples}")
                continue
            conf = float(signal.get("confidence") or 0.0)
            net_edge_pct = float(signal.get("net_expected_edge_pct") or 0.0)
            if not signal.get("actionable") or conf < min_conf or net_edge_pct < min_edge_pct:
                blocked.append(f"{symbol}:{signal.get('regime')} edge={net_edge_pct:.2f}% conf={conf:.2f}")
                continue
            recent = con.execute(
                # SUBSTRATE_EXPOSURE_REPAIR_20260802 (audit A1/A2): the ledger
                # writes 'PAPER_OPENED', never 'PAPER_OPEN'. The cooldown
                # therefore stopped matching the moment a position opened, so a
                # fresh duplicate opportunity was minted every scan interval and
                # immediately refused on exposure. BLOCKED_* states are included
                # so a suppressed candidate is not replaced by a clone.
                "SELECT 1 FROM substrate_opportunities WHERE asset_symbol=? "
                "AND strategy_id=? AND state IN "
                "('NEW','OPEN','READY','PROMOTED','PAPER_OPENED','BLOCKED_CONFIG') "
                "AND created_at>=? LIMIT 1",
                (symbol, STRATEGY_ID, now - cooldown),
            ).fetchone()
            if recent:
                continue
            spec = ASSETS[symbol]
            score_json = json.dumps(signal, sort_keys=True)
            con.execute(
                """INSERT INTO substrate_opportunities
                (source,chain,asset_symbol,asset_address,asset_type,native_or_wrapped,
                 quote_asset,confidence,expected_edge,liquidity_usd,volume_5m_usd,
                 price_usd,price_updated_at,risk_score,route_provider,raw_json,state,
                 created_at,updated_at,price_status,strategy_id,score_json,
                 discovery_at,timeframe_or_regime,liquidity_status,volume_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PRICE_EVIDENCE", spec.get("chain", ""), symbol,
                 spec.get("jupiter_mint") or spec.get("coingecko_id") or symbol,
                 "spot", "native" if symbol == "SOL" else "wrapped", "USDC",
                 # SUBSTRATE_EXPOSURE_REPAIR_20260802 (audit A2/A3): these were
                 # literal 0.0. A false zero is indistinguishable from a real
                 # zero, so every downstream liquidity/volume gate silently
                 # passed on absent evidence. NULL means MISSING; the companion
                 # liquidity_status column names it so no gate can misread it.
                 None, None, float(px["price"]),
                 float(px["source_ts"]), min(1.0, float(signal["volatility_pct"]) / 10.0),
                 str(px.get("source") or "unknown"), score_json, "NEW", now, now,
                 status, STRATEGY_ID, score_json, now, str(signal["regime"]),
                 "MISSING", "MISSING"),
            )
            inserted += 1
        con.commit()
        note = f"inserted={inserted} universe={len(symbols)}"
        if blocked:
            note += " blocked=" + ";".join(blocked[:4])
        heartbeat("substrate_opportunity_scanner", "OK" if inserted or not blocked else "DEGRADED", note, inserted)
        return inserted
    finally:
        con.close()


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_SCANNER_INTERVAL_SEC", "60"))
    while True:
        try:
            scan_once()
        except Exception as exc:
            heartbeat("substrate_opportunity_scanner", "ERROR", repr(exc), 0)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once or os.getenv("SUBSTRATE_RUN_FOREVER", "1") == "0":
        print(f"inserted={scan_once()}")
    else:
        run_forever()
