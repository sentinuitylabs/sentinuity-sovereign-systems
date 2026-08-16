# coding: utf-8
"""
services/council_autobuilder.py — COUNCIL_AUTOBUILD_20260723

The engine that converts council architecture into completed work. Each cycle:
  release expired leases → import legacy task sources → anti-stagnation →
  claim next task (atomic lease) → run its phase machine:

  RESEARCHING   evidence persisted from REAL DB/file probes (never invented)
  EVIDENCE_READY→PROPOSING  polaris_proposals row created
  DEBATING      debate_quorum (degraded quorum, cooldown, attempt identity)
  GATED         apply_policy capability matrix (Tier A/B/C)
  PATCH_READY   handler generates the patch content + code_patches row
  APPLYING      backup → write → compile → smoke test → rollback on ANY failure
  VERIFYING     post-apply verification callable
  COMPLETED     build_retrospectives row written

Tier B ⇒ NEEDS_OPERATOR with a single explicit decision request.
Tier C ⇒ FAILED_FINAL, never applied. Restart resumes from durable phase.
"""
from __future__ import annotations
import os
import json, py_compile, re, shutil, sqlite3, subprocess, sys, time, tempfile, difflib
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
BUILD_DB_PATH = ROOT / "sentinuity_build.db"
MARKET_DB_PATH = ROOT / "sentinuity_matrix.db"
DB_PATH = BUILD_DB_PATH
BACKUP_DIR = ROOT / "backups" / "council_autobuild"

from services import council_task_ledger as ledger
from services import apply_policy
from services import golden_latch_gate
from services import debate_quorum
from services import operator_approval

AGENT = "POLARIS"

AUX_SCHEMA = """
CREATE TABLE IF NOT EXISTS council_task_evidence(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    data TEXT,
    sample_size INTEGER,
    freshness_sec REAL,
    confidence REAL,
    methodology TEXT,
    limitations TEXT
);
CREATE TABLE IF NOT EXISTS council_capability_gaps(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id INTEGER UNIQUE,
    created_at REAL NOT NULL,
    title TEXT NOT NULL,
    requested_capability TEXT NOT NULL,
    status TEXT DEFAULT 'OPEN',
    retry_count INTEGER DEFAULT 0,
    last_seen_at REAL
);
CREATE TABLE IF NOT EXISTS code_patches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    proposal_id INTEGER,
    canonical_task_id INTEGER,
    target_file TEXT NOT NULL,
    patch_kind TEXT DEFAULT 'full_file',
    patch_path TEXT,
    backup_path TEXT,
    diff_chars INTEGER,
    tier TEXT,
    status TEXT DEFAULT 'GENERATED',
    applied_at REAL,
    rolled_back_at REAL,
    test_result TEXT,
    verify_result TEXT
);
"""


def _con(db_path: Optional[Path] = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or BUILD_DB_PATH), timeout=15.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def _ensure_columns(c: sqlite3.Connection, table: str, specs: Dict[str, str]) -> None:
    """Add columns required by this capability without replacing legacy data.

    Several Sentinuity generations used the same table names with different
    contracts. CREATE TABLE IF NOT EXISTS cannot upgrade those tables, so the
    production path must introspect and add only missing columns.
    """
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, decl in specs.items():
        if name not in cols:
            q = '"' + name.replace('"', '""') + '"'
            c.execute(f"ALTER TABLE {table} ADD COLUMN {q} {decl}")


def ensure_schema(db_path: Optional[Path] = None) -> None:
    ledger.ensure_schema(db_path)
    debate_quorum.ensure_schema(db_path)
    c = _con(db_path)
    try:
        c.executescript(AUX_SCHEMA)
        c.execute("""CREATE TABLE IF NOT EXISTS polaris_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_hash TEXT UNIQUE,
            proposal_type TEXT, proposal_text TEXT, suggested_action TEXT,
            confidence REAL DEFAULT 0.0, metrics_json TEXT,
            status TEXT DEFAULT 'open', created_at REAL, last_seen_at REAL,
            seen_count INTEGER DEFAULT 1)""")
        c.execute("""CREATE TABLE IF NOT EXISTS build_retrospectives (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patch_id INTEGER,
            journal_id INTEGER UNIQUE, proposal_id INTEGER,
            inspiration_id INTEGER, target_file TEXT, applied_at REAL,
            outcome TEXT, what_changed TEXT, decision_provenance TEXT,
            runtime_notes TEXT, created_at REAL)""")

        _ensure_columns(c, "council_task_evidence", {
            "canonical_id": "INTEGER", "kind": "TEXT", "summary": "TEXT",
            "data": "TEXT", "sample_size": "INTEGER",
            "freshness_sec": "REAL", "confidence": "REAL",
            "methodology": "TEXT", "limitations": "TEXT"})
        _ensure_columns(c, "code_patches", {
            "created_at": "REAL", "canonical_task_id": "INTEGER",
            "patch_kind": "TEXT DEFAULT 'full_file'", "patch_path": "TEXT",
            "backup_path": "TEXT", "diff_chars": "INTEGER", "tier": "TEXT",
            "rolled_back_at": "REAL", "test_result": "TEXT",
            "verify_result": "TEXT"})
        _ensure_columns(c, "polaris_proposals", {
            "proposal_hash": "TEXT", "proposal_type": "TEXT",
            "proposal_text": "TEXT", "suggested_action": "TEXT",
            "confidence": "REAL DEFAULT 0.0", "metrics_json": "TEXT",
            "status": "TEXT DEFAULT 'open'", "created_at": "REAL",
            "last_seen_at": "REAL", "seen_count": "INTEGER DEFAULT 1"})
        _ensure_columns(c, "build_retrospectives", {
            "patch_id": "INTEGER", "proposal_id": "INTEGER",
            "target_file": "TEXT", "applied_at": "REAL", "outcome": "TEXT",
            "what_changed": "TEXT", "decision_provenance": "TEXT",
            "runtime_notes": "TEXT", "created_at": "REAL"})
        c.execute("CREATE INDEX IF NOT EXISTS cte_canonical_id ON council_task_evidence(canonical_id)")
        c.execute("CREATE INDEX IF NOT EXISTS cp_canonical_task ON code_patches(canonical_task_id)")
        c.commit()
    finally:
        c.close()


