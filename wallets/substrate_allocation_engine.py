from __future__ import annotations

import time
from .substrate_wallet_schema import connect, ensure_schema


def propose_allocation() -> int:
    """Write a lightweight council allocation heartbeat for the Substrate lane.

    This is research/council metadata only. It does not open, sign, or send orders.
    """
    ensure_schema()
    now = time.time()
    rows = [
        ("POLARIS", "SCAN", "solana", "SOL", 35.0, 0.72, "Core liquid Solana exposure; low route complexity."),
        ("IVARIS", "RISK", "base", "WETH", 25.0, 0.66, "EVM test lane; manual-sign only until outcomes exist."),
        ("NUGGET", "CAP", "base", "cbBTC", 10.0, 0.64, "Proxy BTC exposure; tiny size until slippage is measured."),
    ]
    con = connect()
    try:
        inserted = 0
        for r in rows:
            # Avoid unlimited duplicates: one vote per member/asset per 10 minutes.
            recent = con.execute(
                "SELECT 1 FROM substrate_council_votes WHERE council_member=? AND asset_symbol=? AND created_at>=? LIMIT 1",
                (r[0], r[3], now - 600),
            ).fetchone()
            if recent:
                continue
            con.execute(
                "INSERT INTO substrate_council_votes(council_member,phase,chain,asset_symbol,allocation_pct,confidence,thesis,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (*r, now),
            )
            inserted += 1
        con.commit()
        return inserted
    finally:
        con.close()
