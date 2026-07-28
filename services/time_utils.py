# -*- coding: utf-8 -*-
"""
services/time_utils.py
======================
Shared UTC-safe epoch coercion for all Sentinuity services.

RULE: market_snapshots.timestamp is an ISO text column (e.g. "2026-05-25 02:54:10").
      It must NEVER appear inside a numeric SQL COALESCE/MAX freshness expression,
      because SQLite text-vs-integer comparison is undefined for freshness gates and
      causes every row to look ancient (false STALE_PRELAUNCH / VETO_SIGNAL_TOO_OLD).

      All datetime.strptime() calls on DB strings must treat naive timestamps as UTC,
      never local time (bot may run in Melbourne, UTC+10, causing 36000s false-age).

PUBLIC API
----------
coerce_epoch_utc(value, default=0.0) -> float
    Accepts epoch int/float/string OR ISO datetime strings.
    Naive strings are treated as UTC (not local time).
    Returns epoch seconds as float. Never raises.

NUMERIC_TS_COLS
    Ordered list of market_snapshots columns that are guaranteed numeric epoch.
    Use this to build COALESCE expressions — never include 'timestamp'.

numeric_coalesce_sql(cols_present, fallback="0")
    Returns a SQL COALESCE expression using only numeric epoch columns that
    are present in `cols_present`.  Safe to embed directly in WHERE / ORDER BY.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ["coerce_epoch_utc", "NUMERIC_TS_COLS", "numeric_coalesce_sql"]

# Ordered preference for numeric epoch timestamp columns in market_snapshots.
# 'timestamp' is intentionally absent — it stores ISO text.
NUMERIC_TS_COLS: tuple[str, ...] = (
    "price_updated_at",
    "updated_at",
    "created_at",
    "first_seen_at",
    "qualified_at",
    "latched_at",
)


def coerce_epoch_utc(value: Any, default: float = 0.0) -> float:
    """
    Convert ``value`` to a UTC epoch float (seconds).

    Accepts:
      - None / empty string       → ``default``
      - int / float               → returned as epoch seconds
      - numeric string            → parsed as epoch seconds
      - millisecond epoch (>1e10) → divided by 1000 automatically
      - ISO datetime string       → parsed as UTC (naive strings assumed UTC,
                                    NOT local Melbourne time)

    Never raises; returns ``default`` on any failure.

    Millisecond detection threshold: any numeric value > 10_000_000_000
    (i.e. after year 2286 in seconds) is treated as milliseconds.
    Current epoch seconds are ~1.78e9, so this is safe for all realistic dates.
    """
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        return x

    s = str(value).strip()
    if not s:
        return float(default)

    # Fast path: plain numeric string
    try:
        x = float(s)
        if x > 10_000_000_000:
            x /= 1000.0
        return x
    except (ValueError, TypeError):
        pass

    # ISO datetime string — treat naive as UTC
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            # DB stores naive ISO strings in UTC.
            # Do NOT use .timestamp() on a naive dt — that applies local TZ.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        pass

    # Fallback for non-fromisoformat formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            # Force UTC — never local
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue

    return float(default)


def numeric_coalesce_sql(
    cols_present: set[str] | frozenset[str],
    fallback: str = "0",
    extra_cols: tuple[str, ...] = (),
) -> str:
    """
    Return a SQL COALESCE expression using only numeric epoch columns.

    Parameters
    ----------
    cols_present:
        Set of column names that actually exist in the table (from PRAGMA).
    fallback:
        Final fallback value if all columns are NULL (default "0").
    extra_cols:
        Additional numeric columns to include before the NUMERIC_TS_COLS
        order (e.g. service-specific columns).  'timestamp' is silently
        filtered out even if passed here.

    Returns a string like:
        COALESCE(price_updated_at, updated_at, created_at, first_seen_at, 0)
    """
    ordered = [c for c in (*extra_cols, *NUMERIC_TS_COLS)
               if c != "timestamp" and c in cols_present]
    if not ordered:
        return fallback
    parts = ", ".join(ordered) + f", {fallback}"
    return f"COALESCE({parts})"
