# coding: utf-8
"""
services/council_quest_bridge.py — COUNCIL_QUEST_LIFECYCLE_20260813

Persisted, evidence-backed lifecycle for the Council Quest surface.

This is the missing middle between organism_pressure (what looks weak) and the
Quest UI (what the Council is actually doing). It does not invent dialogue or
hidden reasoning. It projects already-persisted runtime/Council events into a
small Tier-3 lifecycle ledger:

    pressure -> ACTIVE QUEST -> evidence-backed stage events -> close/reflection

The bridge is called by council_chamber_bridge, an existing low-priority service.
It never runs on execution/price hot paths and never changes trading policy.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from core.schema import get_connection
except Exception:  # pragma: no cover
    get_connection = None  # type: ignore

try:
    from services.organism_pressure import snapshot as pressure_snapshot, leading_question
except Exception:  # pragma: no cover
    pressure_snapshot = lambda *a, **k: {"ranked": [], "faculties": []}
    leading_question = lambda *a, **k: {}

SERVICE = "council_quest_bridge"
JOURNEY = ("CLUE", "PROPOSITION", "CHALLENGE", "TEST", "FORGE", "POLARIS", "FILE_CHANGE", "REFLECTION")
STAGE_INDEX = {s: i for i, s in enumerate(JOURNEY)}

# Pressure faculty != habitat. This explicit translation fixes the previous
# impossible contracts such as habitat='EDGE', which lumen_field_state rejected.
FACULTY_DESTINATIONS: Dict[str, Tuple[str, ...]] = {
    "EDGE": ("RUNTIME", "CODE", "SOLANA"),
    "PRICE_TRUTH": ("RUNTIME", "PRICE_TRUTH", "CODE"),
    "SMART_MONEY": ("RUNTIME", "CODE", "GITHUB_EXPEDITION"),
    "EXECUTION": ("RUNTIME", "CODE", "SOLANA"),
    "COPYTRADE": ("COPYTRADE", "GITHUB_EXPEDITION", "CODE"),
    "SUBSTRATE": ("SUBSTRATE", "CODE"),
    "INTELLIGENCE": ("INTELLIGENCE", "GITHUB_EXPEDITION", "RESEARCH"),
    "COUNCIL": ("COUNCIL", "CODE"),
    "OBSERVABILITY": ("RUNTIME", "CODE"),
}

KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "EDGE": ("edge", "trading", "pipeline", "candidate", "entry", "runner", "pnl", "position"),
    "PRICE_TRUTH": ("price", "oracle", "mark", "quote", "route", "coverage"),
    "SMART_MONEY": ("smart money", "holder", "wallet", "volume", "sell pressure", "coverage"),
    "EXECUTION": ("execution", "submit", "fill", "jupiter", "route", "live", "paper"),
    "COPYTRADE": ("copytrade", "copy trade", "wallet", "gmgn", "convergence"),
    "SUBSTRATE": ("substrate", "replay", "experiment", "test"),
    "INTELLIGENCE": ("intelligence", "research", "inspiration", "github", "specimen"),
    "COUNCIL": ("council", "debate", "polaris", "forge", "proposal"),
    "OBSERVABILITY": ("telemetry", "observability", "heartbeat", "sensor", "coverage", "audit"),
}

ROLE_ALIASES = {
    "POLAR": "POLARIS", "POLARIS": "POLARIS",
    "IVY": "IVARIS", "IVARIS": "IVARIS", "CRITIC": "IVARIS",
    "NUGGET": "NUGGET", "SCOUT": "NUGGET", "GITHUB_SCOUT": "NUGGET",
    "AXON": "AXON", "ARCHITECT": "AXON",
    "MECHANIST": "MECHANIST",
    "RHIZA": "RHIZA", "MEMORY": "RHIZA", "ARCHIVIST": "RHIZA",
    "SUBSTRATE": "SUBSTRATE", "EXPERIMENTER": "SUBSTRATE",
    "FORGE": "FORGE", "ALPHA_FORGE": "FORGE",
}


def _conn() -> sqlite3.Connection:
    if get_connection is None:
        raise RuntimeError("canonical Sentinuity DB connection unavailable")
    c = get_connection()
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=1500")
    return c


def _tables(c: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _cols(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def ensure_schema(c: Optional[sqlite3.Connection] = None) -> bool:
    own = c is None
    try:
        c = c or _conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS council_quests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty TEXT NOT NULL,
            question TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            current_stage TEXT NOT NULL DEFAULT 'CLUE',
            destinations_json TEXT,
            pressure_json TEXT,
            success_condition TEXT,
            kill_condition TEXT,
            opened_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            closed_at REAL,
            close_reason TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_council_one_active_quest
            ON council_quests(status) WHERE status='ACTIVE';
        CREATE INDEX IF NOT EXISTS idx_council_quests_recent
            ON council_quests(updated_at DESC);

        CREATE TABLE IF NOT EXISTS council_quest_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            next_action TEXT,
            source_table TEXT,
            source_row_id TEXT,
            evidence_json TEXT,
            created_at REAL NOT NULL,
            UNIQUE(quest_id, source_table, source_row_id, event_type),
            FOREIGN KEY(quest_id) REFERENCES council_quests(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_quest_events_quest
            ON council_quest_events(quest_id, created_at ASC);
        """)
        c.commit()
        return True
    except Exception:
        return False
    finally:
        if own and c is not None:
            try: c.close()
            except Exception: pass


