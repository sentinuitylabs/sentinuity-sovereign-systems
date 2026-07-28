"""
services/standing_task_scheduler.py
===================================
SIGNOFF_STANDING_TASK_SCHEDULER_20260720

Fair, starvation-proof standing-task selection for the Council.

AUDIT FINDING THAT MOTIVATED THIS FILE
--------------------------------------
council_execution_spine.select_task() sorts OPEN tasks by
(family_rank, priority, id) and always returns the top row. Consequences:

  * A high-family task that keeps re-opening (or never records progress)
    monopolises every cycle. Nothing rotates.
  * "last worked" / "last meaningful progress" are not tracked anywhere.
  * There is no starvation report: a task can sit unworked for days with
    no visible explanation.

This module provides a DROP-IN replacement selector plus a sidecar schedule
table. It does not ALTER the spine's polaris_standing_tasks schema — all
fairness state lives in standing_task_schedule, so the signed-off baseline
is untouched.

Wiring (one line, operator-applied — this module never patches code itself):
    # in services/council_execution_spine.py
    from services.standing_task_scheduler import select_task_fair as select_task

Fairness policy
---------------
  1. Family rank and priority are still honoured (the doctrine hierarchy
     stays intact).
  2. Anti-monopoly: a task selected MONOPOLY_LIMIT consecutive times without
     recording meaningful progress is PARKED for PARK_SECONDS and the next
     eligible task runs instead. A blocked task therefore cannot freeze
     all other work.
  3. Starvation boost: any task not worked for STARVATION_SECONDS jumps to
     the front of its family, oldest-first.
  4. Progress is measured by artefacts and state transitions — callers report
     it via record_progress(); message volume counts for nothing.

CLI
---
    python -m services.standing_task_scheduler --report
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

DB_FALLBACK = BASE_DIR / "sentinuity_matrix.db"

MONOPOLY_LIMIT = 3          # consecutive no-progress selections before parking
PARK_SECONDS = 1800         # 30 min park for a monopolising / blocked task
STARVATION_SECONDS = 6 * 3600   # 6h without work => starvation boost
PROGRESS_STALE_SECONDS = 24 * 3600  # 24h without artefact progress => report

DDL = """
CREATE TABLE IF NOT EXISTS standing_task_schedule (
    task_id                 INTEGER PRIMARY KEY,   -- polaris_standing_tasks.id
    task_title              TEXT,
    last_selected_at        REAL,
    last_progress_at        REAL,                  -- last ARTEFACT progress
    last_progress_note      TEXT,
    consecutive_selections  INTEGER NOT NULL DEFAULT 0,
    parked_until            REAL,
    park_reason             TEXT,
    total_selections        INTEGER NOT NULL DEFAULT 0,
    total_progress_events   INTEGER NOT NULL DEFAULT 0,
    updated_at              REAL
);
"""

# Every selection decision is persisted (directive: "Persist the reason for
# every selection decision … when a task is parked, skipped, blocked, starved,
# or resumed").
JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS standing_task_selection_journal (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    task_id    INTEGER,
    task_title TEXT,
    decision   TEXT NOT NULL,   -- SELECTED | PARKED | SKIPPED_PARKED |
                                -- STARVATION_BOOST | OPERATOR_PRIORITY |
                                -- BLOCK_NOTED | RESUMED | PROGRESS |
                                -- FALLBACK_DEGRADED | AUTO_UNPARK
    reason     TEXT
);
"""
JOURNAL_IDX = ("CREATE INDEX IF NOT EXISTS stsj_ts ON "
               "standing_task_selection_journal(ts);")


def _journal(conn: sqlite3.Connection, task_id: Optional[int], task_title: str,
             decision: str, reason: str) -> None:
    try:
        conn.execute(
            "INSERT INTO standing_task_selection_journal "
            "(ts, task_id, task_title, decision, reason) VALUES (?,?,?,?,?)",
            (time.time(), task_id, str(task_title or "")[:200],
             decision[:40], str(reason or "")[:400]))
    except sqlite3.Error:
        pass


