# coding: utf-8
"""Pump bonding-curve core-state decoder and exact sell simulator.

Shadow-only module: no network, database, or trading authority.
All amounts remain raw integers until the final display conversion.

CURVE_VERDICT_20260816
----------------------
The previous revision returned a single boolean `ok` computed as `net > 0`,
and reported `curve_impact_bps` derived from the *unclamped theoretical*
proceeds.  Those two facts together let a reserve-clamped quote present itself
as an ordinary 1%-impact executable sale while the caller was in fact being
offered ~0% of the position's value.

Position 5788 is the worked example.  Its curve sat at Pump genesis with
`real_quote_reserves = 150` lamports.  The constant-product result for the
requested 11,839,701.762432 tokens was 327,413,399 lamports (~0.327 SOL,
~$24.72).  `min(theoretical, real_quote_reserves)` collapsed that to 150,
fee took 1, and 149 lamports became a "valid" full-position liquidation
witness at $9.503482047518225e-13 -- a persisted -99.999955% mark.  Impact was
still reported as 109 bps because it was measured against `theoretical`, so
every impact ceiling passed cleanly.  `reserve_bounded` and
`real_reserve_coverage_bps` already recorded the contradiction, and no
consumer read either one.

The invariant that makes this decidable
---------------------------------------
On a pre-graduation Pump curve, `virtual_quote * virtual_token` is conserved
and `real_quote_reserves = virtual_quote_reserves - V0`, where V0 is the
genesis virtual quote.  Selling the entire circulating supply returns the curve
to genesis and pays exactly `real_quote_reserves`; every strict subset pays
strictly less.  Therefore, for any quantity that was actually bought from this
curve, `theoretical <= real_quote_reserves` holds by construction and the clamp
is inert.

A clamp that actually binds is therefore not a liquidity fact.  It is a proof
that the requested quantity was never bought from this curve, and the correct
response is to refuse authority -- not to quote the reserve balance.  That case
is RESERVE_COVERAGE_IMPOSSIBLE.

Corollary: a pre-grad curve cannot price below genesis, because
`virtual_quote_reserves` never falls under V0.  Genuine zero recovery on this
venue arrives only via `complete=1` (PumpSwap handoff), which is refused as
CURVE_INAPPLICABLE_MIGRATED.  An unclamped near-zero pre-grad quote is always a
dust-sized holding, never a rug.

What changed
------------
* `SellQuote.verdict` is now the typed answer.  `ok` is retained and redefined
  as "this verdict is CURVE_EXECUTABLE", so every existing `if quote.ok`
  consumer fails closed on a disputed curve with no change at the call site.
* `realised_impact_bps` measures the loss actually borne, against `marginal`,
  using `payable` rather than `theoretical`.  A binding clamp can no longer
  masquerade as low impact to an impact ceiling.
* `unit_price_usd_from_sol_quote` returns None for every non-executable
  verdict.  An unavailable state never becomes a number, and never becomes 0.0.
* `diagnostic_price_usd` is provided separately and explicitly for forensic
  display of a refused quote.  It is never an executable price.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Optional

BC_DISCRIMINATOR = bytes.fromhex("17b7f83760d8ac60")
OFF_VTOK, OFF_VQUOTE = 8, 16
OFF_RTOK, OFF_RQUOTE = 24, 32
OFF_TOTAL_SUPPLY, OFF_COMPLETE = 40, 48
MIN_CORE_LEN = 49
LAMPORTS_PER_SOL = 1_000_000_000

# ── Typed curve verdicts ─────────────────────────────────────────────────────
# Exactly one of these is returned for every simulate_sell_exact() call.
CURVE_EXECUTABLE = "CURVE_EXECUTABLE"
CURVE_ZERO_RECOVERY = "CURVE_ZERO_RECOVERY"
RESERVE_COVERAGE_IMPOSSIBLE = "RESERVE_COVERAGE_IMPOSSIBLE"
CURVE_INAPPLICABLE_MIGRATED = "CURVE_INAPPLICABLE_MIGRATED"
UNIT_INTEGRITY_FAILURE = "UNIT_INTEGRITY_FAILURE"

CURVE_VERDICTS = (
    CURVE_EXECUTABLE,
    CURVE_ZERO_RECOVERY,
    RESERVE_COVERAGE_IMPOSSIBLE,
    CURVE_INAPPLICABLE_MIGRATED,
    UNIT_INTEGRITY_FAILURE,
)

# Only CURVE_EXECUTABLE may carry executable authority. The rest are typed
# refusals: they are evidence, and they are never a price.
EXECUTABLE_VERDICTS = frozenset({CURVE_EXECUTABLE})

DEFAULT_CURVE_FEE_BPS = 100


def curve_fee_bps() -> int:
    """Single source of truth for the Pump curve fee used by every plane.

    Before this existed the authoritative router used PUMP_EXEC_WITNESS_FEE_BPS
    (100) while the shadow mesh used PUMP_SHADOW_FEE_BPS (125).  The two planes
    could therefore never agree byte-for-byte on the same curve at the same
    slot, which made shadow/authoritative disagreement uninformative as a
    signal.  An explicit operator override is still honoured; the *default* is
    now shared.
    """
    for key in ("PUMP_CURVE_FEE_BPS", "PUMP_EXEC_WITNESS_FEE_BPS"):
        raw = os.getenv(key, "").strip()
        if raw:
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 10_000:
                return value
    return DEFAULT_CURVE_FEE_BPS


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
    # CURVE_VERDICT_20260816 additions. Appended with defaults so any existing
    # positional construction of the historical field order still compiles.
    verdict: str = UNIT_INTEGRITY_FAILURE
    realised_impact_bps: int = 10_000
    reserve_shortfall_raw: int = 0

    @property
    def executable(self) -> bool:
        return self.verdict in EXECUTABLE_VERDICTS

    @property
    def disputed(self) -> bool:
        """True when the curve produced a number that must not become a price."""
        return self.verdict == RESERVE_COVERAGE_IMPOSSIBLE


def _refusal(
    verdict: str,
    reason: str,
    raw_token_in: int,
    *,
    reserve_bounded: bool = False,
    theoretical: int = 0,
    payable: int = 0,
    net: int = 0,
    marginal: int = 0,
    coverage_bps: int = 0,
    curve_impact_bps: int = 0,
    realised_impact_bps: int = 10_000,
    shortfall: int = 0,
) -> SellQuote:
    """Build a typed non-executable quote. `ok` is False for every refusal."""
    return SellQuote(
        ok=False,
        reason=reason,
        raw_token_in=raw_token_in,
        theoretical_gross_quote_raw=theoretical,
        payable_gross_quote_raw=payable,
        fee_quote_raw=max(0, payable - net) if payable else 0,
        net_quote_raw=net,
        marginal_quote_raw=marginal,
        curve_impact_bps=curve_impact_bps,
        reserve_bounded=reserve_bounded,
        real_reserve_coverage_bps=coverage_bps,
        verdict=verdict,
        realised_impact_bps=realised_impact_bps,
        reserve_shortfall_raw=shortfall,
    )


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
    """Exact-quantity sell simulation for a pre-grad Pump curve, typed.

    Returns exactly one verdict:

    CURVE_EXECUTABLE
        The constant-product result is fully covered by real quote reserves and
        survives the fee. `net_quote_raw > 0` and may be priced.

    CURVE_ZERO_RECOVERY
        Fully covered, but the fee consumes the entire proceeds. A genuine
        dust-sized holding. Honest zero recovery -- not a price, not a bug.

    RESERVE_COVERAGE_IMPOSSIBLE
        Real quote reserves cannot cover the constant-product result. On a
        pre-grad curve that is arithmetically impossible for a quantity which
        was actually bought from this curve, so the *quantity* is refuted, not
        the market. This is the position-5788 defect. Never priced.

    CURVE_INAPPLICABLE_MIGRATED
        `complete=1`. Liquidity has moved to PumpSwap; this formula no longer
        describes the venue. Unavailable, not zero.

    UNIT_INTEGRITY_FAILURE
        Inputs or intermediate magnitudes are not internally coherent.
        Unavailable, not zero.
    """
    if raw_token_in <= 0:
        return _refusal(UNIT_INTEGRITY_FAILURE, "ZERO_QUANTITY", raw_token_in)
    if not 0 <= fee_bps <= 10_000:
        return _refusal(UNIT_INTEGRITY_FAILURE, "INVALID_FEE_BPS", raw_token_in)
    if not curve.core_valid:
        return _refusal(UNIT_INTEGRITY_FAILURE, "CURVE_CORE_INVALID", raw_token_in)
    if curve.complete:
        # A graduated curve is not a zero-value venue; it is the wrong venue.
        return _refusal(
            CURVE_INAPPLICABLE_MIGRATED, "CURVE_COMPLETE_HISTORICAL_ONLY", raw_token_in
        )

    vtok = curve.virtual_token_reserves
    vquote = curve.virtual_quote_reserves
    theoretical = (vquote * raw_token_in) // (vtok + raw_token_in)
    marginal = (vquote * raw_token_in) // vtok
    coverage_bps = (
        min(10_000, curve.real_quote_reserves * 10_000 // theoretical)
        if theoretical > 0 else 0
    )
    impact_bps = ((marginal - theoretical) * 10_000 // marginal) if marginal > 0 else 0

    if curve.real_quote_reserves <= 0:
        # The curve owes something and holds nothing. Same class of refutation
        # as a binding clamp. The old code returned this untyped with ok=False
        # and reserve_bounded=True even when theoretical was itself zero.
        return _refusal(
            RESERVE_COVERAGE_IMPOSSIBLE if theoretical > 0 else CURVE_ZERO_RECOVERY,
            "NO_REAL_QUOTE_RESERVES",
            raw_token_in,
            reserve_bounded=theoretical > 0,
            theoretical=theoretical, payable=0, marginal=marginal,
            coverage_bps=0, curve_impact_bps=impact_bps,
            realised_impact_bps=10_000, shortfall=theoretical,
        )

    payable = min(theoretical, curve.real_quote_reserves)
    shortfall = theoretical - payable
    reserve_bounded = shortfall > 0
    realised_impact_bps = (
        ((marginal - payable) * 10_000 // marginal) if marginal > 0 else 10_000
    )

    # ── Unit integrity ───────────────────────────────────────────────────────
    # Magnitudes must satisfy 0 <= payable <= theoretical <= marginal. A
    # violation means a decode, decimals or lamport-normalisation fault
    # upstream, and no number derived from it may be used for anything.
    if not (0 <= payable <= theoretical <= marginal):
        return _refusal(
            UNIT_INTEGRITY_FAILURE, "MAGNITUDE_ORDER_VIOLATION", raw_token_in,
            theoretical=theoretical, payable=payable, marginal=marginal,
            coverage_bps=coverage_bps, curve_impact_bps=impact_bps,
        )

    if reserve_bounded:
        # ── THE POSITION-5788 DEFECT ──────────────────────────────────────────
        # Refuse. Do not quote the reserve balance as if it were proceeds.
        return _refusal(
            RESERVE_COVERAGE_IMPOSSIBLE, "RESERVE_COVERAGE_IMPOSSIBLE", raw_token_in,
            reserve_bounded=True,
            theoretical=theoretical, payable=payable, marginal=marginal,
            coverage_bps=coverage_bps, curve_impact_bps=impact_bps,
            realised_impact_bps=realised_impact_bps, shortfall=shortfall,
        )

    fee = (payable * fee_bps) // 10_000
    net = payable - fee
    if net < 0:
        return _refusal(
            UNIT_INTEGRITY_FAILURE, "NEGATIVE_NET_PROCEEDS", raw_token_in,
            theoretical=theoretical, payable=payable, marginal=marginal,
            coverage_bps=coverage_bps, curve_impact_bps=impact_bps,
        )
    if net == 0:
        # Fully covered; the fee ate it. Genuine zero recovery on a dust
        # holding. Typed, and still not a price.
        return _refusal(
            CURVE_ZERO_RECOVERY, "ZERO_NET_PROCEEDS", raw_token_in,
            theoretical=theoretical, payable=payable, net=0, marginal=marginal,
            coverage_bps=coverage_bps, curve_impact_bps=impact_bps,
            realised_impact_bps=realised_impact_bps, shortfall=0,
        )

    return SellQuote(
        ok=True,
        reason="OK",
        raw_token_in=raw_token_in,
        theoretical_gross_quote_raw=theoretical,
        payable_gross_quote_raw=payable,
        fee_quote_raw=fee,
        net_quote_raw=net,
        marginal_quote_raw=marginal,
        curve_impact_bps=impact_bps,
        reserve_bounded=False,
        real_reserve_coverage_bps=coverage_bps,
        verdict=CURVE_EXECUTABLE,
        realised_impact_bps=realised_impact_bps,
        reserve_shortfall_raw=0,
    )


def _price_from_net(
    net_quote_raw: int, raw_token_in: int, token_decimals: int, sol_usd: float
) -> Optional[float]:
    if raw_token_in <= 0 or token_decimals < 0 or sol_usd <= 0:
        return None
    whole_tokens = raw_token_in / (10 ** token_decimals)
    if whole_tokens <= 0:
        return None
    return (net_quote_raw / LAMPORTS_PER_SOL) * sol_usd / whole_tokens


def unit_price_usd_from_sol_quote(
    quote: SellQuote,
    raw_token_in: int,
    token_decimals: int,
    sol_usd: float,
) -> Optional[float]:
    """Display USD price for an EXECUTABLE legacy/SOL-quoted Pump curve.

    Returns None for every non-executable verdict. An unavailable, migrated,
    disputed or zero-recovery state must reach the caller as absent data; it
    must never arrive as the number 0.0, which is indistinguishable at the
    call site from a real -100% mark.
    """
    if not quote.executable:
        return None
    return _price_from_net(quote.net_quote_raw, raw_token_in, token_decimals, sol_usd)


def diagnostic_price_usd(
    quote: SellQuote,
    raw_token_in: int,
    token_decimals: int,
    sol_usd: float,
) -> Optional[float]:
    """Forensic-only USD value of a quote's net proceeds, executable or not.

    This exists so a refused quote can be *shown* to an operator alongside its
    verdict. It has no authority and must never be written to a column, field
    or payload whose name asserts executability.
    """
    if quote.verdict == UNIT_INTEGRITY_FAILURE:
        return None
    return _price_from_net(quote.net_quote_raw, raw_token_in, token_decimals, sol_usd)
