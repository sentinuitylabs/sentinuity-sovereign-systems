from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    snapshot: Dict

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _csv_env(name: str, default: str) -> set[str]:
    return {x.strip().lower() for x in os.getenv(name, default).split(",") if x.strip()}


class SubstrateRiskGuard:
    def __init__(self) -> None:
        self.allowed_chains = _csv_env("SUBSTRATE_ALLOWED_CHAINS", "solana,base")
        self.allowed_assets = _csv_env("SUBSTRATE_ALLOWED_ASSETS", "USDC,SOL,WETH,cbBTC")
        self.max_paper_position = float(os.getenv("SUBSTRATE_MAX_PAPER_POSITION_USD", "25"))
        self.max_live_position = float(os.getenv("SUBSTRATE_MAX_LIVE_POSITION_USD", "3"))
        self.max_daily_loss = float(os.getenv("SUBSTRATE_MAX_DAILY_LOSS_USD", "10"))
        self.price_fresh_sec = int(os.getenv("SUBSTRATE_REQUIRE_PRICE_FRESH_SEC", "120"))
        self.conf_floor = float(os.getenv("SUBSTRATE_CONFIDENCE_FLOOR", "0.62"))
        self.max_slippage_pct = float(os.getenv("SUBSTRATE_MAX_SLIPPAGE_PCT", "1.0"))

    def check(
        self,
        *,
        mode: str,
        chain: str,
        asset_symbol: str,
        size_usd: float,
        confidence: float,
        price_updated_at: Optional[int],
        cash_usd: float,
        quote: Optional[Dict],
        native_or_wrapped: str = "native",
    ) -> RiskDecision:
        now = int(time.time())
        mode_u = (mode or "PAPER").upper()
        chain_l = chain.lower()
        asset_l = asset_symbol.lower()

        snap = {
            "mode": mode_u,
            "chain": chain,
            "asset_symbol": asset_symbol,
            "size_usd": size_usd,
            "confidence": confidence,
            "cash_usd": cash_usd,
            "price_updated_at": price_updated_at,
            "native_or_wrapped": native_or_wrapped,
            "allowed_chains": sorted(self.allowed_chains),
            "allowed_assets": sorted(self.allowed_assets),
            "quote": quote or {},
        }

        if chain_l not in self.allowed_chains:
            return RiskDecision(False, f"CHAIN_NOT_ALLOWLISTED_{chain}", snap)
        if asset_l not in self.allowed_assets:
            return RiskDecision(False, f"ASSET_NOT_ALLOWLISTED_{asset_symbol}", snap)
        if confidence < self.conf_floor:
            return RiskDecision(False, f"CONFIDENCE_BELOW_FLOOR_{confidence:.2f}", snap)
        if not price_updated_at or now - int(price_updated_at) > self.price_fresh_sec:
            return RiskDecision(False, "PRICE_NOT_FRESH", snap)
        if cash_usd < size_usd:
            return RiskDecision(False, "PAPER_CASH_INSUFFICIENT" if mode_u == "PAPER" else "LIVE_BALANCE_UNKNOWN_OR_INSUFFICIENT", snap)
        if mode_u == "PAPER" and size_usd > self.max_paper_position:
            return RiskDecision(False, f"PAPER_SIZE_EXCEEDS_{self.max_paper_position}", snap)
        if mode_u == "LIVE" and size_usd > self.max_live_position:
            return RiskDecision(False, f"LIVE_SIZE_EXCEEDS_{self.max_live_position}", snap)
        if quote is None:
            return RiskDecision(False, "MISSING_ROUTE_QUOTE", snap)
        if not quote.get("ok", False):
            return RiskDecision(False, f"ROUTE_QUOTE_NOT_OK_{quote.get('reason','UNKNOWN')}", snap)
        if float(quote.get("estimated_slippage_pct", 99.0)) > self.max_slippage_pct:
            return RiskDecision(False, "ROUTE_SLIPPAGE_TOO_HIGH", snap)
        if native_or_wrapped.lower() == "wrapped" and os.getenv("SUBSTRATE_BLOCK_WRAPPED_ASSETS", "0") == "1":
            return RiskDecision(False, "WRAPPED_ASSET_BLOCKED_BY_POLICY", snap)

        if mode_u == "LIVE":
            if os.getenv("SUBSTRATE_LIVE_ENABLED", "0") != "1":
                return RiskDecision(False, "LIVE_DISABLED", snap)
            if os.getenv("SUBSTRATE_LIVE_ARMED", "0") != "1":
                return RiskDecision(False, "LIVE_NOT_ARMED", snap)

        return RiskDecision(True, "ALLOW", snap)
