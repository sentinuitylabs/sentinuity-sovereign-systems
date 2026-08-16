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
from decimal import Decimal

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
    *,
    nominal_source: str = "none",
    raw_source: str = "unknown",
    qualified_source: str = "none",
    source_subtype: str = "unknown",
    precision_class: str = "unknown",
    trusted_source: bool = False,
    upstream_ts_ms: int = 0,
    upstream_tick_id: str = "",
    raw_amount: str = "",
    quote_out_raw: str = "",
    min_out_raw: str = "",
    route_plan_json: str = "",
    context_slot: int = 0,
    latency_ms: float = 0.0,
    provider_identity: str = "",
    request_ts: float = 0.0,
    response_ts: float = 0.0,
    error_class: str = "",
    price_impact_pct: float | None = None,
) -> dict:
    """Build the canonical router result without discarding provenance.

    The execution engine consumes the provenance fields below when writing
    mark_tape and deciding whether two observations can corroborate a runner.
    Keeping them in this result is transport only: it does not change source
    priority, price selection, confidence, freshness, or exit authority.
    """
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
        # PRICE_TRUTH_SIGNOFF_20260809 (blocker 5): price impact was computed
        # here and then discarded, so price_truth_mesh had nothing to persist
        # and hardcoded None. It now flows end-to-end. None = UNKNOWN, never 0.
        "price_impact_pct": price_impact_pct,
        "nominal_source":   nominal_source,
        "raw_source":       raw_source,
        "qualified_source": qualified_source,
        "source_subtype":   source_subtype,
        "precision_class":  precision_class,
        "trusted_source":   bool(trusted_source),
        "upstream_ts_ms":   int(upstream_ts_ms or 0),
        "upstream_tick_id": str(upstream_tick_id or ""),
        "raw_amount":        str(raw_amount or ""),
        "quote_out_raw":     str(quote_out_raw or ""),
        "min_out_raw":       str(min_out_raw or ""),
        "route_plan_json":   str(route_plan_json or ""),
        "context_slot":      int(context_slot or 0),
        "latency_ms":        float(latency_ms or 0.0),
        "provider_identity": str(provider_identity or ""),
        "request_ts":        float(request_ts or 0.0),
        "response_ts":       float(response_ts or 0.0),
        "error_class":       str(error_class or ""),
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
        # MARK_PROVENANCE_20260803: select the source column. It has always
        # existed and always carried the real computation subtype; discarding
        # it here is what merged reserve-derived and quote-API prices into one
        # indistinguishable "intel-mtm" label and produced false runner peaks.
        row = c.execute(
            "SELECT price_usd, ts_ms, COALESCE(source,'unknown') FROM mtm_ticks "
            "WHERE mint_address=? AND ts_ms >= ? "
            "ORDER BY ts_ms DESC LIMIT 1",
            (mint, (opened_at - 0.5) * 1000),  # 500ms drift grace
        ).fetchone()
        c.close()
        if row and row[0] is not None:
            price = float(row[0])
            age   = now - float(row[1]) / 1000.0
            _raw_src = str(row[2] if len(row) > 2 else "unknown")
            if 0 < price < max_sane and age >= 0:
                _tick_ms = int(row[1] or 0)
                _res = {"price": price, "age": age, "source": "intel-mtm",
                        "nominal_source": "intel-mtm",
                        "tier": TIER_INTEL, "raw_source": _raw_src,
                        "upstream_ts_ms": _tick_ms,
                        "upstream_tick_id": f"intel:{mint}:{_raw_src}:{_tick_ms}:{price:.16g}"}
                try:
                    from services.mark_provenance import (
                        classify_subtype, is_trusted_subtype, precision_class,
                        qualified_source,
                    )
                    _res["source_subtype"] = classify_subtype(_raw_src)
                    _res["qualified_source"] = qualified_source("intel-mtm", _raw_src)
                    _res["source"] = _res["qualified_source"]
                    _res["trusted_source"] = is_trusted_subtype(_raw_src)
                    _res["precision_class"] = precision_class(price)
                except Exception:
                    # Preserve the nominal label only when provenance helpers are
                    # unavailable. Unknown provenance must never inherit trust.
                    _res["qualified_source"] = "intel-mtm:unknown:%s" % (
                        ''.join(ch for ch in _raw_src.lower() if ch.isalnum() or ch in '._-')[:48] or 'none'
                    )
                    _res["source"] = _res["qualified_source"]
                    _res["trusted_source"] = False
                return _res
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
        # PRICE_TRUTH_EXEC_SEPARATION_20260809:
        # Native/MTM observations answer "what does a reference source report?",
        # not "what can this exact position be sold for?".  Freshness alone can
        # never promote an observational mark into executable authority.
        is_stale         = age > EXECUTION_STALE_SEC
        can_execute_exit = False
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
    elif mode == "execution":
        warning = "OBSERVATIONAL_ONLY" if not is_stale else "OBSERVATIONAL_STALE"
    elif is_stale and mode == "ui":
        warning = "STALE"
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
        nominal_source   = str(best.get("nominal_source") or source or "none"),
        raw_source       = str(best.get("raw_source") or source or "unknown"),
        qualified_source = str(best.get("qualified_source") or source or "none"),
        source_subtype   = str(best.get("source_subtype") or "unknown"),
        precision_class  = str(best.get("precision_class") or "unknown"),
        trusted_source   = bool(best.get("trusted_source", False)),
        upstream_ts_ms   = int(best.get("upstream_ts_ms") or 0),
        upstream_tick_id = str(best.get("upstream_tick_id") or ""),
    )


