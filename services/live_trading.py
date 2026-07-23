"""Public Community Edition live-execution boundary.

Real-money transaction construction, signing and submission are intentionally
not distributed in the public repository. Paper trading, research, market data,
UI surfaces and execution interfaces remain available.
"""
from __future__ import annotations
from typing import Any

PUBLIC_LIVE_EXECUTION_AVAILABLE = False
BLOCK_REASON = "PUBLIC_BUILD_LIVE_EXECUTION_NOT_INCLUDED"


def _blocked(action: str, **extra: Any) -> dict:
    return {
        "success": False,
        "ok": False,
        "status": "BLOCKED",
        "reason": BLOCK_REASON,
        "action": action,
        "signature": None,
        "tx_signature": None,
        "fill_resolved": False,
        **extra,
    }


def get_live_wallet_balance() -> float:
    return 0.0


def resolve_confirmed_fill(signature: str, wallet_pubkey: str, mint: str) -> dict:
    return _blocked("RESOLVE_FILL", signature=signature, wallet_pubkey=wallet_pubkey, mint=mint)


def inspect_live_token_safety(mint: str) -> dict:
    return _blocked("TOKEN_SAFETY", mint=mint)


def preflight_live_buy(mint: str, pos_size_usd: float) -> dict:
    return _blocked("BUY_PREFLIGHT", mint=mint, requested_usd=float(pos_size_usd or 0.0))


def execute_live_buy(mint: str, pos_size_usd: float, entry_price_usd: float, position_id: int) -> dict:
    return _blocked("BUY", mint=mint, requested_usd=float(pos_size_usd or 0.0), position_id=position_id)


def can_sell(mint: str, quantity: float) -> tuple[bool, str]:
    return False, BLOCK_REASON


def evaluate_exit_quality(mint: str, quantity: float) -> dict:
    return _blocked("EXIT_QUALITY", mint=mint, quantity=float(quantity or 0.0), quality_score=0.0)


def score_liquidity(mint: str, quantity: float) -> float:
    return 0.0


def rug_score(mint: str) -> float:
    return 0.0


def validate_jupiter_route(quote: dict) -> bool:
    return False


def blacklist_mint(mint: str, reason: str) -> None:
    return None


def verify_token_balance(mint: str, wallet_pubkey: str) -> float:
    return 0.0


def execute_live_sell(mint: str, quantity: float, position_id: int, exit_price_usd: float = 0.0, emergency: bool = False) -> dict:
    return _blocked("SELL", mint=mint, quantity=float(quantity or 0.0), position_id=position_id, emergency=bool(emergency))


def is_live_mode() -> bool:
    return False
