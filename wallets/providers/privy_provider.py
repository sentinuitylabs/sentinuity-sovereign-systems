from __future__ import annotations

import os
from typing import Dict
from .base_provider import MockProvider, RouteQuote, ExecutionResult


class PrivyProvider(MockProvider):
    name = "privy"

    def _configured(self) -> bool:
        return bool(os.getenv("PRIVY_APP_ID") and os.getenv("PRIVY_APP_SECRET"))

    def health(self) -> Dict:
        ready = self._configured()
        return {
            "provider": self.name,
            "ready": ready,
            "mode": "live-ready" if ready else "blocked",
            "addresses": {},
            "reason": "Privy credentials present" if ready else "missing PRIVY_APP_ID or PRIVY_APP_SECRET",
        }

    def quote_buy(self, chain: str, asset_symbol: str, size_usd: float) -> RouteQuote:
        q = super().quote_buy(chain, asset_symbol, size_usd)
        q.provider = self.name
        if not self._configured():
            q.ok = False
            q.reason = "PRIVY_NOT_CONFIGURED"
        else:
            q.reason = "PRIVY_STUB_QUOTE_REPLACE_WITH_API"
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
            reason="Privy live execution scaffold present; wire server wallet API after audit",
        )
