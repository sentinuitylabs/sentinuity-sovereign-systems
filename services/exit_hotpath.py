"""
exit_hotpath.py — SENTINUITY exit-evaluator hot-path support.
REGRESSION_AUDIT_20260805.

Purpose
-------
Removes blocking work from the serial open-position exit loop
(`execution_engine.check_open_positions` -> `evaluate_exit_for_position`)
without changing a single trading decision.

Three responsibilities, all strictly non-authoritative:

  1. `note_eval(position_id)` / `last_measured_gap(position_id)`
     Records the true wall-clock interval between consecutive exit
     evaluations of a position, so logs can report MEASURED cadence
     instead of a configured constant.

  2. `intel_ticks_exist(mint, since_ms)`
     Replaces a per-position `SELECT COUNT(*) FROM mtm_ticks ...` that
     opened a fresh SQLite connection (and re-ran 5 PRAGMAs) on every
     position on every cycle. Uses one lazily-created, thread-local,
     read-only-intent connection and an `EXISTS(SELECT 1 ... LIMIT 1)`
     probe, whose result is only ever compared against zero anyway.

  3. `submit_stop_probe(payload)`
     Hands stop-realisability quote probing to a single bounded worker
     thread. The probe performs blocking HTTP (token-decimals RPC +
     Jupiter quote tiers). Running it inline stalled the evaluation of
     every *other* open position at the exact moment a runner was
     collapsing. The probe is quote-only evidence; it never signs,
     submits, or alters a close, so deferring it by a few seconds
     changes no trading outcome.

Explicit non-goals — this module does NOT:
  * change runner thresholds, profit-lock floors, trailing logic
  * change entry qualification or pattern authority
  * change the hard-stop percentage or DUAL authority
  * change confirmed-fill accounting
  * enable, arm, or influence any funded-live path

Fail-closed: every public entry point swallows its own errors and
degrades to the previous observable behaviour (no probe / no tick
evidence), never to a trading action.
"""

from __future__ import annotations

import atexit
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "note_eval",
    "last_measured_gap",
    "intel_ticks_exist",
    "submit_stop_probe",
    "shutdown",
    "stats",
]

# ── configuration (env-overridable, safe defaults) ───────────────────────────
_QUEUE_MAX = max(8, int(os.environ.get("STOP_PROBE_QUEUE_MAX", "256")))
_WORKER_ENABLED = os.environ.get("STOP_PROBE_ASYNC", "1").strip() != "0"
_PROBE_STALE_SEC = float(os.environ.get("STOP_PROBE_MAX_AGE_SEC", "120"))
_INTEL_DB_NAME = "sentinuity_intelligence.db"

_log_lock = threading.Lock()


def _log():
    """Late-bound logger; never import-time fatal."""
    try:
        import logging
        return logging.getLogger("exit_hotpath")
    except Exception:  # pragma: no cover
        return None


def _warn(msg: str, *args) -> None:
    lg = _log()
    if lg is not None:
        try:
            lg.warning(msg, *args)
        except Exception:
            pass


def _debug(msg: str, *args) -> None:
    lg = _log()
    if lg is not None:
        try:
            lg.debug(msg, *args)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. MEASURED EVALUATION CADENCE
# ─────────────────────────────────────────────────────────────────────────────
_eval_lock = threading.Lock()
_last_eval_at: Dict[int, float] = {}
_last_gap: Dict[int, float] = {}


def note_eval(position_id: Any) -> float:
    """
    Record that `position_id` is being evaluated right now.

    Returns the measured seconds since the previous evaluation of the same
    position, or -1.0 if this is the first observation in this process.
    """
    try:
        pid = int(position_id)
    except Exception:
        return -1.0
    now = time.time()
    with _eval_lock:
        prev = _last_eval_at.get(pid)
        _last_eval_at[pid] = now
        if prev is None:
            _last_gap[pid] = -1.0
            return -1.0
        gap = max(0.0, now - prev)
        _last_gap[pid] = gap
        return gap


def last_measured_gap(position_id: Any) -> float:
    """
    Measured seconds between the two most recent evaluations of this
    position. -1.0 when not yet measurable. Never a configured constant.
    """
    try:
        pid = int(position_id)
    except Exception:
        return -1.0
    with _eval_lock:
        return float(_last_gap.get(pid, -1.0))


def forget_position(position_id: Any) -> None:
    """Drop cadence state for a closed position (bounded memory)."""
    try:
        pid = int(position_id)
    except Exception:
        return
    with _eval_lock:
        _last_eval_at.pop(pid, None)
        _last_gap.pop(pid, None)


# ─────────────────────────────────────────────────────────────────────────────
# 2. CACHED INTEL READER (replaces per-position COUNT(*) + connection churn)
# ─────────────────────────────────────────────────────────────────────────────
_tls = threading.local()


def _intel_db_path() -> Path:
    return Path(__file__).resolve().parent.parent / _INTEL_DB_NAME


