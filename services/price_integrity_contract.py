"""
services/price_integrity_contract.py
-------------------------------------------------------------------------------
Sentinuity paper price-integrity contract — source-consensus hardened.

Purpose
- Keep paper entry/MTM/exit prices internally consistent.
- Block unstable paper opens.
- Protect paper calibration from one-tick poisoned marks.
- Preserve live strictness: live exits are never deferred/guarded here.

This module is intentionally self-contained and fail-soft.  It is compatible with
execution_engine.py call sites that import:
  ensure_integrity_columns
  evaluate_pre_open_integrity
  evaluate_first_mark
  paper_hard_stop_exit_policy
  is_dirty_outcome

SIGNOFF intent:
- price_enricher.py stops new poisoned marks entering the tape.
- this contract + the small executor call-site patch stops old/alternate poisoned
  marks from forcing executable paper hard-stop closes without consensus.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

log = logging.getLogger("price_integrity")

# ── CONFIG DEFAULTS (overridable by execution_engine arguments/config) ─────────
DEFAULT_SAME_MINT_SPREAD_MAX_PCT = 10.0
DEFAULT_ENTRY_VS_QUALIFY_DRIFT_PCT = 8.0
DEFAULT_SPREAD_LOOKBACK_SECONDS = 120
DEFAULT_FIRST_TICK_GUARD_SECONDS = 30
DEFAULT_CATASTROPHIC_GAP_PCT = 25.0
DEFAULT_SOURCE_DISAGREE_DEFER_PCT = 50.0

TRUSTED_SOURCE_TOKENS = (
    "helius",
    "bonding_curve_rpc",
    "bonding_curve",
    "pump_curve",
    "quicknode_curve",
    "curve_rpc",
)

# These are allowed for non-critical display/marking, but NOT allowed to be the
# only source that forces a catastrophic paper hard-stop.
SUSPECT_SOURCE_TOKENS = (
    "enricher_open_position",
    "intel-mtm",
    "mtm-snapshot",
    "router:intel-mtm",
    "router:mtm-snapshot",
    "jupiter",
    "dexscreener",
    "birdeye",
    "engine",
    "unknown",
)

INTEGRITY_COLUMNS = (
    ("qualify_price", "REAL"),
    ("qualify_price_updated_at", "REAL"),
    ("qualify_price_age_sec", "REAL"),
    ("entry_price_updated_at", "REAL"),
    ("entry_vs_qualify_pct", "REAL"),
    ("same_mint_price_spread_pct", "REAL"),
    ("price_source_consistent", "INTEGER DEFAULT 1"),
    ("price_integrity_status", "TEXT DEFAULT 'CLEAN'"),
    ("price_integrity_reason", "TEXT"),
    ("first_mark_price", "REAL"),
    ("first_mark_source", "TEXT"),
    ("first_mark_at", "REAL"),
    ("entry_vs_first_mark_pct", "REAL"),
    ("unstable_price_guard_count", "INTEGER DEFAULT 0"),
    ("exit_mark_source", "TEXT"),
    ("trusted_mark_price", "REAL"),
    ("mark_disagreement_pct", "REAL"),
    ("outlier_rejected", "INTEGER DEFAULT 0"),
    ("executable_exit_source_count", "INTEGER DEFAULT 0"),
)


def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _pct(a: Any, b: Any) -> Optional[float]:
    """Absolute percentage difference between a and b relative to a."""
    fa = _safe_float(a)
    fb = _safe_float(b)
    if fa is None or fb is None or fa <= 0:
        return None
    return abs((fb - fa) / fa * 100.0)


def _move_pct(entry: Any, price: Any) -> Optional[float]:
    entry_f = _safe_float(entry)
    price_f = _safe_float(price)
    if entry_f is None or price_f is None or entry_f <= 0:
        return None
    return (price_f - entry_f) / entry_f * 100.0


def _src_text(*sources: Any) -> str:
    return "|".join(str(s or "").lower() for s in sources)


def _is_trusted_source(source: Any) -> bool:
    s = str(source or "").lower()
    return any(tok in s for tok in TRUSTED_SOURCE_TOKENS)


def _is_suspect_source(source: Any) -> bool:
    s = str(source or "").lower().strip()
    if not s:
        return True
    if _is_trusted_source(s):
        return False
    return any(tok in s for tok in SUSPECT_SOURCE_TOKENS)


def ensure_integrity_columns(conn) -> None:
    """Idempotently add price-integrity telemetry columns to paper_positions."""
    try:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
        for col, typ in INTEGRITY_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} {typ}")
        try:
            conn.commit()
        except Exception:
            pass
    except Exception as e:
        # Schema protection must never prevent startup.
        log.debug("ensure_integrity_columns skipped: %s", e)


def compute_same_mint_spread(conn, mint: str, now: Optional[float] = None,
                             lookback_seconds: float = DEFAULT_SPREAD_LOOKBACK_SECONDS) -> Optional[float]:
    """Return same-mint high/low spread pct from recent market_snapshots observed_price."""
    if not mint:
        return None
    now = _safe_float(now, time.time()) or time.time()
    cutoff = now - float(lookback_seconds or DEFAULT_SPREAD_LOOKBACK_SECONDS)
    try:
        row = conn.execute(
            """
            SELECT MIN(observed_price) AS lo, MAX(observed_price) AS hi
            FROM market_snapshots
            WHERE mint_address=?
              AND observed_price IS NOT NULL AND observed_price > 0
              AND COALESCE(price_updated_at, updated_at, created_at, first_seen_at, 0) >= ?
            """,
            (mint, cutoff),
        ).fetchone()
        if not row:
            return None
        lo = _safe_float(row["lo"] if hasattr(row, "keys") else row[0])
        hi = _safe_float(row["hi"] if hasattr(row, "keys") else row[1])
        if lo is None or hi is None or lo <= 0:
            return None
        return (hi - lo) / lo * 100.0
    except Exception:
        return None


def compute_entry_drift_pct(qualify_price: Any, entry_price: Any) -> Optional[float]:
    return _pct(qualify_price, entry_price)


def evaluate_pre_open_integrity(conn, *, mint: str, qualify_price: Any, qualify_ts: Any,
                                entry_price: Any, entry_price_source: Any, now: Optional[float] = None,
                                same_mint_spread_max_pct: float = DEFAULT_SAME_MINT_SPREAD_MAX_PCT,
                                entry_drift_max_pct: float = DEFAULT_ENTRY_VS_QUALIFY_DRIFT_PCT) -> dict:
    """Evaluate price integrity before opening a paper position."""
    now = _safe_float(now, time.time()) or time.time()
    q = _safe_float(qualify_price)
    e = _safe_float(entry_price)
    qts = _safe_float(qualify_ts)
    q_age = (now - qts) if qts and qts > 0 else None

    base = {
        "status": "CLEAN",
        "decision": "OPEN_CLEAN",
        "reason": None,
        "qualify_price_age_sec": q_age,
        "entry_vs_qualify_pct": None,
        "same_mint_price_spread_pct": None,
        "price_source_consistent": 1,
    }

    if e is None or e <= 0:
        base.update(status="BLOCKED", decision="BLOCK_NO_EXECUTABLE_PRICE", reason="NO_EXECUTABLE_PRICE", price_source_consistent=0)
        return base

    drift = compute_entry_drift_pct(q, e) if q and q > 0 else None
    base["entry_vs_qualify_pct"] = drift
    spread = compute_same_mint_spread(conn, mint, now=now, lookback_seconds=DEFAULT_SPREAD_LOOKBACK_SECONDS)
    base["same_mint_price_spread_pct"] = spread

    if drift is not None and drift > float(entry_drift_max_pct):
        base.update(status="BLOCKED", decision="BLOCK_ENTRY_DRIFT_TOO_HIGH",
                    reason=f"ENTRY_DRIFT_{drift:.2f}pct_gt_{float(entry_drift_max_pct):.2f}",
                    price_source_consistent=0)
        return base

    if spread is not None and spread > float(same_mint_spread_max_pct):
        base.update(status="BLOCKED", decision="BLOCK_PRICE_SOURCE_SPREAD_TOO_HIGH",
                    reason=f"SAME_MINT_SPREAD_{spread:.2f}pct_gt_{float(same_mint_spread_max_pct):.2f}",
                    price_source_consistent=0)
        return base

    # Suspect entry source is not blocked by itself; it is stamped for later exit guards.
    if _is_suspect_source(entry_price_source):
        base["reason"] = "ENTRY_SOURCE_SUSPECT_BUT_WITHIN_DRIFT"
    return base


def evaluate_first_mark(*, entry_price: Any, entry_price_source: Any,
                        first_mark_price: Any, first_mark_source: Any) -> dict:
    """Evaluate the first MTM mark after open for paper-price consistency."""
    drift = _pct(entry_price, first_mark_price)
    srcs = _src_text(entry_price_source, first_mark_source)
    same_family = (
        (_is_trusted_source(entry_price_source) and _is_trusted_source(first_mark_source)) or
        (_is_suspect_source(entry_price_source) and _is_suspect_source(first_mark_source))
    )
    out = {
        "entry_vs_first_mark_pct": drift,
        "source_match": 1 if same_family else 0,
        "first_mark_integrity": "CLEAN",
        "reason": None,
    }
    if drift is not None and drift > DEFAULT_SAME_MINT_SPREAD_MAX_PCT:
        out.update(first_mark_integrity="UNSTABLE",
                   reason=f"FIRST_MARK_DRIFT_{drift:.2f}pct")
    if not same_family and drift is not None and drift > 3.0:
        out.update(first_mark_integrity="UNSTABLE",
                   reason=(out.get("reason") or "") + f"|FIRST_MARK_SOURCE_MISMATCH:{srcs}")
    return out


def should_guard_first_tick_stop(*, is_live_mode: bool, opened_at: Any, now: Optional[float] = None,
                                 first_tick_guard_seconds: float = DEFAULT_FIRST_TICK_GUARD_SECONDS) -> tuple[bool, str]:
    """Paper-only guard for very early one-tick stop-loss events."""
    if is_live_mode:
        return False, "LIVE_PATH_STRICT"
    opened = _safe_float(opened_at)
    if not opened:
        return False, "NO_OPEN_TIME"
    now = _safe_float(now, time.time()) or time.time()
    age = now - opened
    if age <= float(first_tick_guard_seconds):
        return True, f"FIRST_TICK_STOP_GUARDED_{age:.1f}s"
    return False, "PAST_FIRST_TICK_WINDOW"


def paper_hard_stop_exit_policy(*, is_live_mode: bool, entry_price: Any, current_price: Any,
                                pnl_pct: Any, hard_stop_pct: Any, opened_at: Any,
                                price_integrity_status: Any = None,
                                price_integrity_reason: Any = None,
                                first_mark_source: Any = None,
                                entry_price_source: Any = None,
                                same_mint_spread_pct: Any = None,
                                entry_vs_first_mark_pct: Any = None,
                                price_source: Any = None,
                                price_age_sec: Any = None,
                                guard_count: Any = 0,
                                catastrophic_gap_pct: float = DEFAULT_CATASTROPHIC_GAP_PCT,
                                same_mint_spread_max_pct: float = DEFAULT_SAME_MINT_SPREAD_MAX_PCT,
                                cap_enabled: bool = True) -> dict:
    """
    Decide how paper should handle a hard-stop trigger.

    Return keys understood by current execution_engine:
      exit_price, exit_reason, capped, dirty, audit_reason

    Extra key for the supplied executor patch:
      defer_close=True means do not close this paper position on this mark.
    """
    entry = _safe_float(entry_price, 0.0) or 0.0
    current = _safe_float(current_price, 0.0) or 0.0
    pnl = _safe_float(pnl_pct, 0.0) or 0.0
    stop = abs(_safe_float(hard_stop_pct, 4.0) or 4.0)
    cat = abs(_safe_float(catastrophic_gap_pct, DEFAULT_CATASTROPHIC_GAP_PCT) or DEFAULT_CATASTROPHIC_GAP_PCT)
    spread = _safe_float(same_mint_spread_pct)
    first_drift = _safe_float(entry_vs_first_mark_pct)
    guard_n = int(_safe_float(guard_count, 0) or 0)

    strict_reason = f"HARD_STOP_LOSS_{pnl:.1f}pct"
    out = {
        "exit_price": current,
        "exit_reason": strict_reason,
        "capped": False,
        "dirty": False,
        "audit_reason": "STRICT",
        "defer_close": False,
        "outlier_rejected": False,
        "trusted_mark_price": None,
        "mark_disagreement_pct": None,
        "exit_mark_source": str(price_source or "engine"),
        "executable_exit_source_count": 1 if _is_trusted_source(price_source) else 0,
    }

    if is_live_mode:
        out["audit_reason"] = "LIVE_PATH_STRICT"
        return out
    if not cap_enabled:
        out["audit_reason"] = "CAP_DISABLED_STRICT"
        return out
    if entry <= 0 or current <= 0:
        out.update(dirty=True, capped=True, audit_reason="INVALID_ENTRY_OR_CURRENT_PRICE")
        out["exit_price"] = max(entry * (1.0 - stop / 100.0), 0.0) if entry > 0 else current
        out["exit_reason"] = f"HARD_STOP_LOSS_CAPPED_{stop:.1f}pct_raw{pnl:.1f}pct"
        return out

    source_blob = _src_text(price_source, first_mark_source, entry_price_source, price_integrity_reason)
    trigger_source_suspect = _is_suspect_source(price_source) or any(tok in source_blob for tok in (
        "enricher_open_position", "intel-mtm", "mtm-snapshot", "router:intel-mtm", "router:mtm-snapshot"
    ))
    integrity_unstable = str(price_integrity_status or "").upper() in ("UNSTABLE", "DIRTY", "SHADOW_BLOCKED", "BLOCKED")
    spread_unstable = spread is not None and spread > float(same_mint_spread_max_pct)
    first_mark_unstable = first_drift is not None and first_drift > float(same_mint_spread_max_pct)
    catastrophic = pnl <= -cat
    first_tick_guard, first_tick_reason = should_guard_first_tick_stop(
        is_live_mode=False, opened_at=opened_at, now=time.time(),
        first_tick_guard_seconds=DEFAULT_FIRST_TICK_GUARD_SECONDS,
    )

    if catastrophic and trigger_source_suspect:
        # This is the 5964/6007 class: a huge loss asserted by suspect source.
        # Do not close if the executor patch honors defer_close. If it does not,
        # the cap still prevents calibration from absorbing a -90% mark.
        out.update(
            capped=True,
            dirty=True,
            defer_close=True,
            outlier_rejected=True,
            audit_reason="MARK_OUTLIER_REJECTED:SUSPECT_SOURCE_CATASTROPHIC_HARD_STOP",
            exit_price=entry * (1.0 - stop / 100.0),
            exit_reason=f"HARD_STOP_DEFERRED_MARK_OUTLIER_raw{pnl:.1f}pct",
        )
        return out

    if first_tick_guard and (integrity_unstable or spread_unstable or first_mark_unstable or catastrophic):
        out.update(
            capped=True,
            dirty=True,
            defer_close=True,
            outlier_rejected=True,
            audit_reason=f"MARK_OUTLIER_REJECTED:{first_tick_reason}",
            exit_price=entry * (1.0 - stop / 100.0),
            exit_reason=f"HARD_STOP_DEFERRED_FIRST_TICK_raw{pnl:.1f}pct",
        )
        return out

    if catastrophic or integrity_unstable or spread_unstable or first_mark_unstable or guard_n > 0:
        # Trusted/confirmed catastrophic paper loss: close, but cap to the stop floor
        # so paper training/calibration reflects the configured 4% doctrine.
        reasons = []
        if catastrophic: reasons.append(f"CATASTROPHIC_GAP_{pnl:.1f}pct")
        if integrity_unstable: reasons.append(f"STATUS_{price_integrity_status}")
        if spread_unstable: reasons.append(f"SPREAD_{spread:.1f}pct")
        if first_mark_unstable: reasons.append(f"FIRST_MARK_{first_drift:.1f}pct")
        if guard_n > 0: reasons.append(f"GUARD_COUNT_{guard_n}")
        out.update(
            capped=True,
            dirty=True,
            audit_reason="|".join(reasons) or "PAPER_HARD_STOP_CAP",
            exit_price=entry * (1.0 - stop / 100.0),
            exit_reason=f"HARD_STOP_LOSS_CAPPED_{stop:.1f}pct_raw{pnl:.1f}pct",
        )
        return out

    return out


def is_dirty_outcome(row_or_status: Any = None, reason: Any = None, **kwargs) -> bool:
    """Return True if a trade outcome should be excluded from calibration."""
    try:
        if isinstance(row_or_status, dict):
            status = str(row_or_status.get("price_integrity_status") or "").upper()
            why = str(row_or_status.get("price_integrity_reason") or "").upper()
            outlier = int(row_or_status.get("outlier_rejected") or 0)
        else:
            status = str(row_or_status or kwargs.get("price_integrity_status") or "").upper()
            why = str(reason or kwargs.get("price_integrity_reason") or "").upper()
            outlier = int(kwargs.get("outlier_rejected") or 0)
        if outlier:
            return True
        if status in ("UNSTABLE", "DIRTY", "BLOCKED", "SHADOW_BLOCKED"):
            return True
        return any(tok in why for tok in ("MARK_OUTLIER", "PAPER_HARD_STOP_CAP", "FIRST_MARK", "SPREAD", "DRIFT"))
    except Exception:
        return False