# ── Task handler registry ───────────────────────────────────────────────────
# A handler receives (task, ctx) and returns:
#   research()  -> evidence dict (summary/data/sample_size/…)
#   propose()   -> proposal dict (text/action/files/test_cmd)
#   build()     -> {target_file, new_content, test: Callable[[Path],bool],
#                   verify: Callable[[Path],bool]}
HANDLERS: Dict[str, Callable] = {}


def register_handler(match_substr: str):
    def deco(fn):
        HANDLERS[match_substr.lower()] = fn
        return fn
    return deco


def _find_handler(task) -> Optional[Callable]:
    """F1: production routing resolves on handler_key ONLY.

    Titles are display strings. The legacy substring table below is consulted
    only for rows that pre-date the migration and still have no handler_key.
    """
    if not isinstance(task, dict):
        task = {"title": task}
    try:
        from services.council_handler_registry import resolve as _resolve
        fn, _key = _resolve(task)
        if fn is not None:
            return fn
    except Exception:
        pass
    t = (task.get("title") or "").lower()
    for k, fn in HANDLERS.items():
        if k in t:
            return fn
    return None


# ── Built-in handler: THE PROOF TASK — canonical substrate chart source ─────
@register_handler("substrate chart")
def substrate_chart_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    db = ctx["market_db_path"]
    target = Path(ctx.get("ui_root", ROOT)) / "ui" / "substrate_node.py"

    def research() -> Dict[str, Any]:
        c = _con(db)
        try:
            def rows(t):
                try:
                    return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:
                    return -1
            canon, legacy = rows("substrate_positions"), rows("substrate_paper_positions")
        finally:
            c.close()
        return {"kind": "db_probe",
                "summary": f"substrate_positions rows={canon}; "
                           f"substrate_paper_positions rows={legacy}; chart "
                           f"currently selects by table EXISTENCE, so a "
                           f"populated canonical table is hidden by an empty "
                           f"legacy one.",
                "data": {"canonical_rows": canon, "legacy_rows": legacy},
                "sample_size": max(canon, 0) + max(legacy, 0),
                "confidence": 0.95,
                "methodology": "direct COUNT(*) of both schemas",
                "limitations": "row counts only; column contract checked at build"}

    def propose(evidence: dict) -> Dict[str, Any]:
        src = target.read_text(encoding="utf-8", errors="ignore")
        return {"proposal_type": "ui_fix",
                "proposal_text": "Select the Substrate cadence chart source by "
                                 "POPULATED canonical rows via "
                                 "substrate_history_adapter.select_cadence_table, "
                                 "not by table existence.",
                "suggested_action": "patch ui/substrate_node.py chart call",
                "files": ["ui/substrate_node.py"],
                "test_cmd": "py_compile + adapter selection assertion",
                "current_has_defect":
                    'table="substrate_paper_positions" if _table_exists(' in src}

    def build() -> Dict[str, Any]:
        src = target.read_text(encoding="utf-8", errors="ignore")
        defect = ('table="substrate_paper_positions" if '
                  '_table_exists("substrate_paper_positions") else '
                  '"substrate_positions",')
        fix = ("table=__import__('wallets.substrate_history_adapter', "
               "fromlist=['select_cadence_table'])"
               ".select_cadence_table(str(DB_PATH)),  "
               "# COUNCIL_AUTOBUILD_20260723: authority = populated rows, "
               "not existence")
        if defect not in src:
            if ("select_cadence_table" in src or
                    ("load_substrate_position_history" in src and "records=" in src)):
                return {"already_applied": True, "target_file": target}
            raise RuntimeError("defect pattern not found and fix absent — "
                               "file drifted; refusing blind patch")
        new = src.replace(defect, fix, 1)

        def test(path: Path) -> bool:
            py_compile.compile(str(path), doraise=True)
            from wallets.substrate_history_adapter import select_cadence_table
            return select_cadence_table(str(db)) == "substrate_positions"

        def verify(path: Path) -> bool:
            s = path.read_text(encoding="utf-8", errors="ignore")
            return ("select_cadence_table" in s) and (defect not in s)

        return {"target_file": target, "new_content": new,
                "test": test, "verify": verify}

    return {"research": research, "propose": propose, "build": build}


# ── Generic handler: schema-selection-defect class (NOT exact-string) ───────
_EXISTENCE_DEFECT_RE = __import__("re").compile(
    r'table\s*=\s*"(?P<a>\w+)"\s+if\s+_table_exists\(\s*"(?P=a)"\s*\)'
    r'\s+else\s+"(?P<b>\w+)"\s*,')


@register_handler("schema-selection defect")
@register_handler("table existence authority")
def generic_schema_authority_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    """Engine-general: inspects the task-declared target module, DETECTS the
    existence-as-authority anti-pattern by structure (regex over arbitrary
    table names), generates the patch, and fails safely (raises → BLOCKED)
    when no grounded match exists."""
    db = ctx["market_db_path"]
    tf = (task.get("description") or "").strip()
    m = __import__("re").search(r"target_file=(\S+)", tf)
    if not m:
        raise RuntimeError("no target_file declared in task description")
    target = Path(ctx.get("ui_root", ROOT)) / m.group(1)

    def research() -> Dict[str, Any]:
        src = target.read_text(encoding="utf-8", errors="ignore")
        hit = _EXISTENCE_DEFECT_RE.search(src)
        return {"kind": "code_inspection",
                "summary": (f"existence-authority defect "
                            f"{'FOUND tables=' + hit.group('a') + '/' + hit.group('b') if hit else 'NOT FOUND'}"
                            f" in {target.name}"),
                "data": {"found": bool(hit)}, "confidence": 0.9 if hit else 0.2,
                "methodology": "structural regex over module source",
                "limitations": "single-file scan"}

    def propose(ev: dict) -> Dict[str, Any]:
        if not ev["data"]["found"]:
            raise RuntimeError("no grounded defect — refusing to invent a patch")
        return {"proposal_type": "ui_fix",
                "proposal_text": "Replace table-existence authority with "
                                 "populated-row authority via adapter.",
                "suggested_action": f"generated structural patch for {target.name}",
                "files": [str(target.relative_to(ctx.get('ui_root', ROOT)))],
                "test_cmd": "engine py_compile + structural assertion"}

    def build() -> Dict[str, Any]:
        src = target.read_text(encoding="utf-8", errors="ignore")
        hit = _EXISTENCE_DEFECT_RE.search(src)
        if not hit:
            raise RuntimeError("defect vanished — refusing blind patch")
        fix = ("table=__import__('wallets.substrate_history_adapter', "
               "fromlist=['select_cadence_table'])"
               ".select_cadence_table(str(DB_PATH)),  # generated: "
               "populated-row authority")
        new = src[:hit.start()] + fix + src[hit.end():]

        def test(path: Path) -> bool:
            return not _EXISTENCE_DEFECT_RE.search(
                path.read_text(encoding="utf-8", errors="ignore"))

        def verify(path: Path) -> bool:
            return "select_cadence_table" in path.read_text(
                encoding="utf-8", errors="ignore")

        return {"target_file": target, "new_content": new,
                "test": test, "verify": verify}

    return {"research": research, "propose": propose, "build": build}


