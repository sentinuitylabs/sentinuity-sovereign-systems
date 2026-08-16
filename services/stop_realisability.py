#!/usr/bin/env python3
"""
SENTINUITY — STOP REALISABILITY PROBE AND LEDGER

The gap this closes
-------------------
execution_engine.py:5051 fires the hard stop on `pnl_pct`, derived from the
observed mark. Lines 5042-5230 contain ZERO executable-quote calls: the paper
stop closes at a mark nobody was offered. `live_trading.evaluate_exit_quality()`
(live_trading.py:1351) is already a side-effect-free quote probe, but it has
zero callers in execution_engine and discards the numbers this question needs --
it returns only viable/recommended_bps/impact and throws away `out_amount`,
quote age, route depth and minimum output.

Result: the six-hour window credited 12 closes at exactly -4.00% with no
evidence any of them could have been sold near -4%. Sensitivity: +$86 at -10%,
+$56 at -20%, ~-$4 at -40%. The edge's live sign hinges on a number that has
never been measured.

This module measures it WITHOUT submitting anything.

Hard contracts:
  * QUOTE ONLY. No signing, no submission, no transaction build. The only
    outbound call is Jupiter's quote endpoint via live_trading._get_jupiter_quote.
  * Writes exactly one table: stop_realisability_ledger.
  * Never raises into the exit path. A probe failure must never block a close.
  * Never alters exit price, exit reason, PnL, sizing, gates or arming.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, Optional

SERVICE = "stop_realisability"
LEDGER_TABLE = "stop_realisability_ledger"

ENABLED = os.environ.get("STOP_PROBE_ENABLED", "1").strip() != "0"
# Probe at most this often per position, so a stop that defers and re-evaluates
# cannot generate an unbounded number of outbound quotes.
MIN_PROBE_INTERVAL_SEC = float(os.environ.get("STOP_PROBE_MIN_INTERVAL", "20"))

_last_probe: Dict[int, float] = {}

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    mint_address TEXT,
    token_name TEXT,

    trigger_ts REAL NOT NULL,
    trigger_mark_price REAL,
    trigger_mark_source TEXT,
    trigger_mark_subtype TEXT,
    trigger_mark_integrity TEXT,
    trigger_mark_age_sec REAL,
    candidate_id INTEGER,
    snapshot_id INTEGER,
    probe_only INTEGER NOT NULL DEFAULT 1,
    intended_stop_pct REAL,
    intended_stop_price REAL,
    entry_price REAL,
    position_size_usd REAL,

    token_raw_amount INTEGER,
    token_decimals INTEGER,

    quote_ts REAL,
    quote_age_sec REAL,
    quote_in_raw INTEGER,
    quote_out_raw INTEGER,
    quote_min_out_raw INTEGER,
    quote_slippage_bps INTEGER,
    quote_price_impact_pct REAL,
    route_hops INTEGER,
    route_labels TEXT,

    sol_usd REAL,
    gross_proceeds_usd REAL,
    est_network_fee_usd REAL,
    est_priority_fee_usd REAL,
    est_total_fee_usd REAL,
    net_proceeds_usd REAL,

    expected_exec_price REAL,
    executable_pct REAL,
    executable_loss_usd REAL,

    credited_stop_pct REAL,
    credited_stop_usd REAL,
    realisability_gap_pct REAL,

    latency_model TEXT,
    latency_p50_sec REAL,
    latency_p90_sec REAL,
    adverse_price_in_latency REAL,
    latency_adjusted_pct REAL,

    trigger_to_quote_sec REAL,
    simulation_status TEXT,
    no_route INTEGER DEFAULT 0,
    quote_stale INTEGER DEFAULT 0,
    probe_status TEXT,
    probe_error TEXT,
    integrity_status TEXT,
    created_at REAL NOT NULL,

    -- STOP_BASIS_REPAIR_20260804: USD-basis provenance and latency semantics.
    sol_usd_source TEXT,
    sol_usd_age_sec REAL,
    quote_start_ts REAL,
    quote_end_ts REAL,
    pre_quote_setup_sec REAL,
    quote_network_sec REAL,
    basis_version INTEGER DEFAULT 2,
    cohort_reason TEXT
);
"""

_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_srl_pos ON {LEDGER_TABLE}(position_id)",
    f"CREATE INDEX IF NOT EXISTS idx_srl_ts ON {LEDGER_TABLE}(trigger_ts)",
    f"CREATE INDEX IF NOT EXISTS idx_srl_status ON {LEDGER_TABLE}(probe_status)",
    f"CREATE INDEX IF NOT EXISTS idx_srl_basis ON {LEDGER_TABLE}(basis_version)",
)

