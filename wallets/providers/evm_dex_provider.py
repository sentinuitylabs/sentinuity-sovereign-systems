from __future__ import annotations

import os
from typing import Dict
from .base_provider import MockProvider, RouteQuote, ExecutionResult


class EVMDexProvider(MockProvider):
    name = "evm_dex"

    def _configured(self) -> bool:
        return bool(os.getenv("SUBSTRATE_EVM_PRIVATE_KEY") and os.getenv("SUBSTRATE_EVM_RPC_URL"))

    def health(self) -> Dict:
        ready = self._configured()
        return {
            "provider": self.name,
            "ready": ready,
            "mode": "live-ready" if ready else "quote/mock",
            "addresses": {},
            "reason": "EVM RPC/signing material present" if ready else "missing SUBSTRATE_EVM_PRIVATE_KEY or SUBSTRATE_EVM_RPC_URL",
        }

    def quote_buy(self, chain: str, asset_symbol: str, size_usd: float) -> RouteQuote:
        q = super().quote_buy(chain, asset_symbol, size_usd)
        q.provider = self.name
        q.reason = "EVM_DEX_STUB_QUOTE_REPLACE_WITH_0X/1INCH/DEX_AGGREGATOR"
        return q

    def execute_buy(self, chain: str, asset_symbol: str, size_usd: float) -> ExecutionResult:
        return ExecutionResult(
            provider=self.name,
            mode="blocked",
            chain=chain,
            asset_symbol=asset_symbol,
            action="BUY",
            size_usd=float(size_usd),
            ok=False,
            status="BLOCKED",
            reason="EVM live execution scaffold present; wire aggregator + signing after audit",
        )