# ── The phase machine ───────────────────────────────────────────────────────

@register_handler("intelligence tab canary")
@register_handler("council stage rail canary")
def intelligence_stage_rail_canary_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    target=Path(ctx.get("ui_root",ROOT))/"ui"/"council_build_stage_rail.py"
    def research():
        src=target.read_text(encoding="utf-8") if target.exists() else ""
        return {"kind":"ui_canary_probe","summary":f"stage rail present={target.exists()} revision={'CANARY_REVISION' in src}","data":{"target":str(target)},"sample_size":1,"confidence":1.0,"methodology":"local source inspection","limitations":"transaction canary only"}
    def propose(evidence):
        return {"proposal_type":"ui_canary","proposal_text":"Increment non-functional Council stage-rail canary revision.","suggested_action":"increment CANARY_REVISION","files":["ui/council_build_stage_rail.py"],"test_cmd":"py_compile + revision assertion","backup_planned":True}
    def build():
        src=target.read_text(encoding="utf-8"); m=re.search(r"^CANARY_REVISION=(\d+)$",src,re.M)
        if not m: raise RuntimeError("CANARY_REVISION missing")
        old=int(m.group(1)); new=src[:m.start(1)]+str(old+1)+src[m.end(1):]
        def test(path): py_compile.compile(str(path),doraise=True); return f"CANARY_REVISION={old+1}" in path.read_text(encoding="utf-8")
        def verify(path): return f"CANARY_REVISION={old+1}" in path.read_text(encoding="utf-8")
        return {"target_file":target,"new_content":new,"test":test,"verify":verify}
    return {"research":research,"propose":propose,"build":build}

# ── STAGE CONTRACT WIRING (COUNCIL_STAGE_WIRED_20260728) ────────────────────
# core/council_stage_contract.record_run() previously had ZERO callers. The
# proof-of-work tables were therefore empty no matter how long the system ran,
# and COUNCIL_CANARY_MODE's release condition (DONE_APPLIED_VERIFIED) named a
# state nothing could record.
#
# Every ledger.transition() inside run_task() now routes through _transition(),
# which writes the ledger phase AND a durable stage-contract row. One wrapper
# instead of ~25 call-site edits, so no transition can be missed.
_PHASE_TO_STAGE = {
    "CLAIMED": "CLAIMED",
    "RESEARCHING": "RESEARCHING",
    "EVIDENCE_READY": "EVIDENCE_RECORDED",
    "PROPOSING": "SPECIFIED",
    "DEBATING": "DEBATING",
    "GATED": "AWAITING_APPROVAL",
    "NEEDS_OPERATOR": "AWAITING_APPROVAL",
    "PATCH_READY": "BUILDING",
    "APPLYING": "STAGED",
    "VERIFYING": "VERIFYING",
    "APPLIED": "APPLIED",
    "COMPLETED": "RETROSPECTIVE",
    "ROLLED_BACK": "REJECTED",
    "FAILED_FINAL": "REJECTED",
    "FAILED_RETRYABLE": "BLOCKED",
    "BLOCKED_TRANSIENT": "BLOCKED",
    "BLOCKED_EXTERNAL": "BLOCKED",
}


def _stage_conn(db):
    import sqlite3 as _s
    c = _s.connect(str(db), timeout=15)
    c.execute("PRAGMA busy_timeout=8000")
    return c


def _record_stage(canonical_id, phase, reason=None, outputs=None, db=None,
                  run_id=None, attempt_id=None):
    """Mirror a ledger phase into the durable stage contract.

    F2: a stage-write failure must not break trading, but it must NEVER be
    swallowed silently. It is persisted to council_stage_write_failures with
    the expected and actual schema so drift is visible instead of invisible."""
    stage = _PHASE_TO_STAGE.get(str(phase).upper())
    if stage is None:
        return
    try:
        from core.council_stage_contract import record_run
    except Exception:
        return
    outputs = outputs or {}
    artifact_kind = artifact_ref = None
    for key in ("patch_id", "proposal_id", "evidence_id", "artifact_ref", "file"):
        if outputs.get(key):
            artifact_kind, artifact_ref = key, str(outputs[key])
            break
    try:
        c = _stage_conn(db or DB_PATH)
        try:
            record_run(
                c, f"council_task:{canonical_id}", stage,
                artifact_kind=artifact_kind, artifact_ref=artifact_ref,
                delta_summary=str(reason or "")[:400],
                blocked_reason=(str(reason or "")[:200] if stage in ("BLOCKED", "REJECTED") else None),
                run_id=run_id, actor=AGENT, status=str(phase).upper(),
                attempt_id=attempt_id,
            )
        finally:
            c.close()
    except Exception as exc:
        _record_stage_failure(canonical_id, phase, db or DB_PATH, run_id, exc)


