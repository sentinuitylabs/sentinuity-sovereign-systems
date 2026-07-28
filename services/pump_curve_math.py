# coding: utf-8
"""Pump bonding-curve core-state decoder and exact sell simulator.

Shadow-only module: no network, database, or trading authority.
All amounts remain raw integers until the final display conversion.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

BC_DISCRIMINATOR = bytes.fromhex("17b7f83760d8ac60")
OFF_VTOK, OFF_VQUOTE = 8, 16
OFF_RTOK, OFF_RQUOTE = 24, 32
OFF_TOTAL_SUPPLY, OFF_COMPLETE = 40, 48
MIN_CORE_LEN = 49
LAMPORTS_PER_SOL = 1_000_000_000


@dataclass(frozen=True)
class CurveState:
    virtual_token_reserves: int
    virtual_quote_reserves: int
    real_token_reserves: int
    real_quote_reserves: int
    token_total_supply: int
    complete: bool
    account_len: int

    @property
    def core_valid(self) -> bool:
        return (
            self.virtual_token_reserves > 0
            and self.virtual_quote_reserves > 0
            and self.token_total_supply > 0
        )

    @property
    def tradeable_pre_grad(self) -> bool:
        return self.core_valid and not self.complete


@dataclass(frozen=True)
class SellQuote:
    ok: bool
    reason: str
    raw_token_in: int
    theoretical_gross_quote_raw: int
    payable_gross_quote_raw: int
    fee_quote_raw: int
    net_quote_raw: int
    marginal_quote_raw: int
    curve_impact_bps: int
    reserve_bounded: bool
    real_reserve_coverage_bps: int


def decode_curve(raw: bytes) -> Optional[CurveState]:
    """Decode the stable core prefix of a Pump bonding-curve account.

    Extended layouts are accepted because the first 49 bytes preserve the
    original reserve and completion fields. Unknown trailing fields are not
    guessed and never gain authority here.
    """
    if not raw or len(raw) < MIN_CORE_LEN or raw[:8] != BC_DISCRIMINATOR:
        return None
    try:
        u64 = lambda offset: struct.unpack_from("<Q", raw, offset)[0]
        return CurveState(
            virtual_token_reserves=u64(OFF_VTOK),
            virtual_quote_reserves=u64(OFF_VQUOTE),
            real_token_reserves=u64(OFF_RTOK),
            real_quote_reserves=u64(OFF_RQUOTE),
            token_total_supply=u64(OFF_TOTAL_SUPPLY),
            complete=bool(raw[OFF_COMPLETE]),
            account_len=len(raw),
        )
    except (IndexError, struct.error, TypeError, ValueError):
        return None


def simulate_sell_exact(curve: CurveState, raw_token_in: int, fee_bps: int) -> SellQuote:
    """Conservative exact-quantity sell estimate for a pre-grad Pump curve.

    The constant-product result is bounded by real quote reserves before fees;
    a curve cannot pay quote assets that are not actually present. This is a
    shadow estimate, not a transaction builder or funded execution quote.
    """
    if raw_token_in <= 0:
        return SellQuote(False, "ZERO_QUANTITY", raw_token_in, 0, 0, 0, 0, 0, 0, False, 0)
    if not 0 <= fee_bps <= 10_000:
        return SellQuote(False, "INVALID_FEE_BPS", raw_token_in, 0, 0, 0, 0, 0, 0, False, 0)
    if not curve.core_valid:
        return SellQuote(False, "CURVE_CORE_INVALID", raw_token_in, 0, 0, 0, 0, 0, 0, False, 0)
    if curve.complete:
        return SellQuote(False, "CURVE_COMPLETE_HISTORICAL_ONLY", raw_token_in, 0, 0, 0, 0, 0, 0, False, 0)
    if curve.real_quote_reserves <= 0:
        return SellQuote(False, "NO_REAL_QUOTE_RESERVES", raw_token_in, 0, 0, 0, 0, 0, 0, True, 0)

    vtok = curve.virtual_token_reserves
    vquote = curve.virtual_quote_reserves
    theoretical = (vquote * raw_token_in) // (vtok + raw_token_in)
    marginal = (vquote * raw_token_in) // vtok
    payable = min(theoretical, curve.real_quote_reserves)
    reserve_bounded = payable < theoretical
    fee = (payable * fee_bps) // 10_000
    net = max(0, payable - fee)
    impact_bps = ((marginal - theoretical) * 10_000 // marginal) if marginal > 0 else 0
    coverage_bps = min(10_000, curve.real_quote_reserves * 10_000 // theoretical) if theoretical > 0 else 0

    return SellQuote(
        ok=net > 0,
        reason="OK" if net > 0 else "ZERO_NET_PROCEEDS",
        raw_token_in=raw_token_in,
        theoretical_gross_quote_raw=theoretical,
        payable_gross_quote_raw=payable,
        fee_quote_raw=fee,
        net_quote_raw=net,
        marginal_quote_raw=marginal,
        curve_impact_bps=impact_bps,
        reserve_bounded=reserve_bounded,
        real_reserve_coverage_bps=coverage_bps,
    )


def unit_price_usd_from_sol_quote(
    quote: SellQuote,
    raw_token_in: int,
    token_decimals: int,
    sol_usd: float,
) -> Optional[float]:
    """Display-only USD price for legacy/SOL-quoted Pump curves."""
    if not quote.ok or raw_token_in <= 0 or token_decimals < 0 or sol_usd <= 0:
        return None
    whole_tokens = raw_token_in / (10 ** token_decimals)
    if whole_tokens <= 0:
        return None
    return (quote.net_quote_raw / LAMPORTS_PER_SOL) * sol_usd / whole_tokens