# ---------------------------------------------------------------------------
# CONVENIENCE WRAPPERS used by execution_engine
# ---------------------------------------------------------------------------
def get_cached_position_liquidation_price(
    position_id: int,
    mint: str,
    quantity: float,
    entry_price: float,
    opened_at: float,
) -> dict:
    """Read the latest exact-position Layer-C route without network I/O.

    This is the canonical PAPER/SIM executable mark.  It reads only rows that
    the price-truth owner already classified VALID + sellable and then verifies
    that the quote raw amount still matches the position quantity.  A native
    curve/MTM/indexer mark can never enter this function.

    REAL positions do not use this cache: they continue to request a fresh
    full-position Jupiter liquidation quote directly.
    """
    if int(position_id or 0) <= 0 or not mint or float(quantity or 0) <= 0 or float(entry_price or 0) <= 0:
        return _make_result(data_status="NO_DATA", warning="NO_DATA: missing cached liquidation inputs")
    try:
        from services.price_truth_schema import connect as _truth_connect
        db = _truth_connect()
        row = db.execute(
            """
            SELECT raw_amount, quote_out_raw, min_out_raw, effective_price_usd,
                   price_impact_pct, route, quote_ts, context_slot, latency_ms,
                   provider_identity, integrity_status
            FROM peak_executable_quotes
            WHERE position_id=? AND mint_address=?
              AND integrity_status='VALID' AND sellable=1
              AND LOWER(COALESCE(provider_identity,'')) IN
                  ('jupiter','jupiter_executable','jupiter-full-position',
                   'metis','quicknode_metis')
              AND quote_ts>=?
            ORDER BY quote_ts DESC, id DESC LIMIT 1
            """,
            (int(position_id), str(mint), float(opened_at or 0) - 0.5),
        ).fetchone()
        if not row:
            db.close()
            return _make_result(data_status="NO_DATA", warning="NO_EXECUTABLE_ROUTE")

        # The mesh records token decimals/raw_quantity for the same position.
        # Use the newest snapshot to prove that this quote represents the
        # current full paper position rather than a partial/different amount.
        qty_row = db.execute(
            """
            SELECT raw_quantity, decimals, observed_at
            FROM price_truth_snapshots
            WHERE position_id=? AND mint_address=?
            ORDER BY observed_at DESC LIMIT 1
            """,
            (int(position_id), str(mint)),
        ).fetchone()
        db.close()

        raw_amount = int(str(row[0] or "0"))
        price = float(row[3] or 0.0)
        quote_ts = float(row[6] or 0.0)
        age = max(0.0, time.time() - quote_ts)
        if raw_amount <= 0 or price <= 0:
            return _make_result(data_status="NO_DATA", warning="MALFORMED_EXECUTABLE_QUOTE")

        amount_ok = False
        expected_raw = 0
        snapshot_raw = 0
        decimals = None
        if qty_row:
            try:
                snapshot_raw = int(str(qty_row[0] or "0"))
                decimals = int(qty_row[1])
                expected_raw = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
                # Two separately persisted witnesses must agree: the current
                # position quantity re-derived with chain decimals AND the mesh
                # snapshot's own raw_quantity.  This prevents an old/partial
                # quote from becoming authoritative after quantity changes.
                amount_ok = (
                    abs(raw_amount - expected_raw) <= 1
                    and snapshot_raw > 0
                    and abs(raw_amount - snapshot_raw) <= 1
                )
            except Exception:
                amount_ok = False

        max_age = max(1.0, float(os.getenv("PAPER_EXECUTABLE_QUOTE_MAX_AGE_SEC", "45")))
        impact = float(row[4]) if row[4] is not None else None
        impact_cap = max(0.0, float(os.getenv(
            "PAPER_EXECUTABLE_MAX_IMPACT_PCT",
            os.getenv("LIVE_LIQUIDATION_MAX_IMPACT_PCT", "12.0"),
        )))
        impact_ok = impact is not None and impact <= impact_cap
        complete = bool(
            str(row[1] or "").strip()
            and str(row[2] or "").strip()
            and str(row[5] or "").strip()
            and int(row[7] or 0) > 0
        )
        can_exit = bool(amount_ok and complete and impact_ok and age <= max_age)
        if not amount_ok:
            warning = "EXEC_AMOUNT_MISMATCH"
        elif not complete:
            warning = "EXEC_EVIDENCE_INCOMPLETE"
        elif not impact_ok:
            warning = "EXEC_IMPACT_BLOCK"
        elif age > max_age:
            warning = "EXEC_QUOTE_STALE"
        else:
            warning = "LIVE"

        return _make_result(
            price=price,
            source="jupiter-cached-full-position",
            source_tier=0,
            age_sec=round(age, 3),
            confidence=1.0 if can_exit else 0.35,
            can_execute_exit=can_exit,
            is_stale=age > max_age,
            warning=warning,
            data_status="OK" if can_exit else "HELD",
            nominal_source="jupiter",
            raw_source="jupiter",
            qualified_source="jupiter-full-position",
            source_subtype="router_executable",
            precision_class="exact_route",
            trusted_source=True,
            raw_amount=str(raw_amount),
            quote_out_raw=str(row[1] or ""),
            min_out_raw=str(row[2] or ""),
            route_plan_json=str(row[5] or ""),
            context_slot=int(row[7] or 0),
            latency_ms=float(row[8] or 0.0),
            provider_identity=str(row[9] or "jupiter"),
            request_ts=0.0,
            response_ts=quote_ts,
            # Stable upstream identity lets the execution engine avoid writing
            # the same cached Layer-C quote into mark_tape on every 2.2s sweep.
            # This is provenance/deduplication only; it grants no authority.
            upstream_ts_ms=int(quote_ts * 1000.0),
            upstream_tick_id=(
                f"jupiter:{int(row[7] or 0)}:{raw_amount}:"
                f"{str(row[1] or '')}:{str(row[2] or '')}"
            ),
            error_class=("OK" if can_exit else warning),
            price_impact_pct=impact,
        )
    except Exception as exc:
        log.debug("cached position liquidation read failed pos=%s mint=%s: %s",
                  position_id, str(mint)[:12], exc)
        return _make_result(data_status="NO_DATA", warning="EXEC_CACHE_UNAVAILABLE")


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




