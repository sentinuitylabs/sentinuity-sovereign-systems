"""
services/price_router.py
========================
SENTINUITY PRICE TRUTH ROUTER — SINGLE AUTHORITATIVE PRICE LAYER

Single canonical function for all price reads in Sentinuity.
Replaces scattered direct reads from mtm_ticks, market_snapshots,
and DexScreener in execution_engine and sovereign_hub.

Two modes:
  mode="execution"  — trusted sources only (intel DB + mtm snapshots)
                      NEVER DexScreener/API. Used for TP/SL/exit decisions.
  mode="ui"         — execution sources first, API fallback allowed.
                      Shows degraded/stale badges. Used for hub display only.

Rules:
  - ALL reads enforce ts >= opened_at (no pre-entry MTM bleed)
  - Stale price (can_execute_exit=False) MUST NOT trigger TP/SL
  - SQLite-safe: no GREATEST(), uses CASE WHEN
  - Fail-open on DB errors (returns NO_DATA result, never crashes)
  - Never modifies wallet_balance or close_position_canonical()
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("price_router")

# ---------------------------------------------------------------------------
# SOURCE TIER CONSTANTS
# ---------------------------------------------------------------------------
TIER_INTEL   = 1   # sentinuity_intelligence.db mtm_ticks  — freshest, WS-live
TIER_MTM     = 2   # market_snapshots candidate_state='mtm' — oracle-written
TIER_UNSCOPED = 3  # market_snapshots any row post opened_at — fallback
TIER_DEX     = 4   # DexScreener API — UI only, never execution
TIER_NONE    = 99  # no data

# ---------------------------------------------------------------------------
# STALE THRESHOLDS
# ---------------------------------------------------------------------------
EXECUTION_STALE_SEC = 300.0   # price older than this: can_execute_exit=False
                               # Raised 180→300: allow TP/SL to fire on prices up to 5min old
UI_STALE_SEC        = 300.0   # price older than this: badge=STALE in UI
MAX_SANE_MULTIPLE   = 1000.0  # price > entry * 1000: reject as corrupt

# ---------------------------------------------------------------------------
# RESULT TYPE
# ---------------------------------------------------------------------------
def _make_result(
    price: float = 0.0,
    source: str = "none",
    source_tier: int = TIER_NONE,
    age_sec: float = 9999.0,
    confidence: float = 0.0,
    can_execute_exit: bool = False,
    is_stale: bool = True,
    warning: str = "",
    data_status: str = "OK",
) -> dict:
    return {
        "price":            price,
        "source":           source,
        "source_tier":      source_tier,
        "age_sec":          age_sec,
        "confidence":       confidence,
        "can_execute_exit": can_execute_exit,
        "is_stale":         is_stale,
        "warning":          warning,
        "data_status":      data_status,
    }

NO_DATA = _make_result(warning="NO_DATA", data_status="NO_DATA")


# ---------------------------------------------------------------------------
# DB HELPERS — resolved at call time, not import time
# ---------------------------------------------------------------------------
def _matrix_conn():
    """Read-only connection to sentinuity_matrix.db."""
    from core.schema import get_connection
    return get_connection()


def _intel_conn():
    """Read-only connection to sentinuity_intelligence.db."""
    base = Path(__file__).resolve().parent.parent
    import sqlite3
    c = sqlite3.connect(str(base / "sentinuity_intelligence.db"), timeout=30.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# INTERNAL SOURCE READERS
# ---------------------------------------------------------------------------
def _read_intel(mint: str, opened_at: float, max_sane: float, now: float) -> Optional[dict]:
    """
    Read from sentinuity_intelligence.db mtm_ticks.
    Enforces ts >= opened_at by using ts_ms >= opened_at * 1000.
    """
    try:
        c = _intel_conn()
        row = c.execute(
            "SELECT price_usd, ts_ms FROM mtm_ticks "
            "WHERE mint_address=? AND ts_ms >= ? "
            "ORDER BY ts_ms DESC LIMIT 1",
            (mint, (opened_at - 0.5) * 1000),  # 500ms drift grace
        ).fetchone()
        c.close()
        if row and row[0] is not None:
            price = float(row[0])
            age   = now - float(row[1]) / 1000.0
            if 0 < price < max_sane and age >= 0:
                return {"price": price, "age": age, "source": "intel-mtm", "tier": TIER_INTEL}
    except Exception as e:
        log.debug("price_router._read_intel mint=%s: %s", mint[:12], e)
    return None


def _read_mtm_snapshot(mint: str, opened_at: float, max_sane: float, now: float) -> Optional[dict]:
    """
    Read from market_snapshots WHERE candidate_state='mtm' AND price_updated_at >= opened_at.
    """
    try:
        c = _matrix_conn()
        row = c.execute(
            """
            SELECT observed_price, price_updated_at
            FROM market_snapshots
            WHERE mint_address=?
              AND candidate_state='mtm'
              AND observed_price > 0
              AND price_updated_at >= ?
            ORDER BY price_updated_at DESC LIMIT 1
            """,
            (mint, opened_at - 0.5),  # 500ms drift grace
        ).fetchone()
        c.close()
        if row:
            price = float(row["observed_price"])
            age   = now - float(row["price_updated_at"] or 0)
            if 0 < price < max_sane and age >= 0:
                return {"price": price, "age": age, "source": "mtm-snapshot", "tier": TIER_MTM}
    except Exception as e:
        log.debug("price_router._read_mtm_snapshot mint=%s: %s", mint[:12], e)
    return None


def _read_unscoped_snapshot(mint: str, opened_at: float, max_sane: float, now: float) -> Optional[dict]:
    """
    Read any market_snapshots row post opened_at — widest fallback for execution.
    """
    try:
        c = _matrix_conn()
        row = c.execute(
            """
            SELECT observed_price, price_updated_at
            FROM market_snapshots
            WHERE mint_address=?
              AND observed_price > 0
              AND price_updated_at >= ?
            ORDER BY price_updated_at DESC LIMIT 1
            """,
            (mint, opened_at - 0.5),  # 500ms drift grace
        ).fetchone()
        c.close()
        if row:
            price = float(row["observed_price"])
            age   = now - float(row["price_updated_at"] or 0)
            if 0 < price < max_sane and age >= 0:
                return {"price": price, "age": age, "source": "unscoped-snapshot", "tier": TIER_UNSCOPED}
    except Exception as e:
        log.debug("price_router._read_unscoped mint=%s: %s", mint[:12], e)
    return None


def _read_dexscreener(mint: str, max_sane: float) -> Optional[dict]:
    """
    DexScreener fallback — UI mode only. Never used for execution exits.
    Returns conservative assumed_age=45s (CDN cache is typically 30-60s stale).
    """
    try:
        import requests
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=5,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return None
        pairs = (resp.json() or {}).get("pairs") or []
        sol   = [p for p in pairs if str(p.get("chainId", "")).lower() == "solana"]
        if not sol:
            return None
        best  = max(sol, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        price = float(best.get("priceUsd") or 0)
        if 0 < price < max_sane:
            return {"price": price, "age": 45.0, "source": "dexscreener", "tier": TIER_DEX}
    except Exception as e:
        log.debug("price_router._read_dexscreener mint=%s: %s", mint[:12], e)
    return None


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
def get_best_trade_price(
    mint: str,
    entry_price: float,
    opened_at: float,
    mode: str = "execution",
) -> dict:
    """
    Single authoritative price read for all Sentinuity price needs.

    Parameters
    ----------
    mint        : token mint address
    entry_price : entry price of the open position (used for sanity bound)
    opened_at   : epoch when position was opened (enforces ts >= opened_at - 0.5)  # drift protection
    mode        : "execution" — trusted sources only, never DexScreener
                  "ui"        — same priority order + DexScreener fallback

    Returns dict with keys:
        price, source, source_tier, age_sec, confidence,
        can_execute_exit, is_stale, warning
    """
    if not mint or entry_price <= 0:
        return _make_result(data_status="NO_DATA", warning="NO_DATA: missing mint or entry_price")

    # opened_at=0 means no filtering — use a safe sentinel so old rows are
    # not accidentally included. If caller passes 0.0, treat as "any post-epoch".
    _opened_at = max(float(opened_at or 0), 0.0)
    _now       = time.time()
    _max_sane  = entry_price * MAX_SANE_MULTIPLE

    best: Optional[dict] = None

    # ── Tier 1: Intel DB (WS-live, freshest) ─────────────────────────────────
    best = _read_intel(mint, _opened_at, _max_sane, _now)

    # ── Tier 2: MTM snapshot (oracle-written, enforces opened_at) ────────────
    if best is None:
        best = _read_mtm_snapshot(mint, _opened_at, _max_sane, _now)

    # ── Tier 3: Unscoped snapshot (UI mode only — too permissive for execution) ─
    # Execution mode trusts only intel DB and mtm-scoped snapshots.
    # Unscoped rows may include qualify-time prices that predate the position open
    # and could produce false PnL readings on execution exits.
    if best is None and mode == "ui":
        best = _read_unscoped_snapshot(mint, _opened_at, _max_sane, _now)

    # ── Tier 4: DexScreener (UI mode only) ───────────────────────────────────
    if best is None and mode == "ui":
        best = _read_dexscreener(mint, _max_sane)

    # ── No data ───────────────────────────────────────────────────────────────
    if best is None:
        return _make_result(data_status="NO_DATA_POST_REFERENCE", warning="NO_DATA_POST_REFERENCE: no trusted price at/after reference timestamp")

    price  = best["price"]
    age    = best["age"]
    source = best["source"]
    tier   = best["tier"]

    # ── Staleness classification ───────────────────────────────────────────────
    if mode == "execution":
        is_stale        = age > EXECUTION_STALE_SEC
        can_execute_exit = not is_stale
        stale_threshold  = EXECUTION_STALE_SEC
    else:
        is_stale        = age > UI_STALE_SEC
        can_execute_exit = False  # UI result is never used for exits
        stale_threshold  = UI_STALE_SEC

    # DexScreener results are never execution-safe
    if tier == TIER_DEX:
        can_execute_exit = False
        is_stale         = True

    # ── Confidence score (0.0–1.0) ────────────────────────────────────────────
    if age < 5:
        confidence = 1.0
    elif age < 15:
        confidence = 0.95
    elif age < 30:
        confidence = 0.85
    elif age < 60:
        confidence = 0.70
    elif age < 120:
        confidence = 0.50
    elif age < 300:
        confidence = 0.25
    else:
        confidence = 0.0

    # Tier penalty
    if tier == TIER_UNSCOPED:
        confidence = max(0.0, confidence - 0.10)
    elif tier == TIER_DEX:
        confidence = max(0.0, confidence - 0.30)

    # ── Warning badge ─────────────────────────────────────────────────────────
    if tier == TIER_DEX:
        warning = "API_FALLBACK"
    elif is_stale and mode == "ui":
        warning = "STALE"
    elif is_stale and mode == "execution":
        warning = "RPC_DEGRADED"
    elif age > 30:
        warning = "RPC_DEGRADED"
    elif confidence < 0.70:
        warning = "LAST_GOOD"
    else:
        warning = "LIVE"

    return _make_result(
        price            = price,
        source           = source,
        source_tier      = tier,
        age_sec          = round(age, 2),
        confidence       = round(confidence, 2),
        can_execute_exit = can_execute_exit,
        is_stale         = is_stale,
        warning          = warning,
    )


# ---------------------------------------------------------------------------
# CONVENIENCE WRAPPERS used by execution_engine
# ---------------------------------------------------------------------------
def get_execution_price(
    mint: str,
    entry_price: float,
    opened_at: float,
) -> dict:
    """Strict execution mode. Never DexScreener."""
    return get_best_trade_price(mint, entry_price, opened_at, mode="execution")


def get_ui_price(
    mint: str,
    entry_price: float,
    opened_at: float,
) -> dict:
    """UI mode with DexScreener fallback. Never use for exit decisions."""
    return get_best_trade_price(mint, entry_price, opened_at, mode="ui")


def get_live_liquidation_price(
    mint: str,
    quantity: float,
    entry_price: float,
    opened_at: float,
) -> dict:
    """Full-position Jupiter reverse quote for canonical REAL open PnL/exits.

    Generic market marks are deliberately excluded.  A returned price is
    executable only for the exact requested quantity and a fresh quote.
    """
    if not mint or float(quantity or 0) <= 0 or float(entry_price or 0) <= 0:
        return _make_result(data_status="NO_DATA", warning="NO_DATA: missing live liquidation inputs")
    try:
        from decimal import Decimal
        from services.live_trading import _get_jupiter_quote, _get_token_decimals, _get_cached_sol_price, _SOL_MINT
        decimals = _get_token_decimals(mint)
        raw_amount = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
        if raw_amount <= 0:
            return _make_result(data_status="NO_DATA", warning="NO_DATA: zero raw liquidation amount")
        started = time.time()
        # Bounded liquidation quote.  This is a valuation, not a submission;
        # do not chase through the sell retry ladder merely to manufacture a mark.
        bps = int(float(os.getenv("LIVE_LIQUIDATION_QUOTE_SLIPPAGE_BPS", "1000")))
        quote = _get_jupiter_quote(mint, _SOL_MINT, raw_amount, bps)
        if not quote or not quote.get("outAmount"):
            return _make_result(data_status="NO_DATA", warning="NO_DATA: Jupiter full-position quote unavailable")
        out_sol = Decimal(str(quote["outAmount"])) / Decimal(1_000_000_000)
        sol_usd = Decimal(str(_get_cached_sol_price() or 0.0))
        qty = Decimal(str(quantity))
        if out_sol <= 0 or sol_usd <= 0 or qty <= 0:
            return _make_result(data_status="NO_DATA", warning="NO_DATA: invalid Jupiter liquidation quote")
        price = float((out_sol * sol_usd) / qty)
        impact = float(quote.get("priceImpactPct") or 0.0) * 100.0
        max_impact = float(os.getenv("LIVE_LIQUIDATION_MAX_IMPACT_PCT", "12.0"))
        can_exit = impact <= max_impact
        return _make_result(
            price=price,
            source="jupiter-full-position",
            source_tier=0,
            age_sec=round(max(0.0, time.time() - started), 3),
            confidence=1.0 if can_exit else 0.4,
            can_execute_exit=can_exit,
            is_stale=False,
            warning="LIVE" if can_exit else f"IMPACT_BLOCK:{impact:.2f}>{max_impact:.2f}",
        )
    except Exception as exc:
        log.warning("live liquidation quote failed mint=%s: %s", mint[:12], exc)
        return _make_result(data_status="NO_DATA", warning=f"NO_DATA: liquidation quote {type(exc).__name__}")
