"""
SENTINUITY FIRE-PATH TERMINAL LEDGER — SIGNOFF_FIRE_TRUTH_ASYNC_20260724

Canonical terminal truth for every decision that reached FIRE_PATH_OPEN.

Hot-path contract
-----------------
`record_terminal()` is non-blocking. It performs no SQLite connection, schema,
lock wait, commit, or filesystem work on the executor thread. Events are placed
on a bounded in-memory queue and persisted by one daemon writer.

Storage contract
----------------
* Database: sentinuity_intelligence.db (never sentinuity_matrix.db)
* One persistent writer connection
* WAL mode
* fail-fast SQLite timeout (100 ms)
* bounded retry in the background only
* bounded queue with explicit drop counters
* runtime schema creation is supported only as a defensive fallback; the
  signed launch procedure pre-creates the schema with:

      python -m services.fire_path_ledger --init-schema

The module changes no gate, sizing, submission, wallet, or execution behaviour.
"""

from __future__ import annotations

import argparse
import atexit
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TERMINAL_OUTCOMES = (
    "SUBMITTED",
    "SUBMISSION_FAILED",
    "CANCELLED_WITH_REASON",
    "EXPIRED_BEFORE_SUBMISSION",
    "BLOCKED_POST_VERDICT",
)

_QUEUE_MAX = max(64, int(os.getenv("FIRE_PATH_LEDGER_QUEUE_MAX", "2048")))
_RETRY_MAX = max(0, int(os.getenv("FIRE_PATH_LEDGER_RETRY_MAX", "4")))
_SQLITE_TIMEOUT_S = max(0.01, float(os.getenv("FIRE_PATH_LEDGER_SQLITE_TIMEOUT_S", "0.10")))
_BUSY_TIMEOUT_MS = max(1, int(os.getenv("FIRE_PATH_LEDGER_BUSY_TIMEOUT_MS", "100")))

_QUEUE: "queue.Queue[_Event]" = queue.Queue(maxsize=_QUEUE_MAX)
_START_LOCK = threading.Lock()
_WRITER: Optional[threading.Thread] = None
_STOP = threading.Event()

_HEALTH_LOCK = threading.Lock()
_HEALTH = {
    "enqueued": 0,
    "persisted": 0,
    "retried": 0,
    "dropped_queue_full": 0,
    "dropped_after_retries": 0,
    "last_error": None,
    "last_persist_ts": None,
}


@dataclass(frozen=True)
class _Event:
    event_ts: float
    position_id: Optional[int]
    mint: Optional[str]
    outcome: str
    stage: Optional[str]
    reason: Optional[str]
    tx_sig: Optional[str]
    would_fire_usd: Optional[float]
    authored_by: str
    attempt: int = 0


def _db_path() -> str:
    override = os.getenv("FIRE_PATH_LEDGER_DB", "").strip()
    if override:
        return override
    return str(Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=_SQLITE_TIMEOUT_S)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_fire_ledger (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_ts       REAL NOT NULL,
            position_id    INTEGER,
            mint           TEXT,
            outcome        TEXT NOT NULL,
            stage          TEXT,
            reason         TEXT,
            tx_sig         TEXT,
            would_fire_usd REAL,
            authored_by    TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_live_fire_pos "
        "ON live_fire_ledger(position_id, event_ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_live_fire_outcome_ts "
        "ON live_fire_ledger(outcome, event_ts)"
    )
    conn.commit()


def init_schema() -> None:
    """Blocking prelaunch migration entry point. Never called by executor."""
    conn = _connect()
    try:
        _create_schema(conn)
    finally:
        conn.close()


def _health_inc(key: str, amount: int = 1) -> None:
    with _HEALTH_LOCK:
        _HEALTH[key] = int(_HEALTH.get(key, 0) or 0) + amount


def _health_set(key: str, value) -> None:
    with _HEALTH_LOCK:
        _HEALTH[key] = value


