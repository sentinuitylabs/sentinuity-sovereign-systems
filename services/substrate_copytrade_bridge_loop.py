from __future__ import annotations

import os
import time

from wallets.substrate_wallet_schema import heartbeat
try:
    from services.substrate_copytrade_bridge import ingest_copytrade_once
except Exception:
    from substrate_copytrade_bridge import ingest_copytrade_once


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_COPYTRADE_INTERVAL_SEC", "45"))
    heartbeat("substrate_copytrade_bridge", "STARTING", f"loop interval={interval}s", 0)

    while True:
        try:
            n = ingest_copytrade_once()
            heartbeat("substrate_copytrade_bridge", "OK", f"loop copytrade_ingested={n}", n)
        except Exception as exc:
            heartbeat("substrate_copytrade_bridge", "ERROR", repr(exc), 0)
        time.sleep(interval)


if __name__ == "__main__":
    run_forever()