def _text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).strip()


def _role(v: Any, default: str = "MECHANIST") -> str:
    s = str(v or "").upper().strip()
    return ROLE_ALIASES.get(s, default)


def _relevant(faculty: str, *values: Any) -> bool:
    blob = _text(*values).lower()
    if not blob:
        return False
    return any(k in blob for k in KEYWORDS.get(faculty, (faculty.lower(),)))


def _event(c: sqlite3.Connection, quest_id: int, stage: str, actor: str,
           event_type: str, summary: str, *, next_action: str = "",
           source_table: str = "", source_row_id: Any = "",
           evidence: Optional[Dict[str, Any]] = None, at: Optional[float] = None) -> bool:
    if stage not in STAGE_INDEX or not summary.strip():
        return False
    now = float(at or time.time())
    try:
        cur = c.execute(
            """INSERT OR IGNORE INTO council_quest_events
               (quest_id,stage,actor_role,event_type,summary,next_action,
                source_table,source_row_id,evidence_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (int(quest_id), stage, _role(actor), str(event_type or "EVENT")[:80],
             str(summary)[:1200], str(next_action or "")[:600],
             str(source_table or "")[:100], str(source_row_id or "")[:160],
             json.dumps(evidence or {}, default=str)[:4000], now),
        )
        return int(cur.rowcount or 0) > 0
    except sqlite3.IntegrityError:
        return False


def _active(c: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return c.execute("SELECT * FROM council_quests WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1").fetchone()


def _faculty_map(snap: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(f.get("key")): f for f in snap.get("faculties", []) if f.get("key")}


def _choose_question(snap: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Choose a pressure worth investigating, not merely a sensor waiting for data.

    Critical correction: EDGE with no closes is not automatically a broken
    trading pipeline. If EXECUTION shows recent opens, it is simply waiting for
    outcomes, so skip it and investigate the next measured pressure instead.
    """
    fmap = _faculty_map(snap)
    execution = fmap.get("EXECUTION", {})
    exec_present = {str(x.get("name")) for x in execution.get("senses_present", [])}

    for f in snap.get("ranked", []):
        if float(f.get("pressure") or 0) <= 0:
            continue
        key = str(f.get("key") or "")
        note = str(f.get("note") or "")
        if key == "EDGE" and f.get("state") == "UNKNOWN" and "No closes" in note and "opens" in exec_present:
            continue
        # A fully alive faculty is not a quest even if some stale importance
        # calculation accidentally leaves a tiny pressure value.
        if f.get("state") == "ALIVE":
            continue
        single = dict(snap)
        single["top_pressure"] = f
        q = leading_question(single) or {}
        if q:
            q["destinations"] = list(FACULTY_DESTINATIONS.get(key, tuple(q.get("destinations", []))))
            return f, q
    return {}, {}


def _open_quest(c: sqlite3.Connection, faculty: Dict[str, Any], q: Dict[str, Any], now: float) -> int:
    cur = c.execute(
        """INSERT INTO council_quests
           (faculty,question,status,current_stage,destinations_json,pressure_json,
            success_condition,kill_condition,opened_at,updated_at)
           VALUES(?,?,'ACTIVE','CLUE',?,?,?,?,?,?)""",
        (str(q.get("faculty") or faculty.get("key") or ""),
         str(q.get("question") or "Measured system pressure requires investigation."),
         json.dumps(q.get("destinations") or []),
         json.dumps(faculty, default=str)[:8000],
         str(q.get("success_condition") or ""), str(q.get("kill_condition") or ""),
         now, now),
    )
    qid = int(cur.lastrowid)
    _event(c, qid, "CLUE", "NUGGET", "PRESSURE_DETECTED",
           str(q.get("current_evidence") or faculty.get("note") or q.get("question") or "Pressure detected."),
           next_action="Follow the evidence into " + " / ".join(q.get("destinations") or []),
           source_table=str(faculty.get("evidence") or "organism_pressure"),
           source_row_id=f"pressure:{int(now)}",
           evidence={"faculty": faculty.get("key"), "state": faculty.get("state"),
                     "pressure": faculty.get("pressure")}, at=now)
    return qid


def _close(c: sqlite3.Connection, row: sqlite3.Row, reason: str, now: float) -> None:
    c.execute("UPDATE council_quests SET status='CLOSED',closed_at=?,updated_at=?,close_reason=? WHERE id=?",
              (now, now, reason[:500], int(row["id"])))


def _common_time_col(cols: set[str]) -> Optional[str]:
    for x in ("created_at", "logged_at", "updated_at", "ts", "timestamp", "applied_at", "completed_at", "finished_at"):
        if x in cols:
            return x
    return None


def _recent_rows(c: sqlite3.Connection, table: str, since: float, limit: int = 80) -> List[Dict[str, Any]]:
    if table not in _tables(c):
        return []
    cs = _cols(c, table)
    tc = _common_time_col(cs)
    try:
        if tc:
            rr = c.execute(f'SELECT rowid AS _rowid,* FROM "{table}" WHERE COALESCE("{tc}",0)>=? ORDER BY "{tc}" ASC LIMIT ?',
                           (since, limit)).fetchall()
        else:
            rr = c.execute(f'SELECT rowid AS _rowid,* FROM "{table}" ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rr]
    except Exception:
        return []


def _summary_from_json(v: Any) -> str:
    if not v:
        return ""
    try:
        d = json.loads(str(v))
        if isinstance(d, dict):
            return str(d.get("summary") or d.get("message") or d.get("text") or d.get("details") or "")
    except Exception:
        pass
    return str(v)


def _project_real_events(c: sqlite3.Connection, quest: sqlite3.Row, now: float) -> int:
    """Project only persisted, relevant events into explicit journey stages."""
    qid = int(quest["id"])
    faculty = str(quest["faculty"] or "")
    since = float(quest["opened_at"] or now) - 2.0
    made = 0

    # Existing field notes are explicit Council statements and therefore the
    # strongest source for the human-readable Chronicle.
    for r in _recent_rows(c, "council_field_notes", since):
        blob = _text(r.get("body"), r.get("next_action"), r.get("habitat"), r.get("related_type"))
        if not _relevant(faculty, blob):
            continue
        role = _role(r.get("author_role"))
        stage = {
            "NUGGET": "CLUE", "AXON": "PROPOSITION", "MECHANIST": "PROPOSITION",
            "RHIZA": "PROPOSITION", "IVARIS": "CHALLENGE", "SUBSTRATE": "TEST",
            "FORGE": "FORGE", "POLARIS": "POLARIS",
        }.get(role, "PROPOSITION")
        made += int(_event(c, qid, stage, role, "FIELD_NOTE", str(r.get("body") or "")[:1200],
                           next_action=str(r.get("next_action") or ""), source_table="council_field_notes",
                           source_row_id=r.get("id", r.get("_rowid")), evidence={"habitat": r.get("habitat")},
                           at=float(r.get("created_at") or now)))

    # GitHub source discoveries become CLUEs only when the quest is actually
    # related. Repository count itself never advances beyond CLUE.
    for r in _recent_rows(c, "github_discovery_ledger", since):
        blob = _text(*[r.get(k) for k in ("repo", "repository", "topic", "query", "title", "summary", "disposition")])
        if not _relevant(faculty, blob):
            continue
        repo = str(r.get("repository") or r.get("repo") or r.get("title") or "external source")
        disp = str(r.get("disposition") or r.get("status") or "")
        summary = f"Found a relevant external trail in {repo}."
        if disp: summary += f" Screening state: {disp}."
        made += int(_event(c, qid, "CLUE", "NUGGET", "GITHUB_DISCOVERY", summary,
                           next_action="Compare the mechanism with Sentinuity before assigning value.",
                           source_table="github_discovery_ledger", source_row_id=r.get("id", r.get("_rowid")),
                           evidence={"repository": repo, "disposition": disp},
                           at=float(r.get("created_at") or r.get("updated_at") or now)))

    # Debate rows advance only when their content is relevant to this faculty.
    for r in _recent_rows(c, "debate_log", since):
        content = _summary_from_json(r.get("content_json"))
        blob = _text(r.get("speaker"), r.get("action"), content)
        if not _relevant(faculty, blob):
            continue
        action = str(r.get("action") or "").upper()
        speaker = _role(r.get("speaker"), "MECHANIST")
        if "CRITIC" in action or "CHALLENGE" in action or "REJECT" in action or speaker == "IVARIS":
            stage = "CHALLENGE"; speaker = "IVARIS"
        else:
            stage = "PROPOSITION"
        summary = content or f"{speaker.title()} recorded {str(r.get('action') or 'a debate event').lower()}."
        made += int(_event(c, qid, stage, speaker, "DEBATE", summary,
                           source_table="debate_log", source_row_id=r.get("id", r.get("_rowid")),
                           evidence={"proposal_id": r.get("proposal_id"), "action": r.get("action")},
                           at=float(r.get("logged_at") or now)))

    # Substrate testing: only rows whose descriptive fields match the quest.
    for table in ("substrate_positions", "substrate_experiments", "replay_results"):
        for r in _recent_rows(c, table, since):
            blob = _text(*r.values())
            if not _relevant(faculty, blob):
                continue
            made += int(_event(c, qid, "TEST", "SUBSTRATE", "SUBSTRATE_RESULT",
                               "A relevant hypothesis reached the proving ground.",
                               next_action="Judge the reproduced result before implementation.",
                               source_table=table, source_row_id=r.get("id", r.get("_rowid")),
                               evidence={k: r.get(k) for k in ("status", "result", "pnl_pct", "proposal_id") if k in r},
                               at=float(r.get(_common_time_col(set(r.keys())) or "") or now)))

    # Forge rows.
    for table in ("forge_research_queue", "forge_build_queue", "forge_patch_queue"):
        for r in _recent_rows(c, table, since):
            blob = _text(*r.values())
            if not _relevant(faculty, blob):
                continue
            made += int(_event(c, qid, "FORGE", "FORGE", "FORGE_WORK",
                               "Forge accepted a relevant survived proposition for implementation work.",
                               source_table=table, source_row_id=r.get("id", r.get("_rowid")),
                               evidence={k: r.get(k) for k in ("status", "proposal_id", "target_path") if k in r},
                               at=float(r.get(_common_time_col(set(r.keys())) or "") or now)))

    # Polaris judgement.
    for r in _recent_rows(c, "polaris_proposals", since):
        blob = _text(*r.values())
        if not _relevant(faculty, blob):
            continue
        status = str(r.get("status") or r.get("decision") or "")
        made += int(_event(c, qid, "POLARIS", "POLARIS", "POLARIS_JUDGEMENT",
                           f"Polaris recorded a judgement on the quest-related proposal: {status or 'reviewed'}.",
                           next_action="Apply only if the approved patch remains inside its authorised territory.",
                           source_table="polaris_proposals", source_row_id=r.get("id", r.get("_rowid")),
                           evidence={"status": status, "proposal_id": r.get("id")},
                           at=float(r.get("created_at") or r.get("updated_at") or now)))

    # Real file application. This is tangible and may only light FILE_CHANGE.
    for r in _recent_rows(c, "patch_apply_journal", since):
        blob = _text(*r.values())
        if not _relevant(faculty, blob):
            continue
        path = str(r.get("target_path") or r.get("path") or r.get("file") or "")
        if not path:
            continue
        made += int(_event(c, qid, "FILE_CHANGE", "POLARIS", "FILE_APPLIED",
                           f"The organism changed: {path}.",
                           next_action="Watch later runtime evidence before calling the change successful.",
                           source_table="patch_apply_journal", source_row_id=r.get("id", r.get("_rowid")),
                           evidence={"path": path, "result": r.get("result") or r.get("status")},
                           at=float(r.get(_common_time_col(set(r.keys())) or "") or now)))

    # Retrospective is the only thing that closes the learning loop.
    for r in _recent_rows(c, "build_retrospective", since):
        blob = _text(*r.values())
        if not _relevant(faculty, blob):
            continue
        made += int(_event(c, qid, "REFLECTION", "RHIZA", "RUNTIME_REFLECTION",
                           "Runtime evidence was recorded after the implementation.",
                           source_table="build_retrospective", source_row_id=r.get("id", r.get("_rowid")),
                           evidence={k: r.get(k) for k in ("result", "status", "proposal_id", "summary") if k in r},
                           at=float(r.get(_common_time_col(set(r.keys())) or "") or now)))

    # Explicit stage is derived from persisted stage events, then stored. The UI
    # no longer infers lifecycle from whichever role happened to speak.
    stage_rows = c.execute("SELECT stage FROM council_quest_events WHERE quest_id=?", (qid,)).fetchall()
    if stage_rows:
        furthest = max((str(r["stage"] or "CLUE") for r in stage_rows),
                       key=lambda s: STAGE_INDEX.get(s, -1))
        c.execute("UPDATE council_quests SET current_stage=?,updated_at=? WHERE id=?",
                  (furthest, now, qid))
    return made


def sync_once(now: Optional[float] = None) -> Dict[str, Any]:
    """One bounded lifecycle projection. Safe for Council bridge cadence."""
    now = float(now or time.time())
    c: Optional[sqlite3.Connection] = None
    try:
        c = _conn()
        if not ensure_schema(c):
            return {"ok": False, "error": "schema"}
        snap = pressure_snapshot(now)
        faculty, q = _choose_question(snap)
        active = _active(c)

        if active:
            amap = _faculty_map(snap)
            af = amap.get(str(active["faculty"]), {})
            # A genuinely ALIVE faculty resolves the premise. EDGE specifically
            # also closes when it was merely waiting for outcomes and execution
            # has resumed, preventing the screenshot's stale "pipeline stopped"
            # quest from surviving launch after launch.
            if af.get("state") == "ALIVE":
                _close(c, active, "MEASURED_FACULTY_RECOVERED", now)
                active = None
            elif str(active["faculty"]) == "EDGE" and af.get("state") == "UNKNOWN" and "No closes" in str(af.get("note") or ""):
                ex = _faculty_map(snap).get("EXECUTION", {})
                ep = {str(x.get("name")) for x in ex.get("senses_present", [])}
                if "opens" in ep:
                    _close(c, active, "WAITING_FOR_OUTCOMES_NOT_PIPELINE_FAILURE", now)
                    active = None

        # If the measured pressure changed materially, close the old question
        # rather than keeping a stale headline forever.
        if active and q and str(active["faculty"]) != str(q.get("faculty")):
            age = now - float(active["opened_at"] or now)
            if age >= 120.0:
                _close(c, active, f"SUPERSEDED_BY_{q.get('faculty')}", now)
                active = None

        if active is None and q:
            qid = _open_quest(c, faculty, q, now)
            active = c.execute("SELECT * FROM council_quests WHERE id=?", (qid,)).fetchone()

        events = _project_real_events(c, active, now) if active else 0
        c.commit()
        return {
            "ok": True,
            "quest_id": int(active["id"]) if active else None,
            "faculty": str(active["faculty"]) if active else None,
            "stage": str(active["current_stage"]) if active else None,
            "events_projected": int(events),
        }
    except Exception as exc:
        if c is not None:
            try: c.rollback()
            except Exception: pass
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        if c is not None:
            try: c.close()
            except Exception: pass
