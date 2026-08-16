"""
cadence_probe.py - measured wall-clock instrumentation for the exit hot path.

WHY THIS EXISTS
---------------
execution_engine.py currently cannot answer directive questions 2, 3 or 14.

  * scan_for_entries() has no duration instrumentation anywhere in the file.
    (`scan_for_entries._latency_cache` measures DB *write* latency, not scan
    duration.)
  * `_runner_aware_poll_interval()` returns a CONFIGURED constant. The engine's
    own comment at execution_engine.py:6365 flags that logging it "was never
    measured elapsed time and therefore could not evidence a cadence failure."
  * exit_hotpath.note_eval/last_measured_gap track the gap between the last two
    evaluations of one position, but nothing aggregates them, and the only
    consumer - RUNNER_EVAL_CADENCE_BREACH - sits on an unreachable branch for
    healthy runners (see findings section 3).

This module is deliberately additive and boring:

  * No DB writes on the hot path. Everything is an in-memory ring buffer.
  * No network. No imports from execution_engine (no cycles).
  * Every public call is wrapped so a probe failure can never affect trading.
  * Bounded memory: fixed-size deques, positions evicted on close.

It changes no behaviour. It only makes the existing behaviour measurable.

WIRING (four lines in services/execution_engine.py)
--------------------------------------------------
    from services import cadence_probe as _cp

    # in run(), around the entry scan:
    with _cp.stage("scan_for_entries"):
        scan_for_entries()

    # in check_open_positions(), around the whole loop:
    with _cp.stage("check_open_positions"):
        ...

    # in evaluate_exit_for_position(), next to the existing note_eval call:
    _cp.note_eval(position.get("id"), pnl_pct=None)

    # in run(), in the existing `if cycle % 12 == 0:` heartbeat block:
    log.info("[CADENCE] %s", _cp.report_line())

Read it back with `python -m services.cadence_probe --watch` or by calling
`snapshot()` from the UI.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Deque, Dict, Iterator, List, Optional, Tuple

__all__ = [
    "stage", "note_eval", "forget_position", "snapshot",
    "report_line", "reset", "STAGES",
]

# Stages we care about. Anything else is accepted but not pre-allocated.
STAGES: Tuple[str, ...] = (
    "check_open_positions",
    "scan_for_entries",
    "evaluate_exit_for_position",
    "trusted_peak_from_tape",
    "runner_profit_lock_decision",
    "update_position_mark",
    "cycle_total",
)

_RING = 512          # samples retained per stage
_MAX_POSITIONS = 256  # eviction bound for per-position gap tracking

_lock = threading.Lock()
_stage_samples: Dict[str, Deque[float]] = {}
_stage_worst: Dict[str, Tuple[float, float]] = {}   # stage -> (ms, epoch)
_eval_last: Dict[Any, float] = {}
_eval_gaps: Dict[Any, Deque[float]] = {}
_started_at = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# percentiles
# ─────────────────────────────────────────────────────────────────────────────

def _pct(sorted_xs: List[float], q: float) -> float:
    """Nearest-rank percentile. `sorted_xs` must already be sorted."""
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = max(0, min(len(sorted_xs) - 1, int(round(q * (len(sorted_xs) - 1)))))
    return sorted_xs[k]


def _summarise(xs: Deque[float]) -> Dict[str, float]:
    s = sorted(xs)
    if not s:
        return {"n": 0, "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "n": len(s),
        "p50": _pct(s, 0.50),
        "p90": _pct(s, 0.90),
        "p99": _pct(s, 0.99),
        "max": s[-1],
        "mean": sum(s) / len(s),
    }


# ─────────────────────────────────────────────────────────────────────────────
# stage timing
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def stage(name: str) -> Iterator[None]:
    """
    Time a named stage. Never raises, never suppresses the wrapped exception.

        with cadence_probe.stage("scan_for_entries"):
            scan_for_entries()
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        try:
            _record(name, (time.perf_counter() - t0) * 1000.0)
        except Exception:
            pass


def _record(name: str, ms: float) -> None:
    with _lock:
        d = _stage_samples.get(name)
        if d is None:
            d = _stage_samples[name] = deque(maxlen=_RING)
        d.append(ms)
        worst = _stage_worst.get(name)
        if worst is None or ms > worst[0]:
            _stage_worst[name] = (ms, time.time())


