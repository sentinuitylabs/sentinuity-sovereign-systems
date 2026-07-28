from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional, Protocol


@dataclass
class RouteQuote:
    provider: str
    chain: str
    from_asset: str
    to_asset: str
    size_usd: float
    estimated_price_usd: float
    estimated_fee_usd: float
    estimated_slippage_pct: float
    ok: bool
    reason: str = "OK"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ExecutionResult:
    provider: str
    mode: str
    chain: str
    asset_symbol: str
    action: str
    size_usd: float
    ok: bool
    status: str
    reason: str
    tx_hash: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


class SubstrateProvider(Protocol):
    name: str

    def health(self) -> Dict:
        ...

    def quote_buy(self, chain: str, asset_symbol: str, size_usd: float) -> RouteQuote:
        ...

    def execute_buy(self, chain: str, asset_symbol: str, size_usd: float) -> ExecutionResult:
        ...


class MockProvider:
    name = "mock"

    def health(self) -> Dict:
        return {
            "provider": self.name,
            "ready": True,
            "mode": "paper/mock",
            "addresses": {},
            "reason": "mock provider ready for paper simulation",
        }

    def quote_buy(self, chain: str, asset_symbol: str, size_usd: float) -> RouteQuote:
        synthetic_price = 1.0
        if asset_symbol.upper() == "SOL":
            synthetic_price = 150.0
        elif asset_symbol.upper() in {"WETH", "ETH"}:
            synthetic_price = 3500.0
        elif asset_symbol.upper() in {"CBBTC", "WBTC", "BTC"}:
            synthetic_price = 100000.0
        return RouteQuote(
            provider=self.name,
            chain=chain,
            from_asset="USDC",
            to_asset=asset_symbol,
            size_usd=float(size_usd),
            estimated_price_usd=synthetic_price,
            estimated_fee_usd=max(0.0025, float(size_usd) * 0.0005),
            estimated_slippage_pct=0.20,
            ok=True,
        )

    def execute_buy(self, chain: str, asset_symbol: str, size_usd: float) -> ExecutionResult:
        return ExecutionResult(
            provider=self.name,
            mode="paper/mock",
            chain=chain,
            asset_symbol=asset_symbol,
            action="BUY",
            size_usd=float(size_usd),
            ok=True,
            status="SIMULATED",
            reason="paper/mock execution only",
        )