# STOP_BASIS_REPAIR_20260804
# Columns added after the original table shipped. CREATE TABLE IF NOT EXISTS
# will not add them to an existing ledger, so they are applied idempotently.
# basis_version distinguishes evidence cohorts:
#   1 = collected before the USD-basis repair; executable_pct was never
#       computable, so these rows are NOT evidence about executable_pct.
#   2 = collected after the repair.
# Readiness scopes to version >= 2. That makes the gate strictly harder to
# pass, never easier: no threshold is altered anywhere in this change.
BASIS_VERSION = 2
LEGACY_BASIS_VERSION = 1
LEGACY_COHORT_REASON = "LEGACY_NO_USD_BASIS"

# Rows written before the repair could not compute executable_pct at all, so
# they are excluded from readiness evidence. Legacy rows are never deleted or
# rewritten -- only ignored by the gate.
# STOP_TRUTH_SIGNOFF_20260808: the cohort had no time bound, so the median was
# computed over every probe ever taken at basis_version >= 2. It mixed oracle
# regimes, moved glacially (111 -> 127 samples across 12 hours) and could not
# reflect a repair even after one landed. Bounded to a rolling window; the
# window is deliberately long enough that it cannot be gamed into a tiny,
# flattering sample, and MIN_SAMPLES_ABSOLUTE still applies inside it.
COHORT_WINDOW_SEC = float(os.environ.get("STOP_PROBE_COHORT_WINDOW_SEC", str(72 * 3600)))


def _cohort_where() -> str:
    cutoff = time.time() - COHORT_WINDOW_SEC
    return (f"WHERE COALESCE(basis_version,{LEGACY_BASIS_VERSION}) >= {BASIS_VERSION} "
            f"AND COALESCE(trigger_ts, created_at, 0) >= {cutoff:.3f}")


# Retained as a module-level name for any external reader; the gate itself uses
# _cohort_where() so the window is evaluated at call time rather than import.
COHORT_WHERE = f"WHERE COALESCE(basis_version,{LEGACY_BASIS_VERSION}) >= {BASIS_VERSION}"

_ADDED_COLUMNS = (
    ("sol_usd_source", "TEXT"),
    ("sol_usd_age_sec", "REAL"),
    ("quote_start_ts", "REAL"),
    ("quote_end_ts", "REAL"),
    ("pre_quote_setup_sec", "REAL"),
    ("quote_network_sec", "REAL"),
    ("basis_version", "INTEGER"),
    ("cohort_reason", "TEXT"),
    # SENTINUITY_EXIT_INFRA_20260805 phase instrumentation
    ("metadata_lookup_sec", "REAL"),
    ("metadata_source", "TEXT"),
    ("metadata_provider", "TEXT"),
    ("metadata_cache_hit", "INTEGER"),
    ("metadata_failure_type", "TEXT"),
)


def _ensure_columns(conn) -> None:
    """Idempotently add post-ship columns. Never drops or rewrites anything."""
    try:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({LEDGER_TABLE})")}
    except Exception:
        return
    for name, decl in _ADDED_COLUMNS:
        if name in have:
            continue
        try:
            conn.execute(f"ALTER TABLE {LEDGER_TABLE} ADD COLUMN {name} {decl}")
        except Exception:
            pass


def ensure_schema(conn) -> bool:
    try:
        conn.execute(_DDL)
        _ensure_columns(conn)
        for ix in _INDEXES:
            try:
                conn.execute(ix)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Latency model
#
# live_latency_telemetry (live_trading.py:175) records submitted_at/confirmed_at
# per signature, but only for REAL fills. With no settled live sells, its
# percentiles are unavailable -- the measurement the canary needs is gated on
# having already run the canary.
#
# This resolves that honestly: measure what CAN be measured now (trigger->quote,
# side-effect free) and carry the unmeasurable submit->confirm leg as a declared
# assumption, clearly labelled, to be replaced by observed values as soon as any
# settled live sell exists. The assumption is never presented as measurement.
# ─────────────────────────────────────────────────────────────────────────────
ASSUMED_SUBMIT_CONFIRM_P50 = float(os.environ.get("STOP_LATENCY_ASSUMED_P50", "2.5"))
ASSUMED_SUBMIT_CONFIRM_P90 = float(os.environ.get("STOP_LATENCY_ASSUMED_P90", "8.0"))