def get_reference_price_details(mint: str, opened_at: float, entry_price: float) -> dict:
    """Shadow-only provenance read. Does not alter canonical routing authority."""
    now=time.time(); max_sane=float(entry_price or 0)*MAX_SANE_MULTIPLE
    try:
        c=_intel_conn()
        row=c.execute("SELECT price_usd,ts_ms,COALESCE(source,'unknown') FROM mtm_ticks WHERE mint_address=? AND ts_ms>=? ORDER BY ts_ms DESC LIMIT 1",(mint,(float(opened_at or 0)-0.5)*1000)).fetchone(); c.close()
        if row and row[0] is not None:
            price=float(row[0]); age=now-float(row[1])/1000.0
            if 0<price<max_sane and age>=0:
                return {"price":price,"source":"intel-mtm","actual_source":str(row[2] or 'unknown'),"age_sec":age,"shadow_only":True}
    except Exception as exc:
        log.debug("reference provenance read failed %s",exc)
    r=dict(get_best_trade_price(mint,entry_price,opened_at,mode="ui")); r["actual_source"]=r.get("source","unknown"); r["shadow_only"]=True; return r

# ═══════════════════════════════════════════════════════════════════════════
# T3 — SHARED ACQUISITION PLANE FOR EXECUTABLE QUOTES
# PACK_T3_MARK_CONTINUITY_20260815
# ═══════════════════════════════════════════════════════════════════════════
#
# MEASURED PROBLEM: held-position executable marks arrive every ~17-44s
# (position 5336: 5 marks / 108.8s, mean gap 27.2s), so a 4% hard stop is
# observed at -8.4%, -19.0% and -28.9%. The stop threshold is correct and
# clamped; the SAMPLING RATE is the risk parameter and nothing set it.
#
# SOURCE CAUSE — and it is not the one the brief assumed. price_truth_mesh
# already targets a 1.5s cycle start whenever positions are open
# (ACTIVE_MAX_INTERVAL), and it already acquires ACROSS positions concurrently
# (ThreadPoolExecutor over rows) and WITHIN a position concurrently (quote +
# curve). The scheduler is not serial and is not slow. The 27.2s gap is very
# nearly the whole cycle, which means the gap IS provider acquisition latency:
# executable quote latency measured 9.4s median / 15.5s p90 / 38.1s max under
# 1,563 rate-limit events.
#
# Therefore polling faster cannot help — the loop already sleeps the 0.25s
# floor — and would only add pressure. The only levers that reduce the gap
# WITHOUT increasing provider load are (a) stop issuing requests we already
# have an answer to, and (b) stop letting non-protective work consume the
# capacity that open positions need.
#
# THIS BLOCK ADDS THREE THINGS, ALL OF WHICH STRICTLY REMOVE REQUESTS:
#
#   1. IN-FLIGHT COALESCING. Concurrent callers asking the identical question
#      share one network call. The key carries every economically material
#      dimension (mint, raw amount, slippage bps), so a quote can never be
#      reused across an incompatible position size or route request.
#
#   2. IMMUTABLE-INPUT REUSE. Token decimals are a property of the mint and
#      never change. price_truth_mesh resolves them, then passes the position
#      to get_live_liquidation_price() which resolves them AGAIN — one
#      redundant RPC per position per 1.5s cycle. Decimals may now be passed
#      down. The SOL/USD conversion rate is likewise fetched per position per
#      cycle and is now briefly memoised.
#
#   3. PRIORITY WITHOUT A GATE ON THE PROTECTION PATH. Non-protective callers
#      acquire a bounded semaphore before issuing a quote and shed when it is
#      saturated. PROTECTION callers never acquire anything and never wait —
#      adding a lock to the stop path would be a worse defect than the one
#      being fixed.
#
# WHAT THIS BLOCK DELIBERATELY DOES NOT DO: it never fabricates a price, never
# grants a stale or last-known mark protective authority, never caches a result
# beyond the in-flight window, never introduces a blocking write, and does not
# touch the 4% constant, sizing, arming or signing.