def _record_stage_failure(canonical_id, phase, db, run_id, exc):
    """Persist STAGE_EVIDENCE_WRITE_FAILED. Best-effort, but loud in the log."""
    import logging as _log
    _log.error("STAGE_EVIDENCE_WRITE_FAILED task=%s phase=%s db=%s err=%s",
               canonical_id, phase, db, exc)
    try:
        c = _stage_conn(db)
        try:
            c.execute(
                "CREATE TABLE IF NOT EXISTS council_stage_write_failures("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task_key TEXT,"
                " run_id TEXT, phase TEXT, db_path TEXT, expected_schema TEXT,"
                " actual_columns TEXT, exception TEXT)")
            actual = []
            for tbl in ("council_stage_evidence", "council_task_evidence"):
                try:
                    actual += [f"{tbl}.{r[1]}" for r in
                               c.execute(f"PRAGMA table_info({tbl})")]
                except Exception:
                    pass
            c.execute(
                "INSERT INTO council_stage_write_failures(ts, task_key, run_id,"
                " phase, db_path, expected_schema, actual_columns, exception)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), f"council_task:{canonical_id}", run_id,
                 str(phase), str(db),
                 "council_stage_evidence(task_key,stage,artifact_kind,"
                 "artifact_ref,delta_summary,is_spin,run_id,attempt_id,actor,"
                 "status,blocker,previous_stage,created_at)",
                 ",".join(actual)[:2000], repr(exc)[:400]))
            c.commit()
        finally:
            c.close()
    except Exception:
        pass          # the failure log above is the durable signal of last resort


def _transition(canonical_id, phase, *, agent=None, reason=None, outputs=None,
                db_path=None, **kw):
    """ledger.transition + durable stage record, in that order."""
    attempt_id = kw.pop("attempt_id", None)
    res = ledger.transition(canonical_id, phase, agent=agent, reason=reason,
                            outputs=outputs, db_path=db_path, **kw)
    _record_stage(canonical_id, phase, reason=reason, outputs=outputs,
                  db=db_path, attempt_id=attempt_id)
    return res


def _record_verified(canonical_id, patch_id, verifier_result, db):
    """Explicit VERIFIED stage. safe_patch_apply sets phase='VERIFIED' with a
    verifier_result; the stage contract now records it with the patch as the
    citing artifact, which is what evaluate_canary_release() requires."""
    _record_stage(canonical_id, "VERIFYING", reason="verification started", db=db)
    try:
        from core.council_stage_contract import record_run
        c = _stage_conn(db or DB_PATH)
        try:
            record_run(c, f"council_task:{canonical_id}", "VERIFIED",
                       artifact_kind="patch_id", artifact_ref=str(patch_id),
                       delta_summary=str(verifier_result or "verified")[:400],
                       actor=AGENT, status="VERIFIED")
        finally:
            c.close()
    except Exception:
        pass


def release_canary_if_verified(canonical_id, db=None) -> dict:
    """After a canary completes, evaluate the DURABLE build-plane latch."""
    try:
        from core.council_stage_contract import evaluate_canary_release
        c = _stage_conn(db or DB_PATH)
        try:
            return evaluate_canary_release(c, f"council_task:{canonical_id}")
        finally:
            c.close()
    except Exception as exc:
        return {"state": "UNKNOWN", "released": False, "reason": f"error:{exc}"}



def _rel_target(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        return ""

def _measure_candidate(target: Path, new_content: str) -> Dict[str, Any]:
    """Engine-measured facts only; never trust proposer verification fields."""
    old = ""
    if target.exists():
        old = target.read_text(encoding="utf-8", errors="ignore")
    matcher = difflib.SequenceMatcher(a=old, b=new_content, autojunk=False)
    changed = 0
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != "equal":
            changed += (a1 - a0) + (b1 - b0)
    changed = max(1, int(changed))

    compile_ok = True
    compile_error = ""
    if target.suffix.lower() == ".py":
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".py", delete=False,
                dir=str(target.parent if target.parent.exists() else ROOT)
            ) as tf:
                tf.write(new_content)
                tmp_name = tf.name
            py_compile.compile(tmp_name, doraise=True)
        except Exception as exc:
            compile_ok = False
            compile_error = str(exc)[:300]
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except Exception:
                    pass
    return {"compile_ok": bool(compile_ok), "compile_error": compile_error,
            "diff_chars": changed, "candidate_chars": len(new_content)}