def latency_model(conn) -> Dict[str, Any]:
    """Observed percentiles when live telemetry exists; declared assumption otherwise."""
    out = {"model": "ASSUMED_NO_LIVE_SAMPLES", "n": 0,
           "p50": ASSUMED_SUBMIT_CONFIRM_P50, "p90": ASSUMED_SUBMIT_CONFIRM_P90}
    try:
        rows = conn.execute(
            "SELECT confirmed_at - submitted_at AS d FROM live_latency_telemetry "
            "WHERE submitted_at IS NOT NULL AND confirmed_at IS NOT NULL "
            "AND confirmed_at > submitted_at"
        ).fetchall()
        vals = sorted(float(r[0]) for r in rows if r and r[0] is not None)
        if len(vals) >= 5:
            def q(p):
                return vals[max(0, min(len(vals) - 1, int(round(p * (len(vals) - 1)))))]
            out.update(model="OBSERVED", n=len(vals), p50=q(0.50), p90=q(0.90))
        elif vals:
            out.update(model=f"ASSUMED_INSUFFICIENT_SAMPLES(n={len(vals)})", n=len(vals))
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Probe
# ─────────────────────────────────────────────────────────────────────────────
def probe_stop(
    conn,
    *,
    position_id: int,
    mint: str,
    quantity: float,
    entry_price: float,
    trigger_mark_price: float,
    intended_stop_pct: float,
    position_size_usd: float,
    credited_stop_pct: Optional[float] = None,
    token_name: str = "",
    mark_source: str = "",
    sol_usd: Optional[float] = None,
    candidate_id: Optional[int] = None,
    snapshot_id: Optional[int] = None,
    trigger_mark_age_sec: Optional[float] = None,
) -> Optional[int]:
    """
    Record what a live sell would ACTUALLY have realised at this stop trigger.

    QUOTE ONLY. Never signs, never submits, never builds a transaction.
    Never raises. Returns the ledger row id, or None.
    """
    if not ENABLED:
        return None
    try:
        now = time.time()
        last = _last_probe.get(int(position_id), 0.0)
        if now - last < MIN_PROBE_INTERVAL_SEC:
            return None
        _last_probe[int(position_id)] = now

        ensure_schema(conn)

        row: Dict[str, Any] = {
            "position_id": int(position_id),
            "mint_address": str(mint or "")[:64],
            "token_name": str(token_name or "")[:64],
            "trigger_ts": now,
            "trigger_mark_price": _f(trigger_mark_price),
            "trigger_mark_source": str(mark_source or "")[:64],
            "intended_stop_pct": _f(intended_stop_pct),
            "entry_price": _f(entry_price),
            "position_size_usd": _f(position_size_usd),
            "credited_stop_pct": _f(credited_stop_pct),
            "probe_status": "started",
            "probe_only": 1,
            "candidate_id": candidate_id,
            "snapshot_id": snapshot_id,
            "trigger_mark_age_sec": _f(trigger_mark_age_sec),
            "created_at": now,
            # Stamped here so every early-return path (unavailable,
            # decimals_failed, zero_quantity, no_route) also lands in the
            # current evidence cohort.
            "basis_version": BASIS_VERSION,
        }
        e = _f(entry_price) or 0.0
        if e > 0 and row["intended_stop_pct"] is not None:
            row["intended_stop_price"] = e * (1.0 + row["intended_stop_pct"] / 100.0)

        # Mark provenance, when the repaired module is present.
        try:
            from services.mark_provenance import classify_subtype
            row["trigger_mark_subtype"] = classify_subtype(mark_source)
        except Exception:
            pass

        try:
            from services import live_trading as LT
        except Exception as exc:
            row.update(probe_status="unavailable",
                       probe_error=f"live_trading_import:{type(exc).__name__}",
                       integrity_status="PROBE_UNAVAILABLE")
            return _insert(conn, row)

        # Raw amount -- the same raw/decimals boundary the live sell uses.
        # SENTINUITY_EXIT_INFRA_20260805: metadata lookup is timed separately so
        # trigger_to_quote_sec can be attributed. probe_error now carries the
        # exact failure type and provider host instead of a bare class name.
        _meta_t0 = time.time()
        try:
            from decimal import Decimal
            try:
                from services import token_metadata as _TM
                _meta = _TM.resolve_decimals(mint)
            except Exception as _meta_exc:
                _meta = {"decimals": None, "source": "resolver_unavailable",
                         "provider": "", "cache_hit": False,
                         "failure_type": f"{type(_meta_exc).__name__}"}
            row["metadata_lookup_sec"] = max(0.0, time.time() - _meta_t0)
            row["metadata_source"] = str(_meta.get("source") or "")
            row["metadata_provider"] = str(_meta.get("provider") or "")
            row["metadata_cache_hit"] = 1 if _meta.get("cache_hit") else 0
            if _meta.get("decimals") is None:
                row["metadata_failure_type"] = str(_meta.get("failure_type") or "unresolved")
                raise RuntimeError(
                    f"token_decimals_unresolved:{row['metadata_failure_type']}"
                    f"@{row['metadata_provider'] or 'none'}")
            decimals = int(_meta["decimals"])
            raw_amount = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
            row["token_decimals"] = decimals
            row["token_raw_amount"] = raw_amount
        except Exception as exc:
            if row.get("metadata_lookup_sec") is None:
                row["metadata_lookup_sec"] = max(0.0, time.time() - _meta_t0)
            row.update(probe_status="decimals_failed",
                       probe_error=f"{type(exc).__name__}: {exc}"[:200],
                       integrity_status="NO_TOKEN_RESOLUTION")
            return _insert(conn, row)

        if raw_amount <= 0:
            row.update(probe_status="zero_quantity", integrity_status="NO_TOKEN_RESOLUTION")
            return _insert(conn, row)

        # Quote across the SAME bounded tiers the live sell would use. No
        # chasing beyond them: _sell_slippage_tiers() caps at 3000 bps.
        best = None
        # STOP_BASIS_REPAIR_20260804: quote_start_ts is stamped BEFORE the
        # network work and quote_end_ts AFTER it. The previous code stamped a
        # single q_ts before the loop and then used it for both
        # trigger_to_quote_sec (which therefore measured only schema setup and
        # the _get_token_decimals RPC) and quote_age_sec (which actually held
        # the Jupiter round trip). The gate checks trigger_to_quote_sec, so it
        # was thresholding the wrong interval.
        quote_start_ts = time.time()
        try:
            for bps in LT._sell_slippage_tiers():
                q = LT._get_jupiter_quote(mint, LT._SOL_MINT, raw_amount, bps)
                if not q:
                    continue
                out_amt = int(q.get("outAmount", 0) or 0)
                if out_amt <= 0 or not LT.validate_jupiter_route(q):
                    continue
                cand = {"bps": bps, "q": q, "out": out_amt}
                best = cand
                # Primary route authority: the live seller uses one primary
                # quote and at most one fallback. Once a valid primary route
                # exists, requesting every higher-slippage tier only adds a
                # second network round trip and makes stop readiness measure
                # quote-shopping latency rather than executable availability.
                break
        except Exception as exc:
            row.update(probe_status="quote_error", probe_error=f"{type(exc).__name__}:{exc}"[:200])

        quote_end_ts = time.time()
        row["quote_start_ts"] = quote_start_ts
        row["quote_end_ts"] = quote_end_ts
        # SENTINUITY_EXIT_INFRA_20260805: metadata lookup is reported on its own
        # axis, so pre_quote_setup_sec now measures only non-metadata setup.
        # trigger_to_quote_sec below is unchanged and remains the gated metric.
        row["pre_quote_setup_sec"] = max(
            0.0, (quote_start_ts - now) - float(row.get("metadata_lookup_sec") or 0.0))
        row["quote_network_sec"] = max(0.0, quote_end_ts - quote_start_ts)
        row["trigger_to_quote_sec"] = max(0.0, quote_end_ts - now)

        if best is None:
            row.update(no_route=1, probe_status="no_route",
                       integrity_status="NO_EXECUTABLE_ROUTE")
            row["quote_age_sec"] = max(0.0, time.time() - quote_end_ts)
            return _insert(conn, row)

        q = best["q"]
        row["quote_ts"] = quote_end_ts
        row["quote_age_sec"] = max(0.0, time.time() - quote_end_ts)
        row["simulation_status"] = "quote_validated"
        row["quote_slippage_bps"] = int(best["bps"])
        row["quote_in_raw"] = raw_amount
        row["quote_out_raw"] = int(best["out"])
        row["quote_min_out_raw"] = int(q.get("otherAmountThreshold") or 0) or None
        row["quote_price_impact_pct"] = abs(_f(q.get("priceImpactPct")) or 0.0)
        try:
            plan = q.get("routePlan") or []
            row["route_hops"] = len(plan)
            row["route_labels"] = json.dumps(
                [((h.get("swapInfo") or {}).get("label") or "?") for h in plan])[:300]
        except Exception:
            pass

        # Proceeds. Uses the MINIMUM output when available: that is the amount
        # the swap actually guarantees, and the honest basis for a worst-case
        # realisable stop.
        if _f(sol_usd) and _f(sol_usd) > 0:
            su = _f(sol_usd)
            row["sol_usd_source"] = "caller"
            basis_error = None
        else:
            _b = sol_usd_basis(conn)
            su = _b["value"]
            row["sol_usd_source"] = _b["source"]
            row["sol_usd_age_sec"] = _b["age_sec"]
            basis_error = _b["error"]
        row["sol_usd"] = su
        guaranteed_raw = row["quote_min_out_raw"] or row["quote_out_raw"]
        if su and guaranteed_raw:
            gross = (guaranteed_raw / 1e9) * su
            row["gross_proceeds_usd"] = gross
            nf, pf = _fee_estimates(conn, su)
            row["est_network_fee_usd"] = nf
            row["est_priority_fee_usd"] = pf
            row["est_total_fee_usd"] = nf + pf
            net = gross - nf - pf
            row["net_proceeds_usd"] = net
            size = _f(position_size_usd) or 0.0
            if size > 0:
                row["executable_pct"] = (net - size) / size * 100.0
                row["executable_loss_usd"] = net - size
                if row["credited_stop_pct"] is not None:
                    row["credited_stop_usd"] = size * row["credited_stop_pct"] / 100.0
                    row["realisability_gap_pct"] = (row["executable_pct"]
                                                    - row["credited_stop_pct"])
            qty = _f(quantity) or 0.0
            if qty > 0:
                row["expected_exec_price"] = gross / qty

        lm = latency_model(conn)
        row["latency_model"] = lm["model"]
        row["latency_p50_sec"] = lm["p50"]
        row["latency_p90_sec"] = lm["p90"]
        row["latency_adjusted_pct"] = row.get("executable_pct")

        # STOP_BASIS_REPAIR_20260804: a probe that could not price its output
        # measured nothing about realisability. It must not be recorded as a
        # plain success, because coverage() and readiness() count probe_status
        # = 'ok' as usable evidence.
        row["basis_version"] = BASIS_VERSION
        if row.get("executable_pct") is not None:
            row["probe_status"] = "ok"
            row["integrity_status"] = "EXECUTABLE_MEASURED"
        else:
            row["probe_status"] = "ok_no_usd_basis"
            row["integrity_status"] = "QUOTE_ONLY_NO_USD_BASIS"
            if basis_error and not row.get("probe_error"):
                row["probe_error"] = str(basis_error)[:200]
        return _insert(conn, row)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# STOP_BASIS_REPAIR_20260804 — SOL/USD BASIS RESOLUTION
