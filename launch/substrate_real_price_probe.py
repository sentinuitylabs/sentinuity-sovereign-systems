#!/usr/bin/env python3
"""
launch/substrate_real_price_probe.py
===============================================================================
SUBSTRATE REAL-SOURCE PRICE PROBE (SUBSTRATE_REAL_PRICE_20260721)

OPERATOR-RUN, READ-ONLY. Performs one live fetch against the real public
providers (CoinGecko, then Jupiter for SOL) and prints the full canonical
contract for each asset. Nothing is opened, closed, or marked; with
--no-persist not even a price mark row is written.

This is the "one real-source read-only cycle" of directive Phase 5. The
deterministic offline cycle is launch/verify_substrate_lifecycle.py; this
probe requires internet access and therefore runs on the operator's machine.

Run:  python launch/substrate_real_price_probe.py [--no-persist]
Exit 0 when every asset resolves FRESH or DEGRADED from a live provider.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.substrate_price_feed import ACTIONABLE_STATUSES, ASSETS, get_prices


def main() -> int:
    persist = "--no-persist" not in sys.argv[1:]
    print(f"Fetching live prices for: {', '.join(ASSETS)} "
          f"(persist={'on' if persist else 'off'})")
    results = get_prices(list(ASSETS), persist=persist)
    bad = 0
    for asset, c in results.items():
        line = (f"  {asset:6s} status={c['status']:<11s} "
                f"price={c['price']} source={c['source']} "
                f"source_ts={c['source_ts']:.0f} age={c['age_sec']}s "
                f"conf={c['confidence']}")
        if c.get("error"):
            line += f"  error={str(c['error'])[:80]}"
        print(line)
        if c["status"] not in ACTIONABLE_STATUSES:
            bad += 1
    if bad:
        print(f"\nPROBE: {bad} asset(s) not actionable — Substrate will honestly "
              "defer entries/marks for them (this is the designed behaviour, "
              "not a crash).")
        return 1
    print("\nPROBE: PASS — all assets FRESH/DEGRADED from live providers with "
          "real provider timestamps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