def run_task(canonical_id: int, *, db_path: Optional[Path] = None,
             ctx: Optional[dict] = None,
             model_router: Optional[Callable] = None,
             get_config: Optional[Callable] = None) -> Dict[str, Any]:
    db = db_path or DB_PATH
    ctx = dict(ctx or {}); ctx.setdefault("build_db_path", db)
    ctx.setdefault("market_db_path", MARKET_DB_PATH)
    task = ledger.get(canonical_id, db)
    if not task:
        return {"ok": False, "reason": "NO_TASK"}
    handler = _find_handler(task)
    if not handler:
        # CAPABILITY-GAP RESOLVER: never endlessly reclaim the same unsupported
        # task. Persist one deduplicated gap, increment the task retry count,
        # release its lease, and request one visible operator/council decision.
        c = _con(db)
        try:
            now = time.time()
            c.execute("""INSERT INTO council_capability_gaps(
                canonical_id,created_at,title,requested_capability,status,retry_count,last_seen_at)
                VALUES(?,?,?,?, 'OPEN',1,?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                  retry_count=retry_count+1,last_seen_at=excluded.last_seen_at""",
                (canonical_id,now,str(task.get("title") or "")[:200],
                 "REGISTER_TYPED_HANDLER",now))
            c.execute("""UPDATE council_task_ledger SET retry_count=retry_count+1,
                blocker_code='NO_HANDLER',claimed_by=NULL,claimed_at=NULL,
                lease_expires_at=NULL,updated_at=? WHERE canonical_id=?""",(now,canonical_id))
            c.execute("""INSERT INTO council_needs_operator(canonical_id,ts,decision_needed,context)
                VALUES(?,?,?,?) ON CONFLICT(canonical_id) DO UPDATE SET
                ts=excluded.ts,decision_needed=excluded.decision_needed,context=excluded.context""",
                (canonical_id,now,
                 f"Capability missing for task #{canonical_id}: {str(task.get('title') or '')[:120]}",
                 "Choose: register bounded handler, supersede task, or keep blocked. No automatic code write occurred."))
            c.commit()
        finally:
            c.close()
        # ── SIGNOFF_CAPABILITY_GAP_BRIDGE_20260813 ───────────────────────────
        # DEFECT (causal, source-proven): this path records the gap in
        # `council_capability_gaps` and parks the task with
        # blocker_code='NO_HANDLER'. But the AUTOMATIC REOPEN path,
        # council_handler_registry.restore_capabilities(), reads ONLY
        # `council_capability_gaps_v2 WHERE status='OPEN'`.
        #
        # Two different tables. Consequence: a task parked here is INVISIBLE to
        # the restore mechanism. Registering the missing handler later never
        # reopens it. It stays NEEDS_OPERATOR | NO_HANDLER permanently and
        # requires the manual operator step that restore_capabilities() was
        # specifically built to eliminate. That is the "tasks stuck at
        # NEEDS_OPERATOR | NO_HANDLER" symptom.
        #
        # Minimal safe bridge: ALSO record the gap in the v2 table the restore
        # path actually reads. Additive only - the legacy row above is still
        # written, so nothing that queries the old table regresses.
        #
        # Note on handler_key: restore_capabilities() skips any gap whose key is
        # not in HANDLERS, so an UNMAPPED task behaves exactly as it does today
        # (stays parked, needs an operator). A task WITH a handler_key becomes
        # automatically restorable the moment that handler is registered. This
        # is therefore a strict improvement with no new failure mode.
        try:
            from services.council_handler_registry import (
                record_capability_gap as _rcg, resolve as _resolve_key)
            _missing_key = str(task.get("handler_key") or "").strip().upper()
            if not _missing_key:
                _missing_key = _resolve_key(task)[1]      # 'UNMAPPED' when unknown
            c2 = _con(db)
            try:
                _rcg(c2,
                     original_task_id=int(canonical_id),
                     missing_handler_key=_missing_key,
                     reason="autobuilder: no registered handler for task",
                     required_inputs=str(task.get("title") or "")[:400],
                     expected_outputs="typed handler registration")
            finally:
                c2.close()
        except Exception as _gap_exc:
            # Never let the bridge break the park path - the legacy row and the
            # operator notification above have already been committed.
            import logging as _log   # module has no package-level logger
            _log.warning("CAPABILITY_GAP_V2_BRIDGE_FAILED task=%s err=%s",
                         canonical_id, _gap_exc)

        _transition(canonical_id, "NEEDS_OPERATOR", agent=AGENT,
                          reason="NO_HANDLER capability gap persisted; retry loop stopped",
                          next_action="operator reviews capability gap",
                          db_path=db)
        return {"ok": False, "reason": "NO_HANDLER_NEEDS_OPERATOR"}
    try:
        h = handler(task, ctx)
    except Exception as _hx:
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                          reason=f"handler init refused: {_hx}"[:200],
                          db_path=db)
        return {"ok": False, "reason": f"HANDLER_REFUSED:{_hx}"[:200]}

    # RESEARCHING → evidence persisted
    _transition(canonical_id, "RESEARCHING", agent=AGENT,
                      reason="handler research start", db_path=db)
    try:
        ev = h["research"]()
    except Exception as _rx:
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                          reason=f"research failed safely: {_rx}"[:200],
                          db_path=db)
        return {"ok": False, "reason": f"RESEARCH_REFUSED:{_rx}"[:200]}
    c = _con(db)
    try:
        cur = c.execute(
            "INSERT INTO council_task_evidence(canonical_id, ts, kind, summary,"
            " data, sample_size, confidence, methodology, limitations)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (canonical_id, time.time(), ev.get("kind", "probe"),
             ev["summary"][:400], json.dumps(ev.get("data"), default=str),
             ev.get("sample_size"), ev.get("confidence"),
             ev.get("methodology", "")[:200], ev.get("limitations", "")[:200]))
        evidence_id = cur.lastrowid
        c.commit()
    finally:
        c.close()
    ledger.attach(canonical_id, evidence_id=evidence_id, db_path=db)
    _transition(canonical_id, "EVIDENCE_READY", agent=AGENT,
                      reason=f"evidence #{evidence_id} persisted",
                      outputs={"evidence_id": evidence_id}, db_path=db)

    # PROPOSING → polaris_proposals row (grounded-refusal is SAFE, not a crash)
    try:
        prop = h["propose"](ev)
    except Exception as _px:
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                          reason=f"proposal refused safely: {_px}"[:200],
                          db_path=db)
        return {"ok": False, "reason": f"PROPOSE_REFUSED:{_px}"[:200]}
    c = _con(db)
    try:
        phash = f"autobuild:{canonical_id}:{prop['suggested_action'][:60]}"
        c.execute("INSERT INTO polaris_proposals(proposal_hash, proposal_type,"
                  " proposal_text, suggested_action, confidence, metrics_json,"
                  " status, created_at, last_seen_at)"
                  " VALUES(?,?,?,?,?,?, 'open', ?, ?)"
                  " ON CONFLICT(proposal_hash) DO UPDATE SET"
                  " last_seen_at=excluded.last_seen_at,"
                  " seen_count=seen_count+1",
                  (phash, prop["proposal_type"], prop["proposal_text"],
                   prop["suggested_action"], 0.8,
                   json.dumps({"files": prop["files"]}), time.time(),
                   time.time()))
        proposal_id = c.execute("SELECT id FROM polaris_proposals WHERE"
                                " proposal_hash=?", (phash,)).fetchone()[0]
        c.commit()
    finally:
        c.close()
    ledger.attach(canonical_id, proposal_id=proposal_id, db_path=db)
    _transition(canonical_id, "PROPOSING", agent=AGENT,
                      reason=f"proposal #{proposal_id} created", db_path=db)

    # P0 PRE-GATE — constitutional territory is checked BEFORE content generation.
    _gcv = get_config
    if _gcv is None:
        try:
            from core.schema import get_config_value as _gcv
        except ImportError:
            _gcv = lambda key, default=None: default

    pre_allowed, pre_tier, pre_why = apply_policy.can_autoapply(prop["files"], _gcv)
    if pre_tier == "C":
        _transition(canonical_id, "FAILED_FINAL", agent=AGENT,
                    reason=f"TIER C PRE-BUILD — never autonomous: {pre_why}",
                    db_path=db)
        try:
            c = _con(db); c.execute("UPDATE polaris_proposals SET status='needs_you' WHERE id=?", (proposal_id,)); c.commit(); c.close()
        except Exception:
            pass
        return {"ok": False, "reason": "TIER_C_REFUSED_PRE_BUILD"}

    # Candidate generation is side-effect free by handler contract. Generate it
    # before debate so verification facts can be ENGINE-MEASURED rather than
    # asserted by the proposer.
    try:
        built = h["build"]()
    except Exception as _bx:
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                    reason=f"candidate build refused safely: {_bx}"[:200],
                    db_path=db)
        return {"ok": False, "reason": f"BUILD_REFUSED:{_bx}"[:200]}

    if built.get("already_applied"):
        ledger.attach(canonical_id, verification="PASS_ALREADY_APPLIED", db_path=db)
        _transition(canonical_id, "COMPLETED", agent=AGENT,
                    reason="fix already present on target — verified, no redundant patch generated",
                    db_path=db)
        try:
            c = _con(db); c.execute("UPDATE polaris_proposals SET status='already_applied' WHERE id=?", (proposal_id,)); c.commit(); c.close()
        except Exception:
            pass
        return {"ok": True, "reason": "ALREADY_APPLIED", "proposal_id": proposal_id}

    target: Path = Path(built["target_file"])

    # WRITE-SITE CONTAINMENT — preserve the 12-Aug opening-capable Council
    # boundary.  Debate tier is not a substitute for a hard write allowlist.
    _ALLOWED_ROOTS = tuple(
        (ROOT / r.strip()).resolve() for r in
        (os.getenv("COUNCIL_BUILD_ALLOWED_ROOTS", "ui").split(",")) if r.strip()
    )
    _PROTECTED_NAMES = {
        "execution_engine.py", "live_trading.py", "live_decision_contract.py",
        "price_integrity_contract.py", "ws_price_oracle.py",
        "market_intelligence.py", "ingest_pipeline.py", "system_guardian.py",
        "pattern_live_arming.py", "schema.py", "live_lane_common.py",
    }
    try:
        _resolved = target.resolve()
        _inside = any(_resolved == _root or _root in _resolved.parents
                      for _root in _ALLOWED_ROOTS)
    except Exception:
        _resolved = target
        _inside = False
    if (not _inside) or _resolved.name in _PROTECTED_NAMES:
        _transition(canonical_id, "BLOCKED_EXTERNAL", agent=AGENT,
                    reason=(f"BUILD_CONTAINMENT_DENIED target={target} "
                            f"outside allowlist {[str(r) for r in _ALLOWED_ROOTS]} "
                            f"or protected module — operator approval required"),
                    db_path=db)
        try:
            c = _con(db); c.execute("UPDATE polaris_proposals SET status='needs_you' WHERE id=?", (proposal_id,)); c.commit(); c.close()
        except Exception:
            pass
        return {"ok": False, "reason": "BUILD_CONTAINMENT_DENIED", "target": str(target)}

    if "new_content" not in built:
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                    reason="candidate build returned no new_content", db_path=db)
        return {"ok": False, "reason": "BUILD_NO_CONTENT"}

    measurements = _measure_candidate(target, built["new_content"])
    prop = dict(prop)
    prop["proposal_id"] = proposal_id
    prop["files"] = [_rel_target(target) or str(target)]
    prop["_engine_measurements"] = measurements
    prop["test_cmd"] = str(prop.get("test_cmd") or "handler smoke test")

    # Persist the exact engine-measured candidate context so the parallel
    # debate presenter can hydrate the same facts if it observes the row.
    try:
        c = _con(db)
        c.execute(
            "UPDATE polaris_proposals SET metrics_json=?, status='autobuild_measured' WHERE id=?",
            (json.dumps({"files": prop["files"],
                         "engine_measurements": measurements,
                         "test_cmd": prop["test_cmd"],
                         "canonical_task_id": canonical_id}, default=str),
             proposal_id),
        )
        c.commit(); c.close()
    except Exception as _persist_exc:
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                    reason=f"measurement persistence failed: {_persist_exc}"[:200], db_path=db)
        return {"ok": False, "reason": "MEASUREMENT_PERSIST_FAILED"}

    # DEBATING — hardened quorum consumes only engine-measured facts.
    _transition(canonical_id, "DEBATING", agent=AGENT,
                reason="measured candidate -> hardened quorum", db_path=db)
    verdict = debate_quorum.run_debate(prop, task["risk_tier"],
                                       model_router=model_router, db_path=db)
    if verdict.get("verdict") == "DUPLICATE_SUPPRESSED":
        try:
            c = _con(db); c.execute("UPDATE polaris_proposals SET status='debate_retryable' WHERE id=?", (proposal_id,)); c.commit(); c.close()
        except Exception:
            pass
        _transition(canonical_id, "BLOCKED_TRANSIENT", agent=AGENT,
                    reason="debate cooldown active — duplicate suppressed", db_path=db)
        return {"ok": False, "reason": "COOLDOWN", "verdict": verdict}
    if not verdict.get("consensus"):
        _status = "insufficient_quorum" if verdict.get("quorum") == "INSUFFICIENT_QUORUM" else "debate_retryable"
        try:
            c = _con(db); c.execute("UPDATE polaris_proposals SET status=? WHERE id=?", (_status, proposal_id)); c.commit(); c.close()
        except Exception:
            pass
        _transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                    reason=f"debate rejected ({verdict.get('quorum')})",
                    outputs=verdict, db_path=db)
        return {"ok": False, "reason": "DEBATE_REJECTED", "verdict": verdict}

    # GATED — quorum success still never bypasses territory/operator policy.
    allowed, tier, why = apply_policy.can_autoapply(prop["files"], _gcv)
    if tier == "B" and operator_approval.approval_is_valid(canonical_id, db):
        allowed, why = True, "OPERATOR_APPROVED_TIER_B"
    _transition(canonical_id, "GATED", agent=AGENT,
                reason=f"tier={tier} {why} quorum={verdict.get('quorum')}", db_path=db)
    if tier == "C":
        _transition(canonical_id, "FAILED_FINAL", agent=AGENT,
                    reason=f"TIER C — never autonomous: {why}", db_path=db)
        return {"ok": False, "reason": "TIER_C_REFUSED"}
    if not allowed:
        c = _con(db)
        try:
            c.execute("INSERT INTO council_needs_operator(canonical_id, ts,"
                      " decision_needed, context) VALUES(?,?,?,?)"
                      " ON CONFLICT(canonical_id) DO NOTHING",
                      (canonical_id, time.time(),
                       f"Approve Tier-{tier} patch for {prop['files']} "
                       f"(proposal #{proposal_id}): {prop['suggested_action']}", why))
            c.execute("UPDATE polaris_proposals SET status='needs_you' WHERE id=?", (proposal_id,))
            c.commit()
        finally:
            c.close()
        _transition(canonical_id, "NEEDS_OPERATOR", agent=AGENT,
                    reason=f"tier {tier}: {why}", db_path=db)
        return {"ok": False, "reason": "NEEDS_OPERATOR", "tier": tier}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{target.name}.{stamp}.bak"
    patch_file = BACKUP_DIR / f"{target.name}.{stamp}.new"
    patch_file.write_text(built["new_content"], encoding="utf-8")
    c = _con(db)
    try:
        cur = c.execute(
            "INSERT INTO code_patches(created_at, proposal_id,"
            " canonical_task_id, target_file, patch_path, backup_path,"
            " diff_chars, tier, status) VALUES(?,?,?,?,?,?,?,?, 'GENERATED')",
            (time.time(), proposal_id, canonical_id, str(target),
             str(patch_file), str(backup),
             measurements["diff_chars"], tier))
        patch_id = cur.lastrowid
        c.commit()
    finally:
        c.close()
    ledger.attach(canonical_id, patch_id=patch_id, db_path=db)
    _transition(canonical_id, "PATCH_READY", agent=AGENT,
                      reason=f"patch #{patch_id} generated backup={backup.name}",
                      db_path=db)

    # APPLYING → backup, write, compile+test, rollback on ANY failure
    _transition(canonical_id, "APPLYING", agent=AGENT,
                      reason="backup+write+compile+test", db_path=db)
    shutil.copy2(target, backup)
    test_note = ""
    try:
        target.write_text(built["new_content"], encoding="utf-8")
        # ENGINE-ENFORCED: compile pass is mandatory for .py targets and is
        # never delegated to the handler's test.
        if target.suffix == ".py":
            py_compile.compile(str(target), doraise=True)
        if not built["test"](target):
            raise RuntimeError("smoke test returned False")
        test_note = "compile+smoke PASS"
    except Exception as exc:
        shutil.copy2(backup, target)                      # automatic rollback
        c = _con(db)
        try:
            c.execute("UPDATE code_patches SET status='ROLLED_BACK',"
                      " rolled_back_at=?, test_result=? WHERE id=?",
                      (time.time(), f"FAIL:{exc}"[:200], patch_id))
            c.execute("INSERT INTO build_retrospectives(patch_id, proposal_id,"
                      " target_file, applied_at, outcome, what_changed,"
                      " decision_provenance, created_at)"
                      " VALUES(?,?,?,?, 'ROLLED_BACK', ?, ?, ?)",
                      (patch_id, proposal_id, str(target), time.time(),
                       f"patch failed test: {exc}"[:250],
                       prop["suggested_action"][:200], time.time()))
            c.commit()
        finally:
            c.close()
        try:
            c3 = _con(db); c3.execute("UPDATE polaris_proposals SET status='apply_failed' WHERE id=?", (proposal_id,)); c3.commit(); c3.close()
        except Exception:
            pass
        _transition(canonical_id, "ROLLED_BACK", agent=AGENT,
                          reason=f"apply failed → restored backup: {exc}",
                          db_path=db)
        return {"ok": False, "reason": "ROLLED_BACK", "error": str(exc)[:200]}

    # VERIFYING
    _transition(canonical_id, "VERIFYING", agent=AGENT,
                      reason="post-apply verification", db_path=db)
    ok = False
    try:
        ok = bool(built["verify"](target))
    except Exception:
        ok = False
    c = _con(db)
    try:
        c.execute("UPDATE code_patches SET status=?, applied_at=?,"
                  " test_result=?, verify_result=? WHERE id=?",
                  ("APPLIED" if ok else "VERIFY_FAILED", time.time(),
                   test_note, "PASS" if ok else "FAIL", patch_id))
        c.commit()
    finally:
        c.close()
    if not ok:
        shutil.copy2(backup, target)
        _transition(canonical_id, "ROLLED_BACK", agent=AGENT,
                          reason="post-apply verification failed → rollback",
                          db_path=db)
        return {"ok": False, "reason": "VERIFY_FAILED_ROLLED_BACK"}
    ledger.attach(canonical_id, verification="PASS", db_path=db)

    # COMPLETED + retrospective
    c = _con(db)
    try:
        c.execute("INSERT INTO build_retrospectives(patch_id, proposal_id,"
                  " target_file, applied_at, outcome, what_changed,"
                  " decision_provenance, runtime_notes, created_at)"
                  " VALUES(?,?,?,?, 'APPLIED', ?, ?, ?, ?)",
                  (patch_id, proposal_id, str(target), time.time(),
                   prop["proposal_text"][:250], prop["suggested_action"][:200],
                   f"quorum={verdict['quorum']} conf={verdict['confidence']}",
                   time.time()))
        c.commit()
    finally:
        c.close()
    ledger.attach(canonical_id, verification="DONE_APPLIED_VERIFIED", db_path=db)
    _record_verified(canonical_id, patch_id, verdict, db)
    # The durable stage contract requires explicit APPLIED evidence before
    # RETROSPECTIVE can release the build-plane canary.  APPLYING only means
    # the write began; it must never be treated as proof of a verified apply.
    _record_stage(canonical_id, "APPLIED", reason="patch applied and verified",
                  outputs={"patch_id": patch_id}, db=db)
    try:
        c3 = _con(db); c3.execute("UPDATE polaris_proposals SET status='auto_applied' WHERE id=?", (proposal_id,)); c3.commit(); c3.close()
    except Exception:
        pass
    _transition(canonical_id, "COMPLETED", agent=AGENT,
                      reason="DONE_APPLIED_VERIFIED: applied+tested+verified; retrospective written",
                      outputs={"patch_id": patch_id, "terminal_status": "DONE_APPLIED_VERIFIED",
                               "proposal_id": proposal_id}, db_path=db)
    _latch = release_canary_if_verified(canonical_id, db)
    return {"ok": True, "reason": "DONE_APPLIED_VERIFIED", "patch_id": patch_id,
            "proposal_id": proposal_id, "verdict": verdict,
            "build_plane": _latch}