#
# The previous implementation silently returned None on every call, which made
# executable_pct null on every row and left readiness permanently at
# RESEARCH_SAMPLE_INCOMPLETE. Two independent faults:
#
#   1. It read config key SOL_USD_PRICE. The key this runtime actually
#      populates is SOLANA_USD_PRICE.
#   2. Its fallback ran "SELECT ... FROM mtm_ticks" against the *supplied*
#      connection, which is sentinuity_matrix.db. mtm_ticks lives in
#      sentinuity_intelligence.db. Every other consumer opens that file
#      explicitly (execution_engine.py:269, :667, :2090, :6499;
#      edge_shadow_tracker.py:79). This one did not, so the query raised
#      "no such table: mtm_ticks" into a bare except that discarded it.
#
# This resolver returns provenance, not just a number, and fails closed with a
# recorded reason rather than silently.
# ─────────────────────────────────────────────────────────────────────────────
WSOL_MINT = "So11111111111111111111111111111111111111112"
INTEL_DB_NAME = "sentinuity_intelligence.db"

# A basis older than this is not used. SOL/USD moving 1% shifts executable_pct
# by about a full point against an -8% median threshold, so staleness matters.
MAX_BASIS_AGE_SEC = float(os.environ.get("STOP_PROBE_MAX_BASIS_AGE_SEC", "900"))


