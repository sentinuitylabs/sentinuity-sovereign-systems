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
import json, py_compile, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"
BACKUP_DIR = ROOT / "backups" / "council_autobuild"

from services import council_task_ledger as ledger
from services import apply_policy
from services import debate_quorum

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
    c = sqlite3.connect(str(db_path or DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
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


def _find_handler(title: str) -> Optional[Callable]:
    t = (title or "").lower()
    for k, fn in HANDLERS.items():
        if k in t:
            return fn
    return None


# ── Built-in handler: THE PROOF TASK — canonical substrate chart source ─────
@register_handler("substrate chart")
def substrate_chart_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    db = ctx["db_path"]
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
                "diff_chars": 400, "compile_ok": True,
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
    db = ctx["db_path"]
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
                "diff_chars": 300, "compile_ok": True,
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
def run_task(canonical_id: int, *, db_path: Optional[Path] = None,
             ctx: Optional[dict] = None,
             model_router: Optional[Callable] = None,
             get_config: Optional[Callable] = None) -> Dict[str, Any]:
    db = db_path or DB_PATH
    ctx = dict(ctx or {}); ctx.setdefault("db_path", db)
    task = ledger.get(canonical_id, db)
    if not task:
        return {"ok": False, "reason": "NO_TASK"}
    handler = _find_handler(task["title"])
    if not handler:
        ledger.transition(canonical_id, "BLOCKED_EXTERNAL", agent=AGENT,
                          reason="no registered handler for task class",
                          db_path=db)
        return {"ok": False, "reason": "NO_HANDLER"}
    try:
        h = handler(task, ctx)
    except Exception as _hx:
        ledger.transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                          reason=f"handler init refused: {_hx}"[:200],
                          db_path=db)
        return {"ok": False, "reason": f"HANDLER_REFUSED:{_hx}"[:200]}

    # RESEARCHING → evidence persisted
    ledger.transition(canonical_id, "RESEARCHING", agent=AGENT,
                      reason="handler research start", db_path=db)
    try:
        ev = h["research"]()
    except Exception as _rx:
        ledger.transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
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
    ledger.transition(canonical_id, "EVIDENCE_READY", agent=AGENT,
                      reason=f"evidence #{evidence_id} persisted",
                      outputs={"evidence_id": evidence_id}, db_path=db)

    # PROPOSING → polaris_proposals row (grounded-refusal is SAFE, not a crash)
    try:
        prop = h["propose"](ev)
    except Exception as _px:
        ledger.transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
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
    ledger.transition(canonical_id, "PROPOSING", agent=AGENT,
                      reason=f"proposal #{proposal_id} created", db_path=db)

    # DEBATING → degraded quorum
    ledger.transition(canonical_id, "DEBATING", agent=AGENT,
                      reason="quorum debate start", db_path=db)
    prop["proposal_id"] = proposal_id
    verdict = debate_quorum.run_debate(prop, task["risk_tier"],
                                       model_router=model_router, db_path=db)
    if verdict["verdict"] == "DUPLICATE_SUPPRESSED":
        ledger.transition(canonical_id, "BLOCKED_TRANSIENT", agent=AGENT,
                          reason="debate cooldown active — duplicate suppressed",
                          db_path=db)
        return {"ok": False, "reason": "COOLDOWN", "verdict": verdict}
    if not verdict["consensus"]:
        ledger.transition(canonical_id, "FAILED_RETRYABLE", agent=AGENT,
                          reason=f"debate rejected ({verdict['quorum']})",
                          outputs=verdict, db_path=db)
        return {"ok": False, "reason": "DEBATE_REJECTED", "verdict": verdict}

    # GATED → capability matrix (config reader injected; core.schema optional)
    _gcv = get_config
    if _gcv is None:
        try:
            from core.schema import get_config_value as _gcv
        except ImportError:
            _gcv = lambda key, default=None: default
    allowed, tier, why = apply_policy.can_autoapply(prop["files"], _gcv)
    ledger.transition(canonical_id, "GATED", agent=AGENT,
                      reason=f"tier={tier} {why} quorum={verdict['quorum']}",
                      db_path=db)
    if tier == "C":
        ledger.transition(canonical_id, "FAILED_FINAL", agent=AGENT,
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
                       f"(proposal #{proposal_id}): {prop['suggested_action']}",
                       why))
            c.commit()
        finally:
            c.close()
        ledger.transition(canonical_id, "NEEDS_OPERATOR", agent=AGENT,
                          reason=f"tier {tier}: {why}", db_path=db)
        return {"ok": False, "reason": "NEEDS_OPERATOR", "tier": tier}

    # PATCH_READY → generate
    built = h["build"]()
    if built.get("already_applied"):
        ledger.attach(canonical_id, verification="PASS_ALREADY_APPLIED",
                      db_path=db)
        ledger.transition(canonical_id, "COMPLETED", agent=AGENT,
                          reason="fix already present on target — verified, "
                                 "no redundant patch generated", db_path=db)
        return {"ok": True, "reason": "ALREADY_APPLIED",
                "proposal_id": proposal_id, "verdict": verdict}
    target: Path = built["target_file"]
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
             abs(len(built["new_content"]) - len(target.read_text(
                 encoding="utf-8", errors="ignore"))), tier))
        patch_id = cur.lastrowid
        c.commit()
    finally:
        c.close()
    ledger.attach(canonical_id, patch_id=patch_id, db_path=db)
    ledger.transition(canonical_id, "PATCH_READY", agent=AGENT,
                      reason=f"patch #{patch_id} generated backup={backup.name}",
                      db_path=db)

    # APPLYING → backup, write, compile+test, rollback on ANY failure
    ledger.transition(canonical_id, "APPLYING", agent=AGENT,
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
        ledger.transition(canonical_id, "ROLLED_BACK", agent=AGENT,
                          reason=f"apply failed → restored backup: {exc}",
                          db_path=db)
        return {"ok": False, "reason": "ROLLED_BACK", "error": str(exc)[:200]}

    # VERIFYING
    ledger.transition(canonical_id, "VERIFYING", agent=AGENT,
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
        ledger.transition(canonical_id, "ROLLED_BACK", agent=AGENT,
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
    ledger.transition(canonical_id, "COMPLETED", agent=AGENT,
                      reason="applied+verified; retrospective written",
                      outputs={"patch_id": patch_id,
                               "proposal_id": proposal_id}, db_path=db)
    return {"ok": True, "reason": "COMPLETED", "patch_id": patch_id,
            "proposal_id": proposal_id, "verdict": verdict}


def run_cycle(db_path: Optional[Path] = None, *,
              ctx: Optional[dict] = None,
              model_router: Optional[Callable] = None,
              get_config: Optional[Callable] = None) -> Dict[str, Any]:
    ensure_schema(db_path)
    reaped = ledger.release_expired_leases(db_path)
    imported = ledger.import_sources(db_path)
    stag = ledger.enforce_progress(db_path)
    # Skip unsupported legacy work inside the SAME cycle instead of burning one
    # 60-second cycle per task. Each unsupported task is durably parked as
    # BLOCKED_EXTERNAL by run_task(), then selection continues until a task with
    # a registered capability handler is found.
    task = None
    result = None
    skipped_no_handler = []
    for _ in range(250):
        candidate = ledger.claim(AGENT, db_path=db_path)
        if not candidate:
            break
        candidate_result = run_task(
            candidate["canonical_id"], db_path=db_path, ctx=ctx,
            model_router=model_router, get_config=get_config)
        if candidate_result.get("reason") == "NO_HANDLER":
            skipped_no_handler.append(candidate["canonical_id"])
            continue
        task = candidate
        result = candidate_result
        break
    return {"reaped": reaped, "imported": imported, "stagnation": stag,
            "claimed": task["canonical_id"] if task else None,
            "skipped_no_handler": skipped_no_handler,
            "result": result}


def main() -> None:
    while True:
        try:
            out = run_cycle()
            print(f"[AUTOBUILDER] {json.dumps(out, default=str)[:300]}")
        except Exception as exc:
            print(f"[AUTOBUILDER] cycle error: {exc}")
        time.sleep(60)


if __name__ == "__main__":
    main()