def _seed_signoff_canary(db: Path) -> int:
    """Ensure one deterministic UI-only task exists for next-launch proof.

    Canary mode intentionally does not delete or rewrite operator tasks. It
    merely creates one canonical, handler-covered task and the ledger claim
    filter makes it the only runnable task until the operator disables
    COUNCIL_CANARY_MODE after DONE_APPLIED_VERIFIED is observed.
    """
    # COUNCIL_CANARY_LATCH_20260728: seed the canary only while the DURABLE
    # build plane still requires one. Once released, this is a no-op instead of
    # re-seeding a canary on every launch.
    _forced = str(os.getenv("COUNCIL_CANARY_MODE", "")).strip()
    if _forced != "1":
        try:
            from core.council_stage_contract import canary_only_mode as _com
            _probe = _con(db)
            try:
                if not _com(_probe):
                    return 0
            finally:
                _probe.close()
        except Exception:
            pass   # contract unavailable -> fail closed, seed the canary
    c = _con(db)
    try:
        now = time.time()
        existing = c.execute(
            "SELECT canonical_id, phase FROM council_task_ledger "
            "WHERE source_table='SIGNOFF_CANARY' AND source_id=1"
        ).fetchone()
        if existing and str(existing[1] or '').upper() not in {
                'OPEN', 'FAILED_RETRYABLE', 'BLOCKED_TRANSIENT'}:
            # A terminal canary cannot be claimed while the durable build plane
            # still requires proof. Re-open only this deterministic UI canary;
            # never rewrite or delete operator work.
            # COUNCIL_LEASE_SCHEMA_20260816 -- this statement aborted every
            # build cycle with "no such column: lease_owner".
            #
            # It sets lease_owner / lease_until / blocker_reason / needs_you.
            # None of those four exist on council_task_ledger. They belong to
            # the polaris_standing_tasks / council_execution_spine family
            # (services/council_execution_spine.py:81), and the statement was
            # copied across table families. Because _seed_signoff_canary() is
            # the FIRST statement of run_cycle() and run_cycle has no inner
            # guard, the exception aborted the cycle before reap / import /
            # claim / run -- so the durable build plane never ran at all, and
            # council_chamber_bridge reported {'seeded':0,'recovered':0,
            # 'events':0} forever.
            #
            # Schema history does not justify adding those columns here: the
            # ledger's own lease vocabulary is claimed_by / claimed_at /
            # lease_expires_at / heartbeat_at, and release_expired_leases()
            # (council_task_ledger.py:314) already defines the intended reset.
            # This mirrors it exactly.
            c.execute(
                "UPDATE council_task_ledger SET phase='OPEN', claimed_by=NULL, "
                "claimed_at=NULL, lease_expires_at=NULL, heartbeat_at=NULL, "
                "blocker_code=NULL, next_action=NULL, updated_at=? "
                "WHERE canonical_id=?",
                (now, int(existing[0])))
            # Phase changes on this ledger are auditable. Reopening the canary
            # is a phase change, so it gets a transition row like any other.
            try:
                c.execute(
                    "INSERT INTO council_task_transitions"
                    "(canonical_id, ts, agent, from_phase, to_phase, reason) "
                    "VALUES(?,?,?,?,?,?)",
                    (int(existing[0]), now, "SIGNOFF_CANARY_SEEDER",
                     str(existing[1] or ""), "OPEN",
                     "canary re-opened: durable build plane still requires proof"))
            except Exception:
                pass
            c.commit()
            return 1
        cur = c.execute(
            "INSERT INTO council_task_ledger(source_table,source_id,title,description,"
            "domain,risk_tier,priority,owner,phase,created_at,updated_at) "
            "VALUES('SIGNOFF_CANARY',1,'Council stage rail canary',"
            "'Increment the non-functional CANARY_REVISION in ui/council_build_stage_rail.py; "
            "compile, verify, retrospect and terminalise.',"
            "'ui','A',0,'POLARIS','OPEN',?,?) "
            "ON CONFLICT(source_table,source_id) DO NOTHING", (now, now))
        c.commit()
        return max(cur.rowcount, 0)
    finally:
        c.close()


