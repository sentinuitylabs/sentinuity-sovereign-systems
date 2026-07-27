"""
SENTINUITY STAGE TELEMETRY — SIGNOFF_FLOW_LATENCY_20260724
===========================================================

Additive, low-overhead stage timing with ONE canonical lineage key.

Contract (from the audit directive, section 7):
  - one canonical trace key per candidate row lineage;
  - structured rows, not regex-parsed text logs;
  - additive only: no table in the matrix DB is altered, no execution,
    qualification, latch, sizing, exit or accounting behaviour is touched;
  - a telemetry failure must NEVER propagate into the hot path.

Design decisions:
  - trace_key = "<mint>:<snapshot_id or 0>". This resolves the duplicate-row
    lineage problem directly: every snapshot row gets its own row-level trace,
    while mint-level timing is a GROUP BY on the mint column at read time.
  - Storage: sentinuity_intelligence.db (same file the oracle already writes
    mtm_ticks into — high write volume is already proven safe there, and the
    matrix DB stays free of telemetry contention).
  - Idempotent stages: UNIQUE(trace_key, stage) + INSERT OR IGNORE means
    FIRST_PRICE / ORACLE_ADMITTED etc. can be emitted opportunistically from
    several call sites without duplicate rows and without callers having to
    coordinate.
  - DISCOVERED backfill: callers that know the row's first_seen_at pass
    discovered_ts=...; the module lazily backfills the DISCOVERED row so the
    ingest pipeline does not need to be modified in this pack.
  - elapsed_from_previous_ms / elapsed_from_discovery_ms are computed at
    insert from the trace's existing rows (two indexed point reads).

Required stage names (directive section 7):
  DISCOVERED SNAPSHOT_PERSISTED ORACLE_ADMITTED FIRST_PRICE QUALIFY_CLAIMED
  QUALIFY_COMPLETE LATCHED PAPER_SCAN_SEEN PAPER_OPEN LIVE_DECISION
  LIVE_PREFLIGHT_START LIVE_PREFLIGHT_COMPLETE QUOTE_START QUOTE_COMPLETE
  TX_SUBMITTED SIGNATURE_PERSISTED CHAIN_CONFIRMED FILL_RECONCILED
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

STAGES = (
    "DISCOVERED",
    "SNAPSHOT_PERSISTED",
    "ORACLE_ADMITTED",
    "FIRST_PRICE",
    "QUALIFY_CLAIMED",
    "QUALIFY_COMPLETE",
    "LATCHED",
    "PAPER_SCAN_SEEN",
    "PAPER_OPEN",
    "LIVE_DECISION",
    "LIVE_PREFLIGHT_START",
    "LIVE_PREFLIGHT_COMPLETE",
    "QUOTE_START",
    "QUOTE_COMPLETE",
    "TX_SUBMITTED",
    "SIGNATURE_PERSISTED",
    "CHAIN_CONFIRMED",
    "FILL_RECONCILED",
)

# Stages that may legitimately repeat (a re-claim after a released claim, a
# re-quote). Everything else is first-occurrence-only via INSERT OR IGNORE.
_REPEATABLE = {"QUALIFY_CLAIMED", "QUOTE_START", "QUOTE_COMPLETE"}

_DB_LOCK = threading.Lock()
_SCHEMA_READY = False


def _db_path() -> str:
    override = os.getenv("STAGE_TELEMETRY_DB", "").strip()
    if override:
        return override
    return str(Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=2.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=1500")
    return conn


def ensure_schema() -> None:
    """Idempotent. Additive table + indexes only. Never raises."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS stage_telemetry (
                        id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                        trace_key                  TEXT NOT NULL,
                        mint                       TEXT NOT NULL,
                        snapshot_id                INTEGER,
                        position_id                INTEGER,
                        stage                      TEXT NOT NULL,
                        event_ts                   REAL NOT NULL,
                        elapsed_from_previous_ms   REAL,
                        elapsed_from_discovery_ms  REAL,
                        provider                   TEXT,
                        source                     TEXT,
                        success                    INTEGER DEFAULT 1,
                        failure_reason             TEXT
                    )
                    """
                )
                # Earlier draft used one UNIQUE(trace_key, stage) index, which
                # accidentally prevented the documented repeatable stages from
                # repeating. Migrate safely to a partial unique index that only
                # deduplicates first-occurrence lifecycle stages.
                conn.execute("DROP INDEX IF EXISTS ux_stage_telemetry_trace_stage")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_stage_telemetry_singleton "
                    "ON stage_telemetry(trace_key, stage) "
                    "WHERE stage NOT IN ('QUALIFY_CLAIMED','QUOTE_START','QUOTE_COMPLETE')"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_stage_telemetry_mint "
                    "ON stage_telemetry(mint, event_ts)"
                )
                conn.commit()
            finally:
                conn.close()
        _SCHEMA_READY = True
    except Exception:
        # Telemetry must never take the pipeline down.
        pass


def make_trace_key(mint: str, snapshot_id: Optional[int]) -> str:
    return f"{(mint or '').strip()}:{int(snapshot_id) if snapshot_id else 0}"


def record_stage(
    stage: str,
    mint: str,
    snapshot_id: Optional[int] = None,
    position_id: Optional[int] = None,
    provider: Optional[str] = None,
    source: Optional[str] = None,
    success: bool = True,
    failure_reason: Optional[str] = None,
    event_ts: Optional[float] = None,
    discovered_ts: Optional[float] = None,
) -> None:
    """Record one lifecycle stage. Best-effort; swallows every exception.

    If ``discovered_ts`` is provided and the trace has no DISCOVERED row yet,
    a DISCOVERED row is backfilled first so elapsed_from_discovery_ms is
    meaningful without modifying the ingest pipeline.
    """
    try:
        stage = str(stage).strip().upper()
        mint = str(mint or "").strip()
        if not mint or stage not in STAGES:
            return
        ensure_schema()
        now = float(event_ts) if event_ts else time.time()
        trace_key = make_trace_key(mint, snapshot_id)

        with _DB_LOCK:
            conn = _connect()
            try:
                if discovered_ts and stage != "DISCOVERED":
                    conn.execute(
                        "INSERT OR IGNORE INTO stage_telemetry "
                        "(trace_key, mint, snapshot_id, position_id, stage, event_ts, "
                        " elapsed_from_previous_ms, elapsed_from_discovery_ms, provider, "
                        " source, success, failure_reason) "
                        "VALUES (?,?,?,?, 'DISCOVERED', ?, NULL, 0.0, NULL, 'backfill', 1, NULL)",
                        (trace_key, mint, snapshot_id, position_id, float(discovered_ts)),
                    )

                row = conn.execute(
                    "SELECT MAX(event_ts), "
                    "       MAX(CASE WHEN stage='DISCOVERED' THEN event_ts END) "
                    "FROM stage_telemetry WHERE trace_key=?",
                    (trace_key,),
                ).fetchone()
                prev_ts = float(row[0]) if row and row[0] else None
                disc_ts = float(row[1]) if row and row[1] else (
                    float(discovered_ts) if discovered_ts else None
                )
                elapsed_prev = (now - prev_ts) * 1000.0 if prev_ts else None
                elapsed_disc = (now - disc_ts) * 1000.0 if disc_ts else None

                verb = "INSERT" if stage in _REPEATABLE else "INSERT OR IGNORE"
                conn.execute(
                    f"{verb} INTO stage_telemetry "
                    "(trace_key, mint, snapshot_id, position_id, stage, event_ts, "
                    " elapsed_from_previous_ms, elapsed_from_discovery_ms, provider, "
                    " source, success, failure_reason) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (trace_key, mint, snapshot_id, position_id, stage, now,
                     elapsed_prev, elapsed_disc, provider, source,
                     1 if success else 0, failure_reason),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def trace_summary(mint: str, snapshot_id: Optional[int] = None) -> list:
    """Read helper for audits/UI. Returns ordered stage rows for one trace."""
    try:
        ensure_schema()
        conn = _connect()
        try:
            cur = conn.execute(
                "SELECT stage, event_ts, elapsed_from_previous_ms, "
                "       elapsed_from_discovery_ms, provider, source, success, failure_reason "
                "FROM stage_telemetry WHERE trace_key=? ORDER BY event_ts ASC",
                (make_trace_key(mint, snapshot_id),),
            )
            return [tuple(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