def health_snapshot() -> dict:
    with _HEALTH_LOCK:
        snap = dict(_HEALTH)
    snap.update({
        "queue_depth": _QUEUE.qsize(),
        "queue_max": _QUEUE_MAX,
        "writer_alive": bool(_WRITER and _WRITER.is_alive()),
        "db_path": _db_path(),
    })
    return snap


def _ensure_writer_started() -> None:
    global _WRITER
    if _WRITER and _WRITER.is_alive():
        return
    with _START_LOCK:
        if _WRITER and _WRITER.is_alive():
            return
        _STOP.clear()
        _WRITER = threading.Thread(
            target=_writer_loop,
            name="fire-path-ledger-writer",
            daemon=True,
        )
        _WRITER.start()


def _writer_loop() -> None:
    conn: Optional[sqlite3.Connection] = None
    schema_ready = False
    while not _STOP.is_set() or not _QUEUE.empty():
        try:
            event = _QUEUE.get(timeout=0.20)
        except queue.Empty:
            continue

        try:
            if conn is None:
                conn = _connect()
            if not schema_ready:
                _create_schema(conn)
                schema_ready = True

            conn.execute(
                "INSERT INTO live_fire_ledger "
                "(event_ts, position_id, mint, outcome, stage, reason, "
                " tx_sig, would_fire_usd, authored_by) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    event.event_ts,
                    event.position_id,
                    event.mint,
                    event.outcome,
                    event.stage,
                    event.reason,
                    event.tx_sig,
                    event.would_fire_usd,
                    event.authored_by,
                ),
            )
            conn.commit()
            _health_inc("persisted")
            _health_set("last_persist_ts", time.time())
            _health_set("last_error", None)
        except Exception as exc:
            _health_set("last_error", f"{type(exc).__name__}: {exc}")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
            schema_ready = False

            if event.attempt < _RETRY_MAX and not _STOP.is_set():
                retry_event = _Event(**{**event.__dict__, "attempt": event.attempt + 1})
                try:
                    _QUEUE.put_nowait(retry_event)
                    _health_inc("retried")
                    time.sleep(min(0.05 * (2 ** event.attempt), 0.50))
                except queue.Full:
                    _health_inc("dropped_queue_full")
            else:
                _health_inc("dropped_after_retries")
        finally:
            _QUEUE.task_done()

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def record_terminal(
    outcome: str,
    position_id: Optional[int] = None,
    mint: Optional[str] = None,
    stage: Optional[str] = None,
    reason: Optional[str] = None,
    tx_sig: Optional[str] = None,
    would_fire_usd: Optional[float] = None,
    authored_by: str = "execution_engine.live_mirror",
) -> bool:
    """Queue a terminal event without blocking the executor hot path."""
    try:
        normalized = str(outcome).strip().upper()
        if normalized not in TERMINAL_OUTCOMES:
            normalized = "BLOCKED_POST_VERDICT"
        event = _Event(
            event_ts=time.time(),
            position_id=position_id,
            mint=(str(mint)[:96] if mint else None),
            outcome=normalized,
            stage=(str(stage)[:80] if stage else None),
            reason=(str(reason)[:400] if reason else None),
            tx_sig=(str(tx_sig)[:128] if tx_sig else None),
            would_fire_usd=would_fire_usd,
            authored_by=str(authored_by)[:120],
        )
        _ensure_writer_started()
        _QUEUE.put_nowait(event)
        _health_inc("enqueued")
        return True
    except queue.Full:
        _health_inc("dropped_queue_full")
        return False
    except Exception as exc:
        _health_set("last_error", f"{type(exc).__name__}: {exc}")
        return False


def flush(timeout: float = 5.0) -> bool:
    """Testing/shutdown helper. Not used by funded execution."""
    deadline = time.monotonic() + max(0.0, timeout)
    while _QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return _QUEUE.unfinished_tasks == 0


def shutdown(timeout: float = 1.0) -> None:
    _STOP.set()
    flush(timeout)


atexit.register(shutdown)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-schema", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.init_schema:
        init_schema()
        print(f"FIRE_PATH_LEDGER_SCHEMA_OK {_db_path()}")
    if args.health:
        print(health_snapshot())
    if not args.init_schema and not args.health:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
