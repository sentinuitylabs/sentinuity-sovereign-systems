"""
services/position_lifecycle_intelligence.py
============================================
Position Lifecycle Intelligence (PLI) — advisory layer for open positions.

Returns a recommended action (HOLD / SCALE_IN / PARTIAL_PROFIT / EXIT)
and optional new trailing stop.  NEVER closes a position itself — it only
returns a recommendation that execution_engine acts on.

Design contract:
  - Never raises.  Returns {"action": "HOLD"} on any error.
  - Never reads from DB directly — receives state via arguments.
  - Uses only values that execution_engine already has in scope.
  - Fail-silent: if any calculation errors the result is HOLD.

Called by: execution_engine.py inside the position-monitoring loop.
"""
from __future__ import annotations

import logging

log = logging.getLogger("pli")

# ── Regime multipliers ────────────────────────────────────────────────────────
_REGIME_MULTIPLIER = {
    "BULL":    1.10,
    "BEAR":    0.85,
    "NEUTRAL": 1.00,
    "RISK_ON": 1.05,
    "RISK_OFF": 0.90,
}


def get_lifecycle_action(
    position: dict,
    current_alpha: float,
    peak_alpha: float,
    substrate_state: str = "NEUTRAL",
    volatility: float = 1.0,
) -> dict:
    """
    Compute the recommended lifecycle action for an open position.

    Args:
        position       : open paper_positions row as a dict
        current_alpha  : current confidence/alpha score (0-1)
        peak_alpha     : highest alpha seen since open (0-1)
        substrate_state: macro regime string from SUBSTRATE_MACRO_REGIME config
        volatility     : volatility metric (1.0 = baseline; >1 = elevated)

    Returns dict with keys:
        action          : "HOLD" | "SCALE_IN" | "PARTIAL_PROFIT" | "EXIT"
        size_change_pct : float — percentage of position to add/remove (0-100)
        new_trailing_stop: float | None — new trailing stop price if applicable
        reason          : str — human-readable rationale
    """
    _default = {"action": "HOLD", "size_change_pct": 0.0,
                "new_trailing_stop": None, "reason": "default_hold"}
    try:
        entry_price  = float(position.get("entry_price")       or 0)
        current_price = float(position.get("last_price")       or 0)
        pos_size_usd  = float(position.get("position_size_usd") or 0)
        hold_s        = float(position.get("hold_seconds", 0)  or 0)
        pnl_pct       = float(position.get("unrealized_pnl_pct") or
                              (((current_price - entry_price) / entry_price * 100)
                               if entry_price > 0 and current_price > 0 else 0))

        # Safety: bail on bad inputs
        if entry_price <= 0 or pos_size_usd <= 0:
            return _default

        # Normalise inputs
        current_alpha = float(current_alpha or 0)
        peak_alpha    = float(peak_alpha or current_alpha)
        volatility    = max(0.1, float(volatility or 1.0))
        regime_mult   = _REGIME_MULTIPLIER.get(str(substrate_state).upper(), 1.00)

        # ── Alpha decay ratio ─────────────────────────────────────────────────
        alpha_decay = (peak_alpha - current_alpha) / peak_alpha if peak_alpha > 0 else 0.0

        # ── Decision tree ─────────────────────────────────────────────────────

        # EXIT: severe alpha collapse (>40%) AND losing trade
        if alpha_decay > 0.40 and pnl_pct < -2.0:
            return {
                "action": "EXIT",
                "size_change_pct": 100.0,
                "new_trailing_stop": None,
                "reason": f"alpha_decay={alpha_decay:.2f} pnl={pnl_pct:.1f}%",
            }

        # PARTIAL_PROFIT: strong gain + alpha still healthy + low volatility
        if pnl_pct >= 15.0 and current_alpha >= 0.70 and volatility < 1.5 and regime_mult >= 1.0:
            harvest_pct = min(50.0, round(pnl_pct * 0.5, 1))
            new_stop = current_price * (1 - 0.08)  # lock in 8% trail from current
            return {
                "action": "PARTIAL_PROFIT",
                "size_change_pct": harvest_pct,
                "new_trailing_stop": round(new_stop, 12),
                "reason": f"partial_harvest pnl={pnl_pct:.1f}% alpha={current_alpha:.2f}",
            }

        # SCALE_IN: alpha improving, early in hold, not yet past TP, low volatility
        alpha_rising = current_alpha > peak_alpha * 0.95  # still near peak
        early_hold   = hold_s < 90
        if (alpha_rising and early_hold and pnl_pct > 0
                and pnl_pct < 10.0 and volatility < 1.3
                and regime_mult >= 1.0 and pos_size_usd < 200.0):
            scale_pct = round(min(25.0, 10.0 * regime_mult), 1)
            return {
                "action": "SCALE_IN",
                "size_change_pct": scale_pct,
                "new_trailing_stop": None,
                "reason": f"scale_in alpha={current_alpha:.2f} hold={hold_s:.0f}s",
            }

        # Tighten trailing stop in high volatility
        if volatility > 2.0 and pnl_pct > 5.0 and current_price > 0:
            tight_stop = current_price * (1 - 0.05)  # tighten to 5% trail
            return {
                "action": "HOLD",
                "size_change_pct": 0.0,
                "new_trailing_stop": round(tight_stop, 12),
                "reason": f"trail_tighten volatility={volatility:.2f}",
            }

        return _default

    except Exception as _pli_err:
        log.debug("PLI calculation error: %s", _pli_err)
        return _default
