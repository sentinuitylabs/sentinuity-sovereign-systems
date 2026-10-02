"""
services/entry_basis.py — ENTRY_BASIS_REPAIR_20261001
======================================================

Pure paper-admission entry-basis contract. No I/O, no network, no DB.

PRIMARY INVARIANT
    A paper trade must never be credited with profit produced solely because
    its booked entry basis materially predates or understates the executable
    market available at admission.

What the engine actually has at admission
    `services.price_router.get_live_liquidation_price()` returns a Jupiter
    token->SOL (or Pump-curve) quote to SELL the exact position quantity. It is
    a SELL quote. The quantity is sized as notional / proposed_entry_price, so
    when the proposed price is stale the quote is also for the wrong amount of
    tokens. It is therefore NOT a valid BUY/fill basis and is never used as one.

    A sell quote can still detect an inconsistent entry: an exact-size sell
    quote materially ABOVE the proposed entry proves the market already traded
    away from the proposed price (a real buy would have paid at least that).
    Paincoin #9618 was admitted with a sell quote +1,249% above its booked entry.

Rules (smallest safe rule set, directive section 2 A-D)
    A. A trustworthy executable BUY quote for the intended paper notional, when
       supplied, becomes the accounting basis (authority EXECUTABLE_BUY_QUOTE),
       provided it agrees with the proposed price within tolerance.
    B. A SELL quote alone never rewrites the entry. It is an inconsistency
       detector only (authority PROPOSED_SELL_CONSISTENT).
    C. A gap beyond tolerance in EITHER direction blocks the admission:
         below: PAPER_ENTRY_EXEC_GAP_BEYOND_STOP   (existing reason, unchanged)
         above: PAPER_ENTRY_ABNORMAL_ENTRY_GAP     (new; previously admitted)
       Buy-quote disagreement: PAPER_ENTRY_BASIS_INVALID.
    D. The caller persists proposed/executable prices, source, gap, timestamp
       and authority. The accounting price returned here is immutable for the
       life of the position.

Tolerance
    PAPER_ENTRY_BASIS_TOLERANCE_PCT, default = the signed hard stop (4%). It is
    clamped to <= the hard stop so configuration can only tighten it. This is
    the same contract the downside check already enforced; no new threshold is
    introduced and the 4% stop itself is untouched.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

AUTH_BUY = "EXECUTABLE_BUY_QUOTE"
AUTH_SELL_CONSISTENT = "PROPOSED_SELL_CONSISTENT"
AUTH_NONE = "NONE"

R_PASS = "PAPER_ENTRY_EXEC_TRUTH_PASS"
R_INVALID_PRICE = "PAPER_ENTRY_INVALID_ACCOUNTING_PRICE"
R_UNAVAILABLE = "PAPER_ENTRY_EXEC_TRUTH_UNAVAILABLE"
R_NO_ROUTE = "PAPER_ENTRY_NO_EXECUTABLE_EXIT_ROUTE"
R_BELOW = "PAPER_ENTRY_EXEC_GAP_BEYOND_STOP"
R_ABOVE = "PAPER_ENTRY_ABNORMAL_ENTRY_GAP"
R_BUY_INVALID = "PAPER_ENTRY_BASIS_INVALID"

DEFAULT_STOP_PCT = 4.0
DEFAULT_BUY_MAX_AGE_SEC = 15.0


def _pos_float(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f > 0.0 else 0.0


def effective_tolerance_pct(hard_stop_pct: Any, configured: Any = None) -> float:
    """Signed tolerance: defaults to the hard stop and can only tighten it."""
    try:
        stop = abs(float(hard_stop_pct))      # same abs() semantics as the previous gate
    except (TypeError, ValueError):
        stop = 0.0
    if not math.isfinite(stop) or stop <= 0.0:
        stop = DEFAULT_STOP_PCT
    stop = min(stop, DEFAULT_STOP_PCT)
    tol = _pos_float(configured)
    return min(tol, stop) if tol > 0.0 else stop


def evaluate_entry_basis(
    proposed_entry_price: Any,
    sell_quote: Optional[Mapping[str, Any]],
    hard_stop_pct: Any = DEFAULT_STOP_PCT,
    *,
    buy_quote: Optional[Mapping[str, Any]] = None,
    tolerance_pct: Any = None,
    buy_max_age_sec: float = DEFAULT_BUY_MAX_AGE_SEC,
    now: Optional[float] = None,
) -> dict:
    """Decide admission and the immutable accounting basis for one paper entry."""
    stop = effective_tolerance_pct(hard_stop_pct, None)
    tol = effective_tolerance_pct(hard_stop_pct, tolerance_pct)
    out = {
        "allow": False,
        "reason": R_UNAVAILABLE,
        "proposed_entry_price": None,
        "accounting_entry_price": None,
        "executable_entry_price": None,
        "executable_entry_source": None,
        "executable_entry_direction": None,
        "entry_basis_gap_pct": None,
        "entry_basis_timestamp": now,
        "entry_basis_authority": AUTH_NONE,
        "sell_gap_pct": None,
        "buy_gap_pct": None,
        "hard_stop_pct": stop,
        "tolerance_pct": tol,
        # legacy keys consumed by the existing engine log/persist path
        "basis_price": None, "basis_source": None, "basis_age_sec": None, "gap_pct": None,
    }
    proposed = _pos_float(proposed_entry_price)
    if proposed <= 0.0:
        out["reason"] = R_INVALID_PRICE
        return out
    out["proposed_entry_price"] = proposed

    if not isinstance(sell_quote, Mapping):
        return out
    sell_px = _pos_float(sell_quote.get("price"))
    if not bool(sell_quote.get("can_execute_exit")) or sell_px <= 0.0:
        out["reason"] = R_NO_ROUTE
        return out

    sell_gap = (sell_px - proposed) / proposed * 100.0
    sell_src = str(sell_quote.get("source") or "router_exact_position")[:96]
    out.update({
        "sell_gap_pct": sell_gap,
        "executable_entry_price": sell_px,
        "executable_entry_source": sell_src,
        "executable_entry_direction": "SELL",
        "entry_basis_gap_pct": sell_gap,
        "basis_price": sell_px, "basis_source": sell_src,
        "basis_age_sec": sell_quote.get("age_sec"), "gap_pct": sell_gap,
    })
    if sell_gap <= -stop:
        out["reason"] = R_BELOW
        return out
    if sell_gap >= tol:
        out["reason"] = R_ABOVE
        return out

    # Rule A: a trustworthy executable BUY quote for the intended notional.
    if isinstance(buy_quote, Mapping):
        buy_px = _pos_float(buy_quote.get("price"))
        age = buy_quote.get("age_sec")
        try:
            age_ok = age is not None and 0.0 <= float(age) <= float(buy_max_age_sec)
        except (TypeError, ValueError):
            age_ok = False
        trustworthy = (buy_px > 0.0 and bool(buy_quote.get("executable"))
                       and bool(buy_quote.get("notional_matches")) and age_ok)
        if trustworthy:
            buy_gap = (buy_px - proposed) / proposed * 100.0
            out["buy_gap_pct"] = buy_gap
            if abs(buy_gap) >= tol:
                out["reason"] = R_BUY_INVALID
                out["entry_basis_gap_pct"] = buy_gap
                return out
            out.update({
                "allow": True, "reason": R_PASS,
                "accounting_entry_price": buy_px,
                "executable_entry_price": buy_px,
                "executable_entry_source": str(buy_quote.get("source") or "buy_quote")[:96],
                "executable_entry_direction": "BUY",
                "entry_basis_gap_pct": buy_gap,
                "entry_basis_authority": AUTH_BUY,
            })
            return out

    # Rule B: sell quote is a detector only; the proposed price stands.
    out.update({
        "allow": True, "reason": R_PASS,
        "accounting_entry_price": proposed,
        "entry_basis_authority": AUTH_SELL_CONSISTENT,
    })
    return out


def counterfactual_admission(proposed_entry_price: Any, sell_price: Any,
                             hard_stop_pct: Any = DEFAULT_STOP_PCT) -> dict:
    """COUNTERFACTUAL helper for replays: would this historical entry pass?"""
    return evaluate_entry_basis(
        proposed_entry_price,
        {"price": sell_price, "can_execute_exit": _pos_float(sell_price) > 0.0,
         "source": "replay"},
        hard_stop_pct,
    )
