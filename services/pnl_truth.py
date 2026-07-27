"""Canonical realised-PnL read contract.

This module never changes fills or execution. It prevents capped/synthetic paper
accounting and favourable excursion fields from being consumed as realised truth.
"""
from __future__ import annotations
from typing import Any, Mapping


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(row, Mapping):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def canonical_realized_pnl_usd(row: Any) -> float:
    """Return observed realised USD truth when a paper stop was capped.

    `raw_realized_pnl_usd` is authoritative only for rows explicitly labelled
    CAPPED_STOP_FLOOR. All other rows retain their normal realised value.
    """
    status = str(_get(row, "pnl_integrity_status", "") or "").upper()
    if status == "CAPPED_STOP_FLOOR":
        raw = _get(row, "raw_realized_pnl_usd")
        if raw is not None:
            return _float(raw)
    trusted = _get(row, "trusted_realized_pnl_usd")
    realized = _get(row, "realized_pnl_usd")
    return _float(realized if realized is not None else trusted)


def canonical_realized_pnl_pct(row: Any) -> float:
    """Return observed realised percentage truth when available."""
    status = str(_get(row, "pnl_integrity_status", "") or "").upper()
    if status == "CAPPED_STOP_FLOOR":
        raw = _get(row, "raw_realized_pnl_pct")
        if raw is None:
            raw = _get(row, "raw_pnl_pct_preclamp")
        if raw is not None:
            return _float(raw)
    trusted = _get(row, "trusted_realized_pnl_pct")
    realized = _get(row, "realized_pnl_pct")
    if realized is None:
        realized = _get(row, "pnl_pct")
    return _float(realized if realized is not None else trusted)
