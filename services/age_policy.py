"""
services/age_policy.py
======================
PERMANENT AGE CONTRACT for Sentinuity.

Three distinct age concepts that MUST NEVER be mixed:

1. signal_age_seconds      = time since Sentinuity discovered/qualified the signal
2. token_birth_age_seconds = time since token/pair created on-chain (DexScreener pairCreatedAt)
3. price_age_seconds       = time since latest usable price update

Doctrine:
- SIGNAL_STALE_* rejections refer ONLY to signal_age_seconds (discovery freshness)
- TOKEN_TOO_OLD_* rejections refer ONLY to token_birth_age_seconds (on-chain age)
- PRICE_STALE_* rejections refer ONLY to price_age_seconds (data freshness)

Policy:
- MI/Paper: PAPER_MAX_TOKEN_BIRTH_AGE_SECONDS (default 28800 = 8 hours)
- Live:     LIVE_MAX_TOKEN_BIRTH_AGE_SECONDS  (default 1800  = 30 minutes, non-bypassable)
- Signal:   SIGNAL_MAX_AGE_MINUTES            (default 30, discovery freshness only)

This module is the single source of truth for age semantics. All other modules
must import from here rather than computing ages locally.
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any, Dict, Optional


def coerce_epoch(value: Any) -> float:
    """
    UTC-safe timestamp coercion. Handles:
    - float/int epoch seconds (preferred)
    - float/int epoch milliseconds (auto-detected, >1e12)
    - ISO datetime strings (assumed UTC)
    - None or invalid values → 0.0
    """
    if value is None:
        return 0.0
    
    # Numeric: epoch seconds or milliseconds
    try:
        f = float(value)
        if f > 1_000_000_000_000:  # milliseconds
            return f / 1000.0
        if f > 1_000_000_000:  # seconds
            return f
        return 0.0  # reject non-epoch floats
    except (TypeError, ValueError):
        pass
    
    # String: ISO datetime
    try:
        s = str(value).strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = _dt.datetime.strptime(s, fmt)
                return dt.replace(tzinfo=_dt.timezone.utc).timestamp()
            except ValueError:
                continue
    except Exception:
        pass
    
    return 0.0


def compute_age_seconds(now: float, epoch: Optional[float]) -> Optional[float]:
    """
    Compute age in seconds. Returns None if epoch is invalid/unknown.
    Never returns negative ages (clamped to 0).
    """
    if epoch is None or epoch <= 0:
        return None
    age = now - epoch
    return max(0.0, age)


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """
    Safe getter for sqlite3.Row objects (no .get() method) AND dicts.
    """
    try:
        if hasattr(row, "keys"):
            keys = list(row.keys()) if callable(row.keys) else row.keys
            if key in keys:
                return row[key]
            return default
        return row.get(key, default) if hasattr(row, "get") else default
    except Exception:
        return default


def classify_ages(
    row: Any,
    metrics: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """
    Classify all three ages from a market_snapshots row + optional metrics dict.
    
    Returns dict with:
        signal_discovered_at:     epoch when Sentinuity first saw the signal
        signal_age_seconds:       age of signal discovery (None if unknown)
        token_birth_at:           epoch when token created on-chain
        token_birth_age_seconds:  age of token on blockchain (None if unknown)
        price_updated_at:         epoch of latest price update
        price_age_seconds:        age of price data (None if unknown)
    """
    if now is None:
        now = time.time()
    if metrics is None:
        metrics = {}
    
    # ── SIGNAL AGE: When did WE discover this? ─────────────────────────────
    # Priority order: signal_discovered_at > first_seen_at > created_at > operational_ts
    # NEVER use pairCreatedAt or token_age_seconds for this!
    signal_discovered_at = coerce_epoch(
        _row_get(row, "signal_discovered_at")
        or (metrics.get("signal_discovered_at") if metrics else None)
        or _row_get(row, "first_seen_at")
        or _row_get(row, "created_at")
        or _row_get(row, "operational_ts")
        or _row_get(row, "timestamp")
    )
    signal_age_seconds = compute_age_seconds(now, signal_discovered_at)
    if signal_age_seconds is None:
        # Fallback for rows where only the computed age was persisted.
        legacy_signal_age = _row_get(row, "signal_age_seconds")
        if legacy_signal_age is None and metrics:
            legacy_signal_age = metrics.get("signal_age_seconds")
        try:
            if legacy_signal_age is not None:
                signal_age_seconds = float(legacy_signal_age)
        except (TypeError, ValueError):
            signal_age_seconds = None
    
    # ── TOKEN BIRTH AGE: When was this token CREATED on-chain? ─────────────
    # Priority: stored token_birth_at > metrics pairCreatedAt > token_age_seconds (legacy)
    token_birth_at = coerce_epoch(
        _row_get(row, "token_birth_at")
        or (metrics.get("token_birth_at") if metrics else None)
        or (metrics.get("pair_created_at") if metrics else None)
    )
    
    # Compute from epoch if available, else use stored age column (legacy)
    token_birth_age_seconds = compute_age_seconds(now, token_birth_at)
    if token_birth_age_seconds is None:
        # Fallback to legacy token_age_seconds column (still represents birth age)
        legacy_age = _row_get(row, "token_birth_age_seconds") or _row_get(row, "token_age_seconds")
        if legacy_age is None and metrics:
            legacy_age = metrics.get("token_birth_age_seconds") or metrics.get("token_age_seconds")
        try:
            if legacy_age is not None:
                token_birth_age_seconds = float(legacy_age)
        except (TypeError, ValueError):
            token_birth_age_seconds = None
    
    # ── PRICE AGE: When was the latest price update? ───────────────────────
    price_updated_at = coerce_epoch(
        _row_get(row, "price_updated_at")
        or (metrics.get("price_updated_at") if metrics else None)
    )
    price_age_seconds = compute_age_seconds(now, price_updated_at)
    if price_age_seconds is None:
        legacy_price_age = _row_get(row, "price_age_seconds")
        if legacy_price_age is None and metrics:
            legacy_price_age = metrics.get("price_age_seconds")
        try:
            if legacy_price_age is not None:
                price_age_seconds = float(legacy_price_age)
        except (TypeError, ValueError):
            price_age_seconds = None
    
    return {
        "signal_discovered_at":    signal_discovered_at if signal_discovered_at > 0 else None,
        "signal_age_seconds":      signal_age_seconds,
        "token_birth_at":          token_birth_at if token_birth_at > 0 else None,
        "token_birth_age_seconds": token_birth_age_seconds,
        "price_updated_at":        price_updated_at if price_updated_at > 0 else None,
        "price_age_seconds":       price_age_seconds,
    }


# ── REJECTION REASON CONSTANTS ─────────────────────────────────────────────
# Use these to ensure consistent labeling across the codebase.

def signal_stale_reason(signal_age_seconds: float) -> str:
    """Format: SIGNAL_STALE_123s — discovery age too old"""
    return f"SIGNAL_STALE_{signal_age_seconds:.0f}s"


def token_too_old_reason(token_birth_age_seconds: float) -> str:
    """Format: TOKEN_TOO_OLD_123s — on-chain birth age too old"""
    return f"TOKEN_TOO_OLD_{token_birth_age_seconds:.0f}s"


def price_stale_reason(price_age_seconds: float) -> str:
    """Format: PRICE_STALE_123s — price update too old"""
    return f"PRICE_STALE_{price_age_seconds:.0f}s"


def token_age_unknown_reason() -> str:
    """Format: TOKEN_AGE_UNKNOWN — cannot determine on-chain birth age"""
    return "TOKEN_AGE_UNKNOWN"
