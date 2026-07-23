"""Public Community Edition wallet-sync stub.

The public build never reads private keys or derives funded wallet identities.
"""
from __future__ import annotations

PUBLIC_LIVE_EXECUTION_AVAILABLE = False
BLOCK_REASON = "PUBLIC_BUILD_LIVE_WALLET_SYNC_NOT_INCLUDED"


def live_lane_enabled() -> bool:
    return False


def fetch_real_wallet_snapshot() -> dict:
    return {"ok": False, "status": "BLOCKED", "reason": BLOCK_REASON, "balance_usd": 0.0, "available_usd": 0.0}


def fetch_real_wallet_usd() -> float:
    return 0.0


def sync_once(*, force: bool = False) -> bool:
    return False


def start_live_wallet_sync() -> bool:
    return False
