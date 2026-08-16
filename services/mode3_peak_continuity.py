"""services/mode3_peak_continuity.py — STICKY QUALIFIED PEAK + FLOOR CONTINUITY
================================================================================
MODE3_FINAL_SIGNOFF_20260806

Implements Mode 3 invariants B and C as a self-contained module so that
execution_engine.py needs only a narrow, anchored call-site change rather than a
structural rewrite.

OBSERVED DEFECT (execution_engine.py:5122-5174)
-----------------------------------------------
`_maybe_runner_profit_lock_close()` recomputes the peak from the mark tape on
EVERY evaluation cycle:

    _pl_px, _pl_pct, _pl_src, _pl_ts = _trusted_peak_from_tape(...)
    peak_price = float(_pl_px) if _pl_px and _pl_px > 0 else 0.0
    if peak_price <= 0:
        ... persisted fallback, gated on peak_authority.runner_exit_authorised()
        ... which returns (False, "authority_unavailable") on ANY exception
    if peak_price <= 0:
        UPDATE paper_positions SET runner_floor_state='RUNNER_FLOOR_UNAVAILABLE'
        return None

Consequences proven against the runtime evidence:

  1. A transient disappearance of the peak-establishing source drives
     peak_price to 0 and OVERWRITES an already-armed 'ARMED_TRUSTED' floor with
     'RUNNER_FLOOR_UNAVAILABLE'. There is no grace window and no stickiness.
  2. With the floor erased, the function returns None and the position falls
     through to MAX_HOLD_TIME. Every close in the 2026-08-06 19:07-21:07 window
     is MAX_HOLD_TIME_900s..1038s. Not one runner-floor exit occurred.
  3. `runner_exit_authorised` failing closed on an exception is correct for
     ARMING but wrong for RETENTION: it disarms a floor that was already
     legitimately armed.

CONTRACT IMPLEMENTED HERE
------------------------
B. Once a peak passes source-quality + anti-outlier validation it is persisted
   as a qualified peak. Transient source disappearance does not erase it.
   The qualified peak is retained for a bounded grace window
   (MODE3_PEAK_GRACE_SEC, default 180s) and then expires explicitly.
   A display-only / unqualified quote can NEVER create a qualified peak.

C. An armed floor stays armed for the grace window. The executable quote may
   come from a different approved source than the peak source. Peak source,
   execution source, quote age and slippage are recorded in separate columns.

FAIL-CLOSED DIRECTION
---------------------
Stickiness only ever RETAINS protection. It never manufactures a peak, never
raises a floor, and never permits an entry. If no qualified peak was ever
recorded, this module returns UNAVAILABLE exactly as before.

ROLLBACK: delete services/mode3_peak_continuity.py and restore
          services/execution_engine.py.mode3_rollback_<timestamp>
================================================================================
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("mode3_peak_continuity")

_ROOT = Path(__file__).resolve().parent.parent
import sys as _sys
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from core.schema import get_config_value  # noqa: E402

try:
    from core.schema import get_critical_connection as _conn_factory  # noqa: E402
except Exception:  # APPLY not yet run — degrade to the hardened general path
    from core.schema import get_connection as _conn_factory  # noqa: E402


MODE3_PEAK_CONTINUITY_VERSION = "MODE3_FINAL_SIGNOFF_20260806"

# Sources permitted to ESTABLISH a qualified peak. A display-only quote
# (UI/aggregator/unconfirmed intel spike) is never sufficient.
_DEFAULT_QUALIFIED_SOURCES = "jupiter,raydium,pumpfun_curve,router_exec,onchain_quote"

# Sources historically implicated in false extreme peaks. Never peak-qualified.
_SUSPECT_SOURCES = {"intel-mtm", "display", "ui", "unknown", "", "estimate", "theoretical"}

# MODE3_AUTHORITY_CONTRACT_20260809 (blocker 2)
# The executor emits AUTHORITY CLASSES; this registry accepts RAW SOURCES. They
# are different concepts and the 08-08 code relied on accidental string
# equality between them, so every executor-side confirmation was silently
# rejected as an unqualified source. Only "onchain_quote" (written by the
# price-truth mesh) ever qualified, which meant the sub-second exit scheduler
# could not arm a floor at all.
#
# This map is the explicit contract. An authority class that may arm a live
# floor maps to a qualified raw source; one that may not, maps to None and is
# refused with a logged reason.
AUTHORITY_CLASS_TO_SOURCE = {
    "EXECUTABLE_CROSS_SOURCE_CONFIRMED": "router_exec",
    "EXECUTABLE_CONFIRMED":              "router_exec",
    # market-only authority is REAL but not REACHABLE; it may never become
    # live executable authority by passing through this map.
    "EXTERNAL_MARKET_CORROBORATED":      None,
    "OBSERVED_ONLY":                     None,
    "NATIVE_OUTLIER":                    None,
    "UNKNOWN":                           None,
}

# Evidence families that are themselves executable-grade raw sources.
EVIDENCE_FAMILY_TO_SOURCE = {
    "router_executable": "router_exec",
    "pool_executable":   "pumpfun_curve",
    "pool_quote":        "onchain_quote",
    "curve_reserve":     "onchain_quote",
}


def canonical_peak_source(*, authority_class: Any = None,
                          evidence_family: Any = None,
                          raw_source: Any = None) -> tuple[str, str]:
    """Resolve (qualified_source, reason). Empty source means REFUSED.

    Resolution order is strongest-evidence-first: an explicit executable
    evidence family beats an authority-class label, which beats a raw source
    string. Nothing is guessed; an unmapped input is refused, not promoted.
    """
    fam = str(evidence_family or "").strip().lower()
    if fam in EVIDENCE_FAMILY_TO_SOURCE:
        return EVIDENCE_FAMILY_TO_SOURCE[fam], "evidence_family"

    cls = str(authority_class or "").strip().upper()
    if cls in AUTHORITY_CLASS_TO_SOURCE:
        mapped = AUTHORITY_CLASS_TO_SOURCE[cls]
        if mapped:
            return mapped, "authority_class"
        return "", f"authority_class_not_executable={cls}"

    raw = str(raw_source or "").strip().lower()
    if raw and source_is_qualified(raw):
        return raw, "raw_source"
    if cls:
        return "", f"unmapped_authority_class={cls[:40]}"
    return "", f"unqualified_raw_source={raw[:40]}"

FLOOR_ARMED = "ARMED_TRUSTED"
FLOOR_ARMED_STICKY = "ARMED_STICKY"
FLOOR_UNAVAILABLE = "RUNNER_FLOOR_UNAVAILABLE"
FLOOR_EXPIRED = "RUNNER_FLOOR_EXPIRED"


# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

def _cfg_float(key: str, default: float) -> float:
    try:
        v = float(get_config_value(key, default))
        return v if math.isfinite(v) else default
    except Exception:
        return default


def peak_grace_sec() -> float:
    """Bounded grace window for a qualified peak after its source disappears."""
    return max(0.0, _cfg_float("MODE3_PEAK_GRACE_SEC", 180.0))


def qualified_sources() -> set[str]:
    raw = str(get_config_value("MODE3_QUALIFIED_PEAK_SOURCES", _DEFAULT_QUALIFIED_SOURCES))
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def source_is_qualified(source: Any) -> bool:
    """Return True for an approved executable family or a corroborated subtype.

    `intel-mtm` by itself remains suspect.  The explicit suffixes
    `:curve_reserve`, `:pool_quote`, and `:rpc_direct` are accepted only because
    record_qualified_peak() is called from the corroborated mark-tape authority
    path.  Fallback/display/market-cap sources remain rejected.
    """
    s = str(source or "").strip().lower()
    if not s or s in _SUSPECT_SOURCES:
        return False
    if any(bad in s for bad in (
        "fallback", "display", "market_cap", "mcap", "theoretical", "estimate",
    )):
        return False
    allowed = qualified_sources()
    # MODE3_SOURCE_AUTHORITY_20260808: do not promote a generic intel/cache
    # label merely because it ends in :pool_quote/:curve_reserve/:rpc_direct.
    # The caller must present an explicit approved authority family such as
    # onchain_quote, pumpfun_curve, router_exec or jupiter.
    return any(s == a or s.startswith(a) for a in allowed)


# ──────────────────────────────────────────────────────────────────────────────
# Storage
# ──────────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS mode3_qualified_peaks (
    position_id           INTEGER PRIMARY KEY,
    mint_address          TEXT,
    entry_price           REAL,
    peak_price            REAL NOT NULL,
    peak_pct              REAL,
    peak_source           TEXT NOT NULL,
    peak_qualified_at     REAL NOT NULL,
    peak_last_confirmed_at REAL NOT NULL,
    confirmations         INTEGER DEFAULT 1,
    floor_state           TEXT,
    floor_pct             REAL,
    floor_price           REAL,
    floor_armed_at        REAL,
    exec_source           TEXT,
    exec_quote_age_sec    REAL,
    exec_slippage_pct     REAL,
    updated_at            REAL
)
"""


