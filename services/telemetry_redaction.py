"""
SENTINUITY — TELEMETRY CREDENTIAL REDACTION
===========================================
CREDENTIAL_REDACTION_20260804

Why this exists
---------------
The 2026-08-04 10:37–15:07 runtime emitted 999 identical warnings of the form

    [GOVERNOR] WARNING Telegram getUpdates failed: 401 Client Error:
    Unauthorized for url: https://api.telegram.org/bot<TOKEN>/getUpdates

Two separate failures produced that line:

  1. CREDENTIAL EXPOSURE. requests' HTTPError stringifies the full request URL.
     The bot token is a path segment of that URL. Passing the exception to
     log.warning therefore writes the live credential to disk on every failure.

  2. EVIDENCE DESTRUCTION. The loop was unbounded, so it saturated the audit
     collector: 1000 of 1000 captured error lines came from this one file,
     displacing all 631 execution_engine error lines and all 254 of its trade
     lines from the exported evidence.

The second failure is the more expensive one. An unbounded warning loop is not
cosmetic noise; it is an evidence-integrity fault that blinds post-hoc audit of
the trading window.

Usage
-----
    from services.telemetry_redaction import redact, warn_once

    except Exception as e:
        warn_once(log, "tg_%s" % method, "Telegram %s failed: %s", method, redact(e))

Never pass a raw exception or URL from any credentialed HTTP call to a logger.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Tuple

# Telegram bot tokens: <digits>:<35-ish base64url chars>, appearing bare or in
# a /bot<token>/ URL path segment.
_TELEGRAM_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{20,}")
_TELEGRAM_BOT_PATH = re.compile(r"(/bot)\d{6,12}:[A-Za-z0-9_\-]{20,}", re.I)

# Defensive: other credentials that travel in query strings or headers.
_QUERY_SECRET = re.compile(
    r"([?&](?:api[-_]?key|apikey|token|access_token|auth|key|secret|password|pwd)=)"
    r"[^&\s\"']+",
    re.I,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I)
_HELIUS = re.compile(r"([?&]api-key=)[0-9a-f\-]{8,}", re.I)
# Solana base58 secrets are long; never let a 64+ char base58 blob reach a log.
_BASE58_SECRET = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{80,}\b")

REDACTED = "[REDACTED]"


def redact(value: Any) -> str:
    """Strip credentials from any value before it reaches a log or a report.

    Safe on exceptions, URLs, dicts and arbitrary objects.
    """
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return "[UNPRINTABLE]"

    text = _TELEGRAM_BOT_PATH.sub(r"\1" + REDACTED, text)
    text = _TELEGRAM_TOKEN.sub(REDACTED, text)
    text = _HELIUS.sub(r"\1" + REDACTED, text)
    text = _QUERY_SECRET.sub(r"\1" + REDACTED, text)
    text = _BEARER.sub(r"\1" + REDACTED, text)
    text = _BASE58_SECRET.sub(REDACTED, text)
    return text


# ── Repeated-warning suppression ──────────────────────────────────────────────
# An unavailable dependency must degrade to a periodic heartbeat, never an
# unbounded per-attempt log. Counts are preserved so nothing is hidden.

_SEEN: Dict[str, Tuple[float, int]] = {}
DEFAULT_INTERVAL_SEC = 300.0


def warn_once(logger, key: str, fmt: str, *args: Any,
              interval_sec: float = DEFAULT_INTERVAL_SEC) -> bool:
    """Emit at most one warning per key per interval; fold the rest into a count.

    Returns True if a line was emitted. All args are redacted.
    """
    now = time.time()
    last, count = _SEEN.get(key, (0.0, 0))
    safe = tuple(redact(a) for a in args)

    if now - last >= interval_sec:
        if count:
            try:
                logger.warning(fmt + " [suppressed %d identical since last report]",
                               *safe, count)
            except Exception:
                pass
        else:
            try:
                logger.warning(fmt, *safe)
            except Exception:
                pass
        _SEEN[key] = (now, 0)
        return True

    _SEEN[key] = (last, count + 1)
    return False


def suppressed_count(key: str) -> int:
    return _SEEN.get(key, (0.0, 0))[1]


def reset_suppression(key: str | None = None) -> None:
    if key is None:
        _SEEN.clear()
    else:
        _SEEN.pop(key, None)
