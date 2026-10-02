"""
services/paper_exit_parity.py — PAPER_EXIT_PARITY_20261001
==========================================================

Paper no-price exit servicing that exercises the same hierarchy live uses.

Before this change, a PAPER position whose cached Layer-C executable quote
disappeared had every exit (TP, hard stop, runner floor, trailing) suppressed
until max-hold + 180 s, when `paper_exit_escalation` closed it at an
OBSERVATIONAL price. Super Inu #9625 sat 17 minutes in that state while its
mark swung +288% / -70% and closed at -71%. A REAL position in the same state
takes `LIVE_EMERGENCY_NO_PRICE` after `LIVE_NO_PRICE_EXIT_GRACE_SEC` (45 s).

Hierarchy (paper now matches live's order; nothing is fabricated):
  1. normal executable quote          -> existing evaluator (unchanged)
  2. approved executable fallback     -> a fresh exact-size liquidation quote
                                         (quote-only; no signing, no send)
  3. signed no-price emergency route  -> after the SAME grace key live uses,
                                         close at that executable quote only
  4. coverage failure                 -> persist + log, keep retrying; never
                                         book a synthetic stop/TP/floor price
  5. existing max-hold escalation     -> unchanged, but its close is labelled
                                         OBSERVATIONAL_EXIT_UNEXECUTABLE so it
                                         can never pass as executable truth

This module is pure policy plus an orchestrator with injected side effects so
it can be tested deterministically without a database or network.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Mapping, Optional

ACTION_WAIT_GRACE = "WAIT_GRACE"
ACTION_BACKOFF = "BACKOFF"
ACTION_EMERGENCY_EXIT = "EMERGENCY_EXIT"
ACTION_COVERAGE_FAILURE = "COVERAGE_FAILURE"
ACTION_ESCALATED = "ESCALATED_OBSERVATIONAL"
ACTION_FRESH_QUOTE = "FRESH_EXECUTABLE_QUOTE"  # PAPER_EXIT_FRESH_QUOTE_FEED_20261003

STATE_COVERAGE_FAILURE = "COVERAGE_FAILURE"
STATE_RECOVERED = "RECOVERED"
INTEGRITY_OBSERVATIONAL = "OBSERVATIONAL_EXIT_UNEXECUTABLE"

DEFAULT_GRACE_SEC = 45.0     # same default as LIVE_NO_PRICE_EXIT_GRACE_SEC
DEFAULT_RETRY_SEC = 10.0

_LAST_ATTEMPT: dict[int, float] = {}


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def quote_is_executable(q: Optional[Mapping[str, Any]]) -> bool:
    return bool(isinstance(q, Mapping) and q.get("can_execute_exit")
                and _f(q.get("price")) > 0.0)


def decide(*, now: float, opened_at: float, grace_sec: float,
           last_attempt_at: Optional[float], retry_sec: float) -> str:
    """Whether to try the emergency route on this sweep."""
    if now - _f(opened_at) < max(0.0, _f(grace_sec, DEFAULT_GRACE_SEC)):
        return ACTION_WAIT_GRACE
    if last_attempt_at is not None and now - _f(last_attempt_at) < max(1.0, _f(retry_sec, DEFAULT_RETRY_SEC)):
        return ACTION_BACKOFF
    return "ATTEMPT"


def service_no_price_paper(
    position: Mapping[str, Any],
    *,
    now: Optional[float] = None,
    grace_sec: float = DEFAULT_GRACE_SEC,
    retry_sec: float = DEFAULT_RETRY_SEC,
    quote_fn: Callable[[], Optional[Mapping[str, Any]]],
    close_fn: Callable[[float, str], bool],
    record_failure_fn: Callable[[str], None],
    escalate_fn: Callable[[], Optional[Mapping[str, Any]]],
    mark_observational_fn: Callable[[str], None],
    attempts: Optional[dict] = None,
) -> dict:
    """Service one PAPER position that has no executable cached quote.

    Returns {"action", "price", "reason"}. Side effects only via callbacks.
    """
    now = time.time() if now is None else float(now)
    attempts = _LAST_ATTEMPT if attempts is None else attempts
    pid = int(position.get("id") or 0)
    opened_at = _f(position.get("opened_at"))
    hold = max(0.0, now - opened_at)

    step = decide(now=now, opened_at=opened_at, grace_sec=grace_sec,
                  last_attempt_at=attempts.get(pid), retry_sec=retry_sec)
    if step == "ATTEMPT":
        attempts[pid] = now
        q = None
        try:
            q = quote_fn()
        except Exception as exc:  # a failed quote is a coverage failure, not a price
            q = {"can_execute_exit": False, "warning": f"{type(exc).__name__}"}
        if quote_is_executable(q):
            # PAPER_EXIT_FRESH_QUOTE_FEED_20261003: a successful fresh exact-size
            # quote IS executable price truth. Live consumes exactly this quote
            # every sweep and keeps managing; it never exits because a cache was
            # late. Hand the quote back so the normal evaluator (stop / TP /
            # runner lock / trail / max-hold) decides. No close happens here.
            # attempts[pid] is kept so the retry backoff still bounds quote load.
            return {"action": ACTION_FRESH_QUOTE, "price": _f(q.get("price")),
                    "reason": None, "quote": dict(q)}
        why = str((q or {}).get("warning") or (q or {}).get("data_status") or "NO_EXECUTABLE_ROUTE")[:120]
        record_failure_fn(why)
        step = ACTION_COVERAGE_FAILURE

    # The pre-existing max-hold escalation still runs (unchanged thresholds);
    # its close is labelled observational so it cannot pass as executable truth.
    esc = None
    try:
        esc = escalate_fn()
    except Exception:
        esc = None
    if esc:
        reason = str(esc.get("reason") or "PAPER_EXIT_ESCALATION")
        if close_fn(_f(esc.get("price")), reason):
            mark_observational_fn(reason)
            attempts.pop(pid, None)
            return {"action": ACTION_ESCALATED, "price": _f(esc.get("price")), "reason": reason}
    return {"action": step, "price": None, "reason": None}
