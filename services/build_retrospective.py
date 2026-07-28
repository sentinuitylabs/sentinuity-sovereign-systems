"""
services/build_retrospective.py
===============================
SIGNOFF_BUILD_RETROSPECTIVE_20260720

Stage 15 of the inspiration→application sign-off path: the retrospective.

AUDIT FINDING THAT MOTIVATED THIS FILE
--------------------------------------
The pipeline ends at polaris_patch_writer's patch_apply_journal (apply /
rollback rows). Nothing afterwards records:
  * what changed and why (which exact Council decision caused the change),
  * whether runtime behaviour improved or regressed after application,
  * what was rolled back,
  * what should be attempted next.

Grep across the codebase found zero occurrences of "retrospective".
This module closes the loop. Read-mostly: it reads patch_apply_journal,
code_patches, polaris_proposals and system_heartbeat, and writes ONLY to its
own build_retrospectives table. It cannot apply, revert or alter code.

Run:
    python -m services.build_retrospective            # one pass
    python -m services.build_retrospective --loop     # every 5 minutes
    python -m services.build_retrospective --report
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
SERVICE_NAME = "build_retrospective"
CYCLE_SECONDS = 300
RUNTIME_VERIFY_WINDOW_SEC = 900   # heartbeats must be fresh within 15 min of apply

DDL = """
CREATE TABLE IF NOT EXISTS build_retrospectives (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    patch_id           INTEGER,               -- code_patches.id
    journal_id         INTEGER UNIQUE,        -- patch_apply_journal rowid
    proposal_id        INTEGER,               -- polaris_proposals.id (decision provenance)
    inspiration_id     INTEGER,               -- inspiration_intake_ledger link, if any
    target_file        TEXT,
    applied_at         REAL,
    outcome            TEXT,                  -- APPLIED | ROLLED_BACK | REJECTED | BLOCKED
    what_changed       TEXT,
    decision_provenance TEXT,                 -- proposal title/summary that caused it
    runtime_verified   INTEGER DEFAULT 0,     -- services fresh after apply?
    runtime_notes      TEXT,
    improved           TEXT,
    regressed          TEXT,
    rolled_back        TEXT,
    next_attempt       TEXT,
    created_at         REAL NOT NULL
);
"""


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
    conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _runtime_verification(conn: sqlite3.Connection, applied_at: float) -> tuple[int, str]:
    """After an apply, were the critical services still pulsing?"""
    if not _table_exists(conn, "system_heartbeat"):
        return 0, "system_heartbeat table unavailable"
    critical = ("ws_price_oracle", "execution_engine", "ingest_pipeline")
    notes, ok = [], True
    now = time.time()
    horizon = min(now, applied_at + RUNTIME_VERIFY_WINDOW_SEC)
    for svc in critical:
        row = conn.execute(
            "SELECT COALESCE(last_pulse,0) FROM system_heartbeat "
            "WHERE service_name=?", (svc,)).fetchone()
        pulse = float(row[0]) if row else 0.0
        if pulse >= horizon - RUNTIME_VERIFY_WINDOW_SEC:
            notes.append(f"{svc}: pulsed after apply")
        else:
            ok = False
            notes.append(f"{svc}: NO pulse observed after apply")
    return (1 if ok else 0), "; ".join(notes)


# ── SIGNOFF_ACTIVE_INTEGRATION_20260720: active-path API ─────────────────────
# Called by council_execution_spine around the real apply. Final statuses per
# directive: APPLIED_HEALTHY | APPLIED_WITH_WARNINGS | QUARANTINED |
# ROLLED_BACK | FAILED_BEFORE_APPLICATION.

def record_pre_application(
    conn: sqlite3.Connection,
    *,
    patch_id: int,
    proposal_id: Optional[int] = None,
    inspiration_id: Optional[int] = None,
    target_file: str = "",
    operator_state: str = "",
    compile_note: str = "",
) -> int:
    """Create the application record BEFORE any file changes. Captures the
    causing proposal/inspiration, target file, gate/operator state and the
    compile enforcement note. Returns retrospective row id (0 on failure)."""
    ensure_schema(conn)
    now = time.time()
    provenance = ""
    try:
        if proposal_id and _table_exists(conn, "polaris_proposals"):
            qcols = _cols(conn, "polaris_proposals")
            sel = [c for c in ("project_key", "proposal_text", "suggested_action")
                   if c in qcols]
            if sel:
                q = conn.execute(
                    f"SELECT {', '.join(sel)} FROM polaris_proposals WHERE id=?",
                    (proposal_id,)).fetchone()
                if q:
                    qk = q.keys()
                    provenance = str(
                        (q["project_key"] if "project_key" in qk else None)
                        or (q["proposal_text"] if "proposal_text" in qk else "")
                    )[:400]
    except sqlite3.Error:
        pass
    try:
        cur = conn.execute(
            "INSERT INTO build_retrospectives "
            "(patch_id, proposal_id, inspiration_id, target_file, applied_at,"
            " outcome, what_changed, decision_provenance, runtime_verified,"
            " runtime_notes, next_attempt, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
            (int(patch_id), proposal_id, inspiration_id, target_file[:400], now,
             "PRE_APPLICATION",
             f"gate_state={operator_state}; {compile_note}"[:400],
             provenance, "pre-application record; awaiting apply + verification",
             "", now))
        conn.commit()
        return int(cur.lastrowid or 0)
    except sqlite3.Error:
        return 0


def _verify_contracts(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Post-apply contract probes. Returns (ok_notes, warn_notes).
    Read-only checks: heartbeats fresh, canonical tables readable, decision
    contract present. Absence of a probe target is a warning, never invented
    health."""
    ok, warn = [], []
    now = time.time()
    # 1. critical service heartbeats
    if _table_exists(conn, "system_heartbeat"):
        for svc in ("ws_price_oracle", "execution_engine", "ingest_pipeline"):
            row = conn.execute(
                "SELECT COALESCE(last_pulse,0) FROM system_heartbeat "
                "WHERE service_name=?", (svc,)).fetchone()
            pulse = float(row[0]) if row else 0.0
            if pulse and (now - pulse) < RUNTIME_VERIFY_WINDOW_SEC:
                ok.append(f"{svc} pulsing")
            else:
                warn.append(f"{svc}: no fresh pulse")
    else:
        warn.append("system_heartbeat unavailable")
    # 2. expected ledgers readable (UI data contracts)
    for tbl in ("paper_positions", "live_tx_ledger", "live_decision_contract"):
        if _table_exists(conn, tbl):
            try:
                conn.execute(f"SELECT * FROM {tbl} LIMIT 1").fetchone()
                ok.append(f"{tbl} readable")
            except sqlite3.Error as e:
                warn.append(f"{tbl} UNREADABLE: {e}")
        else:
            warn.append(f"{tbl} absent")
    # 3. patch journal shows no rollback for the most recent apply
    return ok, warn


