"""
services/inspiration_intake_ledger.py
=====================================
SIGNOFF_INSPIRATION_INTAKE_LEDGER_20260720

Structured Inspiration Intake Ledger — the missing bridge between external
inspiration (GitHub, hashtag channels, Telegram calls, operator ideas) and the
Council build pipeline.

AUDIT FINDING THAT MOTIVATED THIS FILE
--------------------------------------
The pre-existing pipeline (github_scout -> forge_research_cache -> ivaris
debate) had:
  * NO licence field, NO security screen, NO author provenance.
  * A 24h TTL on evidence (forge_research_cache.expires_at) — provenance
    evaporated before any build completed.
  * NO disposition lifecycle: an inspiration was either "in cache" or gone.
  * NO auditable link from inspiration -> debate -> patch.

This module adds a durable ledger with the full 15-stage sign-off path.
It is PURE data infrastructure: it never applies code, never touches trading
configuration, never reads secrets. External code registered here is
EVIDENCE AND INSPIRATION — NEVER AUTHORITY. Nothing in this ledger can move
past STAGE_APPLICATION without an explicit operator_approved flag, and this
module itself has no code-apply capability at all.

Usage
-----
    from services.inspiration_intake_ledger import (
        record_inspiration, advance_stage, set_disposition, backfill_from_forge_cache
    )

    iid = record_inspiration(
        source_type="github_repo",
        source_ref="https://github.com/example/solana-sniper",
        topic_tags="solana,routing,jito",
        standing_task="Live Lane Readiness",
        extracted_concept="Pre-simulated route with bounded compute budget",
        council_sponsor="POLARIS",
        author="example",
        licence="MIT",
    )
    advance_stage(iid, "RELEVANCE_SCREENING", note="matches Live Lane Readiness")

CLI
---
    python -m services.inspiration_intake_ledger --report
    python -m services.inspiration_intake_ledger --backfill
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

# ── 15-stage sign-off path (directive: "SIGN-OFF PATH FROM INSPIRATION TO
#    CODE APPLICATION"). Order is a hard contract: advance_stage() refuses
#    skips and refuses regression except via explicit disposition changes. ──
STAGES = (
    "INTAKE",                 # 1  source discovered + persisted with provenance
    "RELEVANCE_SCREENING",    # 2  supports a standing task or recorded opportunity
    "SECURITY_LICENCE_SCREEN",# 3  unsafe / licence-problematic material rejected
    "INTERNAL_ABSTRACTION",   # 4  principle extracted, not code copied
    "RESEARCH_ROUND",         # 5  supporting + opposing evidence gathered
    "DEBATE_FALSIFICATION",   # 6  at least one explicit disproof attempt
    "BUILD_CONTRACT",         # 7  narrow directive with acceptance criteria
    "SANDBOXED_PATCH",        # 8  changes produced without overwriting baseline
    "STATIC_VALIDATION",      # 9  compile/import/schema/secret/scope checks
    "TARGETED_TESTS",         # 10 subsystem tests / deterministic replays
    "PAPER_EVALUATION",       # 11 trading-policy changes prove out in paper
    "CANARY_CONSIDERATION",   # 12 operator-controlled canary approval
    "APPLICATION",            # 13 approved replacement pack alters baseline
    "RUNTIME_VERIFICATION",   # 14 services up, ledgers writing, no stale paths
    "RETROSPECTIVE",          # 15 what changed / improved / regressed / next
)
_STAGE_INDEX = {s: i for i, s in enumerate(STAGES)}

DISPOSITIONS = (
    "REJECTED", "ARCHIVED", "RESEARCH_REQUIRED", "DEBATE_QUEUED",
    "PROTOTYPE_QUEUED", "PAPER_EXPERIMENT", "CANARY_CANDIDATE",
    "APPLIED", "ROLLED_BACK", "SUPERSEDED",
)

SOURCE_TYPES = (
    "github_repo", "github_commit", "github_issue", "github_release",
    "hashtag_channel", "telegram_channel", "research_feed",
    "operator_idea", "council_hypothesis", "other",
)

DDL = """
CREATE TABLE IF NOT EXISTS inspiration_intake_ledger (
    inspiration_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type        TEXT NOT NULL,
    source_ref         TEXT NOT NULL,          -- URL or identifier
    retrieved_at       REAL NOT NULL,          -- provenance: retrieval time
    author             TEXT,                   -- provenance: author / project
    licence            TEXT,                   -- provenance: licence
    relevance          TEXT,                   -- provenance: why it matters
    security_concerns  TEXT,                   -- provenance: recorded risks
    files_examined     TEXT,                   -- provenance: files / concepts
    topic_tags         TEXT,
    standing_task      TEXT,                   -- standing-task relationship
    extracted_concept  TEXT,                   -- the PRINCIPLE, not the code
    expected_benefit   TEXT,
    novelty            TEXT,
    system_overlap     TEXT,                   -- existing-system overlap
    risks              TEXT,
    council_sponsor    TEXT,
    stage              TEXT NOT NULL DEFAULT 'INTAKE',
    stage_entered_at   REAL,
    stage_history      TEXT NOT NULL DEFAULT '[]',   -- JSON [{stage,at,note}]
    research_status    TEXT DEFAULT 'PENDING',
    debate_status      TEXT DEFAULT 'PENDING',
    build_status       TEXT DEFAULT 'PENDING',
    validation_status  TEXT DEFAULT 'PENDING',
    disposition        TEXT DEFAULT 'RESEARCH_REQUIRED',
    operator_approved  INTEGER NOT NULL DEFAULT 0,   -- required for stage >= APPLICATION
    linked_proposal_id INTEGER,                -- polaris_proposals.id
    linked_patch_id    INTEGER,                -- code_patches.id
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);
"""
IDX = (
    "CREATE INDEX IF NOT EXISTS iil_stage ON inspiration_intake_ledger(stage);",
    "CREATE INDEX IF NOT EXISTS iil_disp  ON inspiration_intake_ledger(disposition);",
    "CREATE INDEX IF NOT EXISTS iil_task  ON inspiration_intake_ledger(standing_task);",
)

# Stages that require trading-policy paper proof before advancing further.
_PAPER_REQUIRED_TAGS = ("signal", "entry", "exit", "sizing", "policy", "risk", "pattern")


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
    for stmt in IDX:
        conn.execute(stmt)
    conn.commit()


# ── write API ────────────────────────────────────────────────────────────────
def record_inspiration(
    *,
    source_type: str,
    source_ref: str,
    extracted_concept: str = "",
    topic_tags: str = "",
    standing_task: str = "",
    expected_benefit: str = "",
    novelty: str = "",
    system_overlap: str = "",
    risks: str = "",
    council_sponsor: str = "",
    author: str = "",
    licence: str = "",
    relevance: str = "",
    security_concerns: str = "",
    files_examined: str = "",
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> int:
    """Persist a new inspiration at STAGE 1 (INTAKE). Returns inspiration_id.

    Idempotency: an identical (source_type, source_ref) that is not REJECTED /
    SUPERSEDED is reused rather than duplicated.
    """
    if source_type not in SOURCE_TYPES:
        source_type = "other"
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        now = time.time()
        row = conn.execute(
            "SELECT inspiration_id FROM inspiration_intake_ledger "
            "WHERE source_type=? AND source_ref=? "
            "AND disposition NOT IN ('REJECTED','SUPERSEDED') "
            "ORDER BY inspiration_id DESC LIMIT 1",
            (source_type, source_ref),
        ).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO inspiration_intake_ledger ("
            " source_type, source_ref, retrieved_at, author, licence, relevance,"
            " security_concerns, files_examined, topic_tags, standing_task,"
            " extracted_concept, expected_benefit, novelty, system_overlap, risks,"
            " council_sponsor, stage, stage_entered_at, stage_history,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'INTAKE', ?, ?, ?, ?)",
            (source_type, source_ref, now, author, licence, relevance,
             security_concerns, files_examined, topic_tags, standing_task,
             extracted_concept, expected_benefit, novelty, system_overlap, risks,
             council_sponsor, now,
             json.dumps([{"stage": "INTAKE", "at": now, "note": "recorded"}]),
             now, now),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        if own:
            conn.close()


def advance_stage(
    inspiration_id: int,
    target_stage: str,
    *,
    note: str = "",
    operator_approved: Optional[bool] = None,
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> tuple[bool, str]:
    """Advance one inspiration to `target_stage`.

    Enforced contract (entry conditions):
      * Stages may only advance one step at a time — no skipping.
      * SECURITY_LICENCE_SCREEN cannot be passed with an empty licence field
        for github_* sources (licence unknown => must be recorded as such and
        the record must be REJECTED or held, not silently advanced).
      * PAPER_EVALUATION is mandatory before CANARY_CONSIDERATION for any
        inspiration tagged with trading-policy topics.
      * APPLICATION and beyond require operator_approved=1.
    Returns (ok, reason).
    """
    if target_stage not in _STAGE_INDEX:
        return False, f"unknown stage {target_stage!r}"
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM inspiration_intake_ledger WHERE inspiration_id=?",
            (inspiration_id,),
        ).fetchone()
        if not row:
            return False, "no such inspiration"
        cur_stage = str(row["stage"] or "INTAKE")
        cur_i, tgt_i = _STAGE_INDEX.get(cur_stage, 0), _STAGE_INDEX[target_stage]
        if tgt_i != cur_i + 1:
            return False, (f"stage skip refused: {cur_stage} -> {target_stage} "
                           f"(stages advance one step at a time)")
        # Entry condition: licence recorded before security screen passes.
        if target_stage == "INTERNAL_ABSTRACTION":
            src = str(row["source_type"] or "")
            lic = str(row["licence"] or "").strip()
            if src.startswith("github") and not lic:
                return False, ("SECURITY_LICENCE_SCREEN exit refused: licence "
                               "not recorded for a github source")
        # Entry condition: paper proof before canary for trading-policy topics.
        if target_stage == "CANARY_CONSIDERATION":
            tags = str(row["topic_tags"] or "").lower()
            if any(t in tags for t in _PAPER_REQUIRED_TAGS):
                if str(row["validation_status"] or "").upper() != "PAPER_PASS":
                    return False, ("CANARY refused: trading-policy inspiration "
                                   "requires validation_status=PAPER_PASS first")
        # Entry condition: operator approval at the application boundary.
        approved = int(row["operator_approved"] or 0)
        if operator_approved is True:
            approved = 1
        if _STAGE_INDEX[target_stage] >= _STAGE_INDEX["APPLICATION"] and not approved:
            return False, ("APPLICATION refused: operator_approved=0 — external "
                           "inspiration is evidence, never authority")
        now = time.time()
        history = json.loads(row["stage_history"] or "[]")
        history.append({"stage": target_stage, "at": now, "note": note})
        conn.execute(
            "UPDATE inspiration_intake_ledger SET stage=?, stage_entered_at=?,"
            " stage_history=?, operator_approved=?, updated_at=?"
            " WHERE inspiration_id=?",
            (target_stage, now, json.dumps(history), approved, now, inspiration_id),
        )
        conn.commit()
        return True, f"advanced to {target_stage}"
    finally:
        if own:
            conn.close()


def set_disposition(
    inspiration_id: int,
    disposition: str,
    *,
    note: str = "",
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> tuple[bool, str]:
    if disposition not in DISPOSITIONS:
        return False, f"unknown disposition {disposition!r}"
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        now = time.time()
        cur = conn.execute(
            "UPDATE inspiration_intake_ledger SET disposition=?, updated_at=?,"
            " stage_history=json_insert(stage_history, '$[#]', "
            "  json_object('disposition', ?, 'at', ?, 'note', ?))"
            " WHERE inspiration_id=?",
            (disposition, now, disposition, now, note, inspiration_id),
        )
        conn.commit()
        return (cur.rowcount > 0), ("ok" if cur.rowcount else "no such inspiration")
    finally:
        if own:
            conn.close()


def find_for_task(
    conn: sqlite3.Connection, task_title: str, limit: int = 3
) -> list[sqlite3.Row]:
    """Evidence retrieval for the Council research phase.

    Returns intake records relevant to a standing task (matched on the
    standing_task column or topic tags — stable references, not free text
    scoring). Only records that have not been rejected/superseded and that
    have at least reached INTAKE are returned.
    """
    ensure_schema(conn)
    t = (task_title or "").strip()
    if not t:
        return []
    like = f"%{t[:40]}%"
    try:
        return conn.execute(
            "SELECT inspiration_id, source_type, source_ref, author, licence,"
            " extracted_concept, topic_tags, stage, standing_task"
            " FROM inspiration_intake_ledger"
            " WHERE disposition NOT IN ('REJECTED','SUPERSEDED')"
            " AND (standing_task LIKE ? OR topic_tags LIKE ?)"
            " ORDER BY updated_at DESC LIMIT ?",
            (like, like, int(limit)),
        ).fetchall()
    except sqlite3.Error:
        return []


def mark_research_used(
    conn: sqlite3.Connection, inspiration_ids: list[int], task_id: Optional[int] = None
) -> None:
    """Record that these inspirations were consumed as research evidence."""
    if not inspiration_ids:
        return
    ensure_schema(conn)
    now = time.time()
    for iid in inspiration_ids:
        try:
            conn.execute(
                "UPDATE inspiration_intake_ledger SET research_status=?,"
                " updated_at=? WHERE inspiration_id=?",
                (f"USED_IN_RESEARCH:task={task_id}" if task_id else
                 "USED_IN_RESEARCH", now, int(iid)),
            )
        except sqlite3.Error:
            pass
    conn.commit()


QUARANTINE_DDL = """
CREATE TABLE IF NOT EXISTS inspiration_intake_quarantine (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT,
    source_ref  TEXT,
    payload     TEXT,       -- JSON of the fields that could not be ledgered
    error       TEXT,
    created_at  REAL NOT NULL,
    retried     INTEGER NOT NULL DEFAULT 0
);
"""


def quarantine_intake(
    source_type: str,
    source_ref: str,
    payload: dict[str, Any],
    error: str,
    db_path: Optional[str | Path] = None,
) -> bool:
    """Last-resort persistence when record_inspiration fails: the source item
    must not be lost and must not advance. Uses its own connection so a broken
    caller transaction cannot take the quarantine down with it."""
    try:
        conn = _connect(db_path)
        try:
            conn.execute(QUARANTINE_DDL)
            conn.execute(
                "INSERT INTO inspiration_intake_quarantine "
                "(source_type, source_ref, payload, error, created_at) "
                "VALUES (?,?,?,?,?)",
                (str(source_type)[:60], str(source_ref)[:400],
                 json.dumps(payload, default=str)[:4000], str(error)[:400],
                 time.time()),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def retry_quarantine(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
    limit: int = 50,
) -> int:
    """Re-attempt ledgering of quarantined items. Returns count recovered."""
    own = conn is None
    conn = conn or _connect(db_path)
    recovered = 0
    try:
        conn.execute(QUARANTINE_DDL)
        rows = conn.execute(
            "SELECT id, source_type, source_ref, payload FROM "
            "inspiration_intake_quarantine WHERE retried=0 LIMIT ?", (limit,)
        ).fetchall()
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:
                payload = {}
            try:
                iid = record_inspiration(
                    source_type=str(r["source_type"] or "other"),
                    source_ref=str(r["source_ref"] or ""),
                    conn=conn,
                    **{k: str(v)[:2000] for k, v in payload.items()
                       if k in ("extracted_concept", "topic_tags", "standing_task",
                                "expected_benefit", "author", "licence",
                                "relevance", "security_concerns",
                                "council_sponsor", "risks")},
                )
                if iid:
                    conn.execute(
                        "UPDATE inspiration_intake_quarantine SET retried=1 "
                        "WHERE id=?", (r["id"],))
                    recovered += 1
            except Exception:
                continue
        conn.commit()
        return recovered
    finally:
        if own:
            conn.close()


def link_build_artifacts(
    inspiration_id: int,
    *,
    proposal_id: Optional[int] = None,
    patch_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> None:
    """Auditable link: which Council decision / patch this inspiration caused."""
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        sets, vals = ["updated_at=?"], [time.time()]
        if proposal_id is not None:
            sets.append("linked_proposal_id=?"); vals.append(int(proposal_id))
        if patch_id is not None:
            sets.append("linked_patch_id=?"); vals.append(int(patch_id))
        vals.append(int(inspiration_id))
        conn.execute(
            f"UPDATE inspiration_intake_ledger SET {', '.join(sets)} "
            "WHERE inspiration_id=?", vals)
        conn.commit()
    finally:
        if own:
            conn.close()


# ── backfill from the legacy 24h evidence cache ──────────────────────────────
def backfill_from_forge_cache(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> int:
    """Convert surviving forge_research_cache rows into durable intake records.

    Legacy rows lack author / licence / security fields; those gaps are
    recorded honestly ("NOT_CAPTURED_BY_LEGACY_PIPELINE") rather than invented.
    Returns number of rows imported.
    """
    own = conn is None
    conn = conn or _connect(db_path)
    imported = 0
    try:
        ensure_schema(conn)
        try:
            rows = conn.execute(
                "SELECT project_key, topic, summary, source, created_at "
                "FROM forge_research_cache ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        except sqlite3.Error:
            return 0
        for r in rows:
            src = str(r["source"] or "unknown")
            stype = "github_repo" if "github" in src.lower() else (
                "hashtag_channel" if any(k in src.lower() for k in ("x_scout", "twitter", "hashtag"))
                else "research_feed")
            iid = record_inspiration(
                source_type=stype,
                source_ref=f"forge_cache:{r['project_key']}:{r['topic']}",
                extracted_concept=str(r["summary"] or "")[:2000],
                topic_tags=str(r["topic"] or ""),
                standing_task=str(r["project_key"] or ""),
                author="NOT_CAPTURED_BY_LEGACY_PIPELINE",
                licence="NOT_CAPTURED_BY_LEGACY_PIPELINE",
                relevance=f"legacy forge_research_cache row from {src}",
                conn=conn,
            )
            if iid:
                imported += 1
        return imported
    finally:
        if own:
            conn.close()


# ── report ───────────────────────────────────────────────────────────────────
def ledger_report(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    own = conn is None
    conn = conn or _connect(db_path)
    try:
        ensure_schema(conn)
        by_stage = dict(conn.execute(
            "SELECT stage, COUNT(*) FROM inspiration_intake_ledger GROUP BY stage"
        ).fetchall())
        by_disp = dict(conn.execute(
            "SELECT disposition, COUNT(*) FROM inspiration_intake_ledger "
            "GROUP BY disposition").fetchall())
        stalled = conn.execute(
            "SELECT inspiration_id, source_ref, stage, "
            " (strftime('%s','now') - stage_entered_at) AS age_sec "
            "FROM inspiration_intake_ledger "
            "WHERE disposition NOT IN ('REJECTED','ARCHIVED','APPLIED',"
            " 'ROLLED_BACK','SUPERSEDED') "
            "AND stage_entered_at IS NOT NULL "
            "AND (strftime('%s','now') - stage_entered_at) > 259200 "
            "ORDER BY age_sec DESC LIMIT 20").fetchall()
        return {
            "by_stage": by_stage,
            "by_disposition": by_disp,
            "stalled_over_72h": [dict(r) for r in stalled],
            "total": sum(by_stage.values()),
        }
    finally:
        if own:
            conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspiration Intake Ledger")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.backfill:
        n = backfill_from_forge_cache(db_path=args.db)
        print(f"backfilled {n} legacy forge_research_cache rows")
    if args.report or not args.backfill:
        print(json.dumps(ledger_report(db_path=args.db), indent=2, default=str))


if __name__ == "__main__":
    main()