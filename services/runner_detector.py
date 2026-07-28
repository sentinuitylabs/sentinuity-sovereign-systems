"""
Sentinuity Runner Detector — updated for real schema.

Real market_snapshots columns (confirmed from live DB audit):
  buy_velocity        — exists (may have data)
  curve_progress_pct  — bonding curve % complete
  curve_sol_reserves  — SOL in bonding curve
  market_cap_usd      — HAS DATA (confirmed sample=3802.5)
  token_liquidity_usd — liquidity
  holder_count        — holder count
  top10_holder_pct    — top 10 holder concentration
  mint_confidence     — confidence score
  freshness_score     — freshness

NO velocity price columns exist (price_5s_ago etc not in schema).
Runner scoring uses curve momentum + market cap + liquidity instead.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def detect_runners(limit: int = 50, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    """
    Returns ranked runner candidates using real schema columns.
    Scoring based on: curve momentum, market cap, liquidity, confidence, buy_velocity.
    """
    with _connect(db_path) as conn:
        rows = conn.execute("""
            SELECT
                token_name,
                mint_address,
                candidate_state,
                mint_confidence,
                confidence,
                observed_price,
                market_cap_usd,
                token_liquidity_usd,
                curve_progress_pct,
                curve_sol_reserves,
                holder_count,
                top10_holder_pct,
                buy_velocity,
                freshness_score,
                price_updated_at,
                created_at
            FROM market_snapshots
            WHERE candidate_state NOT IN ('vetoed','dead','executed','expired_stale','EXECUTOR_STALE_GATE')
              AND COALESCE(price_updated_at, created_at, 0) > ?
              AND observed_price IS NOT NULL AND observed_price > 0
            ORDER BY COALESCE(price_updated_at, created_at) DESC
            LIMIT ?
        """, (int(time.time() - 3600), int(limit))).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        mcap      = _num(r["market_cap_usd"])   or 0.0
        liq       = _num(r["token_liquidity_usd"]) or 0.0
        curve_pct = _num(r["curve_progress_pct"]) or 0.0
        curve_sol = _num(r["curve_sol_reserves"]) or 0.0
        holders   = _num(r["holder_count"])     or 0.0
        top10     = _num(r["top10_holder_pct"]) or 100.0
        bv        = _num(r["buy_velocity"])     or 0.0
        fresh     = _num(r["freshness_score"])  or 0.0
        conf      = _num(r["mint_confidence"] or r["confidence"]) or 0.0

        score   = 0.0
        reasons = []

        # Curve momentum — high curve progress with SOL reserves = active bonding
        if curve_pct >= 80:
            score += 30.0
            reasons.append(f"curve={curve_pct:.0f}%")
        elif curve_pct >= 50:
            score += 15.0
            reasons.append(f"curve={curve_pct:.0f}%")

        # Early low-mcap with liquidity — high upside potential
        if 0 < mcap < 10_000 and liq > 500:
            score += 25.0
            reasons.append(f"low-mcap=${mcap:.0f}")
        elif 0 < mcap < 30_000 and liq > 1000:
            score += 15.0
            reasons.append(f"mcap=${mcap:.0f}")

        # Buy velocity (if populated)
        if bv > 5:
            score += min(bv * 2.0, 20.0)
            reasons.append(f"buy_vel={bv:.1f}")

        # Holder distribution — low concentration = healthier
        if holders > 50 and top10 < 40:
            score += 10.0
            reasons.append(f"holders={holders:.0f}")

        # Confidence
        if conf >= 0.85:
            score += 15.0
            reasons.append(f"conf={conf:.2f}")
        elif conf >= 0.75:
            score += 8.0

        # Freshness — recently active signal
        if fresh >= 0.8:
            score += 10.0
            reasons.append("fresh")

        tier = "NONE"
        if score >= 70:
            tier = "RUNNER_A"
        elif score >= 45:
            tier = "RUNNER_B"
        elif score >= 25:
            tier = "WATCH"

        if tier != "NONE":
            out.append({
                "token_name":       r["token_name"],
                "mint_address":     r["mint_address"],
                "tier":             tier,
                "runner_score":     round(score, 2),
                "market_cap_usd":   mcap,
                "liquidity_usd":    liq,
                "curve_pct":        curve_pct,
                "buy_velocity":     bv,
                "confidence":       conf,
                "reasons":          reasons,
            })

    return sorted(out, key=lambda x: x["runner_score"], reverse=True)


def _num(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def print_report() -> None:
    runners = detect_runners(limit=100)
    print("\n=== SENTINUITY RUNNER DETECTOR ===")
    if not runners:
        print("No current runner candidates.")
        return
    for r in runners[:15]:
        print(
            f"{r['tier']} score={r['runner_score']} "
            f"token={str(r.get('token_name',''))[:16]} "
            f"mcap=${r.get('market_cap_usd',0):.0f} "
            f"curve={r.get('curve_pct',0):.0f}% "
            f"conf={r.get('confidence',0):.2f} "
            f"reasons={','.join(r.get('reasons', []))}"
        )


if __name__ == "__main__":
    print_report()
