# -*- coding: utf-8 -*-
"""
SENTINUITY — CANONICAL POSITION TRUTH PAYLOAD
=============================================

ONE read-only presentation payload per position. Every UI surface — the living
trade meter, Truth Lens, the Sovereign Glassbox execution trace, and any future
signed-log instrument — must consume THIS and only this.

Contract
--------
  * READ ONLY. Opens every database with ``mode=ro``. No writes, ever.
  * NO EXECUTION IMPORTS. This module must never become an execution dependency.
  * NEVER RAISES into a renderer. Every failure degrades to an explicit ABSENT
    status with a reason code.
  * NEVER PROMOTES a stage. An observation is not an executable quote. A row
    write is not a market tick. A pool simulation is not a signed route.
  * NEVER SUBSTITUTES. If a stage has no evidence, the field is ``None`` and the
    status says why. Absence is rendered as absence.

Why this module exists
----------------------
Before this, four surfaces each derived PnL independently:

  services/sovereign_hub.fetch_live_open_positions_breathe()
      live_exec_pct, overridden by an unfiltered mtm_ticks read, with freshness
      taken from the ROW WRITE TIME (live_exec_updated_at).

  services/sovereign_hub.truth_lens_modal()
      live_exec_pct directly, with a different staleness ladder, then passed a
      RAW DB ROW into render_living_trade_meter() which expects the breathe
      payload shape — so the meter inside Truth Lens always read n/a.

  ui/sovereign_glassbox._panel_execution_trace()
      paper_positions.unrealized_pnl_pct (written by a SEPARATE process),
      A from peak_onchain_state, C from peak_executable_quotes.

  ui/execution_glassbox.render_execution_glassbox()
      paper_positions.current_price. (Unmounted/dead as of this writing.)

Four derivations, four answers, one position.

Semantic stages
---------------
  D  OBSERVATION    A price someone saw. Provenance-classified, never assumed
                    executable. Source: intelligence.mtm_ticks (carrying its
                    real ``source`` subtype), else matrix.market_snapshots.
  A  POOL           On-chain reserve-derived state. price_truth.peak_onchain_state.
  B  TAPE           Signature-backed realised trade. price_truth.peak_trade_tape.
  C  EXECUTABLE     A route that could be signed for the ACTUAL position size.
                    price_truth.peak_executable_quotes, EXCLUDING rows whose
                    provider is the pool simulation re-registered as a quote
                    (see EXCLUDED_QUOTE_PROVIDERS) — that is layer A wearing
                    layer C's badge and it is why A<->C divergence reads 0.0%.
  TRUSTED           Adjudicated high-water mark. price_truth.peak_truth_candidates.
  PROTECTED         Armed runner floor. A GUARANTEE, never a current value.

Freshness vocabulary (all four are distinct and all four are carried)
---------------------------------------------------------------------
  row_write_age_sec     how long ago OUR ROW was written
  source_market_age_sec how long ago the MARKET produced the observation
  quote_age_sec         how long ago the executable quote was returned
  fallback_age_sec      age of the degraded/fallback source, if one was used

A row written 2 seconds ago from a 10-minute-old upstream price has
row_write_age_sec=2 and source_market_age_sec=600. It is NOT fresh. The UI may
display row_write_age only when explicitly labelled as write recency.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SERVICE = "position_truth_payload"
PAYLOAD_CONTRACT_VERSION = "2.0.0"

# ── stage status vocabulary ──────────────────────────────────────────────────
ST_ABSENT = "ABSENT"            # no evidence of any kind
ST_FRESH = "FRESH"              # evidence exists and is inside its age budget
ST_STALE = "STALE"              # evidence exists but is outside its age budget
ST_UNTRUSTED = "UNTRUSTED"      # evidence exists, provenance forbids authority
ST_QUARANTINED = "QUARANTINED"  # evidence exists, integrity check rejected it
ST_WITHHELD = "WITHHELD"        # stage deliberately not granted (e.g. floor not armed)

# ── authority stages, lowest to highest ──────────────────────────────────────
AUTH_NONE = "NONE"
AUTH_OBSERVED = "OBSERVED"
AUTH_POOL = "POOL"
AUTH_TAPE = "TAPE"
AUTH_EXECUTABLE = "EXECUTABLE"
AUTH_TRUSTED = "TRUSTED"
AUTH_PROTECTED = "PROTECTED"

_AUTH_RANK = {
    AUTH_NONE: 0, AUTH_OBSERVED: 1, AUTH_POOL: 2, AUTH_TAPE: 3,
    AUTH_EXECUTABLE: 4, AUTH_TRUSTED: 5, AUTH_PROTECTED: 6,
}

# ── reason codes (closed set; renderers map code -> text, never invent) ──────
R_OK = "OK"
R_NO_POSITION = "NO_POSITION_ROW"
R_NO_ENTRY = "NO_POSITIVE_ENTRY_PRICE"
R_NO_OBS = "NO_OBSERVATION_AT_OR_AFTER_OPEN"
R_OBS_STALE = "OBSERVATION_OLDER_THAN_BUDGET"
R_OBS_UNTRUSTED = "OBSERVATION_SUBTYPE_NOT_TRUSTED"
R_NO_QUOTE = "NO_EXECUTABLE_QUOTE_ROW"
R_QUOTE_STALE = "QUOTE_OLDER_THAN_BUDGET"
R_QUOTE_NOT_SELLABLE = "QUOTE_NOT_SELLABLE"
R_QUOTE_DIAGNOSTIC = "QUOTE_DIAGNOSTIC_ONLY"
R_QUOTE_IS_POOL_ECHO = "QUOTE_IS_POOL_SIMULATION_ECHO"
R_NO_TRUSTED = "NO_ADJUDICATED_TRUSTED_PEAK"
R_FLOOR_NOT_ARMED = "RUNNER_FLOOR_NOT_ARMED"
R_DB_UNAVAILABLE = "DATABASE_UNAVAILABLE"

# ── age budgets (presentation only; these do NOT gate execution) ─────────────
OBS_FRESH_SEC = float(os.environ.get("SNT_OBS_FRESH_SEC", "15"))
QUOTE_FRESH_SEC = float(os.environ.get("SNT_QUOTE_FRESH_SEC", "20"))
LAYER_FRESH_SEC = float(os.environ.get("SNT_LAYER_FRESH_SEC", "30"))

# Providers whose rows sit in peak_executable_quotes but are NOT independent
# executable evidence. price_truth_mesh.py registers the bonding-curve exact-size
# simulation under this identity, using virtual_token_reserves as both
# quote_out_raw and min_out_raw. It is layer A arithmetic, not a signable route.
EXCLUDED_QUOTE_PROVIDERS = frozenset({
    "pump_curve_exact_sell",
})


# ── presentation contract v2: socket states + fixed shared axis ─────────────
# These are PRESENTATION states, not execution authority.  The payload computes
# them once so every renderer remains stateless.
SOCK_OCCUPIED = "OCCUPIED"
SOCK_REJECTED = "REJECTED"
SOCK_VERIFYING = "VERIFYING"
SOCK_UNAVAILABLE = "UNAVAILABLE"

# One fixed axis for every position card.  Entry never moves, so positions are
# directly comparable by eye across rows.  Mapping is visual only.
AXIS_DATUM_PCT = 23.0
AXIS_LEFT_CLAMP_PCT = 2.5
AXIS_RIGHT_CLAMP_PCT = 97.0
AXIS_LOSS_MAX_PCT = 100.0
AXIS_GAIN_KNEE_PCT = 120.0
AXIS_GAIN_TAIL_MAX_PCT = 2000.0


def axis_position_pct(pnl_pct: Any) -> Optional[float]:
    """Map position PnL to the shared asymmetric signed-log display axis.

    No trading threshold is encoded here.  This is geometry only.
    """
    x = _f(pnl_pct)
    if x is None:
        return None
    if x <= 0:
        # High resolution around entry, increasingly compressed into the loss
        # region.  -100% lands at the left visual clamp.
        mag = min(abs(x), AXIS_LOSS_MAX_PCT)
        frac = math.log1p(mag / 4.0) / math.log1p(AXIS_LOSS_MAX_PCT / 4.0)
        return max(AXIS_LEFT_CLAMP_PCT,
                   AXIS_DATUM_PCT - frac * (AXIS_DATUM_PCT - AXIS_LEFT_CLAMP_PCT))
    if x <= AXIS_GAIN_KNEE_PCT:
        # Profit receives more visual room than loss.  +120% reaches ~69%.
        frac = math.log1p(x / 6.0) / math.log1p(AXIS_GAIN_KNEE_PCT / 6.0)
        return AXIS_DATUM_PCT + frac * (68.88 - AXIS_DATUM_PCT)
    # Soft-log tail: +120% -> 68.88, +2000% -> 97.  Extreme observations remain
    # visible without crushing the entry / stop region.
    tail = math.log(x / AXIS_GAIN_KNEE_PCT) / math.log(
        AXIS_GAIN_TAIL_MAX_PCT / AXIS_GAIN_KNEE_PCT
    )
    return min(AXIS_RIGHT_CLAMP_PCT, 68.88 + max(0.0, tail) * (AXIS_RIGHT_CLAMP_PCT - 68.88))


def axis_overflow(pnl_pct: Any) -> str:
    x = _f(pnl_pct)
    if x is None:
        return ""
    if x < -AXIS_LOSS_MAX_PCT:
        return "low"
    if x > AXIS_GAIN_TAIL_MAX_PCT:
        return "high"
    return ""


def _socket_for(stage: Dict[str, Any], *, protected: bool = False) -> str:
    status = str(stage.get("status") or ST_ABSENT).upper()
    has_coord = stage.get("pnl_pct") is not None
    if status == ST_FRESH and has_coord:
        return SOCK_OCCUPIED
    if protected and status == ST_WITHHELD:
        return SOCK_UNAVAILABLE
    if status in (ST_QUARANTINED, ST_UNTRUSTED) and has_coord:
        return SOCK_REJECTED
    if status in (ST_STALE,) and has_coord:
        return SOCK_VERIFYING
    if has_coord:
        return SOCK_VERIFYING
    return SOCK_UNAVAILABLE


def _stage_view(stage: Dict[str, Any], *, protected: bool = False) -> Dict[str, Any]:
    out = dict(stage or {})
    out["axis_pct"] = axis_position_pct(out.get("pnl_pct"))
    out["axis_overflow"] = axis_overflow(out.get("pnl_pct"))
    out["socket_state"] = _socket_for(out, protected=protected)
    return out


def _visual_divergence_strength(delta_pts: Any) -> Optional[float]:
    """Normalised geometric intensity only; NEVER an authority threshold."""
    d = _f(delta_pts)
    if d is None:
        return None
    # smooth saturation, so tiny disagreements remain visible without allowing
    # the renderer to invent a policy boundary.
    return max(0.0, min(1.0, 1.0 - math.exp(-abs(d) / 75.0)))


# ── default database locations ───────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MATRIX_DB = _ROOT / "sentinuity_matrix.db"
DEFAULT_PRICE_TRUTH_DB = _ROOT / "sentinuity_price_truth.db"
DEFAULT_INTEL_DB = _ROOT / "sentinuity_intelligence.db"


# ═════════════════════════════ primitives ════════════════════════════════════

def _f(v: Any) -> Optional[float]:
    """Float or None. NaN and inf are None, not numbers."""
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _pos_int(v: Any) -> int:
    try:
        n = int(str(v).strip() or 0)
        return n if n > 0 else 0
    except Exception:
        return 0


def _pct_from(entry: Any, price: Any) -> Optional[float]:
    # SIGNOFF_MARK_TRUTH_20260812 (surviving -100% formula, site 3 of 3):
    # Same defect shape as ui/execution_glassbox._pct — the denominator was
    # guarded, the numerator was not, so a sentinel/missing zero mark produced
    # exactly -100.0% rather than None.
    #
    # This site matters more than the other two because position_truth_payload
    # is the CANONICAL truth payload other surfaces consume. A -100% invented
    # here propagates to every renderer downstream, including any that correctly
    # trusts this module rather than recomputing. Returning None makes the
    # absence of an executable mark explicit and lets each surface render
    # NO MARK / ACQUIRING MARK / UNAVAILABLE.
    #
    # p > 0 quotes are untouched; a real near-zero mark still reports its real
    # result. Negative PnL is never clamped.
    e, p = _f(entry), _f(price)
    if e is None or p is None or e <= 0 or p <= 0:
        return None
    return (p / e - 1.0) * 100.0


def _epoch(ts: Any) -> Optional[float]:
    """Normalise a timestamp to epoch SECONDS.

    mtm_ticks.ts_ms is milliseconds; several other tables carry seconds. A
    magnitude test is safer than trusting the column name, because a writer that
    ever put seconds in a _ms column produces a 55-year-old 'observation' that
    silently disappears behind an age filter.
    """
    x = _f(ts)
    if x is None or x <= 0:
        return None
    if x > 1e11:          # milliseconds
        return x / 1000.0
    if x > 1e10:          # ambiguous band -> treat as ms, flag via age sanity
        return x / 1000.0
    return x


def _age(ts: Any, now: float) -> Optional[float]:
    e = _epoch(ts)
    if e is None:
        return None
    return max(0.0, now - e)


def _ro(path: Any) -> Optional[sqlite3.Connection]:
    try:
        p = Path(str(path))
        if not p.exists():
            return None
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=0.75)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def _has_table(c: Optional[sqlite3.Connection], name: str) -> bool:
    if c is None:
        return False
    try:
        return c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except Exception:
        return False


def _cols(c: Optional[sqlite3.Connection], name: str) -> set:
    if c is None:
        return set()
    try:
        return {str(r[1]) for r in c.execute(f"PRAGMA table_info({name})").fetchall()}
    except Exception:
        return set()


def _row(c: Optional[sqlite3.Connection], sql: str, args: Iterable = ()) -> Dict[str, Any]:
    if c is None:
        return {}
    try:
        r = c.execute(sql, tuple(args)).fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}


def _provenance():
    """mark_provenance if importable. Absence means 'cannot prove trust'."""
    try:
        from services import mark_provenance as mp  # type: ignore
        return mp
    except Exception:
        try:
            import mark_provenance as mp  # type: ignore
            return mp
        except Exception:
            return None


def _classify_observation(raw_source: Any, price: Any) -> Dict[str, Any]:
    mp = _provenance()
    if mp is None:
        return {
            "subtype": "unknown",
            "trusted": False,
            "degraded": True,
            "precision_class": "unknown",
            "witness": "UNVERIFIABLE",
        }
    try:
        return {
            "subtype": str(mp.classify_subtype(raw_source)),
            "trusted": bool(mp.is_trusted_subtype(raw_source)),
            "degraded": bool(mp.is_degraded_source(raw_source)),
            "precision_class": str(mp.precision_class(price)),
            "witness": str(mp.witness_id(raw_source)),
        }
    except Exception:
        return {
            "subtype": "unknown", "trusted": False, "degraded": True,
            "precision_class": "unknown", "witness": "UNVERIFIABLE",
        }


def _divergence_pct(a: Any, b: Any) -> Optional[float]:
    """Symmetric percentage gap between two prices. None if either is missing."""
    x, y = _f(a), _f(b)
    if x is None or y is None or x <= 0 or y <= 0:
        return None
    return abs(x - y) / max(x, y) * 100.0


def _blank_stage(status: str = ST_ABSENT, reason: str = R_OK) -> Dict[str, Any]:
    return {
        "price": None, "pnl_pct": None, "source": None, "age_sec": None,
        "status": status, "reason_code": reason, "slot": None,
        "row_id": None, "table": None, "database": None, "column": None,
        "timestamp": None,
    }


# ═════════════════════════ stage hydration ═══════════════════════════════════

def _hydrate_observation(intel, matrix, mint: str, opened_at: float,
                         entry: float, now: float) -> Dict[str, Any]:
    """Layer D. The freshest MARKET OBSERVATION, provenance-labelled.

    Scoped to this mint and to ts >= opened_at - 0.5s, matching
    price_router._read_intel(). The unscoped global 'ORDER BY ts_ms DESC LIMIT
    200' read in fetch_live_open_positions_breathe() is not reproduced here: it
    can return a tick that predates the position open, and it silently drops any
    mint whose newest tick falls outside the global 200 most recent rows.
    """
    st = _blank_stage(ST_ABSENT, R_NO_OBS)
    st["fallback_used"] = False

    if _has_table(intel, "mtm_ticks"):
        c = _cols(intel, "mtm_ticks")
        src_col = "source" if "source" in c else None
        sel = "price_usd, ts_ms" + (", COALESCE(source,'unknown')" if src_col else "")
        r = _row(
            intel,
            f"SELECT {sel}, rowid FROM mtm_ticks "
            "WHERE mint_address=? AND ts_ms >= ? ORDER BY ts_ms DESC LIMIT 1",
            (mint, (float(opened_at or 0) - 0.5) * 1000.0),
        )
        if r:
            vals = list(r.values())
            price = _f(vals[0])
            ts = vals[1]
            raw_src = str(vals[2]) if src_col else "unknown"
            if price and price > 0:
                prov = _classify_observation(raw_src, price)
                age = _age(ts, now)
                st.update({
                    "price": price,
                    "pnl_pct": _pct_from(entry, price),
                    "source": f"intel-mtm:{prov['subtype']}:{raw_src}"[:120],
                    "age_sec": age,
                    "slot": None,
                    "row_id": r.get("rowid"),
                    "table": "mtm_ticks",
                    "database": "sentinuity_intelligence.db",
                    "column": "price_usd",
                    "timestamp": _epoch(ts),
                    "raw_source": raw_src,
                    "subtype": prov["subtype"],
                    "precision_class": prov["precision_class"],
                    "witness": prov["witness"],
                    "trusted_subtype": prov["trusted"],
                    "degraded_subtype": prov["degraded"],
                })
                # An observation is UNTRUSTED only when its source is a
                # recovery/failsafe/synthetic path. A vendor quote is a real
                # observation; it simply may not advance a runner peak. That
                # narrower judgement is carried in `trusted_subtype` for the
                # renderer to express (hollow vs solid), never by suppressing
                # the observation itself.
                if age is None or age > OBS_FRESH_SEC:
                    st["status"], st["reason_code"] = ST_STALE, R_OBS_STALE
                elif prov["degraded"]:
                    st["status"], st["reason_code"] = ST_UNTRUSTED, R_OBS_UNTRUSTED
                else:
                    st["status"], st["reason_code"] = ST_FRESH, R_OK
                return st

    # Fallback: oracle-written mtm snapshot in the matrix DB.
    if _has_table(matrix, "market_snapshots"):
        r = _row(
            matrix,
            "SELECT id, observed_price, price_updated_at FROM market_snapshots "
            "WHERE mint_address=? AND candidate_state='mtm' AND observed_price>0 "
            "AND price_updated_at >= ? ORDER BY price_updated_at DESC LIMIT 1",
            (mint, float(opened_at or 0) - 0.5),
        )
        price = _f(r.get("observed_price"))
        if price and price > 0:
            age = _age(r.get("price_updated_at"), now)
            st.update({
                "price": price,
                "pnl_pct": _pct_from(entry, price),
                "source": "matrix-mtm-snapshot",
                "age_sec": age,
                "row_id": r.get("id"),
                "table": "market_snapshots",
                "database": "sentinuity_matrix.db",
                "column": "observed_price",
                "timestamp": _epoch(r.get("price_updated_at")),
                "raw_source": "market_snapshots:mtm",
                "subtype": "unknown",
                "precision_class": _classify_observation("mtm", price)["precision_class"],
                "witness": "UNVERIFIABLE",
                "trusted_subtype": False,
                "degraded_subtype": False,
                "fallback_used": True,
            })
            # A fallback observation is still an observation. It is marked as a
            # fallback so the renderer can hatch it; it is not silently upgraded
            # and it is never eligible for the executable stage.
            if age is None or age > OBS_FRESH_SEC:
                st["status"], st["reason_code"] = ST_STALE, R_OBS_STALE
            else:
                st["status"], st["reason_code"] = ST_FRESH, R_OK
            return st

    return st


def _hydrate_pool(truth, pid: int, entry: float, now: float) -> Dict[str, Any]:
    """Layer A. On-chain reserve-derived state."""
    st = _blank_stage()
    if not _has_table(truth, "peak_onchain_state"):
        return st
    r = _row(truth,
             "SELECT * FROM peak_onchain_state WHERE position_id=? "
             "ORDER BY observed_at DESC, id DESC LIMIT 1", (pid,))
    if not r:
        return st
    price = _f(r.get("executable_curve_price_usd")) or _f(r.get("derived_price_usd"))
    age = _age(r.get("observed_at"), now)
    integrity = str(r.get("integrity_status") or "").upper()
    st.update({
        "price": price,
        "pnl_pct": _pct_from(entry, price),
        "source": str(r.get("rpc_label") or r.get("source_kind") or "unknown")[:80],
        "age_sec": age,
        "slot": _pos_int(r.get("context_slot")) or None,
        "row_id": r.get("id"),
        "table": "peak_onchain_state",
        "database": "sentinuity_price_truth.db",
        "column": "executable_curve_price_usd|derived_price_usd",
        "timestamp": _epoch(r.get("observed_at")),
        "integrity_status": integrity or None,
        "migration_state": r.get("migration_state"),
        "account_hash": r.get("account_hash"),
    })
    if price is None:
        st["status"] = ST_ABSENT
    elif integrity in ("INVALID", "QUARANTINED") or integrity.startswith("QUARANTINED"):
        st["status"], st["reason_code"] = ST_QUARANTINED, R_QUOTE_DIAGNOSTIC
    elif age is None or age > LAYER_FRESH_SEC:
        st["status"], st["reason_code"] = ST_STALE, R_OBS_STALE
    else:
        st["status"] = ST_FRESH
    return st


def _hydrate_tape(truth, pid: int, entry: float, now: float) -> Dict[str, Any]:
    """Layer B. Signature-backed realised trade."""
    st = _blank_stage()
    if not _has_table(truth, "peak_trade_tape"):
        return st
    r = _row(truth,
             "SELECT * FROM peak_trade_tape WHERE position_id=? "
             "ORDER BY observed_at DESC, id DESC LIMIT 1", (pid,))
    if not r:
        return st
    price = _f(r.get("effective_price_usd"))
    age = _age(r.get("observed_at"), now)
    recon = str(r.get("reconciliation_status") or "").upper()
    st.update({
        "price": price,
        "pnl_pct": _pct_from(entry, price),
        "source": str(r.get("source") or r.get("source_kind") or "unknown")[:80],
        "age_sec": age,
        "slot": _pos_int(r.get("context_slot")) or None,
        "row_id": r.get("id"),
        "table": "peak_trade_tape",
        "database": "sentinuity_price_truth.db",
        "column": "effective_price_usd",
        "timestamp": _epoch(r.get("observed_at")),
        "tx_signature": r.get("tx_signature") or r.get("signature"),
        "reconciliation_status": recon or None,
    })
    if price is None:
        st["status"] = ST_ABSENT
    elif recon != "CHAIN_RECONCILED":
        st["status"], st["reason_code"] = ST_UNTRUSTED, R_QUOTE_DIAGNOSTIC
    elif age is None or age > LAYER_FRESH_SEC:
        st["status"], st["reason_code"] = ST_STALE, R_OBS_STALE
    else:
        st["status"] = ST_FRESH
    return st


def _hydrate_executable(truth, pid: int, entry: float, now: float) -> Dict[str, Any]:
    """Layer C. A route that could actually be signed for the real position size.

    Rows whose provider identity is a pool-simulation echo are read but never
    granted executable status: they carry layer A's own price, which is exactly
    why the old A<->C divergence readout showed 0.0%.
    """
    st = _blank_stage(ST_ABSENT, R_NO_QUOTE)
    st["pool_echo_excluded"] = False
    if not _has_table(truth, "peak_executable_quotes"):
        return st

    rows = []
    try:
        rows = [dict(r) for r in truth.execute(
            "SELECT * FROM peak_executable_quotes WHERE position_id=? "
            "ORDER BY quote_ts DESC, id DESC LIMIT 8", (pid,)).fetchall()]
    except Exception:
        rows = []
    if not rows:
        return st

    def _is_echo(r: Dict[str, Any]) -> bool:
        prov = str(r.get("provider_identity") or "").strip().lower()
        route = str(r.get("route") or "").strip().lower()
        return prov in EXCLUDED_QUOTE_PROVIDERS or route in EXCLUDED_QUOTE_PROVIDERS

    genuine = [r for r in rows if not _is_echo(r)]
    st["pool_echo_excluded"] = len(genuine) != len(rows)
    r = genuine[0] if genuine else rows[0]

    price = _f(r.get("effective_price_usd"))
    age = _age(r.get("quote_ts"), now)
    integrity = str(r.get("integrity_status") or "").upper()
    sellable = _pos_int(r.get("sellable")) == 1
    st.update({
        "price": price,
        "pnl_pct": _pct_from(entry, price),
        "source": str(r.get("provider_identity") or r.get("route") or "unknown")[:80],
        "age_sec": age,
        "slot": _pos_int(r.get("context_slot")) or None,
        "row_id": r.get("id"),
        "table": "peak_executable_quotes",
        "database": "sentinuity_price_truth.db",
        "column": "effective_price_usd",
        "timestamp": _epoch(r.get("quote_ts")),
        "integrity_status": integrity or None,
        "sellable": sellable,
        "route": str(r.get("route") or "")[:200] or None,
        "price_impact_pct": _f(r.get("price_impact_pct")),
        "error_class": r.get("error_class"),
        "provider_family": r.get("provider_family"),
        "quote_out_raw": r.get("quote_out_raw"),
        "min_out_raw": r.get("min_out_raw"),
        "latency_ms": _f(r.get("latency_ms")),
    })
    if not genuine:
        st["status"], st["reason_code"] = ST_UNTRUSTED, R_QUOTE_IS_POOL_ECHO
    elif price is None:
        st["status"], st["reason_code"] = ST_ABSENT, R_NO_QUOTE
    elif integrity != "VALID":
        st["status"], st["reason_code"] = ST_QUARANTINED, R_QUOTE_DIAGNOSTIC
    elif not sellable:
        st["status"], st["reason_code"] = ST_UNTRUSTED, R_QUOTE_NOT_SELLABLE
    elif age is None or age > QUOTE_FRESH_SEC:
        st["status"], st["reason_code"] = ST_STALE, R_QUOTE_STALE
    else:
        st["status"], st["reason_code"] = ST_FRESH, R_OK
    return st


def _hydrate_trusted(truth, pos: Dict[str, Any], entry: float,
                     now: float) -> Dict[str, Any]:
    """Adjudicated high-water mark. Candidate table first, position row second."""
    st = _blank_stage(ST_ABSENT, R_NO_TRUSTED)
    pid = _pos_int(pos.get("id"))
    if _has_table(truth, "peak_truth_candidates") and pid:
        r = _row(truth,
                 "SELECT * FROM peak_truth_candidates WHERE position_id=? "
                 "ORDER BY candidate_ts DESC, id DESC LIMIT 1", (pid,))
        if r:
            price = _f(r.get("trusted_price_usd"))
            state = str(r.get("state") or "").upper()
            st.update({
                "price": price,
                "pnl_pct": _pct_from(entry, price),
                "source": f"peak_truth_candidates:{state or 'UNKNOWN'}"[:80],
                "age_sec": _age(r.get("candidate_ts"), now),
                "row_id": r.get("id"),
                "table": "peak_truth_candidates",
                "database": "sentinuity_price_truth.db",
                "column": "trusted_price_usd",
                "timestamp": _epoch(r.get("candidate_ts")),
                "adjudicator_state": state or None,
                "adjudicator_reason": str(r.get("reason") or "")[:200] or None,
                "divergence_pct": _f(r.get("divergence_pct")),
            })
            if price is not None and "TRUST" in state:
                st["status"], st["reason_code"] = ST_FRESH, R_OK
                return st
            st["status"], st["reason_code"] = ST_WITHHELD, R_NO_TRUSTED
            return st

    # Position-row mirror. Kept distinguishable from adjudicated truth by source.
    for col in ("trusted_peak_pct", "runner_peak_pct"):
        v = _f(pos.get(col))
        if v is not None:
            st.update({
                "price": _f(pos.get("trusted_peak_price")),
                "pnl_pct": v,
                "source": f"paper_positions.{col}",
                "age_sec": _age(pos.get("trusted_peak_at") or pos.get("updated_at"), now),
                "row_id": pos.get("id"),
                "table": "paper_positions",
                "database": "sentinuity_matrix.db",
                "column": col,
                "timestamp": _epoch(pos.get("trusted_peak_at") or pos.get("updated_at")),
                "adjudicator_state": "MIRRORED_TO_POSITION_ROW",
                "adjudicator_reason": str(pos.get("runner_peak_trust_source") or "")[:200] or None,
                "status": ST_FRESH,
                "reason_code": R_OK,
            })
            return st
    return st


def _hydrate_floor(pos: Dict[str, Any]) -> Dict[str, Any]:
    """Armed runner floor. A GUARANTEE, never a current figure."""
    floor_pct = _f(pos.get("runner_lock_floor_pct"))
    state = str(pos.get("runner_floor_state") or "NOT_ARMED")
    armed = floor_pct is not None and (
        "ARMED" in state.upper() or _pos_int(pos.get("runner_protected")) == 1
    )
    return {
        "pnl_pct": floor_pct if armed else None,
        "price": _f(pos.get("runner_lock_price")) if armed else None,
        "status": ST_FRESH if armed else ST_WITHHELD,
        "reason_code": R_OK if armed else R_FLOOR_NOT_ARMED,
        "floor_state": state,
        "source": "paper_positions.runner_lock_floor_pct",
        "table": "paper_positions",
        "database": "sentinuity_matrix.db",
        "column": "runner_lock_floor_pct",
    }


# ═══════════════════════════ hero selection ══════════════════════════════════

def _select_hero(observed: Dict[str, Any], executable: Dict[str, Any],
                 floor: Dict[str, Any], trusted: Dict[str, Any]) -> Dict[str, Any]:
    """The hero number is the HIGHEST-AUTHORITY QUALIFIED CURRENT figure.

    Deliberately NOT the largest number, and deliberately NOT the protected
    floor. A floor is a guaranteed minimum on exit; a trusted peak is a
    high-water mark. Neither is a current value, so neither may occupy the hero
    slot. Both are carried in the payload and rendered in their own sockets.

    Priority:
      1. EXECUTABLE, fresh and qualified  -> hero is executable_pnl_pct
      2. OBSERVED, fresh and trusted subtype -> hero is observed_pnl_pct, marked unverified
      3. OBSERVED, present but stale/untrusted -> hero is None; the stage is shown as doubt
      4. nothing -> hero is None, ABSENCE
    """
    if executable.get("status") == ST_FRESH and executable.get("pnl_pct") is not None:
        return {
            "hero_pnl_pct": executable["pnl_pct"],
            "hero_stage": AUTH_EXECUTABLE,
            "hero_source": executable.get("source"),
            "hero_age_sec": executable.get("age_sec"),
            "hero_age_kind": "quote_age_sec",
            "hero_qualified": True,
            "hero_peak_trustworthy": True,
            "hero_fallback_used": False,
            "hero_reason_code": R_OK,
        }
    if (observed.get("status") == ST_FRESH and observed.get("pnl_pct") is not None):
        return {
            "hero_pnl_pct": observed["pnl_pct"],
            "hero_stage": AUTH_OBSERVED,
            "hero_source": observed.get("source"),
            "hero_age_sec": observed.get("age_sec"),
            "hero_age_kind": "source_market_age_sec",
            "hero_qualified": False,       # observed, not executable: render hollow
            "hero_peak_trustworthy": bool(observed.get("trusted_subtype")),
            "hero_fallback_used": bool(observed.get("fallback_used")),
            "hero_reason_code": executable.get("reason_code") or R_NO_QUOTE,
        }
    return {
        "hero_pnl_pct": None,
        "hero_stage": AUTH_NONE,
        "hero_source": None,
        "hero_age_sec": observed.get("age_sec"),
        "hero_age_kind": "source_market_age_sec",
        "hero_qualified": False,
        "hero_peak_trustworthy": False,
        "hero_fallback_used": bool(observed.get("fallback_used")),
        "hero_reason_code": observed.get("reason_code") or R_NO_OBS,
    }


def _authority_stage(observed, pool, tape, executable, trusted, floor) -> str:
    if floor.get("status") == ST_FRESH:
        return AUTH_PROTECTED
    if trusted.get("status") == ST_FRESH:
        return AUTH_TRUSTED
    if executable.get("status") == ST_FRESH:
        return AUTH_EXECUTABLE
    if tape.get("status") == ST_FRESH:
        return AUTH_TAPE
    if pool.get("status") == ST_FRESH:
        return AUTH_POOL
    if observed.get("status") in (ST_FRESH, ST_UNTRUSTED, ST_STALE) and observed.get("pnl_pct") is not None:
        return AUTH_OBSERVED
    return AUTH_NONE


# ═══════════════════════════ public entry points ═════════════════════════════

def build_position_truth_payload(
    position_id: int,
    *,
    matrix_db: Any = None,
    price_truth_db: Any = None,
    intel_db: Any = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The single canonical presentation payload for one position.

    Read-only. Never raises. Every surface consumes this and nothing else.
    """
    now = float(now or time.time())
    mpath = matrix_db or DEFAULT_MATRIX_DB
    tpath = price_truth_db or DEFAULT_PRICE_TRUTH_DB
    ipath = intel_db or DEFAULT_INTEL_DB

    matrix = _ro(mpath)
    truth = _ro(tpath)
    intel = _ro(ipath)

    try:
        if matrix is None:
            return _degraded_payload(position_id, R_DB_UNAVAILABLE, now)

        pos = _row(matrix, "SELECT * FROM paper_positions WHERE id=? LIMIT 1",
                   (int(position_id),))
        if not pos:
            return _degraded_payload(position_id, R_NO_POSITION, now)

        pid = _pos_int(pos.get("id"))
        mint = str(pos.get("mint_address") or "")
        entry = _f(pos.get("entry_price")) or 0.0
        size = _f(pos.get("position_size_usd")) or 0.0
        opened_at = _f(pos.get("opened_at")) or 0.0
        status = str(pos.get("status") or "").upper()

        if entry <= 0:
            return _degraded_payload(position_id, R_NO_ENTRY, now, pos=pos)

        observed = _hydrate_observation(intel, matrix, mint, opened_at, entry, now)
        pool = _hydrate_pool(truth, pid, entry, now)
        tape = _hydrate_tape(truth, pid, entry, now)
        executable = _hydrate_executable(truth, pid, entry, now)
        trusted = _hydrate_trusted(truth, pos, entry, now)
        floor = _hydrate_floor(pos)

        hero = _select_hero(observed, executable, floor, trusted)
        auth = _authority_stage(observed, pool, tape, executable, trusted, floor)

        # ── freshness contract: four distinct clocks, never conflated ────────
        freshness = {
            "row_write_age_sec": _age(pos.get("live_exec_updated_at")
                                      or pos.get("last_marked_at"), now),
            "source_market_age_sec": observed.get("age_sec"),
            "quote_age_sec": executable.get("age_sec"),
            "fallback_age_sec": observed.get("age_sec") if observed.get("fallback_used") else None,
            # Written by the engine as the router's own age at write time. This
            # is the closest persisted analogue of source_market_age_sec on the
            # position row, and it is what the old hero badge should have used.
            "persisted_router_age_sec": _f(pos.get("live_exec_age_sec")),
        }

        settlement_pct = None
        if status == "CLOSED":
            try:
                from services.pnl_truth import canonical_realized_pnl_pct  # type: ignore
                settlement_pct = _f(canonical_realized_pnl_pct(pos))
            except Exception:
                settlement_pct = _f(pos.get("realized_pnl_pct"))

        obs_exec = None
        if observed.get("pnl_pct") is not None and executable.get("pnl_pct") is not None:
            obs_exec = observed["pnl_pct"] - executable["pnl_pct"]

        payload: Dict[str, Any] = {
            "contract_version": PAYLOAD_CONTRACT_VERSION,
            "generated_at": now,
            "position_id": pid,
            "mint_address": mint,
            "token_name": str(pos.get("token_name") or "")[:64],
            "status": status,
            "entry_price": entry,
            "position_size_usd": size,
            "opened_at": opened_at,

            # ── canonical semantic fields (directive contract) ───────────────
            "observed_pnl_pct": observed.get("pnl_pct"),
            "observed_price": observed.get("price"),
            "observed_source": observed.get("source"),
            "observed_age_sec": observed.get("age_sec"),
            "observed_status": observed.get("status"),

            "executable_pnl_pct": executable.get("pnl_pct"),
            "executable_price": executable.get("price"),
            "executable_source": executable.get("source"),
            "executable_age_sec": executable.get("age_sec"),
            "executable_status": executable.get("status"),

            "trusted_peak_pct": trusted.get("pnl_pct"),
            "trusted_peak_source": trusted.get("source"),
            "trusted_peak_status": trusted.get("status"),

            "protected_floor_pct": floor.get("pnl_pct"),
            "protected_floor_status": floor.get("status"),

            "settlement_pnl_pct": settlement_pct,

            "authority_stage": auth,
            "authority_rank": _AUTH_RANK.get(auth, 0),
            "reason_code": hero.get("hero_reason_code"),

            # ── source layers, unflattened ───────────────────────────────────
            "A_pool": pool,
            "B_tape": tape,
            "C_exec": executable,
            "D_observation": observed,

            # ── divergences: carried, not necessarily surfaced ───────────────
            "obs_exec_divergence_pct": obs_exec,
            "pool_exec_divergence_pct": _divergence_pct(pool.get("price"), executable.get("price")),
            "tape_exec_divergence_pct": _divergence_pct(tape.get("price"), executable.get("price")),
            "a_c_divergence_pct": _divergence_pct(pool.get("price"), executable.get("price")),
            "obs_pool_price_ratio": (
                (observed["price"] / pool["price"])
                if (_f(observed.get("price")) and _f(pool.get("price")) and pool["price"] > 0)
                else None
            ),

            # ── freshness contract ───────────────────────────────────────────
            "freshness": freshness,

            # ── hero rule output ─────────────────────────────────────────────
            **hero,

            # ── unrealised USD, derived ONLY from the hero figure ────────────
            "hero_pnl_usd": (
                size * (hero["hero_pnl_pct"] / 100.0)
                if (hero.get("hero_pnl_pct") is not None and size > 0) else None
            ),

            # ── legacy field mirrors, for audit only. NEVER RENDER THESE. ────
            "_legacy": {
                "live_exec_pct": _f(pos.get("live_exec_pct")),
                "live_exec_price": _f(pos.get("live_exec_price")),
                "live_exec_source": pos.get("live_exec_source"),
                "live_exec_can_exit": _pos_int(pos.get("live_exec_can_exit")),
                "live_exec_updated_at": _f(pos.get("live_exec_updated_at")),
                "live_exec_age_sec": _f(pos.get("live_exec_age_sec")),
                "unrealized_pnl_pct": _f(pos.get("unrealized_pnl_pct")),
                "unrealized_pnl_usd": _f(pos.get("unrealized_pnl_usd")),
                "current_price": _f(pos.get("current_price")),
                "last_price": _f(pos.get("last_price")),
                "highest_price_seen": _f(pos.get("highest_price_seen")),
                "mark_source": pos.get("mark_source"),
            },
        }

        # ── presentation v2: one shared coordinate system + explicit sockets ─
        payload["A_pool"] = _stage_view(payload["A_pool"])
        payload["B_tape"] = _stage_view(payload["B_tape"])
        payload["C_exec"] = _stage_view(payload["C_exec"])
        payload["D_observation"] = _stage_view(payload["D_observation"])

        payload["stage_observed"] = _stage_view(observed)
        payload["stage_executable"] = _stage_view(executable)
        payload["stage_trusted"] = _stage_view(trusted)
        payload["stage_protected"] = _stage_view(floor, protected=True)

        payload["hero_axis_pct"] = axis_position_pct(payload.get("hero_pnl_pct"))
        payload["hero_axis_overflow"] = axis_overflow(payload.get("hero_pnl_pct"))
        payload["obs_exec_divergence_strength"] = _visual_divergence_strength(obs_exec)

        # Evidence coverage is descriptive only: a rejected coordinate still
        # counts as evidence that was actually investigated.
        layers = (payload["A_pool"], payload["B_tape"],
                  payload["C_exec"], payload["D_observation"])
        have = sum(1 for s in layers if s.get("pnl_pct") is not None)
        payload["evidence_coverage_have"] = have
        payload["evidence_coverage_total"] = 4

        # Compact decision spine.  It mirrors evidence / ledger state; it does
        # not make a trading decision.
        funding = str(pos.get("funding_mode") or pos.get("mode") or "SIM").upper()
        payload["spine_lane"] = "REAL" if funding in ("REAL", "LIVE") else "SIM"
        payload["spine_lane_ok"] = True
        payload["spine_exec_ok"] = (executable.get("status") == ST_FRESH)
        payload["spine_mode_ok"] = False
        payload["spine_mode_label"] = "MODE"
        payload["spine_capital"] = "WAITING"

        # If a Mode-B ledger exists, mirror its most recent verdict.  Unknown
        # schema/absence remains WAITING rather than becoming an invented pass.
        try:
            if _has_table(matrix, "mode_b_decision_ledger"):
                mc = _cols(matrix, "mode_b_decision_ledger")
                fk = "position_id" if "position_id" in mc else ("paper_position_id" if "paper_position_id" in mc else None)
                if fk:
                    order = "evaluated_at" if "evaluated_at" in mc else ("created_at" if "created_at" in mc else "rowid")
                    mr = _row(matrix,
                        f"SELECT * FROM mode_b_decision_ledger WHERE {fk}=? ORDER BY {order} DESC LIMIT 1",
                        (pid,))
                    if mr:
                        mv = str(mr.get("verdict") or mr.get("status") or "").upper()
                        payload["spine_mode_label"] = mv or "MODE"
                        payload["spine_mode_ok"] = mv in {
                            "ELIGIBLE","PASS","PASSED","ALLOW","ALLOWED","APPROVED",
                            "READY","LIVE_OK","QUALIFIED"
                        }
        except Exception:
            pass

        payload["spine_reason"] = reason_text(payload.get("reason_code"))
        return payload
    except Exception as exc:                                    # pragma: no cover
        return _degraded_payload(position_id, f"HYDRATION_ERROR:{type(exc).__name__}", now)
    finally:
        for c in (matrix, truth, intel):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass


