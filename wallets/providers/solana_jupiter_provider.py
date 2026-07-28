from __future__ import annotations

import os
from typing import Dict
from .base_provider import MockProvider, RouteQuote, ExecutionResult


class SolanaJupiterProvider(MockProvider):
    name = "jupiter"

    def _configured(self) -> bool:
        return bool(os.getenv("SOLANA_PRIVATE_KEY") or os.getenv("SUBSTRATE_SOLANA_KEYPAIR_PATH"))

    def health(self) -> Dict:
        ready = self._configured()
        return {
            "provider": self.name,
            "ready": ready,
            "mode": "live-ready" if ready else "paper-quote-only",
            "addresses": {},
            "reason": "Solana signing material present" if ready else "missing SOLANA_PRIVATE_KEY or SUBSTRATE_SOLANA_KEYPAIR_PATH",
        }

    def quote_buy(self, chain: str, asset_symbol: str, size_usd: float) -> RouteQuote:
        q = super().quote_buy("solana", asset_symbol, size_usd)
        q.provider = self.name
        q.reason = "JUPITER_STUB_QUOTE_REPLACE_WITH_SWAP_V2_API"
        return q

    def execute_buy(self, chain: str, asset_symbol: str, size_usd: float) -> ExecutionResult:
        return ExecutionResult(
            provider=self.name,
            mode="blocked",
            chain="solana",
            asset_symbol=asset_symbol,
            action="BUY",
            size_usd=float(size_usd),
            ok=False,
            status="BLOCKED",
            reason="Jupiter live execution scaffold present; wire Swap V2 quote/build/send after audit",
        )