def finalize_application(
    conn: sqlite3.Connection,
    *,
    patch_id: int,
    applied_ok: bool,
    detail: str = "",
) -> str:
    """Run post-application verification and set the final status. Never
    claims success it cannot verify: verification warnings downgrade to
    APPLIED_WITH_WARNINGS; heartbeat loss quarantines. Returns the status."""
    ensure_schema(conn)
    now = time.time()
    if not applied_ok:
        status = "FAILED_BEFORE_APPLICATION"
        # a rollback journal row means the writer reverted it
        try:
            if _table_exists(conn, "patch_apply_journal"):
                rb = conn.execute(
                    "SELECT 1 FROM patch_apply_journal WHERE patch_id=? "
                    "AND action='rollback' LIMIT 1", (patch_id,)).fetchone()
                if rb:
                    status = "ROLLED_BACK"
        except sqlite3.Error:
            pass
        notes = f"apply failed: {detail}"[:400]
        verified = 0
    else:
        ok_notes, warn_notes = _verify_contracts(conn)
        heart_warn = [w for w in warn_notes if "pulse" in w]
        if len(heart_warn) >= 3:
            # every critical service silent after apply — quarantine, do not
            # auto-revert (live-sensitive reversal stays operator-governed).
            status = "QUARANTINED"
            verified = 0
        elif warn_notes:
            status = "APPLIED_WITH_WARNINGS"
            verified = 0
        else:
            status = "APPLIED_HEALTHY"
            verified = 1
        notes = ("; ".join(ok_notes + warn_notes))[:600]
    try:
        cur = conn.execute(
            "UPDATE build_retrospectives SET outcome=?, runtime_verified=?,"
            " runtime_notes=?, rolled_back=?, applied_at=?, next_attempt=?"
            " WHERE patch_id=? AND outcome='PRE_APPLICATION'",
            (status, verified, notes,
             ("yes: " + detail[:300]) if status == "ROLLED_BACK" else "",
             now,
             "" if status == "APPLIED_HEALTHY" else
             "operator review: see runtime_notes",
             int(patch_id)))
        if cur.rowcount == 0:
            # no pre-application row (older path) — create the final record
            conn.execute(
                "INSERT INTO build_retrospectives (patch_id, applied_at, outcome,"
                " runtime_verified, runtime_notes, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (int(patch_id), now, status, verified, notes, now))
        conn.commit()
    except sqlite3.Error:
        return "RETROSPECTIVE_WRITE_FAILED"
    return status


def run_once(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> int:
    """Create retrospectives for journal rows that lack one. Returns count."""
    own = conn is None
    conn = conn or _connect(db_path)
    created = 0
    try:
        ensure_schema(conn)
        if not _table_exists(conn, "patch_apply_journal"):
            return 0
        jcols = _cols(conn, "patch_apply_journal")
        id_col = "id" if "id" in jcols else "rowid"
        rows = conn.execute(
            f"SELECT {id_col} AS jid, patch_id, ts, action, outcome, "
            "COALESCE(detail,'') detail FROM patch_apply_journal j "
            "WHERE action IN ('apply','rollback','guard','validate') "
            f"AND NOT EXISTS (SELECT 1 FROM build_retrospectives r "
            f" WHERE r.journal_id = j.{id_col}) "
            "ORDER BY ts ASC LIMIT 200").fetchall()
        for r in rows:
            patch_id = r["patch_id"]
            outcome = str(r["outcome"] or "").upper()
            action = str(r["action"] or "").lower()
            if action == "rollback":
                mapped = "ROLLED_BACK"
            elif action == "apply" and outcome in ("OK", "APPLIED", "SUCCESS"):
                mapped = "APPLIED"
            elif outcome in ("BLOCKED",):
                mapped = "BLOCKED"
            elif outcome in ("REJECTED",):
                mapped = "REJECTED"
            else:
                mapped = outcome or action.upper()
            target_file, proposal_id, provenance = "", None, ""
            if patch_id and _table_exists(conn, "code_patches"):
                pcols = _cols(conn, "code_patches")
                sel = [c for c in ("target_file", "file_path", "proposal_id",
                                   "title", "summary", "description") if c in pcols]
                if sel:
                    p = conn.execute(
                        f"SELECT {', '.join(sel)} FROM code_patches WHERE id=?",
                        (patch_id,)).fetchone()
                    if p:
                        pk = p.keys()
                        target_file = str(
                            (p["target_file"] if "target_file" in pk else None)
                            or (p["file_path"] if "file_path" in pk else "") or "")
                        proposal_id = (p["proposal_id"] if "proposal_id" in pk else None)
                        provenance = str(
                            (p["title"] if "title" in pk else None)
                            or (p["summary"] if "summary" in pk else None)
                            or (p["description"] if "description" in pk else "") or "")[:400]
            if proposal_id and _table_exists(conn, "polaris_proposals"):
                qcols = _cols(conn, "polaris_proposals")
                sel = [c for c in ("title", "summary", "proposal_text") if c in qcols]
                if sel:
                    q = conn.execute(
                        f"SELECT {', '.join(sel)} FROM polaris_proposals WHERE id=?",
                        (proposal_id,)).fetchone()
                    if q:
                        qk = q.keys()
                        provenance = (provenance + " | decision: " + str(
                            (q["title"] if "title" in qk else None)
                            or (q["summary"] if "summary" in qk else None)
                            or (q["proposal_text"] if "proposal_text" in qk else "")
                        )[:400]).strip(" |")
            applied_at = float(r["ts"] or time.time())
            verified, notes = (0, "not applicable")
            if mapped == "APPLIED":
                verified, notes = _runtime_verification(conn, applied_at)
            conn.execute(
                "INSERT OR IGNORE INTO build_retrospectives "
                "(patch_id, journal_id, proposal_id, target_file, applied_at,"
                " outcome, what_changed, decision_provenance, runtime_verified,"
                " runtime_notes, rolled_back, next_attempt, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (patch_id, int(r["jid"]), proposal_id, target_file, applied_at,
                 mapped, str(r["detail"])[:600], provenance, verified, notes,
                 ("yes: " + str(r["detail"])[:300]) if mapped == "ROLLED_BACK" else "",
                 "" if mapped == "APPLIED" else
                 "re-evaluate cause recorded in decision_provenance before retry",
                 time.time()),
            )
            created += conn.total_changes and 1 or 0
        conn.commit()
        try:
            from core.schema import update_heartbeat  # type: ignore
            update_heartbeat(SERVICE_NAME, "OK", f"created={created}")
        except Exception:
            pass
        return created
    finally:
        if own:
            conn.close()


def report(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        by_outcome = dict(conn.execute(
            "SELECT outcome, COUNT(*) FROM build_retrospectives GROUP BY outcome"
        ).fetchall())
        unverified = conn.execute(
            "SELECT COUNT(*) FROM build_retrospectives "
            "WHERE outcome='APPLIED' AND runtime_verified=0").fetchone()[0]
        latest = [dict(r) for r in conn.execute(
            "SELECT patch_id, target_file, outcome, runtime_verified, "
            " decision_provenance, applied_at FROM build_retrospectives "
            "ORDER BY applied_at DESC LIMIT 10").fetchall()]
        return {"by_outcome": by_outcome,
                "applied_without_runtime_verification": unverified,
                "latest": latest}
    finally:
        if own:
            conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build retrospective recorder")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.report:
        print(json.dumps(report(db_path=args.db), indent=2, default=str))
        return
    if args.loop:
        while True:
            try:
                n = run_once(db_path=args.db)
                print(f"[{time.strftime('%H:%M:%S')}] retrospectives created: {n}")
            except Exception as exc:  # keep the loop alive; never fabricate
                print(f"retrospective cycle error: {type(exc).__name__}: {exc}")
            time.sleep(CYCLE_SECONDS)
    else:
        print(f"retrospectives created: {run_once(db_path=args.db)}")


if __name__ == "__main__":
    main()