import threading as _t3_threading

#: callers that must never be delayed or shed
PRIORITY_PROTECTION = "open_position_protection"
PRIORITY_RECONCILE = "funded_reconciliation"
PRIORITY_QUALIFY = "candidate_qualification"
PRIORITY_DISCOVERY = "broad_discovery"
PRIORITY_RESEARCH = "research_ui"

_T3_SHEDDABLE = (PRIORITY_QUALIFY, PRIORITY_DISCOVERY, PRIORITY_RESEARCH)

#: concurrent non-protective executable quotes permitted at once
T3_NONPRIORITY_CONCURRENCY = max(1, int(os.getenv("T3_NONPRIORITY_QUOTE_CONCURRENCY", "2")))
#: how long a non-protective caller waits for a slot before shedding
T3_NONPRIORITY_WAIT_SEC = max(0.0, float(os.getenv("T3_NONPRIORITY_QUOTE_WAIT_SEC", "0.5")))
#: how long a coalesced follower waits for the leader's answer. On expiry the
#: follower returns empty rather than issuing its own call: a follower that
#: falls back to the network converts coalescing into amplification.
T3_FOLLOWER_WAIT_SEC = max(1.0, float(os.getenv("T3_COALESCE_FOLLOWER_WAIT_SEC", "12")))
#: SOL/USD reuse window. Short enough to stay a conversion rate, long enough
#: that one mesh cycle does not re-fetch it once per position.
T3_SOL_USD_TTL_SEC = max(0.0, float(os.getenv("T3_SOL_USD_TTL_SEC", "2.0")))