def _intel_db_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / INTEL_DB_NAME


def _config_basis(key: str):
    """(value, age_sec) from system_config. age is None when untimestamped."""
    try:
        from core.schema import get_config_value
        v = _f(get_config_value(key, None))
    except Exception:
        return None, None
    if not v or v <= 0:
        return None, None
    age = None
    try:
        from core.schema import get_connection
        with get_connection() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(system_config)")}
            if "updated_at" in cols:
                r = c.execute("SELECT updated_at FROM system_config WHERE key=?",
                              (key,)).fetchone()
                if r and r[0]:
                    age = max(0.0, time.time() - float(r[0]))
    except Exception:
        age = None
    return v, age


def _intel_basis():
    """(value, age_sec) from mtm_ticks in the INTELLIGENCE database."""
    ic = None
    try:
        p = _intel_db_path()
        if not p.exists():
            return None, None
        ic = sqlite3.connect(str(p), timeout=3.0)
        r = ic.execute(
            "SELECT price_usd, ts_ms FROM mtm_ticks WHERE mint_address=? "
            "ORDER BY ts_ms DESC LIMIT 1", (WSOL_MINT,)).fetchone()
        if not r or not r[0]:
            return None, None
        val = _f(r[0])
        if not val or val <= 0:
            return None, None
        age = None
        if r[1]:
            age = max(0.0, time.time() - (float(r[1]) / 1000.0))
        return val, age
    except Exception:
        return None, None
    finally:
        if ic is not None:
            try:
                ic.close()
            except Exception:
                pass