def _intel_conn() -> Optional[sqlite3.Connection]:
    """
    One connection per thread, created once, PRAGMAs applied once.

    The previous hot-path code called get_intel_connection() per position per
    cycle: a fresh file open plus `PRAGMA journal_mode` / `synchronous` /
    `busy_timeout` / `temp_store` / `foreign_keys` on every iteration, then
    an immediate close.
    """
    conn = getattr(_tls, "intel", None)
    if conn is not None:
        return conn
    try:
        conn = sqlite3.connect(
            str(_intel_db_path()),
            timeout=5.0,
            check_same_thread=False,
            isolation_level=None,
        )
        # Read-only intent. Do not touch journal_mode: interrogating or
        # switching WAL state during a writer storm is itself contention,
        # and this connection never writes.
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA query_only=ON")
        _tls.intel = conn
        return conn
    except Exception as exc:
        _debug("intel connection unavailable: %s", exc)
        _tls.intel = None
        return None


def intel_ticks_exist(mint: str, since_ms: float) -> Optional[bool]:
    """
    True  -> at least one mtm_tick for `mint` at/after `since_ms`
    False -> none
    None  -> could not determine (treated by callers as "do not warn")

    Uses EXISTS/LIMIT 1 rather than COUNT(*): the caller only ever compared
    the count against zero, so counting every matching row was wasted work.
    """
    if not mint:
        return None
    conn = _intel_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM mtm_ticks "
            "WHERE mint_address=? AND ts_ms>=? LIMIT 1)",
            (str(mint), float(since_ms)),
        ).fetchone()
        return bool(row and row[0])
    except Exception as exc:
        # Drop the cached handle so a transient fault cannot pin a bad
        # connection for the life of the thread.
        try:
            conn.close()
        except Exception:
            pass
        _tls.intel = None
        _debug("intel tick probe failed mint=%s: %s", str(mint)[:16], exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. BOUNDED STOP-PROBE WORKER
# ─────────────────────────────────────────────────────────────────────────────
_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_stopping = threading.Event()

_counters = {
    "submitted": 0,
    "dropped_full": 0,
    "dropped_stale": 0,
    "executed": 0,
    "failed": 0,
}
_counter_lock = threading.Lock()


def _bump(key: str) -> None:
    with _counter_lock:
        _counters[key] = _counters.get(key, 0) + 1


def stats() -> Dict[str, int]:
    """Snapshot of probe-queue counters (diagnostics only)."""
    with _counter_lock:
        return dict(_counters)


def _run_probe(payload: Dict[str, Any]) -> None:
    """Execute one stop probe on the worker thread, with its own connection."""
    try:
        from services.stop_realisability import probe_stop
    except Exception as exc:
        _debug("[STOP_REALISABILITY_PROBE_UNAVAILABLE] %s", exc)
        _bump("failed")
        return
    conn = None
    try:
        from core.schema import get_connection
        conn = get_connection()
        probe_stop(conn, **payload)
        try:
            conn.commit()
        except Exception:
            pass
        _bump("executed")
    except Exception as exc:
        _bump("failed")
        _debug(
            "[STOP_REALISABILITY_PROBE_FAIL] pos=%s %s",
            payload.get("position_id"), exc,
        )
    finally:
        # Explicit closure: the worker must not accumulate handles.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _worker_loop() -> None:
    while not _stopping.is_set():
        try:
            item = _q.get(timeout=0.5)
        except queue.Empty:
            continue
        if item is None:
            _q.task_done()
            break
        try:
            enqueued_at = float(item.pop("_enqueued_at", 0.0) or 0.0)
            if enqueued_at and (time.time() - enqueued_at) > _PROBE_STALE_SEC:
                # A probe older than the staleness bound describes a market
                # state that no longer exists. Recording it would pollute
                # the readiness cohort with misleading evidence.
                _bump("dropped_stale")
            else:
                _run_probe(item)
        except Exception as exc:  # pragma: no cover
            _bump("failed")
            _debug("probe worker error: %s", exc)
        finally:
            _q.task_done()


def _ensure_worker() -> bool:
    global _worker
    if not _WORKER_ENABLED:
        return False
    if _worker is not None and _worker.is_alive():
        return True
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return True
        try:
            _stopping.clear()
            _worker = threading.Thread(
                target=_worker_loop,
                name="stop-probe-worker",
                daemon=True,
            )
            _worker.start()
            return True
        except Exception as exc:
            _warn("stop-probe worker could not start: %s", exc)
            _worker = None
            return False


def submit_stop_probe(**payload: Any) -> bool:
    """
    Queue a stop-realisability probe for asynchronous execution.

    Returns True if accepted, False if dropped. Dropping is safe and
    intentional: the probe is quote-only evidence. Under no circumstance
    does this call block the exit evaluator on network I/O.
    """
    if not _ensure_worker():
        return False
    try:
        payload["_enqueued_at"] = time.time()
        _q.put_nowait(payload)
        _bump("submitted")
        return True
    except queue.Full:
        # Bounded by design. A saturated queue means probes are arriving
        # faster than they can complete; shedding is preferable to
        # unbounded memory growth or evaluator back-pressure.
        _bump("dropped_full")
        return False
    except Exception as exc:
        _debug("probe submit failed: %s", exc)
        return False


def shutdown(timeout: float = 5.0) -> None:
    """Drain and stop the worker. Safe to call more than once."""
    global _worker
    if _worker is None:
        return
    try:
        _stopping.set()
        try:
            _q.put_nowait(None)
        except Exception:
            pass
        _worker.join(timeout=max(0.1, float(timeout)))
    except Exception:
        pass
    finally:
        _worker = None
    conn = getattr(_tls, "intel", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _tls.intel = None


atexit.register(shutdown)
