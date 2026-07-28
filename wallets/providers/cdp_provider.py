from __future__ import annotations

import os
from typing import Dict
from .base_provider import MockProvider, RouteQuote, ExecutionResult


class CDPProvider(MockProvider):
    """Coinbase Developer Platform provider scaffold.

    This file is live-infra-ready but safe by default. It will not place live orders
    until credentials and explicit live arming are present. Wire actual CDP SDK calls
    inside quote_buy/execute_buy once your CDP app and wallet policy are configured.
    """

    name = "cdp"

    def _configured(self) -> bool:
        return bool(os.getenv("CDP_API_KEY_NAME") and os.getenv("CDP_API_KEY_PRIVATE_KEY"))

    def health(self) -> Dict:
        ready = self._configured()
        return {
            "provider": self.name,
            "ready": ready,
            "mode": "live-ready" if ready else "blocked",
            "addresses": {},
            "reason": "CDP credentials present" if ready else "missing CDP_API_KEY_NAME or CDP_API_KEY_PRIVATE_KEY",
        }

    def quote_buy(self, chain: str, asset_symbol: str, size_usd: float) -> RouteQuote:
        if not self._configured():
            q = super().quote_buy(chain, asset_symbol, size_usd)
            q.provider = self.name
            q.ok = False
            q.reason = "CDP_NOT_CONFIGURED"
            return q
        q = super().quote_buy(chain, asset_symbol, size_usd)
        q.provider = self.name
        q.reason = "CDP_STUB_QUOTE_REPLACE_WITH_SDK"
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
            reason="CDP live execution scaffold present; wire SDK + policy after audit",
        )