def sol_usd_basis(conn=None) -> Dict[str, Any]:
    """
    Resolve the SOL/USD basis with provenance.

    Returns {"value", "source", "age_sec", "error"}. `value` is None when no
    usable basis exists; `error` then explains why. `conn` is accepted for
    signature compatibility and is deliberately NOT used for mtm_ticks.
    """
    out = {"value": None, "source": None, "age_sec": None, "error": None}
    tried = []

    for key, label in (("SOLANA_USD_PRICE", "config:SOLANA_USD_PRICE"),
                       ("SOL_USD_PRICE", "config:SOL_USD_PRICE(legacy)")):
        v, age = _config_basis(key)
        if v is None:
            tried.append(f"{key}=absent")
            continue
        if age is not None and age > MAX_BASIS_AGE_SEC:
            tried.append(f"{key}=stale({age:.0f}s)")
            continue
        out.update(value=v, source=label, age_sec=age)
        return out

    v, age = _intel_basis()
    if v is None:
        tried.append("intel_mtm_ticks=unavailable")
    elif age is not None and age > MAX_BASIS_AGE_SEC:
        tried.append(f"intel_mtm_ticks=stale({age:.0f}s)")
    else:
        out.update(value=v, source=f"intel_db:mtm_ticks:{WSOL_MINT[:8]}", age_sec=age)
        return out

    out["error"] = "no_usd_basis:" + ",".join(tried)
    return out


def _sol_usd(conn=None) -> Optional[float]:
    """Backward-compatible scalar accessor. Prefer sol_usd_basis()."""
    return sol_usd_basis(conn)["value"]


def _fee_estimates(conn, sol_usd: float):
    """Network + priority fee in USD. Config-driven, conservative defaults."""
    try:
        from core.schema import get_config_value
        base = _f(get_config_value("LIVE_BASE_FEE_SOL", 0.000005)) or 0.000005
        prio = _f(get_config_value("LIVE_PRIORITY_FEE_SOL", 0.0005)) or 0.0005
    except Exception:
        base, prio = 0.000005, 0.0005
    return base * sol_usd, prio * sol_usd


def _insert(conn, row: Dict[str, Any]) -> Optional[int]:
    try:
        cols = ", ".join(row.keys())
        qs = ", ".join("?" for _ in row)
        cur = conn.execute(f"INSERT INTO {LEDGER_TABLE} ({cols}) VALUES ({qs})",
                           list(row.values()))
        return int(cur.lastrowid)
    except Exception:
        return None