_t3_nonpriority_sem = _t3_threading.BoundedSemaphore(T3_NONPRIORITY_CONCURRENCY)
_t3_inflight_lock = _t3_threading.Lock()
_t3_inflight: dict = {}
_t3_decimals_lock = _t3_threading.Lock()
_t3_decimals: dict = {}
_t3_sol_lock = _t3_threading.Lock()
_t3_sol_usd = {"value": 0.0, "ts": 0.0}

T3_STATS = {
    "quotes_issued": 0,        # actual network calls made
    "quotes_coalesced": 0,     # callers served by someone else's call
    "coalesce_timeouts": 0,
    "shed_nonpriority": 0,
    "decimals_reused": 0,
    "sol_usd_reused": 0,
}


def _t3_bump(key: str, n: int = 1) -> None:
    try:
        T3_STATS[key] = T3_STATS.get(key, 0) + n
    except Exception:
        pass


def t3_stats() -> dict:
    """Provider-economy telemetry. Read-only; safe to poll from UI/Council."""
    d = dict(T3_STATS)
    issued = max(1, d.get("quotes_issued", 0) + d.get("quotes_coalesced", 0))
    d["coalesce_ratio"] = round(d.get("quotes_coalesced", 0) / issued, 4)
    return d


def t3_token_decimals(mint: str, resolver) -> int:
    """Decimals are immutable per mint. Resolve once per process."""
    key = str(mint)
    with _t3_decimals_lock:
        hit = _t3_decimals.get(key)
    if hit is not None:
        _t3_bump("decimals_reused")
        return int(hit)
    val = int(resolver(mint))
    with _t3_decimals_lock:
        _t3_decimals[key] = val
    return val


def t3_sol_usd(resolver) -> float:
    """Reuse the SOL/USD conversion rate inside one acquisition cycle.

    market_intelligence was observed retrying api.jup.ag/price/v3 for the SOL
    mint on a 1.0s read timeout, three attempts, 40 times in six hours. This is
    the most cacheable value in the system and had the tightest timeout.
    """
    now = time.time()
    with _t3_sol_lock:
        if _t3_sol_usd["value"] > 0 and (now - _t3_sol_usd["ts"]) <= T3_SOL_USD_TTL_SEC:
            _t3_bump("sol_usd_reused")
            return float(_t3_sol_usd["value"])
    val = float(resolver() or 0.0)
    if val > 0:
        with _t3_sol_lock:
            _t3_sol_usd["value"] = val
            _t3_sol_usd["ts"] = now
    return val


def t3_coalesce(key: tuple, producer, *, priority: str = PRIORITY_QUALIFY):
    """Run `producer` once for identical concurrent `key`s.

    `key` must already carry every dimension that makes two requests
    economically interchangeable. Callers construct it; this function does not
    infer it, because inferring it is how a quote for one position size gets
    reused for another.

    Returns (value, served_by) where served_by is 'network', 'coalesced',
    'timeout' or 'shed'. A shed or timed-out caller receives None and must
    treat that as no data — never as a price.
    """
    leader = False
    with _t3_inflight_lock:
        entry = _t3_inflight.get(key)
        if entry is None:
            entry = {"event": _t3_threading.Event(), "value": None, "error": None}
            _t3_inflight[key] = entry
            leader = True

    if not leader:
        # Someone is already asking this exact question. Wait for their answer
        # instead of asking it again.
        if entry["event"].wait(timeout=T3_FOLLOWER_WAIT_SEC):
            if entry["error"] is not None:
                raise entry["error"]
            _t3_bump("quotes_coalesced")
            return entry["value"], "coalesced"
        _t3_bump("coalesce_timeouts")
        return None, "timeout"

    acquired = False
    try:
        if priority in _T3_SHEDDABLE:
            acquired = _t3_nonpriority_sem.acquire(timeout=T3_NONPRIORITY_WAIT_SEC)
            if not acquired:
                _t3_bump("shed_nonpriority")
                entry["value"] = None
                return None, "shed"
        # PRIORITY_PROTECTION and PRIORITY_RECONCILE acquire nothing and wait
        # for nothing. Capacity is yielded to them by the shedding above, not
        # taken by them through a lock they could block on.
        _t3_bump("quotes_issued")
        entry["value"] = producer()
        return entry["value"], "network"
    except BaseException as exc:
        entry["error"] = exc
        raise
    finally:
        if acquired:
            try:
                _t3_nonpriority_sem.release()
            except Exception:
                pass
        with _t3_inflight_lock:
            _t3_inflight.pop(key, None)
        entry["event"].set()