def _connect(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    if db_path is None:
        try:
            from core.schema import get_connection  # type: ignore
            return get_connection()
        except Exception:
            db_path = DB_FALLBACK
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    conn.execute(JOURNAL_DDL)
    conn.execute(JOURNAL_IDX)
    conn.commit()


# Mirror of council_execution_spine's family ranking so doctrine order holds.
def _family_rank(title: str, domain: str, risk: str) -> int:
    t = (title or "").lower()
    if any(k in t for k in ("launch", "blocker", "critical")):
        return 1
    if any(k in t for k in ("oracle", "executor", "latch", "price", "freshness")):
        return 2
    if any(k in t for k in ("council", "self-repair", "build pipeline", "spine")):
        return 3
    if any(k in t for k in ("copytrade", "substrate", "wallet")):
        return 4
    if any(k in t for k in ("ui", "world", "panel", "hub")):
        return 5
    return 6


def _sched_row(conn: sqlite3.Connection, task_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM standing_task_schedule WHERE task_id=?", (task_id,)
    ).fetchone()


def select_task_fair(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Drop-in replacement for council_execution_spine.select_task().

    Reads polaris_standing_tasks (spine schema), applies fairness policy,
    records the selection in standing_task_schedule, and returns the chosen
    task row (same shape the spine expects) or None.
    """
    ensure_schema(conn)
    try:
        rows = conn.execute(
            "SELECT * FROM polaris_standing_tasks "
            "WHERE status='OPEN' AND COALESCE(blocked_reason,'')=''"
        ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    now = time.time()

    def keys(r: sqlite3.Row):
        cols = r.keys()
        title = r["title"] if "title" in cols else ""
        domain = r["domain"] if "domain" in cols else ""
        risk = r["risk_level"] if "risk_level" in cols else ""
        prio = (r["priority"] if "priority" in cols else 5) or 5
        return title, domain, risk, prio

    task_cols = rows[0].keys() if rows else []

    def _operator_priority(r: sqlite3.Row) -> bool:
        """Operator priority outranks fairness rotation where explicitly set:
        either an explicit operator_priority column flag, or priority<=1
        (the operator-reserved band, e.g. the recurring Solana edge audit)."""
        if "operator_priority" in task_cols:
            try:
                if int(r["operator_priority"] or 0) == 1:
                    return True
            except (TypeError, ValueError):
                pass
        try:
            return int(r["priority"] or 5) <= 1
        except (TypeError, ValueError):
            return False

    eligible: list[tuple[tuple, sqlite3.Row, list[str]]] = []
    for r in rows:
        title, domain, risk, prio = keys(r)
        tid = int(r["id"])
        s = _sched_row(conn, tid)
        operator = _operator_priority(r)
        parked_until = float(s["parked_until"] or 0) if s else 0.0
        if parked_until > now and not operator:
            _journal(conn, tid, title, "SKIPPED_PARKED",
                     f"parked until {time.strftime('%H:%M:%S', time.localtime(parked_until))}"
                     f": {str(s['park_reason'] or '')[:200]}")
            continue  # parked: another task gets the cycle
        last_worked = float(s["last_selected_at"] or 0) if s else 0.0
        starving = (now - last_worked) > STARVATION_SECONDS if last_worked else True
        notes: list[str] = []
        if operator:
            notes.append("OPERATOR_PRIORITY")
        if starving:
            notes.append("STARVATION_BOOST" if last_worked else "NEVER_WORKED")
        if parked_until > now and operator:
            notes.append("operator priority overrides park")
        # sort key: operator override first, then family, starvation boost,
        # priority, least-recently-worked, id
        eligible.append((
            (0 if operator else 1,
             _family_rank(title, domain, risk), 0 if starving else 1,
             prio, last_worked, tid),
            r, notes,
        ))
    if not eligible:
        # everything parked — unpark the least-recently-parked so work resumes
        conn.execute(
            "UPDATE standing_task_schedule SET parked_until=NULL, "
            "park_reason=COALESCE(park_reason,'') || ' (auto-unparked: all tasks parked)' "
            "WHERE parked_until IS NOT NULL")
        _journal(conn, None, "", "AUTO_UNPARK",
                 "all eligible tasks were parked; unparked to resume work")
        conn.commit()
        return select_task_fair(conn) if rows else None

    eligible.sort(key=lambda t: t[0])
    chosen, chosen_notes = eligible[0][1], eligible[0][2]
    tid = int(chosen["id"])
    title = chosen["title"] if "title" in chosen.keys() else ""

    s = _sched_row(conn, tid)
    consec = (int(s["consecutive_selections"] or 0) + 1) if s else 1
    progressed_since = bool(
        s and s["last_progress_at"] and s["last_selected_at"]
        and float(s["last_progress_at"]) >= float(s["last_selected_at"])
    )
    if progressed_since:
        # genuine recent progress: never unfairly parked; counter resets
        consec = 1
        _journal(conn, tid, title, "RESUMED",
                 "artefact progress since last selection — rotation counter reset")
    parked_until, park_reason = None, None
    operator_chosen = "OPERATOR_PRIORITY" in chosen_notes
    if consec > MONOPOLY_LIMIT and not operator_chosen:
        parked_until = now + PARK_SECONDS
        park_reason = (f"parked after {consec - 1} consecutive selections "
                       f"without artefact progress")
        consec = 0
    conn.execute(
        "INSERT INTO standing_task_schedule (task_id, task_title, "
        " last_selected_at, consecutive_selections, parked_until, park_reason, "
        " total_selections, updated_at) VALUES (?,?,?,?,?,?,1,?) "
        "ON CONFLICT(task_id) DO UPDATE SET "
        " task_title=excluded.task_title, last_selected_at=excluded.last_selected_at,"
        " consecutive_selections=excluded.consecutive_selections,"
        " parked_until=excluded.parked_until, park_reason=excluded.park_reason,"
        " total_selections=standing_task_schedule.total_selections+1,"
        " updated_at=excluded.updated_at",
        (tid, str(title), now, consec, parked_until, park_reason, now),
    )
    if parked_until:
        _journal(conn, tid, title, "PARKED", park_reason or "")
        conn.commit()
        # this task just got parked: pick again without it
        return select_task_fair(conn)
    _journal(conn, tid, title, "SELECTED",
             ("; ".join(chosen_notes) or "family/priority order")
             + f"; consecutive={consec}")
    conn.commit()
    return chosen


def record_progress(
    conn: sqlite3.Connection, task_id: int, note: str = ""
) -> None:
    """Report ARTEFACT progress (evidence note, patch, state transition —
    not message volume). Resets the monopoly counter."""
    ensure_schema(conn)
    now = time.time()
    conn.execute(
        "INSERT INTO standing_task_schedule (task_id, last_progress_at, "
        " last_progress_note, consecutive_selections, total_progress_events, "
        " updated_at) VALUES (?,?,?,0,1,?) "
        "ON CONFLICT(task_id) DO UPDATE SET "
        " last_progress_at=excluded.last_progress_at,"
        " last_progress_note=excluded.last_progress_note,"
        " consecutive_selections=0,"
        " total_progress_events=standing_task_schedule.total_progress_events+1,"
        " updated_at=excluded.updated_at",
        (int(task_id), now, note[:400], now),
    )
    _journal(conn, int(task_id), "", "PROGRESS", note[:300])
    conn.commit()


def note_block(conn: sqlite3.Connection, task_id: int, reason: str) -> None:
    """Persist that a task entered a blocked state (directive: persist when a
    task is parked, skipped, blocked, starved, or resumed)."""
    ensure_schema(conn)
    _journal(conn, int(task_id), "", "BLOCK_NOTED", str(reason or "")[:300])
    conn.commit()


def note_fallback_degraded(conn: sqlite3.Connection, error: str) -> None:
    """Persist that fair selection failed and the original selector ran."""
    ensure_schema(conn)
    _journal(conn, None, "", "FALLBACK_DEGRADED",
             f"fair scheduler unavailable; original selector used: "
             f"{str(error or '')[:250]}")
    conn.commit()


# ── starvation report (directive: "Do not silently show searching") ──────────
_CAUSE_CHECKS = (
    # (cause label, heartbeat service that must be fresh for the cause NOT to apply)
    ("DATA_SOURCE_FAILURE: oracle stale", "ws_price_oracle"),
    ("DATA_SOURCE_FAILURE: ingest stale", "ingest_pipeline"),
    ("BUILD_SYSTEM_FAILURE: council spine stale", "council_build_orchestrator"),
)


def starvation_report(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Explain WHY tasks are not progressing, per the directive taxonomy:
    no valid opportunities / data-source failure / excessively strict debate /
    missing integration / task-cycle starvation / build-system failure."""
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        now = time.time()
        out: dict[str, Any] = {"generated_at": now, "starved": [], "healthy": 0}
        # heartbeat freshness for cause attribution
        hb: dict[str, float] = {}
        try:
            for svc, pulse in conn.execute(
                "SELECT service_name, COALESCE(last_pulse,0) FROM system_heartbeat"
            ).fetchall():
                hb[str(svc)] = float(pulse or 0)
        except sqlite3.Error:
            pass
        try:
            tasks = conn.execute(
                "SELECT id, title, status, COALESCE(blocked_reason,'') br "
                "FROM polaris_standing_tasks").fetchall()
        except sqlite3.Error:
            return {"error": "polaris_standing_tasks unavailable"}
        for t in tasks:
            s = _sched_row(conn, int(t["id"]))
            last_prog = float(s["last_progress_at"] or 0) if s else 0.0
            last_sel = float(s["last_selected_at"] or 0) if s else 0.0
            if last_prog and (now - last_prog) < PROGRESS_STALE_SECONDS:
                out["healthy"] += 1
                continue
            causes: list[str] = []
            if t["br"]:
                causes.append(f"BLOCKED: {t['br']}")
            if not last_sel:
                causes.append("TASK_CYCLE_STARVATION: never selected by scheduler")
            elif (now - last_sel) > STARVATION_SECONDS:
                causes.append(
                    f"TASK_CYCLE_STARVATION: last selected {int((now-last_sel)/3600)}h ago")
            for label, svc in _CAUSE_CHECKS:
                pulse = hb.get(svc, 0.0)
                if pulse and (now - pulse) > 600:
                    causes.append(label)
            if s and int(s["consecutive_selections"] or 0) >= MONOPOLY_LIMIT:
                causes.append("EXCESSIVELY_STRICT_DEBATE_OR_NO_EXIT: selected "
                              "repeatedly without artefact progress")
            if not causes:
                causes.append("NO_VALID_OPPORTUNITIES_OR_MISSING_INTEGRATION: "
                              "selected and unblocked but produced no artefact")
            out["starved"].append({
                "task_id": int(t["id"]),
                "title": str(t["title"]),
                "status": str(t["status"]),
                "last_progress_h": round((now - last_prog) / 3600, 1) if last_prog else None,
                "causes": causes,
            })
        return out
    finally:
        if own:
            conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Standing-task fair scheduler")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    print(json.dumps(starvation_report(db_path=args.db), indent=2, default=str))


if __name__ == "__main__":
    main()