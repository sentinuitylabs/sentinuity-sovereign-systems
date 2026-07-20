#!/usr/bin/env python3
"""Canonical realised-outcome taxonomy for Sentinuity.

Capital doctrine:
- Only realised PnL may confirm a funded pattern.
- Peak/MFE is telemetry only and can never override a realised loss.
- G is deliberately positive-neutral: it preserves context but does not confirm.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

SUCCESS_OUTCOMES = frozenset({"R", "P", "S"})
HARD_RESET_OUTCOMES = frozenset({"L", "H", "X"})
NEUTRAL_OUTCOMES = frozenset({"G", "B"})

@dataclass(frozen=True)
class OutcomeBand:
    code: str
    label: str
    lower: Optional[float]
    upper: Optional[float]
    confirms: bool
    resets: bool

BANDS = (
    OutcomeBand("R", "RUNNER", 100.0, None, True, False),
    OutcomeBand("P", "PRIME", 75.0, 100.0, True, False),
    OutcomeBand("S", "SUCCESS", 25.0, 75.0, True, False),
    OutcomeBand("G", "POSITIVE_NEUTRAL", 5.0, 25.0, False, False),
    OutcomeBand("B", "BALANCE", -5.0, 5.0, False, False),
    OutcomeBand("L", "LIGHT_LOSS", -10.0, -5.0, False, True),
    OutcomeBand("H", "HEAVY_LOSS", -30.0, -10.0, False, True),
    OutcomeBand("X", "CRITICAL_LOSS", None, -30.0, False, True),
)

def classify_realised(realized_pct: float) -> str:
    """Return R/P/S/G/B/L/H/X using realised PnL only.

    Boundary doctrine:
      R >= 100
      P >= 75 and < 100
      S >= 25 and < 75
      G > 5 and < 25
      B >= -5 and <= 5
      L > -10 and < -5
      H > -30 and <= -10
      X <= -30
    """
    r = float(realized_pct or 0.0)
    if r >= 100.0: return "R"
    if r >= 75.0: return "P"
    if r >= 25.0: return "S"
    if r > 5.0: return "G"
    if r >= -5.0: return "B"
    if r > -10.0: return "L"
    if r > -30.0: return "H"
    return "X"

def is_success(code: str) -> bool:
    return str(code or "").upper() in SUCCESS_OUTCOMES

def is_reset(code: str) -> bool:
    return str(code or "").upper() in HARD_RESET_OUTCOMES