def run_cycle(db_path: Optional[Path] = None, *,
              ctx: Optional[dict] = None,
              model_router: Optional[Callable] = None,
              get_config: Optional[Callable] = None) -> Dict[str, Any]:
    db = Path(db_path or BUILD_DB_PATH)
    if not db.exists():
        return {"ok": False, "reason": "BUILD_DB_MISSING_RUN_MIGRATION"}
    # COUNCIL_LEASE_SCHEMA_20260816: canary seeding is a convenience, not a
    # precondition for reap/import/claim/run. It previously had no guard, so a
    # single bad column name in it silenced the entire durable build plane on
    # every cycle. A seeding fault is now named, counted and stepped over.
    seed_error = ""
    try:
        seeded = _seed_signoff_canary(db)
    except Exception as _seed_exc:
        seeded = 0
        seed_error = f"{type(_seed_exc).__name__}:{_seed_exc}"
        print(f"[AUTOBUILDER] canary seeding failed (cycle continues): {seed_error}")
    reaped = ledger.release_expired_leases(db)
    # Intake is read-only against market truth and writes only to build DB.
    imported = ledger.import_sources(db, source_db_path=MARKET_DB_PATH)
    stag = ledger.enforce_progress(db)
    candidate = ledger.claim(AGENT, db_path=db)
    if not candidate:
        return {"reaped": reaped, "imported": imported, "seeded_canary": seeded,
                "seed_error": seed_error,
                "stagnation": stag, "claimed": None, "result": None}
    result = run_task(candidate["canonical_id"], db_path=db, ctx=ctx,
                      model_router=model_router, get_config=get_config)
    return {"reaped": reaped, "imported": imported, "seeded_canary": seeded,
            "seed_error": seed_error,
            "stagnation": stag, "claimed": candidate["canonical_id"], "result": result}


def main() -> None:
    while True:
        try:
            out = run_cycle()
            print(f"[AUTOBUILDER] {json.dumps(out, default=str)[:300]}")
        except Exception as exc:
            print(f"[AUTOBUILDER] cycle error: {exc}")
        time.sleep(max(10, int(os.getenv("COUNCIL_AUTOBUILD_INTERVAL_SEC", "300"))))


if __name__ == "__main__":
    main()