def _try_pump_exact_liquidation(
    mint: str,
    raw_amount: int,
    decimals: int,
    *,
    request_ts: float,
) -> dict | None:
    """Return a fail-closed exact Pump bonding-curve liquidation witness.

    This is the same executable witness previously used only *after* a Jupiter
    no-route result.  Running it first for a still-tradeable pre-graduation
    curve avoids asking Jupiter for a venue that cannot exist yet.  Every
    authority check from the former fallback is preserved: mint-derived curve
    read, Pump owner, confirmed context slot/account hash, exact raw quantity,
    fee economics, reserve-bounded simulation, and impact ceiling.

    None means Pump-native executable truth was unavailable or inappropriate
    (for example a completed/graduated curve); the caller must then continue to
    Jupiter rather than inventing a price.
    """
    try:
        from services.live_trading import _get_cached_sol_price
        from services.pump_curve_reader import read_curve
        from services.pump_curve_math import (
            simulate_sell_exact,
            unit_price_usd_from_sol_quote,
            curve_fee_bps as _curve_fee_bps,
        )

        cr = read_curve(
            mint, timeout_sec=float(os.getenv("PUMP_EXEC_WITNESS_TIMEOUT_SEC", "1.25"))
        )
        if not (
            cr.ok
            and cr.state is not None
            and cr.state.tradeable_pre_grad
            and cr.context_slot
            and cr.curve_address
            and cr.account_hash
            and str(cr.owner or "") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
        ):
            # Typed, observable refusal. A completed (graduated) curve is the
            # wrong venue rather than a zero-value one; Jupiter now owns it and
            # the caller continues there. This must never become a price.
            _st = getattr(cr, "state", None)
            log.info(
                "PUMP_CURVE_WITNESS_INAPPLICABLE mint=%s ok=%s reason=%s "
                "complete=%s slot=%s -- deferring to Jupiter",
                mint[:12], getattr(cr, "ok", None), getattr(cr, "reason", None),
                (None if _st is None else _st.complete), getattr(cr, "context_slot", None),
            )
            return None

        fee_bps = _curve_fee_bps()
        sq = simulate_sell_exact(cr.state, raw_amount, fee_bps)

        # CURVE_VERDICT_20260816 -- the authority gate.
        #
        # The old gate was `sq.ok and pump_px > 0 and curve_impact <= ceiling`,
        # where `ok` meant `net > 0` and `curve_impact_bps` was measured against
        # the UNCLAMPED theoretical proceeds. On position 5788 that accepted
        # 149 lamports against a 327,413,399-lamport theoretical result and
        # reported 1.09% impact while the caller was actually taking 99.99995%.
        # Both halves are now typed:
        #   * only CURVE_EXECUTABLE may proceed;
        #   * the ceiling is applied to realised_impact_bps, which measures the
        #     loss actually borne, so a binding reserve clamp cannot present
        #     itself as a low-impact sale.
        if not sq.executable:
            # Refuse and fall through to Jupiter. Never fabricate a price from
            # a refused curve, and never let the refusal become 0.0.
            log.warning(
                "PUMP_CURVE_AUTHORITY_REFUSED mint=%s verdict=%s reason=%s "
                "theoretical=%d payable=%d shortfall=%d coverage_bps=%d "
                "realised_impact_bps=%d reported_impact_bps=%d -- falling "
                "through to Jupiter",
                mint[:12], sq.verdict, sq.reason,
                sq.theoretical_gross_quote_raw, sq.payable_gross_quote_raw,
                sq.reserve_shortfall_raw, sq.real_reserve_coverage_bps,
                sq.realised_impact_bps, sq.curve_impact_bps,
            )
            return None

        sol_usd = t3_sol_usd(_get_cached_sol_price)
        pump_px = unit_price_usd_from_sol_quote(sq, raw_amount, decimals, sol_usd)
        pump_impact = float(sq.realised_impact_bps) / 100.0
        max_impact = float(os.getenv("LIVE_LIQUIDATION_MAX_IMPACT_PCT", "12.0"))
        if not (pump_px and pump_px > 0 and pump_impact <= max_impact):
            if pump_px is not None and pump_impact > max_impact:
                log.warning(
                    "PUMP_CURVE_IMPACT_BLOCK mint=%s realised_impact=%.4f%% > "
                    "ceiling=%.2f%% (theoretical-based impact would have been "
                    "%.4f%%)",
                    mint[:12], pump_impact, max_impact,
                    float(sq.curve_impact_bps) / 100.0,
                )
            return None

        resp = time.time()
        return _make_result(
            price=float(pump_px), source="pump-curve-exact", source_tier=0,
            age_sec=max(0.0, resp - float(cr.observed_at or resp)), confidence=0.95,
            can_execute_exit=True, is_stale=False, warning="LIVE_NATIVE_PUMP_EXACT",
            raw_amount=str(raw_amount), quote_out_raw=str(sq.net_quote_raw),
            min_out_raw=str(sq.net_quote_raw), route_plan_json="pump_curve_exact",
            context_slot=int(cr.context_slot), latency_ms=float(cr.latency_ms or 0.0),
            provider_identity="pump_curve_exact", request_ts=request_ts, response_ts=resp,
            upstream_ts_ms=int(float(cr.observed_at or resp) * 1000.0),
            upstream_tick_id=f"pump:{cr.context_slot}:{cr.account_hash}:{raw_amount}:{sq.net_quote_raw}",
            error_class="OK", price_impact_pct=pump_impact,
        )
    except Exception as exc:
        log.debug("native Pump executable witness unavailable mint=%s: %s", mint[:12], exc)
        return None