# ─────────────────────────────────────────────────────────────────────────────
# per-position evaluation cadence
# ─────────────────────────────────────────────────────────────────────────────

def note_eval(position_id: Any, pnl_pct: Optional[float] = None) -> float:
    """
    Record that `position_id` is being evaluated now. Returns the measured gap
    in seconds since the previous evaluation of that position (0.0 on first
    sight). `pnl_pct` is accepted for call-site symmetry and ignored.
    """
    try:
        now = time.time()
        with _lock:
            prev = _eval_last.get(position_id)
            _eval_last[position_id] = now
            if prev is None:
                if len(_eval_last) > _MAX_POSITIONS:
                    # evict the stalest tracked position
                    oldest = min(_eval_last, key=lambda k: _eval_last[k])
                    _eval_last.pop(oldest, None)
                    _eval_gaps.pop(oldest, None)
                return 0.0
            gap = now - prev
            g = _eval_gaps.get(position_id)
            if g is None:
                g = _eval_gaps[position_id] = deque(maxlen=_RING)
            g.append(gap)
            return gap
    except Exception:
        return 0.0


def forget_position(position_id: Any) -> None:
    """Drop cadence state for a closed position."""
    try:
        with _lock:
            _eval_last.pop(position_id, None)
            _eval_gaps.pop(position_id, None)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# reporting
# ─────────────────────────────────────────────────────────────────────────────

def snapshot() -> Dict[str, Any]:
    """
    Full measured picture. Safe to call from the UI or the heartbeat block.

    Returns
    -------
    {
      "uptime_sec": float,
      "stages": {name: {n, p50, p90, p99, max, mean}},   # milliseconds
      "stage_worst": {name: {"ms": float, "at": epoch}},
      "eval_gaps_sec": {"all": {...}, "per_position": {pid: {...}}},
      "positions_tracked": int,
    }
    """
    with _lock:
        stages = {k: _summarise(v) for k, v in _stage_samples.items()}
        worst = {k: {"ms": v[0], "at": v[1]} for k, v in _stage_worst.items()}
        per_pos = {str(k): _summarise(v) for k, v in _eval_gaps.items()}
        pooled: Deque[float] = deque(maxlen=_RING * 4)
        for v in _eval_gaps.values():
            pooled.extend(v)
        pooled_summary = _summarise(pooled)
        tracked = len(_eval_last)

    return {
        "uptime_sec": time.time() - _started_at,
        "stages": stages,
        "stage_worst": worst,
        "eval_gaps_sec": {"all": pooled_summary, "per_position": per_pos},
        "positions_tracked": tracked,
    }


def report_line() -> str:
    """
    One-line summary for the heartbeat log. This is the line that answers
    directive questions 2 and 3 directly.
    """
    try:
        s = snapshot()
        scan = s["stages"].get("scan_for_entries") or {}
        chk = s["stages"].get("check_open_positions") or {}
        gaps = s["eval_gaps_sec"]["all"]
        return (
            "scan_ms p50=%.0f p90=%.0f max=%.0f n=%d | "
            "check_ms p50=%.0f p90=%.0f max=%.0f | "
            "eval_gap_s p50=%.2f p90=%.2f max=%.2f n=%d | pos=%d"
            % (
                scan.get("p50", 0), scan.get("p90", 0), scan.get("max", 0), scan.get("n", 0),
                chk.get("p50", 0), chk.get("p90", 0), chk.get("max", 0),
                gaps.get("p50", 0), gaps.get("p90", 0), gaps.get("max", 0), gaps.get("n", 0),
                s["positions_tracked"],
            )
        )
    except Exception as e:
        return f"cadence_probe_unavailable:{type(e).__name__}"


def reset() -> None:
    """Clear all samples. Diagnostics only."""
    global _started_at
    with _lock:
        _stage_samples.clear()
        _stage_worst.clear()
        _eval_last.clear()
        _eval_gaps.clear()
        _started_at = time.time()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="cadence_probe self-test")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        for i in range(50):
            with stage("scan_for_entries"):
                time.sleep(0.002 + (i % 7) * 0.001)
            note_eval(1)
            note_eval(2)
            time.sleep(0.001)
        print(report_line())
        print(json.dumps(snapshot()["stages"], indent=2))
    else:
        ap.print_help()