def _degraded_payload(position_id: Any, reason: str, now: float,
                      pos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pos = pos or {}
    blank = _blank_stage(ST_ABSENT, reason)
    return {
        "contract_version": PAYLOAD_CONTRACT_VERSION,
        "generated_at": now,
        "position_id": _pos_int(position_id),
        "mint_address": str(pos.get("mint_address") or ""),
        "token_name": str(pos.get("token_name") or "")[:64],
        "status": str(pos.get("status") or "UNKNOWN").upper(),
        "entry_price": _f(pos.get("entry_price")),
        "position_size_usd": _f(pos.get("position_size_usd")),
        "opened_at": _f(pos.get("opened_at")),
        "observed_pnl_pct": None, "observed_price": None, "observed_source": None,
        "observed_age_sec": None, "observed_status": ST_ABSENT,
        "executable_pnl_pct": None, "executable_price": None, "executable_source": None,
        "executable_age_sec": None, "executable_status": ST_ABSENT,
        "trusted_peak_pct": None, "trusted_peak_source": None, "trusted_peak_status": ST_ABSENT,
        "protected_floor_pct": None, "protected_floor_status": ST_WITHHELD,
        "settlement_pnl_pct": None,
        "authority_stage": AUTH_NONE, "authority_rank": 0, "reason_code": reason,
        "A_pool": dict(blank), "B_tape": dict(blank),
        "C_exec": dict(blank), "D_observation": dict(blank),
        "obs_exec_divergence_pct": None, "pool_exec_divergence_pct": None,
        "tape_exec_divergence_pct": None, "a_c_divergence_pct": None,
        "obs_pool_price_ratio": None,
        "freshness": {"row_write_age_sec": None, "source_market_age_sec": None,
                      "quote_age_sec": None, "fallback_age_sec": None,
                      "persisted_router_age_sec": None},
        "hero_pnl_pct": None, "hero_stage": AUTH_NONE, "hero_source": None,
        "hero_age_sec": None, "hero_age_kind": None, "hero_qualified": False,
        "hero_peak_trustworthy": False, "hero_fallback_used": False,
        "hero_reason_code": reason, "hero_pnl_usd": None,
        "hero_axis_pct": None, "hero_axis_overflow": "",
        "stage_observed": _stage_view(blank),
        "stage_executable": _stage_view(blank),
        "stage_trusted": _stage_view(blank),
        "stage_protected": _stage_view({"status": ST_WITHHELD, "pnl_pct": None, "reason_code": reason}, protected=True),
        "obs_exec_divergence_strength": None,
        "evidence_coverage_have": 0, "evidence_coverage_total": 4,
        "spine_lane": "SIM", "spine_lane_ok": False,
        "spine_exec_ok": False, "spine_mode_ok": False,
        "spine_mode_label": "MODE", "spine_capital": "WAITING",
        "spine_reason": reason_text(reason),
        "_legacy": {},
    }


def build_open_position_truth_payloads(
    *, matrix_db: Any = None, price_truth_db: Any = None, intel_db: Any = None,
    limit: int = 24, now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Payloads for every OPEN position, newest first. Read-only, never raises."""
    now = float(now or time.time())
    matrix = _ro(matrix_db or DEFAULT_MATRIX_DB)
    ids: List[int] = []
    try:
        if matrix is not None and _has_table(matrix, "paper_positions"):
            rows = matrix.execute(
                "SELECT id FROM paper_positions WHERE UPPER(COALESCE(status,''))='OPEN' "
                "ORDER BY opened_at DESC LIMIT ?", (int(limit),)).fetchall()
            ids = [int(r[0]) for r in rows]
    except Exception:
        ids = []
    finally:
        try:
            if matrix is not None:
                matrix.close()
        except Exception:
            pass

    return [
        build_position_truth_payload(
            i, matrix_db=matrix_db, price_truth_db=price_truth_db,
            intel_db=intel_db, now=now)
        for i in ids
    ]


# ═══════════════════════ presentation-layer helpers ══════════════════════════
# Pure mappings. The UI may use these. It may NOT add its own thresholds.

def status_tone(status: str) -> str:
    """status -> semantic tone key. Renderers map tone -> colour."""
    return {
        ST_FRESH: "affirm",
        ST_STALE: "doubt",
        ST_UNTRUSTED: "doubt",
        ST_QUARANTINED: "refuse",
        ST_WITHHELD: "absent",
        ST_ABSENT: "absent",
    }.get(str(status), "absent")


def age_bucket(age_sec: Optional[float]) -> str:
    """age -> decay bucket key. Renderers map bucket -> opacity/fracture."""
    if age_sec is None:
        return "unknown"
    if age_sec < 5:
        return "live"
    if age_sec < 15:
        return "recent"
    if age_sec < 60:
        return "ageing"
    if age_sec < 300:
        return "stale"
    return "dead"


REASON_TEXT = {
    R_OK: "",
    R_NO_POSITION: "no position row",
    R_NO_ENTRY: "no positive entry price",
    R_NO_OBS: "no observation at or after position open",
    R_OBS_STALE: "observation older than freshness budget",
    R_OBS_UNTRUSTED: "observation subtype cannot carry authority",
    R_NO_QUOTE: "no executable quote recorded",
    R_QUOTE_STALE: "executable quote older than freshness budget",
    R_QUOTE_NOT_SELLABLE: "quote returned but not sellable",
    R_QUOTE_DIAGNOSTIC: "quote is diagnostic only",
    R_QUOTE_IS_POOL_ECHO: "only quote present is a pool simulation echo of layer A",
    R_NO_TRUSTED: "no adjudicated trusted peak",
    R_FLOOR_NOT_ARMED: "runner floor not armed",
    R_DB_UNAVAILABLE: "database unavailable",
}


def reason_text(code: Any) -> str:
    c = str(code or "")
    if c in REASON_TEXT:
        return REASON_TEXT[c]
    if c.startswith("HYDRATION_ERROR:"):
        return "payload hydration error"
    return c.lower().replace("_", " ")


__all__ = [
    "PAYLOAD_CONTRACT_VERSION",
    "build_position_truth_payload",
    "build_open_position_truth_payloads",
    "status_tone", "age_bucket", "reason_text", "REASON_TEXT",
    "ST_ABSENT", "ST_FRESH", "ST_STALE", "ST_UNTRUSTED", "ST_QUARANTINED", "ST_WITHHELD",
    "AUTH_NONE", "AUTH_OBSERVED", "AUTH_POOL", "AUTH_TAPE", "AUTH_EXECUTABLE",
    "AUTH_TRUSTED", "AUTH_PROTECTED",
    "SOCK_OCCUPIED", "SOCK_REJECTED", "SOCK_VERIFYING", "SOCK_UNAVAILABLE",
    "AXIS_DATUM_PCT", "axis_position_pct", "axis_overflow",
]
