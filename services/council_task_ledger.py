# coding: utf-8
"""
services/council_task_ledger.py — COUNCIL_AUTOBUILD_20260723

ONE canonical execution identity for council work. Imports tasks from the
overlapping legacy systems (council_work_queue, council_world_tasks,
polaris_standing_tasks) into a single durable ledger row each, with:
atomic lease claiming, persisted state transitions, restart-resume,
anti-stagnation enforcement, and weighted fair scheduling.

Lifecycle:
  OPEN → CLAIMED → RESEARCHING → EVIDENCE_READY → PROPOSING → DEBATING
  → GATED → PATCH_READY → APPLYING|NEEDS_OPERATOR → VERIFYING → COMPLETED
Recovery/terminal: BLOCKED_TRANSIENT, BLOCKED_EXTERNAL, FAILED_RETRYABLE,
  FAILED_FINAL, ROLLED_BACK, SUPERSEDED
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_build.db"

PHASES = ["OPEN", "CLAIMED", "RESEARCHING", "EVIDENCE_READY", "PROPOSING",
          "DEBATING", "GATED", "PATCH_READY", "APPLYING", "NEEDS_OPERATOR",
          "VERIFYING", "COMPLETED", "BLOCKED_TRANSIENT", "BLOCKED_EXTERNAL",
          "FAILED_RETRYABLE", "FAILED_FINAL", "ROLLED_BACK", "SUPERSEDED"]
ACTIVE_PHASES = set(PHASES[:11]) - {"NEEDS_OPERATOR"}
TERMINAL = {"COMPLETED", "FAILED_FINAL", "ROLLED_BACK", "SUPERSEDED"}

DEFAULT_LEASE_SEC = 900.0          # dead agent loses lease after 15 min silence
PHASE_DEADLINE_SEC = 1800.0        # same active phase > 30 min ⇒ stagnation
UNCLAIMED_ESCALATE_SEC = 300.0     # one scheduling cycle

SCHEMA = """
CREATE TABLE IF NOT EXISTS council_task_ledger(
    canonical_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    domain TEXT DEFAULT 'ui',
    risk_tier TEXT DEFAULT 'A',
    priority INTEGER DEFAULT 5,
    owner TEXT DEFAULT 'POLARIS',
    phase TEXT NOT NULL DEFAULT 'OPEN',
    claimed_by TEXT,
    claimed_at REAL,
    lease_expires_at REAL,
    heartbeat_at REAL,
    progress_pct REAL DEFAULT 0,
    next_action TEXT,
    blocker_code TEXT,
    retry_count INTEGER DEFAULT 0,
    evidence_ids TEXT DEFAULT '[]',
    proposal_id INTEGER,
    patch_id INTEGER,
    verification_result TEXT,
    completed_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(source_table, source_id)
);
CREATE TABLE IF NOT EXISTS council_task_transitions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    agent TEXT,
    from_phase TEXT,
    to_phase TEXT NOT NULL,
    reason TEXT,
    inputs TEXT,
    outputs TEXT,
    next_action TEXT
);
CREATE INDEX IF NOT EXISTS ctl_phase ON council_task_ledger(phase, priority);
CREATE TABLE IF NOT EXISTS council_needs_operator(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id INTEGER UNIQUE,
    ts REAL NOT NULL,
    decision_needed TEXT NOT NULL,
    context TEXT
);
"""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or DB_PATH), timeout=15.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def ensure_schema(db_path: Optional[Path] = None) -> None:
    c = connect(db_path)
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def _risk_tier(risk_level: str, target: str, task_type: str) -> str:
    r = (risk_level or "LOW").upper()
    blob = f"{target} {task_type}".lower()
    if any(k in blob for k in ("wallet", "key", "live_arm", "withdraw", "signing")):
        return "C"
    if r in ("HIGH", "CRITICAL") or any(k in blob for k in
            ("sizing", "threshold", "risk_gate", "strategy_promotion", "execution")):
        return "B"
    return "A"


def _table_columns(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _pick(cols: set[str], *names: str) -> Optional[str]:
    lower = {x.lower(): x for x in cols}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def import_sources(db_path: Optional[Path] = None,
                   source_db_path: Optional[Path] = None) -> int:
    """Import task authorities from the live matrix into the isolated build DB.

    Source access is read-only and fail-fast. Build bookkeeping is written only
    to sentinuity_build.db, so a busy trading database causes a skipped intake
    rather than delaying market or execution services.
    """
    ensure_schema(db_path)
    source = Path(source_db_path or (ROOT / "sentinuity_matrix.db"))
    if not source.exists():
        return 0
    uri = f"file:{source.as_posix()}?mode=ro"
    try:
        src = sqlite3.connect(uri, uri=True, timeout=0.2)
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        src.execute("PRAGMA busy_timeout=200")
    except sqlite3.Error:
        return 0
    dst = connect(db_path)
    n = 0
    now = time.time()
    try:
        specs = [
            ("council_work_queue", ("id","task_id"), ("title","task_name","name"), "ui"),
            ("council_world_tasks", ("task_id","id"), ("title","task_name","name"), "world"),
            ("polaris_standing_tasks", ("id","task_id"), ("title","task_name","name"), "research"),
        ]
        for table, ids, titles, default_domain in specs:
            cols = _table_columns(src, table)
            idc, titlec = _pick(cols, *ids), _pick(cols, *titles)
            if not idc or not titlec:
                continue
            desc = _pick(cols, "description", "details", "prompt")
            pri = _pick(cols, "priority", "operator_priority")
            risk = _pick(cols, "risk_level", "risk_tier")
            domainc = _pick(cols, "target_tab", "domain", "world_location", "building_id")
            typ = _pick(cols, "task_type", "type")
            agent = _pick(cols, "assigned_agent", "owner", "agent_owner")
            status = _pick(cols, "status")
            enabled = _pick(cols, "enabled", "is_enabled")
            clauses = []
            if enabled:
                clauses.append(f"COALESCE(CAST({_qi(enabled)} AS INTEGER),1)=1")
            if status:
                clauses.append(f"lower(COALESCE(CAST({_qi(status)} AS TEXT),'active')) NOT IN ('disabled','cancelled','superseded','completed','failed')")
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            for r in src.execute(f"SELECT * FROM {_qi(table)} {where}"):
                domain = str(r[domainc] if domainc and r[domainc] else default_domain).lower()
                risk_val = str(r[risk] if risk and r[risk] is not None else "LOW")
                type_val = str(r[typ] if typ and r[typ] is not None else "")
                owner = str(r[agent] if agent and r[agent] else "POLARIS").upper()
                try:
                    priority = int(r[pri] if pri and r[pri] is not None else 7)
                except Exception:
                    priority = 7
                cur = dst.execute(
                    "INSERT INTO council_task_ledger(source_table,source_id,title,"
                    "description,domain,risk_tier,priority,owner,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_table,source_id) DO NOTHING",
                    (table, r[idc], str(r[titlec]),
                     str(r[desc] if desc and r[desc] is not None else ""),
                     domain, _risk_tier(risk_val, domain, type_val), priority,
                     owner, now, now))
                n += max(cur.rowcount, 0)
        dst.commit()
    except sqlite3.OperationalError:
        dst.rollback()
        return 0
    finally:
        src.close()
        dst.close()
    return n


def transition(canonical_id: int, to_phase: str, *, agent: str = "",
               reason: str = "", inputs: Any = None, outputs: Any = None,
               next_action: str = "", db_path: Optional[Path] = None,
               con: Optional[sqlite3.Connection] = None) -> bool:
    """Persist EVERY state change with full provenance. Restart resumes from
    the durable phase — nothing is re-debated or duplicated."""
    if to_phase not in PHASES:
        return False
    own = con is None
    c = con or connect(db_path)
    now = time.time()
    try:
        row = c.execute("SELECT phase FROM council_task_ledger WHERE canonical_id=?",
                        (canonical_id,)).fetchone()
        if not row:
            return False
        c.execute("INSERT INTO council_task_transitions(canonical_id, ts, agent,"
                  " from_phase, to_phase, reason, inputs, outputs, next_action)"
                  " VALUES(?,?,?,?,?,?,?,?,?)",
                  (canonical_id, now, agent, row["phase"], to_phase, reason[:300],
                   json.dumps(inputs, default=str)[:2000] if inputs else None,
                   json.dumps(outputs, default=str)[:2000] if outputs else None,
                   next_action[:200]))
        prog = round(PHASES.index(to_phase) / 10 * 100, 1) \
            if to_phase in PHASES[:12] else None
        c.execute("UPDATE council_task_ledger SET phase=?, updated_at=?,"
                  " next_action=?, completed_at=CASE WHEN ?='COMPLETED' THEN ?"
                  " ELSE completed_at END,"
                  " progress_pct=COALESCE(?,progress_pct)"
                  " WHERE canonical_id=?",
                  (to_phase, now, next_action[:200], to_phase, now, prog,
                   canonical_id))
        if own:
            c.commit()
        return True
    finally:
        if own:
            c.close()


def claim(agent: str, canonical_id: Optional[int] = None,
          lease_sec: float = DEFAULT_LEASE_SEC,
          db_path: Optional[Path] = None) -> Optional[dict]:
    """Atomic lease claim. Weighted-fair pick when no id given: priority, then
    age, blocked last, and domain rotation so one task can't starve the queue."""
    c = connect(db_path)
    now = time.time()
    try:
        if canonical_id is None:
            last = c.execute(
                "SELECT domain FROM council_task_ledger WHERE claimed_by=? "
                "ORDER BY updated_at DESC LIMIT 1", (agent,)).fetchone()
            last_dom = last["domain"] if last else ""
            # COUNCIL_CANARY_LATCH_20260728: the durable build-plane state is
            # authoritative. The env var may only FORCE canary on; it can no
            # longer be the reason the system is permanently stuck in it.
            canary_only = _canary_only_mode(c)
            canary_clause = " AND lower(title) LIKE '%council stage rail canary%'" if canary_only else ""
            row = c.execute(
                "SELECT canonical_id FROM council_task_ledger "
                "WHERE phase IN ('OPEN','FAILED_RETRYABLE','BLOCKED_TRANSIENT') "
                "AND blocker_code IS NOT 'NO_HANDLER' "
                "AND (claimed_by IS NULL OR lease_expires_at < ?) "
                + canary_clause +
                " ORDER BY priority ASC, (domain=?) ASC, created_at ASC LIMIT 1",
                (now, last_dom)).fetchone()
            if not row:
                return None
            canonical_id = row["canonical_id"]
        cur = c.execute(
            "UPDATE council_task_ledger SET claimed_by=?, claimed_at=?,"
            " lease_expires_at=?, heartbeat_at=?, phase='CLAIMED', updated_at=?"
            " WHERE canonical_id=? AND (claimed_by IS NULL OR claimed_by=?"
            " OR lease_expires_at < ?)",
            (agent, now, now + lease_sec, now, now, canonical_id, agent, now))
        if cur.rowcount == 0:
            return None
        transition(canonical_id, "CLAIMED", agent=agent,
                   reason="atomic lease acquired", con=c)
        c.commit()
        r = c.execute("SELECT * FROM council_task_ledger WHERE canonical_id=?",
                      (canonical_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def heartbeat(canonical_id: int, agent: str,
              lease_sec: float = DEFAULT_LEASE_SEC,
              db_path: Optional[Path] = None) -> None:
    c = connect(db_path)
    try:
        c.execute("UPDATE council_task_ledger SET heartbeat_at=?,"
                  " lease_expires_at=? WHERE canonical_id=? AND claimed_by=?",
                  (time.time(), time.time() + lease_sec, canonical_id, agent))
        c.commit()
    finally:
        c.close()


def release_expired_leases(db_path: Optional[Path] = None) -> int:
    """A dead or stalled agent loses its lease automatically."""
    c = connect(db_path)
    now = time.time()
    n = 0
    try:
        for r in c.execute(
                "SELECT canonical_id, claimed_by, phase FROM council_task_ledger "
                "WHERE claimed_by IS NOT NULL AND lease_expires_at < ? "
                "AND phase NOT IN ('COMPLETED','FAILED_FINAL','ROLLED_BACK',"
                "'SUPERSEDED','NEEDS_OPERATOR')", (now,)).fetchall():
            transition(r["canonical_id"], "OPEN", agent="LEASE_REAPER",
                       reason=f"lease expired (holder={r['claimed_by']} "
                              f"phase={r['phase']})", con=c)
            c.execute("UPDATE council_task_ledger SET claimed_by=NULL,"
                      " claimed_at=NULL, lease_expires_at=NULL,"
                      " retry_count=retry_count+1 WHERE canonical_id=?",
                      (r["canonical_id"],))
            n += 1
        c.commit()
    finally:
        c.close()
    return n


def enforce_progress(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Anti-stagnation supervisor.
    - unclaimed beyond one cycle ⇒ priority escalation
    - same active phase beyond deadline ⇒ BLOCKED_TRANSIENT with blocker code
    - retry_count>=2 ⇒ reroute flag; >=3 ⇒ decompose into a smaller work item
    - still blocked ⇒ ONE deduplicated NEEDS_YOU operator request"""
    c = connect(db_path)
    now = time.time()
    out = {"escalated": 0, "blocked": 0, "rerouted": 0, "decomposed": 0,
           "needs_operator": 0}
    try:
        out["escalated"] = c.execute(
            "UPDATE council_task_ledger SET priority=0,"
            " updated_at=? WHERE phase='OPEN' AND claimed_by IS NULL"
            " AND priority>0 AND updated_at < ?",
            (now, now - UNCLAIMED_ESCALATE_SEC)).rowcount
        for r in c.execute(
                "SELECT canonical_id, phase, retry_count, title, domain,"
                " risk_tier, priority FROM council_task_ledger WHERE phase IN "
                "('CLAIMED','RESEARCHING','EVIDENCE_READY','PROPOSING',"
                "'DEBATING','GATED','PATCH_READY','APPLYING','VERIFYING')"
                " AND updated_at < ?", (now - PHASE_DEADLINE_SEC,)).fetchall():
            cid, rc = r["canonical_id"], int(r["retry_count"] or 0)
            if rc >= 3:
                c.execute("INSERT INTO council_task_ledger(source_table, source_id,"
                          " title, description, domain, risk_tier, priority,"
                          " created_at, updated_at) VALUES('decomposed',?,?,?,?,?,?,?,?)"
                          " ON CONFLICT(source_table, source_id) DO NOTHING",
                          (cid, f"[SUBTASK] {r['title'][:100]} — first blocking step",
                           f"Decomposed from canonical {cid} after {rc} failures",
                           r["domain"], r["risk_tier"],
                           max(1, int(r["priority"]) - 1), now, now))
                transition(cid, "NEEDS_OPERATOR", agent="ANTI_STAGNATION",
                           reason=f"3+ failures; decomposed; operator decision "
                                  f"needed", con=c)
                c.execute("INSERT INTO council_needs_operator(canonical_id, ts,"
                          " decision_needed, context) VALUES(?,?,?,?)"
                          " ON CONFLICT(canonical_id) DO NOTHING",
                          (cid, now,
                           f"Task '{r['title'][:80]}' failed {rc}x in {r['phase']}."
                           f" Approve decomposed subtask, reassign, or supersede.",
                           r["phase"]))
                out["decomposed"] += 1
                out["needs_operator"] += 1
            elif rc >= 2:
                c.execute("UPDATE council_task_ledger SET blocker_code="
                          "'REROUTE_ALT_MODEL', retry_count=retry_count+1,"
                          " updated_at=? WHERE canonical_id=?", (now, cid))
                transition(cid, "BLOCKED_TRANSIENT", agent="ANTI_STAGNATION",
                           reason="2 transient failures → reroute to alternate "
                                  "provider", con=c)
                out["rerouted"] += 1
            else:
                c.execute("UPDATE council_task_ledger SET blocker_code="
                          "'PHASE_DEADLINE', retry_count=retry_count+1,"
                          " updated_at=? WHERE canonical_id=?", (now, cid))
                transition(cid, "BLOCKED_TRANSIENT", agent="ANTI_STAGNATION",
                           reason=f"phase {r['phase']} exceeded "
                                  f"{PHASE_DEADLINE_SEC:.0f}s deadline", con=c)
                out["blocked"] += 1
        c.commit()
    finally:
        c.close()
    return out


def get(canonical_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    c = connect(db_path)
    try:
        r = c.execute("SELECT * FROM council_task_ledger WHERE canonical_id=?",
                      (canonical_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def attach(canonical_id: int, *, evidence_id: Optional[int] = None,
           proposal_id: Optional[int] = None, patch_id: Optional[int] = None,
           verification: Optional[str] = None,
           db_path: Optional[Path] = None) -> None:
    c = connect(db_path)
    try:
        if evidence_id is not None:
            r = c.execute("SELECT evidence_ids FROM council_task_ledger"
                          " WHERE canonical_id=?", (canonical_id,)).fetchone()
            ids = json.loads(r["evidence_ids"] or "[]") if r else []
            ids.append(evidence_id)
            c.execute("UPDATE council_task_ledger SET evidence_ids=?"
                      " WHERE canonical_id=?", (json.dumps(ids), canonical_id))
        if proposal_id is not None:
            c.execute("UPDATE council_task_ledger SET proposal_id=?"
                      " WHERE canonical_id=?", (proposal_id, canonical_id))
        if patch_id is not None:
            c.execute("UPDATE council_task_ledger SET patch_id=?"
                      " WHERE canonical_id=?", (patch_id, canonical_id))
        if verification is not None:
            c.execute("UPDATE council_task_ledger SET verification_result=?"
                      " WHERE canonical_id=?", (verification[:300], canonical_id))
        c.commit()
    finally:
        c.close()


# ── DURABLE CANARY GATE (COUNCIL_CANARY_LATCH_20260728) ─────────────────────
def _canary_only_mode(conn) -> bool:
    """Read the DURABLE build-plane state, not a hardcoded env default.

    Previously: os.getenv("COUNCIL_CANARY_MODE", "1") -- defaulting to ON in
    code meant unsetting the environment variable released nothing and the
    backlog was permanently unclaimable.

    Now: once a canary is verified and the latch is set to BUILD_READY, normal
    tasks become claimable and STAY claimable across launches. The environment
    variable is retained only as an explicit override to force canary mode ON
    (an operator safety brake); it can never be the cause of a permanent stall.
    """
    import os as _os
    try:
        from core.council_stage_contract import canary_only_mode as _com
    except Exception:
        try:
            from council_stage_contract import canary_only_mode as _com  # type: ignore
        except Exception:
            _com = None
    forced = str(_os.getenv("COUNCIL_CANARY_MODE", "")).strip()
    if forced == "1":
        return True          # explicit operator brake
    if _com is None:
        return forced == "1"  # contract unavailable: honour explicit value only
    try:
        return bool(_com(conn))
    except Exception:
        return True          # fail closed