def coverage(conn) -> Dict[str, Any]:
    """Sign-off statistics. Read-only."""
    COHORT_WHERE = _cohort_where()
    out = {"n": 0, "quote_coverage_pct": 0.0, "no_route_pct": 0.0,
           "median_executable_pct": None, "p75": None, "p90": None, "worst": None,
           "latency_model": latency_model(conn)["model"]}
    try:
        out["n"] = conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} {COHORT_WHERE}").fetchone()[0]
        if not out["n"]:
            return out
        ok = conn.execute(f"SELECT COUNT(*) FROM {LEDGER_TABLE} "
                          f"{COHORT_WHERE} AND probe_status='ok'").fetchone()[0]
        nr = conn.execute(f"SELECT COUNT(*) FROM {LEDGER_TABLE} "
                          f"{COHORT_WHERE} AND no_route=1").fetchone()[0]
        out["quote_coverage_pct"] = 100.0 * ok / out["n"]
        out["no_route_pct"] = 100.0 * nr / out["n"]
        vals = sorted(float(r[0]) for r in conn.execute(
            f"SELECT executable_pct FROM {LEDGER_TABLE} "
            f"{COHORT_WHERE} AND executable_pct IS NOT NULL") if r[0] is not None)
        if vals:
            def q(p):
                return vals[max(0, min(len(vals) - 1, int(round(p * (len(vals) - 1)))))]
            out.update(median_executable_pct=q(0.50), p75=q(0.25),
                       p90=q(0.10), worst=vals[0])
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Live-readiness thresholds
#
# These are the sign-off gate. They are deliberately strict and are NEVER
# relaxed to reach live status: the point of the measurement is to be capable
# of returning "no".
# ─────────────────────────────────────────────────────────────────────────────
MIN_SAMPLES_PREFERRED = 100
MIN_SAMPLES_ABSOLUTE = 50
MIN_QUOTE_COVERAGE_PCT = 95.0
MAX_NO_ROUTE_PCT = 3.0
MAX_MEDIAN_STOP_PCT = -8.0      # median must be no worse than this
MAX_P90_STOP_PCT = -15.0
MAX_WORST_STOP_PCT = -25.0
MAX_MEDIAN_TRIGGER_TO_QUOTE_SEC = 1.5
MAX_P90_TRIGGER_TO_QUOTE_SEC = 3.0
# STOP_TRUTH_SIGNOFF_20260808: trigger_to_quote_sec measures only probe-internal
# latency (our own call -> Jupiter's reply) and was passing comfortably. The
# metric that actually explains a -31% median is the age of the MARK that fired
# the stop: the engine cannot react to a collapse it has not observed yet.
# trigger_mark_age_sec was already recorded on every row and gated by nothing.
# Mode-B rejections in the 2026-08-07/08 window show marks of 31s-141s. A stop
# commanded on a 60s-old price on a token of this volatility is not a -4% stop
# in any meaningful sense, and the executable measurement was correctly
# reporting the consequence.
MAX_MEDIAN_TRIGGER_MARK_AGE_SEC = 5.0
MAX_P90_TRIGGER_MARK_AGE_SEC = 15.0
# Concentration guard for the reduced-sample path: no single mint may supply
# more than this share, so 50 probes of one token cannot stand in for 50 probes.
MAX_MINT_CONCENTRATION_PCT = 25.0

STATUS_INCOMPLETE = "RESEARCH_SAMPLE_INCOMPLETE"
STATUS_FAILED = "STOP_REALISABILITY_FAILED"
STATUS_READY = "STOP_REALISABILITY_PASSED"


def _pctile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))]


