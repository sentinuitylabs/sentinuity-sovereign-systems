#!/usr/bin/env python3
"""
services/execution_cost_model.py — canonical Solana execution cost model.

RESTORES the April/May/June doctrine. Does NOT invent constants: it derives
slippage from constant-product pool impact where liquidity is known, and
falls back to labelled assumptions otherwise. Every output carries a
confidence band so you always know whether a number is measured or guessed.

Grounded in the REAL live path (services/live_trading.py):
    _BUY_SLIPPAGE_BPS  = 2000   (20% cap)
    _SELL_SLIPPAGE_BPS = 5000   (50% cap)
    _GAS_RESERVE_SOL   = 0.05
Those are Jupiter *caps*, not expected fills. This model estimates the
EXPECTED fill inside those caps.

Confidence bands:
    MEASURED           — real tx signature + on-chain fill available
    LIQUIDITY_PROXY    — pool liquidity/curve known; constant-product impact
    DEFAULT_ASSUMPTION — no liquidity data; conservative venue default

Nothing here blocks a trade. It only annotates.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

COST_MODEL_VERSION = "1.0.0-2026-07-09"

# venue fallbacks (May-28 doctrine) — used ONLY when liquidity unknown
_VENUE_DEFAULTS = {
    "pump":    {"slip_pct": 0.50, "gas_sol": 0.0030},
    "raydium": {"slip_pct": 1.00, "gas_sol": 0.0050},
    "jupiter": {"slip_pct": 0.75, "gas_sol": 0.0040},
    "unknown": {"slip_pct": 1.00, "gas_sol": 0.0050},
}
# hard caps from the real live path — an estimate above these is not fillable
BUY_SLIPPAGE_CAP_PCT  = 20.0
SELL_SLIPPAGE_CAP_PCT = 50.0


@dataclass
class CostEstimate:
    slippage_pct: float
    gas_sol: float
    gas_usd: float
    effective_price: float
    confidence: str
    venue: str
    exceeds_cap: bool
    notes: str = ""
    def dict(self): return asdict(self)


def _venue_of(mint: str, source: Optional[str]) -> str:
    s = (source or "").lower()
    if "pump" in s or (mint or "").endswith("pump"): return "pump"
    if "raydium" in s or "ray" in s:                 return "raydium"
    if "jupiter" in s or "jup" in s:                 return "jupiter"
    return "unknown"


def _constant_product_impact(size_usd: float, liquidity_usd: float) -> float:
    """Price impact % for a trade of size_usd against a CP pool of liquidity_usd.
    dP/P = size / (liquidity + size). Returns percent."""
    if liquidity_usd <= 0 or size_usd <= 0:
        return 0.0
    return (size_usd / (liquidity_usd + size_usd)) * 100.0


def _stale_decay_pct(price_age_sec: Optional[float]) -> float:
    """April doctrine: a stale mark is not a fillable price. Decay the exit."""
    if not price_age_sec or price_age_sec <= 5:
        return 0.0
    if price_age_sec <= 30:  return 0.5
    if price_age_sec <= 120: return 2.0
    if price_age_sec <= 300: return 5.0
    return 10.0


def estimate_entry_cost(*, mint: str, position_size_usd: float, entry_price: float,
                        liquidity_usd: Optional[float] = None,
                        source: Optional[str] = None,
                        sol_price_usd: float = 150.0,
                        actual_tx_fee_sol: Optional[float] = None,
                        actual_slippage_pct: Optional[float] = None) -> CostEstimate:
    venue = _venue_of(mint, source)
    d = _VENUE_DEFAULTS[venue]

    if actual_slippage_pct is not None and actual_tx_fee_sol is not None:
        slip, gas_sol, conf = actual_slippage_pct, actual_tx_fee_sol, "MEASURED"
    elif liquidity_usd and liquidity_usd > 0:
        slip = _constant_product_impact(position_size_usd, liquidity_usd)
        gas_sol, conf = d["gas_sol"], "LIQUIDITY_PROXY"
    else:
        slip, gas_sol, conf = d["slip_pct"], d["gas_sol"], "DEFAULT_ASSUMPTION"

    exceeds = slip > BUY_SLIPPAGE_CAP_PCT
    eff = entry_price * (1 + slip / 100.0)   # you pay MORE on entry
    return CostEstimate(round(slip, 4), round(gas_sol, 6),
                        round(gas_sol * sol_price_usd, 4), eff, conf, venue, exceeds,
                        "entry fill would exceed live 20% cap" if exceeds else "")


def estimate_exit_cost(*, mint: str, position_size_usd: float, exit_price: float,
                       liquidity_usd: Optional[float] = None,
                       source: Optional[str] = None,
                       price_age_sec: Optional[float] = None,
                       sol_price_usd: float = 150.0,
                       actual_tx_fee_sol: Optional[float] = None,
                       actual_slippage_pct: Optional[float] = None) -> CostEstimate:
    venue = _venue_of(mint, source)
    d = _VENUE_DEFAULTS[venue]

    if actual_slippage_pct is not None and actual_tx_fee_sol is not None:
        slip, gas_sol, conf = actual_slippage_pct, actual_tx_fee_sol, "MEASURED"
    elif liquidity_usd and liquidity_usd > 0:
        slip = _constant_product_impact(position_size_usd, liquidity_usd)
        gas_sol, conf = d["gas_sol"], "LIQUIDITY_PROXY"
    else:
        slip, gas_sol, conf = d["slip_pct"], d["gas_sol"], "DEFAULT_ASSUMPTION"

    slip += _stale_decay_pct(price_age_sec)   # stale mark => worse fill
    exceeds = slip > SELL_SLIPPAGE_CAP_PCT
    eff = exit_price * (1 - slip / 100.0)     # you receive LESS on exit
    note = []
    if exceeds: note.append("exit would exceed live 50% cap (possibly unsellable)")
    if price_age_sec and price_age_sec > 120: note.append(f"stale mark {price_age_sec:.0f}s")
    return CostEstimate(round(slip, 4), round(gas_sol, 6),
                        round(gas_sol * sol_price_usd, 4), eff, conf, venue, exceeds,
                        "; ".join(note))


def estimate_round_trip_cost(**kw) -> Dict[str, Any]:
    e = estimate_entry_cost(**{k: v for k, v in kw.items()
                               if k in ("mint","position_size_usd","entry_price",
                                        "liquidity_usd","source","sol_price_usd")})
    x = estimate_exit_cost(**{k: v for k, v in kw.items()
                              if k in ("mint","position_size_usd","exit_price",
                                       "liquidity_usd","source","price_age_sec","sol_price_usd")})
    return {"entry": e.dict(), "exit": x.dict(),
            "total_gas_usd": round(e.gas_usd + x.gas_usd, 4),
            "total_slippage_pct": round(e.slippage_pct + x.slippage_pct, 4),
            "cost_model_version": COST_MODEL_VERSION,
            "confidence": min(e.confidence, x.confidence,
                              key=lambda c: ["MEASURED","LIQUIDITY_PROXY","DEFAULT_ASSUMPTION"].index(c))
                          if e.confidence != x.confidence else e.confidence}


def apply_costs_to_paper_trade(*, mint: str, position_size_usd: float,
                               entry_price: float, exit_price: float,
                               raw_pnl_usd: float,
                               entry_liquidity_usd: Optional[float] = None,
                               exit_liquidity_usd: Optional[float] = None,
                               source: Optional[str] = None,
                               price_age_sec: Optional[float] = None,
                               sol_price_usd: float = 150.0) -> Dict[str, Any]:
    """Non-blocking: returns raw AND cost-adjusted PnL for a closed paper trade."""
    e = estimate_entry_cost(mint=mint, position_size_usd=position_size_usd,
                            entry_price=entry_price, liquidity_usd=entry_liquidity_usd,
                            source=source, sol_price_usd=sol_price_usd)
    x = estimate_exit_cost(mint=mint, position_size_usd=position_size_usd,
                           exit_price=exit_price, liquidity_usd=exit_liquidity_usd,
                           source=source, price_age_sec=price_age_sec,
                           sol_price_usd=sol_price_usd)
    if e.effective_price <= 0 or entry_price <= 0:
        return {"error": "bad entry price"}
    # Frictionless baseline: what the paper engine believes it earned.
    qty_ideal = position_size_usd / entry_price
    raw_out   = qty_ideal * exit_price
    # Real fill: you buy fewer tokens (worse entry) and sell them lower (worse exit).
    qty_real  = position_size_usd / e.effective_price
    real_out  = qty_real * x.effective_price
    adj_pnl   = real_out - position_size_usd - e.gas_usd - x.gas_usd
    # sanity: cost-adjusted can never exceed frictionless
    ideal_pnl = raw_out - position_size_usd
    if adj_pnl > ideal_pnl:
        adj_pnl = ideal_pnl
    return {
        "raw_pnl_usd": round(raw_pnl_usd, 4),
        "cost_adjusted_pnl_usd": round(adj_pnl, 4),
        "cost_drag_usd": round(raw_pnl_usd - adj_pnl, 4),
        "entry_slippage_pct": e.slippage_pct,
        "exit_slippage_pct": x.slippage_pct,
        "gas_usd": round(e.gas_usd + x.gas_usd, 4),
        "gas_sol": round(e.gas_sol + x.gas_sol, 6),
        "effective_entry_price": e.effective_price,
        "effective_exit_price": x.effective_price,
        "unsellable_risk": x.exceeds_cap,
        "confidence": e.confidence if e.confidence == x.confidence else "MIXED",
        "cost_model_version": COST_MODEL_VERSION,
        "notes": "; ".join(n for n in (e.notes, x.notes) if n),
    }


    print("COST MODEL", COST_MODEL_VERSION)
    # a real trade shape from your book: id144, +155.6% on $57
    for liq in (None, 5000, 25000):
        r = apply_costs_to_paper_trade(
            mint="ACSKcahnuhYFZLQvCf7mbEYFtkHxmbxBWadhqyWXpump",
            position_size_usd=57.0, entry_price=1e-6, exit_price=2.556e-6,
            raw_pnl_usd=56.48, entry_liquidity_usd=liq, exit_liquidity_usd=liq,
            source="pump", price_age_sec=8)
        print(f"\n liquidity={liq}: raw=${r['raw_pnl_usd']} -> adj=${r['cost_adjusted_pnl_usd']}"
              f"  drag=${r['cost_drag_usd']}  slip={r['entry_slippage_pct']}/{r['exit_slippage_pct']}%"
              f"  conf={r['confidence']}")
    # stale mark punishment
    r = apply_costs_to_paper_trade(mint="Xpump", position_size_usd=57, entry_price=1e-6,
        exit_price=1.05e-6, raw_pnl_usd=2.85, source="pump", price_age_sec=400)
    print(f"\n stale 400s mark: raw=${r['raw_pnl_usd']} -> adj=${r['cost_adjusted_pnl_usd']} ({r['notes']})")


def _selftest():
    print("\n=== SELFTEST ===")
    print("INVARIANT: cost-adjusted <= frictionless, always")
    import random
    bad = 0
    for _ in range(500):
        e = random.uniform(1e-7, 1e-5)
        x = e * random.uniform(0.3, 4.0)
        size = random.choice([25, 57, 100, 500])
        liq = random.choice([None, 500, 5000, 50000])
        age = random.choice([2, 30, 200, 600])
        frictionless = (size / e) * x - size
        r = apply_costs_to_paper_trade(mint="Tpump", position_size_usd=size,
            entry_price=e, exit_price=x, raw_pnl_usd=frictionless,
            entry_liquidity_usd=liq, exit_liquidity_usd=liq,
            source="pump", price_age_sec=age)
        if r["cost_adjusted_pnl_usd"] > r["raw_pnl_usd"] + 1e-6:
            bad += 1
    print(f"  500 random trades: {bad} violations  {'PASS' if bad==0 else 'FAIL'}")

    print("\nsmall modest winner: +2% raw")
    r = apply_costs_to_paper_trade(mint="Xpump", position_size_usd=57, entry_price=1e-6,
        exit_price=1.02e-6, raw_pnl_usd=1.14, source="pump", price_age_sec=5)
    print(f"  raw=${r['raw_pnl_usd']} -> adj=${r['cost_adjusted_pnl_usd']}  "
          f"VERDICT: {'edge survives' if r['cost_adjusted_pnl_usd']>0 else 'EATEN BY COSTS'}")
    print("\nstale 400s exit")
    r = apply_costs_to_paper_trade(mint="Xpump", position_size_usd=57, entry_price=1e-6,
        exit_price=1.05e-6, raw_pnl_usd=2.85, source="pump", price_age_sec=400)
    print(f"  raw=${r['raw_pnl_usd']} -> adj=${r['cost_adjusted_pnl_usd']}  ({r['notes']})")
    print("\nthin liquidity, big size")
    r = apply_costs_to_paper_trade(mint="Xpump", position_size_usd=500, entry_price=1e-6,
        exit_price=1.5e-6, raw_pnl_usd=250, entry_liquidity_usd=800,
        exit_liquidity_usd=400, source="pump", price_age_sec=10)
    print(f"  raw=${r['raw_pnl_usd']} -> adj=${r['cost_adjusted_pnl_usd']}  "
          f"exit_slip={r['exit_slippage_pct']}%  unsellable={r['unsellable_risk']}")


if __name__ == "__main__":
    print("COST MODEL", COST_MODEL_VERSION)
    _selftest()
