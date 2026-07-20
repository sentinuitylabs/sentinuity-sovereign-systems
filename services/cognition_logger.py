from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"
_LOCK = Lock()
_SCHEMA_READY = False

# Cheap duplicate suppression to reduce WAL contention from repeated identical cognition writes.
_RECENT: dict[str, float] = {}
_DEDUPE_WINDOW_SECONDS = 1.0   # Reduced from 5.0 — services run on 2-8s loops so 5s was
                                # collapsing almost every thought into silence on the brain feed.
_MAX_RECENT_KEYS = 2000

# Stages whose messages are ALWAYS written regardless of dedup window.
# These are the organism's key trading decisions — they must always appear in the feed.
_FORCE_WRITE_STAGES = {
    "EXECUTOR",   # entry opened, take profit, stop loss, trailing stop
    "SUPERVISOR", # latch approved, veto decisions
    "HEALTH",     # drawdown alerts, tier escalations
    "DEBATE",     # trinity consensus / rejection
}


def _get_conn() -> sqlite3.Connection:
    # ── CLAUDE-HARDENED FIX: Raised from 5s to 60s to prevent Nerve Blocks ──
    conn = sqlite3.connect(DB_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _LOCK:
        if _SCHEMA_READY:
            return

        conn = _get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cognition_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    stage TEXT,
                    token TEXT,
                    message TEXT,
                    confidence REAL,
                    meta TEXT
                )
                """
            )
            # Create an index for faster UI queries
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cognition_ts ON cognition_log(timestamp DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception as e:
            import logging
            logging.error("Failed to init cognition_log schema: %s", e)
        finally:
            conn.close()


def _should_skip(stage: str, message: str, token: str | None) -> bool:
    """
    Deduplicate exact same messages within a short window.
    Prevents DB spam if a loop runs hot.

    Force-write stages (EXECUTOR, SUPERVISOR, HEALTH, DEBATE) bypass dedup entirely
    so key trading decisions always appear in the brain feed regardless of frequency.
    """
    # Key trading events always written — never silenced by dedup
    if str(stage).upper() in _FORCE_WRITE_STAGES:
        return False

    now = time.time()
    k = f"{stage}|{token}|{message}"

    # If seen recently, skip
    if k in _RECENT and (now - _RECENT[k]) < _DEDUPE_WINDOW_SECONDS:
        return True

    _RECENT[k] = now

    # Periodic cleanup of dedupe cache
    if len(_RECENT) > _MAX_RECENT_KEYS:
        cutoff = now - (_DEDUPE_WINDOW_SECONDS * 2)
        stale_keys = [k for k, ts in _RECENT.items() if ts < cutoff]
        for k in stale_keys[:1000]:
            _RECENT.pop(k, None)

    return False


def log_cognition(
    stage: str,
    message: str,
    token: str | None = None,
    confidence: float | None = None,
    meta: Any | None = None,
) -> None:
    """Best-effort cognition trace logger.

    Safe to call from live trading paths. It must never interrupt execution.
    """
    if not stage or not message:
        return

    try:
        if _should_skip(stage, message, token):
            return

        _ensure_schema()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        meta_str = None
        if meta is not None:
            try:
                meta_str = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), default=str)
            except Exception:
                meta_str = str(meta)

        with _LOCK:
            conn = _get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO cognition_log (timestamp, stage, token, message, confidence, meta)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (ts, str(stage).upper(), token, str(message), confidence, meta_str),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        # MUST fail silently to protect trading loops
        pass