def get_live_liquidation_price(
    mint: str,
    quantity: float,
    entry_price: float,
    opened_at: float,
    *,
    decimals: Optional[int] = None,
    priority: str = PRIORITY_PROTECTION,
) -> dict:
    """Full-position Jupiter reverse quote for canonical REAL open PnL/exits.

    Generic market marks are deliberately excluded.  A returned price is
    executable only for the exact requested quantity and a fresh quote.

    T3: `decimals` may be supplied by a caller that has already resolved them
    (price_truth_mesh does), removing one redundant RPC per position per cycle.
    `priority` defaults to PRIORITY_PROTECTION because the open-position mark
    path is this function's primary consumer and must never be shed or made to
    wait; qualification/discovery/UI callers must pass their own class.
    """
    if not mint or float(quantity or 0) <= 0 or float(entry_price or 0) <= 0:
        return _make_result(data_status="NO_DATA", warning="NO_DATA: missing live liquidation inputs")
    try:
        from decimal import Decimal
        from services.live_trading import _get_jupiter_quote, _get_token_decimals, _get_cached_sol_price, _SOL_MINT
        if decimals is None:
            decimals = t3_token_decimals(mint, _get_token_decimals)
        decimals = int(decimals)
        raw_amount = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
        if raw_amount <= 0:
            return _make_result(data_status="NO_DATA", warning="NO_DATA: zero raw liquidation amount")
        started = time.time()
        request_ts = started
        # Bounded liquidation quote.  This is a valuation, not a submission;
        # do not chase through the sell retry ladder merely to manufacture a mark.
        bps = int(float(os.getenv(
            "LIVE_LIQUIDATION_QUOTE_SLIPPAGE_BPS",
            os.getenv("LIVE_SELL_SLIPPAGE_BPS", "1500"),
        )))
        # EDGE_SIGNOFF_20260815: pre-graduation Pump assets have deterministic
        # exact-quantity curve exitability but no Jupiter venue yet. The old
        # ordering asked Jupiter first on every refresh, then performed this
        # same Pump witness only after NO_ROUTES_FOUND. That wasted provider
        # budget and stretched open-position truth cycles without adding
        # authority. Preserve the exact witness contract, but ask the venue
        # that actually exists first. Completed/unavailable curves fall through
        # unchanged to Jupiter.
        pump_exact = _try_pump_exact_liquidation(
            mint, raw_amount, decimals, request_ts=request_ts
        )
        if pump_exact is not None:
            return pump_exact

        # T3: identical concurrent questions share one network call. The key
        # carries mint, exact raw amount and slippage, so a quote can never be
        # reused across an incompatible position size or route request.
        quote, _served_by = t3_coalesce(
            ("jup_liq", str(mint), int(raw_amount), int(bps)),
            lambda: _get_jupiter_quote(mint, _SOL_MINT, raw_amount, bps),
            priority=priority,
        )
        if _served_by in ("timeout", "shed"):
            # No answer was obtained. This is explicitly NOT a price: it must
            # reach the caller as absent data so no protective decision can be
            # taken on it.
            return _make_result(
                data_status="NO_DATA",
                warning=("QUOTE_COALESCE_TIMEOUT" if _served_by == "timeout"
                         else "QUOTE_SHED_LOWER_PRIORITY"),
                error_class=("QUOTE_COALESCE_TIMEOUT" if _served_by == "timeout"
                             else "QUOTE_SHED_LOWER_PRIORITY"),
                request_ts=request_ts, response_ts=time.time(),
            )
        if not quote or not quote.get("outAmount"):
            # Pump-native exact truth was already attempted above. A missing
            # Jupiter route now means neither supported executable venue could
            # produce a witness for this pass; fail closed exactly as before.
            return _make_result(
                data_status="NO_DATA",
                warning="NO_DATA: Pump exact and Jupiter liquidation witnesses unavailable",
                request_ts=request_ts, response_ts=time.time(),
                error_class="NO_EXECUTABLE_ROUTE",
            )
        out_sol = Decimal(str(quote["outAmount"])) / Decimal(1_000_000_000)
        sol_usd = Decimal(str(t3_sol_usd(_get_cached_sol_price)))
        qty = Decimal(str(quantity))
        if out_sol <= 0 or sol_usd <= 0 or qty <= 0:
            return _make_result(data_status="NO_DATA", warning="NO_DATA: invalid Jupiter liquidation quote")
        price = float((out_sol * sol_usd) / qty)
        _impact_raw = quote.get("priceImpactPct")
        if _impact_raw is None or str(_impact_raw).strip() == "":
            return _make_result(
                data_status="HELD", warning="IMPACT_UNKNOWN",
                raw_amount=str(raw_amount),
                quote_out_raw=str(quote.get("outAmount") or ""),
                min_out_raw=str(quote.get("otherAmountThreshold") or quote.get("minOutAmount") or ""),
                context_slot=int(quote.get("contextSlot") or 0),
                provider_identity="jupiter",
                request_ts=request_ts, response_ts=time.time(),
                error_class="IMPACT_UNKNOWN", price_impact_pct=None,
            )
        impact = float(_impact_raw) * 100.0
        max_impact = float(os.getenv("LIVE_LIQUIDATION_MAX_IMPACT_PCT", "12.0"))
        can_exit = impact <= max_impact
        response_ts = time.time()
        route_plan = quote.get("routePlan") or []
        min_out = quote.get("otherAmountThreshold") or quote.get("minOutAmount") or ""
        context_slot = int(quote.get("contextSlot") or 0)
        route_json = __import__("json").dumps(route_plan, sort_keys=True, separators=(",", ":"))
        evidence_complete = bool(
            int(str(quote.get("outAmount") or "0")) > 0
            and int(str(min_out or "0")) > 0
            and context_slot > 0
            and route_plan
        )
        can_exit = bool(can_exit and evidence_complete)
        error_class = (
            "OK" if can_exit else
            ("MALFORMED_RESPONSE" if not evidence_complete else "IMPACT_BLOCK")
        )
        return _make_result(
            price=price,
            source="jupiter-full-position",
            source_tier=0,
            age_sec=round(max(0.0, response_ts - started), 3),
            confidence=1.0 if can_exit else 0.4,
            can_execute_exit=can_exit,
            is_stale=False,
            warning=("LIVE" if can_exit else
                     ("MALFORMED_RESPONSE" if not evidence_complete
                      else f"IMPACT_BLOCK:{impact:.2f}>{max_impact:.2f}")),
            raw_amount=str(raw_amount),
            quote_out_raw=str(quote.get("outAmount") or ""),
            min_out_raw=str(min_out or ""),
            route_plan_json=route_json,
            context_slot=context_slot,
            latency_ms=(response_ts - request_ts) * 1000.0,
            provider_identity="jupiter",
            request_ts=request_ts,
            response_ts=response_ts,
            upstream_ts_ms=int(response_ts * 1000.0),
            upstream_tick_id=(
                f"jupiter:{context_slot}:{raw_amount}:"
                f"{str(quote.get('outAmount') or '')}:{str(min_out or '')}"
            ),
            error_class=error_class,
            price_impact_pct=impact,
        )
    except Exception as exc:
        log.warning("live liquidation quote failed mint=%s: %s", mint[:12], exc)
        return _make_result(data_status="NO_DATA", warning=f"NO_DATA: liquidation quote {type(exc).__name__}")
