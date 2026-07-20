"""
Sentinuity Smart Exit Policy — sign-off service module.

Drop into:
    services/smart_exit_policy.py

Purpose:
    Decision helper for partial exits and runner holds.

This file does NOT execute swaps. It returns decisions your execution engine can consume later.
"""

from __future__ import annotations

from typing import Any, Dict


def smart_exit_decision(position: Dict[str, Any]) -> Dict[str, Any]:
    """
    Input expected keys, best-effort:
        pnl_pct: current PnL percentage, e.g. 80 = +80%
        peak_pnl_pct: highest seen PnL percentage
        is_runner: bool
        tp1_done: bool
        tp2_done: bool

    Output:
        {"action": "HOLD"|"SELL_PARTIAL"|"SELL_ALL", "fraction": 0.0-1.0, "reason": "..."}
    """
    pnl = _num(position.get("pnl_pct"), 0.0)
    peak = max(_num(position.get("peak_pnl_pct"), pnl), pnl)
    is_runner = bool(position.get("is_runner", False))
    tp1_done = bool(position.get("tp1_done", False))
    tp2_done = bool(position.get("tp2_done", False))

    # First secure profit, then let a moonbag breathe.
    if pnl >= 80 and not tp1_done:
        return {"action": "SELL_PARTIAL", "fraction": 0.30, "reason": "TP1 +80% secure 30%"}

    if pnl >= 150 and not tp2_done:
        return {"action": "SELL_PARTIAL", "fraction": 0.30, "reason": "TP2 +150% secure another 30%"}

    # Runner logic: do not kill a strong token just because normal TP fired.
    if is_runner and pnl > 0:
        drawdown_from_peak = peak - pnl
        if peak >= 200 and drawdown_from_peak >= 55:
            return {"action": "SELL_ALL", "fraction": 1.0, "reason": "runner peak drawdown lock profit"}
        return {"action": "HOLD", "fraction": 0.0, "reason": "runner hold"}

    # Standard trailing protection.
    if peak >= 80 and pnl <= peak * 0.70:
        return {"action": "SELL_ALL", "fraction": 1.0, "reason": "30% giveback from peak"}

    # Loss protection should remain controlled elsewhere too.
    if pnl <= -25:
        return {"action": "SELL_ALL", "fraction": 1.0, "reason": "hard stop -25%"}

    return {"action": "HOLD", "fraction": 0.0, "reason": "no exit condition"}


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


if __name__ == "__main__":
    samples = [
        {"pnl_pct": 85, "peak_pnl_pct": 90, "is_runner": False},
        {"pnl_pct": 220, "peak_pnl_pct": 260, "is_runner": True, "tp1_done": True, "tp2_done": True},
        {"pnl_pct": 120, "peak_pnl_pct": 200, "is_runner": False, "tp1_done": True},
    ]
    for s in samples:
        print(s, "=>", smart_exit_decision(s))