def readiness(conn) -> Dict[str, Any]:
    """
    Evaluate the stop-realisability sign-off gate.

    Returns {"status": ..., "blocking": [...], "stats": {...}}.
    FAIL-CLOSED: any error returns STATUS_INCOMPLETE with the error recorded.
    """
    out = {"status": STATUS_INCOMPLETE, "blocking": [], "stats": {}}
    COHORT_WHERE = _cohort_where()
    try:
        ensure_schema(conn)
        n = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} {COHORT_WHERE}").fetchone()[0] or 0)
        ok_n = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} {COHORT_WHERE} AND probe_status='ok'"
        ).fetchone()[0] or 0)
        nr = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} {COHORT_WHERE} AND no_route=1"
        ).fetchone()[0] or 0)

        pcts = [float(r[0]) for r in conn.execute(
            f"SELECT executable_pct FROM {LEDGER_TABLE} "
            f"{COHORT_WHERE} AND executable_pct IS NOT NULL")
            if r[0] is not None]
        lats = [float(r[0]) for r in conn.execute(
            f"SELECT trigger_to_quote_sec FROM {LEDGER_TABLE} "
            f"{COHORT_WHERE} AND trigger_to_quote_sec IS NOT NULL") if r[0] is not None]
        mark_ages = [float(r[0]) for r in conn.execute(
            f"SELECT trigger_mark_age_sec FROM {LEDGER_TABLE} "
            f"{COHORT_WHERE} AND trigger_mark_age_sec IS NOT NULL") if r[0] is not None]

        cov = (100.0 * ok_n / n) if n else 0.0
        nrp = (100.0 * nr / n) if n else 0.0
        top_mint = conn.execute(
            f"SELECT mint_address, COUNT(*) c FROM {LEDGER_TABLE} "
            f"{COHORT_WHERE} GROUP BY mint_address ORDER BY c DESC LIMIT 1").fetchone()
        conc = (100.0 * float(top_mint[1]) / n) if (n and top_mint) else 0.0

        st = {
            "n": n, "quote_coverage_pct": cov, "no_route_pct": nrp,
            "median_executable_pct": _pctile(pcts, 0.50),
            "p90_executable_pct": _pctile(pcts, 0.10),   # worst decile
            "worst_executable_pct": min(pcts) if pcts else None,
            "median_trigger_to_quote_sec": _pctile(lats, 0.50),
            "p90_trigger_to_quote_sec": _pctile(lats, 0.90),
            "median_trigger_mark_age_sec": _pctile(mark_ages, 0.50),
            "p90_trigger_mark_age_sec": _pctile(mark_ages, 0.90),
            "mark_age_samples": len(mark_ages),
            "max_mint_concentration_pct": conc,
            "latency_model": latency_model(conn)["model"],
        }
        out["stats"] = st
        B = out["blocking"]

        # Sample sufficiency. The reduced floor is available only when coverage
        # is high AND the sample is not concentrated in one token.
        if n < MIN_SAMPLES_ABSOLUTE:
            B.append(f"samples {n} < absolute minimum {MIN_SAMPLES_ABSOLUTE}")
        elif n < MIN_SAMPLES_PREFERRED:
            if cov < MIN_QUOTE_COVERAGE_PCT:
                B.append(f"reduced-sample path needs coverage >= "
                         f"{MIN_QUOTE_COVERAGE_PCT}%, have {cov:.1f}%")
            if conc > MAX_MINT_CONCENTRATION_PCT:
                B.append(f"sample concentrated: one mint is {conc:.0f}% "
                         f"(max {MAX_MINT_CONCENTRATION_PCT:.0f}%)")

        if cov < MIN_QUOTE_COVERAGE_PCT:
            B.append(f"quote coverage {cov:.1f}% < {MIN_QUOTE_COVERAGE_PCT}%")
        if nrp > MAX_NO_ROUTE_PCT:
            B.append(f"no-route {nrp:.1f}% > {MAX_NO_ROUTE_PCT}%")

        failed = False
        if st["median_executable_pct"] is None:
            B.append("no executable_pct measured")
        else:
            if st["median_executable_pct"] < MAX_MEDIAN_STOP_PCT:
                B.append(f"median stop {st['median_executable_pct']:.1f}% worse "
                         f"than {MAX_MEDIAN_STOP_PCT}%"); failed = True
            if st["p90_executable_pct"] < MAX_P90_STOP_PCT:
                B.append(f"p90 stop {st['p90_executable_pct']:.1f}% worse "
                         f"than {MAX_P90_STOP_PCT}%"); failed = True
            if st["worst_executable_pct"] < MAX_WORST_STOP_PCT:
                B.append(f"worst stop {st['worst_executable_pct']:.1f}% worse "
                         f"than {MAX_WORST_STOP_PCT}%"); failed = True

        if st["median_trigger_to_quote_sec"] is not None:
            if st["median_trigger_to_quote_sec"] > MAX_MEDIAN_TRIGGER_TO_QUOTE_SEC:
                B.append(f"median trigger->quote "
                         f"{st['median_trigger_to_quote_sec']:.2f}s > "
                         f"{MAX_MEDIAN_TRIGGER_TO_QUOTE_SEC}s")
            if st["p90_trigger_to_quote_sec"] > MAX_P90_TRIGGER_TO_QUOTE_SEC:
                B.append(f"p90 trigger->quote "
                         f"{st['p90_trigger_to_quote_sec']:.2f}s > "
                         f"{MAX_P90_TRIGGER_TO_QUOTE_SEC}s")

        # STOP_TRUTH_SIGNOFF_20260808: stale marks are a hard failure, not an
        # incomplete sample. Firing live money on a price this old is the
        # specific danger the governor exists to prevent, and naming it
        # explicitly tells the operator WHICH repair unblocks live.
        if st["median_trigger_mark_age_sec"] is None:
            B.append("trigger_mark_age_sec not measured on any probe")
        else:
            if st["median_trigger_mark_age_sec"] > MAX_MEDIAN_TRIGGER_MARK_AGE_SEC:
                B.append(f"median trigger mark age "
                         f"{st['median_trigger_mark_age_sec']:.1f}s > "
                         f"{MAX_MEDIAN_TRIGGER_MARK_AGE_SEC}s -- stops fire on "
                         f"prices too old to be actionable"); failed = True
            if st["p90_trigger_mark_age_sec"] > MAX_P90_TRIGGER_MARK_AGE_SEC:
                B.append(f"p90 trigger mark age "
                         f"{st['p90_trigger_mark_age_sec']:.1f}s > "
                         f"{MAX_P90_TRIGGER_MARK_AGE_SEC}s"); failed = True

        if B:
            out["status"] = STATUS_FAILED if failed else STATUS_INCOMPLETE
        else:
            out["status"] = STATUS_READY
        return out
    except Exception as exc:
        out["blocking"].append(f"readiness_error:{type(exc).__name__}")
        return out
