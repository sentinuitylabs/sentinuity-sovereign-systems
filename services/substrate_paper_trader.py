from __future__ import annotations

"""Compatibility service for the canonical Substrate paper lifecycle.

The codebase previously launched two independent Substrate paper executors:
this legacy target-based trader and substrate_portfolio_supervisor. That split
created two ledgers, two admission paths and conflicting PnL. This service now
acts only as a compatibility heartbeat and delegates to the canonical
opportunity -> substrate_positions lifecycle in substrate_portfolio_supervisor.
It cannot open a second, parallel position.
"""

import argparse
import os
import time

from wallets.substrate_wallet_schema import heartbeat
from services.substrate_portfolio_supervisor import supervise_once

SERVICE = "substrate_paper_trader"


def run_once() -> dict:
    result = supervise_once()
    heartbeat(
        SERVICE,
        "OK",
        "compatibility_delegate canonical=substrate_portfolio_supervisor "
        f"opened={result.get('opened', 0)} closed={result.get('exits', {}).get('closed', 0)}",
        int(result.get("opened", 0)) + int(result.get("exits", {}).get("closed", 0)),
    )
    return result


def run_forever() -> None:
    interval = int(os.getenv("SUBSTRATE_TRADER_INTERVAL_SEC", "60"))
    while True:
        try:
            run_once()
        except Exception as exc:
            heartbeat(SERVICE, "ERROR", repr(exc), 0)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once or os.getenv("SUBSTRATE_RUN_FOREVER", "1") == "0":
        print(run_once())
    else:
        run_forever()
