from __future__ import annotations

from .base_provider import MockProvider
from .cdp_provider import CDPProvider
from .privy_provider import PrivyProvider
from .solana_jupiter_provider import SolanaJupiterProvider
from .evm_dex_provider import EVMDexProvider


def get_provider(name: str):
    key = (name or "mock").lower().strip()
    if key == "cdp":
        return CDPProvider()
    if key == "privy":
        return PrivyProvider()
    if key in {"jupiter", "solana_jupiter"}:
        return SolanaJupiterProvider()
    if key in {"evm", "evm_dex", "base"}:
        return EVMDexProvider()
    return MockProvider()