def ensure_tables(conn=None) -> None:
    own = conn is None
    c = conn or _conn_factory()
    try:
        c.execute(_DDL)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_m3qp_confirmed "
            "ON mode3_qualified_peaks(peak_last_confirmed_at)"
        )
        try:
            c.commit()
        except Exception:
            pass
    finally:
        if own:
            try:
                c.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# B — sticky qualified peak
# ──────────────────────────────────────────────────────────────────────────────

def record_qualified_peak(
    position_id: int,
    mint_address: str,
    entry_price: float,
    peak_price: float,
    peak_source: str,
    *,
    now: Optional[float] = None,
    anti_outlier_ok: bool = True,
    authority_class: Any = None,
    evidence_family: Any = None,
) -> dict:
    """Persist a peak that has ALREADY passed source-quality + anti-outlier checks.

    Refuses silently (returns accepted=False) when the source is unqualified or
    the caller reports an anti-outlier failure. Never lowers an existing peak.
    """
    now = float(now if now is not None else time.time())
    result = {"accepted": False, "reason": "", "peak_price": 0.0, "peak_source": ""}

    try:
        position_id = int(position_id)
        peak_price = float(peak_price)
        entry_price = float(entry_price or 0.0)
    except Exception:
        result["reason"] = "bad_numeric_input"
        return result

    if not math.isfinite(peak_price) or peak_price <= 0:
        result["reason"] = "non_positive_peak"
        return result
    if not anti_outlier_ok:
        result["reason"] = "anti_outlier_rejected"
        return result
    if not source_is_qualified(peak_source):
        # Resolve through the canonical authority contract before refusing, so a
        # legitimate executable authority class is not rejected merely because
        # its label differs from a raw-source name (blocker 2).
        _mapped, _why = canonical_peak_source(
            authority_class=authority_class, evidence_family=evidence_family,
            raw_source=peak_source)
        if _mapped:
            peak_source = _mapped
        else:
            # INVARIANT B: never manufacture a peak from a display-only quote.
            result["reason"] = f"unqualified_source={str(peak_source)[:40]}:{_why}"
            log.warning("[MODE3_PEAK_REFUSED] pos=%s source=%s class=%s family=%s reason=%s",
                        position_id, str(peak_source)[:40],
                        str(authority_class or "")[:40],
                        str(evidence_family or "")[:40], _why)
            return result

    src = str(peak_source).strip().lower()
    peak_pct = ((peak_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0

    conn = _conn_factory()
    try:
        ensure_tables(conn)
        row = conn.execute(
            "SELECT peak_price, confirmations FROM mode3_qualified_peaks WHERE position_id=?",
            (position_id,),
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO mode3_qualified_peaks ("
                "position_id, mint_address, entry_price, peak_price, peak_pct, "
                "peak_source, peak_qualified_at, peak_last_confirmed_at, "
                "confirmations, floor_state, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,1,NULL,?)",
                (position_id, str(mint_address or ""), entry_price, peak_price,
                 peak_pct, src, now, now, now),
            )
        else:
            prev = float(row[0] or 0.0)
            confirmations = int(row[1] or 0) + 1
            if peak_price > prev:
                conn.execute(
                    "UPDATE mode3_qualified_peaks SET peak_price=?, peak_pct=?, "
                    "peak_source=?, peak_last_confirmed_at=?, confirmations=?, "
                    "updated_at=? WHERE position_id=?",
                    (peak_price, peak_pct, src, now, confirmations, now, position_id),
                )
            else:
                # Peak not exceeded, but the source is alive: refresh liveness
                # only. The stored high-water mark is monotonic.
                conn.execute(
                    "UPDATE mode3_qualified_peaks SET peak_last_confirmed_at=?, "
                    "confirmations=?, updated_at=? WHERE position_id=?",
                    (now, confirmations, now, position_id),
                )
        try:
            conn.commit()
        except Exception:
            pass

        cur = conn.execute(
            "SELECT peak_price, peak_source FROM mode3_qualified_peaks WHERE position_id=?",
            (position_id,),
        ).fetchone()
        result.update({
            "accepted": True,
            "reason": "ok",
            "peak_price": float(cur[0]) if cur else peak_price,
            "peak_source": str(cur[1]) if cur else src,
        })
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sticky_qualified_peak(position_id: int, *, now: Optional[float] = None) -> dict:
    """Return the retained qualified peak, if any, with its grace status.

    status:
      LIVE      - source confirmed within the last confirmation interval
      STICKY    - source temporarily absent, still inside the grace window
      EXPIRED   - grace window elapsed
      NONE      - no qualified peak was ever recorded for this position
    """
    now = float(now if now is not None else time.time())
    out = {
        "status": "NONE", "peak_price": 0.0, "peak_pct": 0.0, "peak_source": "",
        "age_sec": None, "grace_sec": peak_grace_sec(), "confirmations": 0,
        "qualified_at": None,
    }
    conn = _conn_factory()
    try:
        ensure_tables(conn)
        row = conn.execute(
            "SELECT peak_price, peak_pct, peak_source, peak_last_confirmed_at, "
            "confirmations, peak_qualified_at "
            "FROM mode3_qualified_peaks WHERE position_id=?",
            (int(position_id),),
        ).fetchone()
        if not row:
            return out

        peak_price = float(row[0] or 0.0)
        if peak_price <= 0:
            return out

        last_confirmed = float(row[3] or 0.0)
        age = max(0.0, now - last_confirmed)
        grace = peak_grace_sec()
        live_window = max(1.0, _cfg_float("MODE3_PEAK_LIVE_WINDOW_SEC", 15.0))

        out.update({
            "peak_price": peak_price,
            "peak_pct": float(row[1] or 0.0),
            "peak_source": str(row[2] or ""),
            "age_sec": age,
            "confirmations": int(row[4] or 0),
            "qualified_at": float(row[5] or 0.0),
            "grace_sec": grace,
        })
        if age <= live_window:
            out["status"] = "LIVE"
        elif age <= grace:
            out["status"] = "STICKY"
        else:
            out["status"] = "EXPIRED"
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# C — runner-floor continuity
# ──────────────────────────────────────────────────────────────────────────────

def resolve_floor_state(
    position_id: int,
    *,
    tape_peak_price: float = 0.0,
    tape_peak_source: str = "",
    exec_source: str = "",
    exec_quote_age_sec: Optional[float] = None,
    exec_slippage_pct: Optional[float] = None,
    now: Optional[float] = None,
) -> dict:
    """Resolve the authoritative floor state for a position.

    INVARIANT C: an already-armed floor is NOT reverted to UNAVAILABLE merely
    because the peak-establishing source disappeared. It is retained as
    ARMED_STICKY until the grace window elapses, then EXPIRED (explicit, logged)
    - never silently downgraded.

    The executable quote may come from a different approved source than the peak
    source. exec_source, exec_quote_age_sec and exec_slippage_pct are recorded
    separately from peak_source and are never conflated with it.
    """
    now = float(now if now is not None else time.time())

    # A live tape peak refreshes stickiness (only if genuinely qualified).
    if tape_peak_price and float(tape_peak_price) > 0 and source_is_qualified(tape_peak_source):
        try:
            conn = _conn_factory()
            try:
                ensure_tables(conn)
                r = conn.execute(
                    "SELECT entry_price, mint_address FROM mode3_qualified_peaks "
                    "WHERE position_id=?", (int(position_id),)).fetchone()
                entry = float(r[0]) if r and r[0] else 0.0
                mint = str(r[1]) if r and r[1] else ""
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            record_qualified_peak(position_id, mint, entry, float(tape_peak_price),
                                  tape_peak_source, now=now)
        except Exception:
            pass

    peak = sticky_qualified_peak(position_id, now=now)

    prior_state = ""
    prior_floor_pct = None
    prior_floor_price = None
    conn = _conn_factory()
    try:
        ensure_tables(conn)
        row = conn.execute(
            "SELECT floor_state, floor_pct, floor_price FROM mode3_qualified_peaks "
            "WHERE position_id=?", (int(position_id),)).fetchone()
        if row:
            prior_state = str(row[0] or "")
            prior_floor_pct = row[1]
            prior_floor_price = row[2]

        was_armed = prior_state in (FLOOR_ARMED, FLOOR_ARMED_STICKY)

        if peak["status"] == "NONE":
            state, reason = FLOOR_UNAVAILABLE, "no_qualified_peak_ever_recorded"
        elif peak["status"] == "LIVE":
            state, reason = FLOOR_ARMED, "peak_source_live"
        elif peak["status"] == "STICKY":
            if was_armed:
                state = FLOOR_ARMED_STICKY
                reason = (f"peak_source_absent_{peak['age_sec']:.0f}s"
                          f"_within_grace_{peak['grace_sec']:.0f}s")
            else:
                # Never ARM for the first time off a stale peak.
                state, reason = FLOOR_UNAVAILABLE, "peak_stale_never_armed"
        else:  # EXPIRED
            state = FLOOR_EXPIRED if was_armed else FLOOR_UNAVAILABLE
            reason = f"grace_elapsed_age={peak['age_sec']:.0f}s>{peak['grace_sec']:.0f}s"

        conn.execute(
            "UPDATE mode3_qualified_peaks SET floor_state=?, exec_source=?, "
            "exec_quote_age_sec=?, exec_slippage_pct=?, updated_at=? "
            "WHERE position_id=?",
            (state, str(exec_source or "")[:64],
             (float(exec_quote_age_sec) if exec_quote_age_sec is not None else None),
             (float(exec_slippage_pct) if exec_slippage_pct is not None else None),
             now, int(position_id)),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "position_id": int(position_id),
        "floor_state": state,
        "reason": reason,
        "prior_state": prior_state,
        "peak_price": peak["peak_price"],
        "peak_pct": peak["peak_pct"],
        "peak_source": peak["peak_source"],
        "peak_status": peak["status"],
        "peak_age_sec": peak["age_sec"],
        "grace_sec": peak["grace_sec"],
        "confirmations": peak["confirmations"],
        # Recorded separately per invariant C — never merged with peak_source.
        "exec_source": str(exec_source or ""),
        "exec_quote_age_sec": exec_quote_age_sec,
        "exec_slippage_pct": exec_slippage_pct,
        "floor_is_protective": state in (FLOOR_ARMED, FLOOR_ARMED_STICKY),
        "prior_floor_pct": prior_floor_pct,
        "prior_floor_price": prior_floor_price,
    }


def arm_floor(position_id: int, floor_pct: float, floor_price: float,
              *, now: Optional[float] = None) -> None:
    """Persist the computed floor level once the caller has derived it."""
    now = float(now if now is not None else time.time())
    conn = _conn_factory()
    try:
        ensure_tables(conn)
        conn.execute(
            "UPDATE mode3_qualified_peaks SET floor_pct=?, floor_price=?, "
            "floor_armed_at=COALESCE(floor_armed_at, ?), "
            "floor_state=CASE WHEN floor_state IN (?,?) THEN floor_state ELSE ? END, "
            "updated_at=? WHERE position_id=?",
            (float(floor_pct), float(floor_price), now,
             FLOOR_ARMED, FLOOR_ARMED_STICKY, FLOOR_ARMED, now, int(position_id)),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def clear_position(position_id: int) -> None:
    """Remove continuity state after a position is closed and settled."""
    conn = _conn_factory()
    try:
        ensure_tables(conn)
        conn.execute("DELETE FROM mode3_qualified_peaks WHERE position_id=?",
                     (int(position_id),))
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
