#!/usr/bin/env python3
"""
council_execution_spine.py — SIGNOFF_COUNCIL_EXECUTION_SPINE_20260618

The missing engine behind council_build_orchestrator.run_once().

Audit established the council was a scaffold: run_once() seeded roles/tasks,
assigned models, refreshed heartbeats, resumed stale tasks — but NEVER ran a
debate, generated a proposal, evaluated a gate, or applied a patch. code_patches
was 0 forever. This module installs the execution cycle.

Each call to run_execution_cycle():
  1. seed/normalize standing tasks (from council_work_queue / council_world_tasks)
  2. recover stale ACTIVE tasks (heartbeat older than COUNCIL_STALE_ACTIVE_SEC)
  3. select highest-priority runnable task
  4. mark ACTIVE + heartbeat
  5. research step  (real model call via polaris_complete)
  6. debate/convergence step (real model call)
  7. write a proposal row into polaris_proposals
  8. push to Golden Latch Gate (golden_latch_gate.evaluate_proposal)
  9. if gate approves a writable target -> build patch artifact + apply via
     polaris_patch_writer
 10. verify, journal, mark DONE / BLOCKED / FAILED with EXACT reason

HARD SAFETY: this module never applies trading-core files directly. Core risk
classification + the gate decide that. If models are unavailable it marks the
task BLOCKED with the real reason — it NEVER fabricates debate output or patches.
"""
from __future__ import annotations
import os, time, json, logging, sqlite3, traceback
from pathlib import Path

# WIRING_FIX_20260723: os/BASE_DIR were referenced on the NO_API_KEY retry
# path (dotenv reload + key re-check) but never imported/defined, raising
# NameError and silently killing standing-task retries once keys were added.
BASE_DIR = Path(__file__).resolve().parent.parent
from typing import Optional

log = logging.getLogger("council_execution_spine")
_MODEL_LAST_ERROR = None

# ── DB ────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    try:
        from core.schema import get_connection
        return get_connection()
    except Exception:
        c = sqlite3.connect("sentinuity_matrix.db", timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

def _cfg(conn, key, default):
    try:
        r = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return r[0] if r else default
    except Exception:
        return default

def _cfg_int(conn, key, default):
    try: return int(float(_cfg(conn, key, default)))
    except Exception: return default

def _now() -> float:
    return time.time()

# ── standing task schema ────────────────────────────────────────────────────
STANDING_COLS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("source", "TEXT"), ("domain", "TEXT"), ("title", "TEXT"), ("description", "TEXT"),
    ("priority", "INTEGER DEFAULT 5"), ("status", "TEXT DEFAULT 'OPEN'"),
    ("stage", "TEXT DEFAULT 'seeded'"), ("current_owner", "TEXT"), ("assigned_model", "TEXT"),
    ("created_at", "REAL"), ("updated_at", "REAL"), ("started_at", "REAL"),
    ("completed_at", "REAL"), ("blocked_reason", "TEXT"), ("last_error", "TEXT"),
    ("next_action", "TEXT"), ("progress_pct", "REAL DEFAULT 0"), ("vote_state", "TEXT"),
    ("golden_gate_state", "TEXT"), ("proposal_id", "INTEGER"), ("patch_id", "INTEGER"),
    ("artifact_path", "TEXT"), ("file_targets", "TEXT"), ("risk_level", "TEXT DEFAULT 'LOW'"),
    ("launch_run_id", "TEXT"), ("heartbeat_at", "REAL"), ("retry_count", "INTEGER DEFAULT 0"),
    ("max_retries", "INTEGER DEFAULT 3"),
    ("blocker_code", "TEXT"), ("needs_you", "INTEGER DEFAULT 0"),
    ("last_model_error", "TEXT"), ("last_recovered_at", "REAL"),
    # SIGNOFF_ACTIVE_INTEGRATION_20260720 — additive, idempotent via
    # ensure_standing_schema's ALTER-if-missing. Stable inspiration linkage
    # (directive: "Do not rely only on free-text matching").
    ("inspiration_id", "INTEGER"),
    ("operator_priority", "INTEGER DEFAULT 0"),
]

def ensure_standing_schema(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS polaris_standing_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    existing = {r[1] for r in conn.execute("PRAGMA table_info(polaris_standing_tasks)")}
    for name, spec in STANDING_COLS:
        if name not in existing and name != "id":
            try:
                conn.execute(f"ALTER TABLE polaris_standing_tasks ADD COLUMN {name} {spec}")
            except Exception as e:
                log.debug("add col %s: %s", name, e)
    conn.commit()

# core-risk file classification (mirrors golden_latch_gate)
CORE_RISK_MARKERS = (
    "execution_engine", "ws_price_oracle", "market_intelligence", "neural_supervisor",
    "prelaunch", "launch_config", "schema", "wallet", "live_trading", "router",
    "set_live_mode", "kill_live",
)
def _classify_risk(file_targets: str) -> str:
    ft = (file_targets or "").lower()
    if any(m in ft for m in CORE_RISK_MARKERS):
        return "CORE_RISK"
    if any(m in ft for m in ("orchestrator", "queue", "telemetry", "logging", "migration")):
        return "MEDIUM"
    return "LOW"

# ── PHASE: normalize standing tasks from the stale source queues ──────────────
def _standing_table_info(conn):
    """Return (existing_cols:set, notnull_cols:set) for the live table."""
    cols=set(); notnull=set()
    for r in conn.execute("PRAGMA table_info(polaris_standing_tasks)"):
        # PRAGMA: (cid, name, type, notnull, dflt_value, pk)
        cols.add(r[1])
        if r[3] == 1 and r[5] == 0:  # notnull and not primary key
            notnull.add(r[1])
    return cols, notnull

# safe defaults for any NOT NULL column the live table requires but we don't set
_NN_DEFAULTS = {
    "task_type": "COUNCIL_BUILD", "domain": "BUILD", "status": "OPEN", "stage": "seeded",
    "priority": 5, "progress_pct": 0, "risk_level": "LOW", "retry_count": 0,
    "max_retries": 3, "created_at": 0.0, "updated_at": 0.0, "source": "migration",
    "title": "untitled", "phase": "DISCOVER",
}

def _insert_standing(conn, existing_cols, notnull_cols, values: dict):
    """Insert using only columns that exist; fill required NOT NULL cols with defaults."""
    row = {k: v for k, v in values.items() if k in existing_cols}
    # ensure every NOT NULL col is present
    for col in notnull_cols:
        if col not in row or row[col] is None:
            row[col] = _NN_DEFAULTS.get(col, "" )
    cols = list(row.keys())
    ph = ",".join("?" for _ in cols)
    conn.execute(f"INSERT INTO polaris_standing_tasks ({','.join(cols)}) VALUES ({ph})",
                 [row[c] for c in cols])

def normalize_standing_tasks(conn, launch_run_id):
    ensure_standing_schema(conn)
    existing, notnull = _standing_table_info(conn)
    inserted = 0
    def _exists(title) -> bool:
        try:
            return bool(conn.execute("SELECT 1 FROM polaris_standing_tasks WHERE title=? LIMIT 1",(title,)).fetchone())
        except Exception:
            return False

    # from durable global standing_tasks. The prior spine ignored this table,
    # leaving ACTIVE operator tasks visible in the UI but never executable.
    try:
        has_global = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='standing_tasks'"
        ).fetchone()
        if has_global:
            gcols = {r[1] for r in conn.execute("PRAGMA table_info(standing_tasks)")}
            title_col = "title" if "title" in gcols else ("task_name" if "task_name" in gcols else None)
            if title_col:
                rows = conn.execute(
                    f"SELECT * FROM standing_tasks WHERE UPPER(COALESCE(status,'ACTIVE')) IN ('ACTIVE','OPEN','READY')"
                ).fetchall()
                for r in rows:
                    t = (r[title_col] or "").strip()
                    if not t or _exists(t):
                        continue
                    task_key = r["task_key"] if "task_key" in r.keys() else None
                    desc = r["description"] if "description" in r.keys() else ""
                    owner = r["owner"] if "owner" in r.keys() else "POLARIS"
                    priority = r["priority"] if "priority" in r.keys() else 5
                    domain = r["domain"] if "domain" in r.keys() else "COUNCIL"
                    acceptance = r["acceptance_criteria"] if "acceptance_criteria" in r.keys() else ""
                    _insert_standing(conn, existing, notnull, {
                        "source": "standing_tasks", "task_type": "COUNCIL_BUILD",
                        "domain": domain or "COUNCIL", "title": t,
                        "description": (desc or "") + (f"\nACCEPTANCE: {acceptance}" if acceptance else ""),
                        "priority": priority or 5, "status": "OPEN", "stage": "seeded",
                        "current_owner": owner or "POLARIS", "risk_level": "LOW",
                        "phase": "DISCOVER", "next_action": "research and debate",
                        "created_at": _now(), "updated_at": _now(),
                        "launch_run_id": launch_run_id,
                    })
                    inserted += 1
                    if task_key and "last_outcome" in gcols:
                        conn.execute(
                            "UPDATE standing_tasks SET last_outcome=?, updated_at=? WHERE task_key=?",
                            ("NORMALIZED_TO_POLARIS_STANDING_TASKS", _now(), task_key),
                        )
    except Exception as e:
        log.warning("normalize standing_tasks FAILED: %s", e)

    # from council_work_queue
    try:
        for r in conn.execute("SELECT title, description, priority, risk_level, assigned_agent, files_touched "
                              "FROM council_work_queue WHERE status='OPEN'"):
            t=(r["title"] or "").strip()
            if not t or _exists(t): continue
            ft=r["files_touched"] if "files_touched" in r.keys() else ""
            _insert_standing(conn, existing, notnull, {
                "source":"council_work_queue","task_type":"COUNCIL_BUILD","domain":"BUILD",
                "title":t,"description":r["description"],"priority":r["priority"] or 5,
                "status":"OPEN","stage":"seeded","current_owner":r["assigned_agent"],
                "file_targets":ft,"risk_level":_classify_risk(ft),"phase":"DISCOVER",
                "created_at":_now(),"updated_at":_now(),"launch_run_id":launch_run_id})
            inserted+=1
    except Exception as e:
        log.warning("normalize council_work_queue FAILED: %s", e); print(f"  [normalize council_work_queue error] {e}")

    # from council_world_tasks (optional legacy/UI source)
    try:
        has_world_tasks = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='council_world_tasks'"
        ).fetchone()
        if has_world_tasks:
            for r in conn.execute("SELECT title, backend_files, agent_owner, risk_tier "
                                  "FROM council_world_tasks WHERE COALESCE(status,'OPEN')!='DONE'"):
                t=(r["title"] or "").strip()
                if not t or _exists(t): continue
                ft=r["backend_files"] if "backend_files" in r.keys() else ""
                _insert_standing(conn, existing, notnull, {
                    "source":"council_world_tasks","task_type":"COUNCIL_WORLD","domain":"WORLD",
                    "title":t,"priority":(r["risk_tier"] or 3),"status":"OPEN","stage":"seeded",
                    "current_owner":r["agent_owner"],"file_targets":ft,"risk_level":_classify_risk(ft),
                    "phase":"DISCOVER","created_at":_now(),"updated_at":_now(),"launch_run_id":launch_run_id})
                inserted+=1
        else:
            log.debug("optional council_world_tasks table absent; continuing with council_work_queue")
    except Exception as e:
        log.warning("normalize optional council_world_tasks source failed: %s", e)

    # recurring Solana edge audit — seeded every launch/cycle if missing.
    # This is the durable standing task requested by operator: always inspect the
    # main Solana interface, recent closes, latch freshness, price integrity,
    # and propose the next edge toward profitability.
    try:
        _sol_title = "Recurring Solana edge audit: main interface profitability research"
        if not _exists(_sol_title):
            _insert_standing(conn, existing, notnull, {
                "source": "SIGNOFF_SOLANA_EDGE_RECURRING",
                "task_type": "SOLANA_EDGE_AUDIT",
                "domain": "SOLANA",
                "title": _sol_title,
                "description": (
                    "Every launch/periodic cycle: audit latest 30/100 paper closes, "
                    "entry_signal_age, entry_price_age_sec, latch_to_open_sec, "
                    "execution_ready_age_sec, peak_pnl_pct, price_integrity failures, "
                    "TAKE_PROFIT/RUNNER/DEAD_TOKEN balance, and propose one low-risk edge improvement."
                ),
                "priority": 1,
                "status": "OPEN",
                "stage": "seeded",
                "current_owner": "POLARIS",
                "file_targets": "services/execution_engine.py,services/price_integrity_contract.py,services/sovereign_hub.py",
                "risk_level": "MEDIUM",
                "phase": "DISCOVER",
                "next_action": "research Solana edge and propose improvement",
                "created_at": _now(),
                "updated_at": _now(),
                "launch_run_id": launch_run_id,
            })
            inserted += 1
    except Exception as e:
        log.warning("normalize recurring solana edge audit FAILED: %s", e); print(f"  [normalize solana edge audit error] {e}")

    conn.commit()
    return inserted

# ── PHASE: recover stale ACTIVE tasks ─────────────────────────────────────────
def recover_stale(conn) -> int:
    stale_sec = _cfg_int(conn, "COUNCIL_STALE_ACTIVE_SEC", 900)
    cut = _now() - stale_sec
    n = 0
    try:
        rows = conn.execute("""SELECT id, retry_count, max_retries FROM polaris_standing_tasks
            WHERE status IN ('ACTIVE','RESEARCHING','DEBATING','PROPOSING','APPLYING','VERIFYING')
            AND COALESCE(heartbeat_at,0) < ?""", (cut,)).fetchall()
        for r in rows:
            rc = (r["retry_count"] or 0) + 1
            if rc > (r["max_retries"] or 3):
                conn.execute("UPDATE polaris_standing_tasks SET status='BLOCKED', "
                             "blocked_reason='MAX_RETRIES_EXCEEDED_AFTER_STALE', updated_at=? WHERE id=?",
                             (_now(), r["id"]))
            else:
                conn.execute("UPDATE polaris_standing_tasks SET status='OPEN', stage='seeded', "
                             "retry_count=?, last_error='recovered from stale active', updated_at=? WHERE id=?",
                             (rc, _now(), r["id"]))
            n += 1
        conn.commit()
    except Exception as e:
        log.debug("recover_stale: %s", e)
    return n


TRANSIENT_BLOCK_PREFIXES = (
    "MODEL_CALL", "MODEL_CLIENT", "MODEL_EMPTY", "DB_BUSY", "PARSE_ERROR",
    "PROPOSAL_WRITE_FAILED", "GATE_UNAVAILABLE", "PATCH_WRITER_UNAVAILABLE",
    "SEARCH_ZERO_RESULTS",
)


def recover_transient_blocked(conn) -> int:
    """Retry transiently blocked tasks after a cooldown instead of leaving the
    council permanently stuck at BLOCKED when API/network/search recovers."""
    cooldown = _cfg_int(conn, "COUNCIL_TRANSIENT_BLOCK_RETRY_SECONDS", 900)
    cut = _now() - cooldown
    n = 0
    try:
        rows = conn.execute("""
            SELECT id, blocked_reason, retry_count, max_retries, updated_at
            FROM polaris_standing_tasks
            WHERE status='BLOCKED'
              AND COALESCE(updated_at,0) < ?
        """, (cut,)).fetchall()
        for r in rows:
            reason = str(r["blocked_reason"] or "")
            # A missing key is an operator dependency, but once the workspace .env
            # contains it the council should recover automatically, including tasks
            # that previously exhausted retries. Never burn retries while it is absent.
            if reason.startswith("NO_API_KEY"):
                try:
                    from dotenv import load_dotenv
                    load_dotenv(BASE_DIR / ".env", override=True)
                except Exception:
                    pass
                if not os.getenv("OPENAI_API_KEY", "").strip():
                    continue
                conn.execute("""
                    UPDATE polaris_standing_tasks
                    SET status='OPEN', stage='seeded', progress_pct=0, blocked_reason='',
                        blocker_code='', needs_you=0, retry_count=0,
                        last_error='auto-recovered after OPENAI_API_KEY became available',
                        last_recovered_at=?, updated_at=?, next_action='retry research'
                    WHERE id=?
                """, (_now(), _now(), r["id"]))
                n += 1
                continue
            if not any(reason.startswith(p) or p in reason for p in TRANSIENT_BLOCK_PREFIXES):
                continue
            rc = int(r["retry_count"] or 0) + 1
            if rc > int(r["max_retries"] or 3):
                conn.execute("""
                    UPDATE polaris_standing_tasks
                    SET needs_you=1, blocker_code='MAX_RETRIES_TRANSIENT_BLOCK',
                        next_action='operator review / fix dependency',
                        last_error=?, updated_at=?
                    WHERE id=?
                """, (reason, _now(), r["id"]))
            else:
                conn.execute("""
                    UPDATE polaris_standing_tasks
                    SET status='OPEN', stage='seeded', blocked_reason='',
                        blocker_code='', needs_you=0, retry_count=?,
                        last_error='auto-recovered transient blocker: ' || ?,
                        last_recovered_at=?, updated_at=?, next_action='retry research'
                    WHERE id=?
                """, (rc, reason[:300], _now(), _now(), r["id"]))
            n += 1
        conn.commit()
    except Exception as e:
        log.debug("recover_transient_blocked: %s", e)
    return n

# ── PHASE: select highest-priority runnable task ──────────────────────────────
# priority families (lower number = higher priority)
def _family_rank(title: str, domain: str, risk: str) -> int:
    t = (title or "").lower()
    if any(k in t for k in ("launch", "blocker", "critical")): return 1
    if any(k in t for k in ("oracle", "executor", "latch", "price", "freshness")): return 2
    if any(k in t for k in ("council", "self-repair", "build pipeline", "spine")): return 3
    if any(k in t for k in ("copytrade", "substrate", "wallet")): return 4
    if any(k in t for k in ("ui", "world", "panel", "hub")): return 5
    return 6

def select_task(conn) -> Optional[sqlite3.Row]:
    rows = conn.execute("""SELECT * FROM polaris_standing_tasks
        WHERE status='OPEN' AND COALESCE(blocked_reason,'')='' """).fetchall()
    if not rows:
        return None
    ranked = sorted(rows, key=lambda r: (_family_rank(r["title"], r["domain"], r["risk_level"]),
                                         r["priority"] or 5, r["id"]))
    return ranked[0]


# ── SIGNOFF_ACTIVE_INTEGRATION_20260720: fair selection is the ACTIVE path ────
# The fair scheduler (services/standing_task_scheduler.py) prevents a blocked
# or progress-free task from monopolising every cycle, honours operator
# priority, and journals every selection decision. The original select_task()
# above is preserved unchanged and used ONLY as a fallback if the scheduler
# fails unexpectedly — logged and journaled as a degraded state, never silent.
def _select_task_active(conn) -> Optional[sqlite3.Row]:
    try:
        from services.standing_task_scheduler import select_task_fair
        return select_task_fair(conn)
    except Exception as e:
        log.error("[SCHEDULER_DEGRADED] fair scheduler failed (%s: %s) — "
                  "falling back to original monopolistic selector", type(e).__name__, e)
        try:
            from services.standing_task_scheduler import note_fallback_degraded
            note_fallback_degraded(conn, f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return select_task(conn)


def _record_task_progress(conn, task_id, note: str) -> None:
    """Artefact-progress hook into the fair scheduler. Best-effort: progress
    accounting must never be able to break the council cycle itself."""
    try:
        from services.standing_task_scheduler import record_progress
        record_progress(conn, int(task_id), note)
    except Exception as e:
        log.debug("record_progress unavailable: %s", e)


def _note_task_block(conn, task_id, reason: str) -> None:
    try:
        from services.standing_task_scheduler import note_block
        note_block(conn, int(task_id), reason)
    except Exception as e:
        log.debug("note_block unavailable: %s", e)

# ── model call wrapper (honest: returns None if unavailable) ──────────────────
def _classify_model_error(e: Exception | str) -> str:
    msg = str(e)
    low = msg.lower()
    if "ssl" in low or "ssleof" in low or "eof occurred" in low:
        return "MODEL_CALL_SSL_FAIL"
    if "httpsconnectionpool" in low or "max retries" in low or "remote end closed" in low:
        return "MODEL_CALL_NETWORK_FAIL"
    if "rate limit" in low or "429" in low:
        return "MODEL_CALL_RATE_LIMIT"
    if "api key" in low or "unauthorized" in low or "401" in low:
        return "MODEL_CALL_AUTH_FAIL"
    return "MODEL_CALL_FAILED"


def _model(system_prompt: str, user_msg: str, *, code_touch=False, file=None, risk="low") -> Optional[str]:
    global _MODEL_LAST_ERROR
    _MODEL_LAST_ERROR = None
    try:
        from llm_client import polaris_complete, get_last_error as _llm_last_error
    except Exception:
        try:
            from services.llm_client import polaris_complete, get_last_error as _llm_last_error
        except Exception as e:
            _MODEL_LAST_ERROR = f"MODEL_CLIENT_UNAVAILABLE:{e}"
            log.warning("polaris_complete unavailable: %s", e)
            return None
    try:
        res = polaris_complete(system_prompt, user_msg, task_type="council_build",
                               risk_level=risk, code_touch=code_touch, code_touch_file=file,
                               max_tokens=1200, temperature=0.7)
        if res and res.get("text"):
            return res["text"]
        # COUNCIL_LLM_DIAG_20260623: store the REAL reason (HTTP_401 / HTTP_404
        # model_not_found / TIMEOUT / EMPTY_CONTENT_200 / NO_API_KEY) so the
        # blocker is diagnosable, not the generic "MODEL_EMPTY_RESPONSE".
        try:
            _real = _llm_last_error()
        except Exception:
            _real = None
        _MODEL_LAST_ERROR = _real or "MODEL_EMPTY_RESPONSE"
        return None
    except Exception as e:
        code = _classify_model_error(e)
        _MODEL_LAST_ERROR = f"{code}:{str(e)[:220]}"
        log.warning("model call failed: %s", e)
        return None

def _set(conn, tid, **kw):
    kw["updated_at"] = _now(); kw["heartbeat_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in kw)
    conn.execute(f"UPDATE polaris_standing_tasks SET {cols} WHERE id=?", (*kw.values(), tid))
    conn.commit()

def _block(conn, tid, reason):
    reason = str(reason or "UNKNOWN_BLOCKER")
    transient = any(reason.startswith(p) or p in reason for p in TRANSIENT_BLOCK_PREFIXES)
    next_action = (
        "auto-retry after transient cooldown; check API/network/search if repeated"
        if transient else
        "operator review / fix dependency"
    )
    _set(conn, tid,
         status="BLOCKED",
         blocked_reason=reason,
         blocker_code=reason.split(":", 1)[0][:80],
         needs_you=0 if transient else 1,
         last_error=reason,
         next_action=next_action)
    log.warning("[COUNCIL_BLOCKED] id=%s reason=%s", tid, reason)
    _note_task_block(conn, tid, reason)
    return {"task_id": tid, "result": "BLOCKED", "reason": reason}

def _archive_task_report(conn, task, status: str, summary: str, *, research=None, debate=None, metadata=None):
    """Persist Council evidence without making report storage part of task success."""
    try:
        try:
            from services.audit_artifact_store import persist_report
        except ImportError:
            from audit_artifact_store import persist_report
        evidence = {}
        if research is not None:
            evidence["research"] = research
        if debate is not None:
            evidence["debate"] = debate
        result = persist_report(
            conn, source="COUNCIL", report_type="council-build",
            title=f"{task['title']} — {status}", task_id=int(task["id"]),
            task_name=str(task["title"]), status=status, summary=summary,
            evidence=evidence, tags=["council", "build-process"],
            metadata=metadata or {},
        )
        _set(conn, task["id"], artifact_path=result.get("canonical_path"))
        return result
    except Exception as exc:
        log.warning("Council audit artifact persistence failed task=%s: %s", task.get("id"), exc)
        return None


# ── the cycle ─────────────────────────────────────────────────────────────────
def run_execution_cycle(conn=None, launch_run_id: Optional[str] = None,
                        max_tasks_per_cycle: int = 1) -> dict:
    own = conn is None
    if own:
        conn = _connect()
    out = {"normalized": 0, "recovered": 0, "advanced": 0, "blocked": 0, "selected": None, "result": None}
    try:
        if str(_cfg(conn, "COUNCIL_EXECUTION_ENABLED", "1")).strip() in ("0", "false", "False", ""):
            out["result"] = "EXECUTION_DISABLED"
            return out

        out["normalized"] = normalize_standing_tasks(conn, launch_run_id)
        out["recovered"] = recover_stale(conn)
        out["recovered_transient"] = recover_transient_blocked(conn)

        for _ in range(max(1, max_tasks_per_cycle)):
            # SIGNOFF_ACTIVE_INTEGRATION_20260720: fair scheduler is the live
            # selection path; original selector remains as journaled fallback.
            task = _select_task_active(conn)
            if task is None:
                # no runnable task — record exact reason, create self-repair if pipeline broken
                _ensure_self_repair_if_idle(conn, launch_run_id)
                out["result"] = "NO_RUNNABLE_TASKS"
                log.info("[COUNCIL_EXECUTION] no runnable tasks this cycle")
                break

            tid = task["id"]
            out["selected"] = {"id": tid, "title": task["title"], "risk": task["risk_level"]}
            log.info("[COUNCIL_TASK_SELECTED] id=%s priority=%s title=%s", tid, task["priority"], task["title"])
            _set(conn, tid, status="ACTIVE", stage="selected", started_at=_now(),
                 next_action="research", progress_pct=10)

            # ── research ──
            _set(conn, tid, status="RESEARCHING", stage="research", progress_pct=25)
            # SIGNOFF_ACTIVE_INTEGRATION_20260720: durable intake-ledger
            # evidence (with stable inspiration IDs) joins the research prompt.
            # External material is evidence only — the prompt and downstream
            # gates remain unchanged authority-wise.
            intake_evidence, intake_ids = "", []
            try:
                from services.inspiration_intake_ledger import find_for_task
                for _ev in find_for_task(conn, str(task["title"] or ""), limit=3):
                    intake_ids.append(int(_ev["inspiration_id"]))
                    intake_evidence += (
                        f"\nINTAKE#{_ev['inspiration_id']} [{_ev['source_type']}] "
                        f"{str(_ev['source_ref'])[:120]} "
                        f"licence={_ev['licence'] or 'UNRESOLVED'} :: "
                        f"{str(_ev['extracted_concept'] or '')[:300]}")
            except Exception as _ie:
                log.debug("intake evidence unavailable: %s", _ie)
            research = _model(
                "You are a Sentinuity council research agent. Given a build task, produce a concise "
                "technical analysis: what files are involved, what the change should accomplish, and "
                "risks. Do not write code yet. Intake evidence below is inspiration only — never "
                "copy external code; extract principles.",
                f"TASK: {task['title']}\nDESC: {task['description'] or ''}\nFILES: {task['file_targets'] or ''}"
                + (f"\nINTAKE EVIDENCE:{intake_evidence}" if intake_evidence else ""),
                risk="low")
            if research is None:
                out["blocked"] += 1
                out["result"] = _block(conn, tid, _MODEL_LAST_ERROR or "MODEL_CALL_UNAVAILABLE")
                break
            _record_task_progress(conn, tid, "artefact: research analysis produced"
                                  + (f" using intake evidence {intake_ids}" if intake_ids else ""))
            if intake_ids:
                try:
                    from services.inspiration_intake_ledger import mark_research_used
                    mark_research_used(conn, intake_ids, task_id=tid)
                except Exception as _mru:
                    log.debug("mark_research_used: %s", _mru)

            # ── debate / convergence ──
            _set(conn, tid, status="DEBATING", stage="debate", progress_pct=45,
                 vote_state="in_progress")
            debate = _model(
                "You are the Sentinuity council convergence step. Given research, decide whether a code "
                "change is warranted. If yes, respond with a short rationale and, on a line starting "
                "'TARGET:', the single file to change. If code is needed include the full replacement in a "
                "block after a line 'CODE:'. If no change is safe/warranted, respond starting with 'NO_CHANGE:'.",
                f"TASK: {task['title']}\nRESEARCH:\n{research}",
                code_touch=True, file=(task["file_targets"] or None), risk="medium")
            if debate is None:
                out["blocked"] += 1
                out["result"] = _block(conn, tid, _MODEL_LAST_ERROR or "MODEL_CALL_UNAVAILABLE_DEBATE")
                break

            _record_task_progress(conn, tid, "artefact: debate/convergence completed")

            if debate.strip().upper().startswith("NO_CHANGE"):
                _set(conn, tid, status="DONE", stage="complete", completed_at=_now(),
                     progress_pct=100, vote_state="converged_no_change",
                     next_action="none", blocked_reason="")
                _record_task_progress(conn, tid, "artefact: task closed — converged no change")
                _archive_task_report(
                    conn, task, "DONE_NO_CHANGE",
                    "Council converged that no safe code change was warranted.",
                    research=research, debate=debate,
                    metadata={"vote_state": "converged_no_change"},
                )
                out["advanced"] += 1
                out["result"] = "DONE_NO_CHANGE"
                log.info("[COUNCIL_EXECUTION] task %s converged: no change warranted", tid)
                continue

            # ── proposal ──
            _set(conn, tid, status="PROPOSING", stage="proposal", progress_pct=60)
            primary_iid = intake_ids[0] if intake_ids else (
                task["inspiration_id"] if "inspiration_id" in task.keys() else None)
            proposal_id = _write_proposal(conn, task, research, debate,
                                          inspiration_id=primary_iid)
            if proposal_id is None:
                out["blocked"] += 1
                out["result"] = _block(conn, tid, "PROPOSAL_WRITE_FAILED")
                break
            _set(conn, tid, proposal_id=proposal_id,
                 **({"inspiration_id": primary_iid} if primary_iid else {}))
            _record_task_progress(conn, tid, f"artefact: proposal #{proposal_id} written")
            # stable inspiration → debate/proposal linkage (both directions)
            if intake_ids:
                try:
                    from services.inspiration_intake_ledger import link_build_artifacts
                    for _iid in intake_ids:
                        link_build_artifacts(_iid, proposal_id=proposal_id, conn=conn)
                except Exception as _lba:
                    log.debug("link_build_artifacts(proposal): %s", _lba)

            # ── golden latch gate ──
            _set(conn, tid, status="GOLDEN_GATE", stage="gate_review", progress_pct=75)
            gate = _evaluate_gate(proposal_id, conn)
            gstate = gate.get("golden_gate_state", "BLOCKED")
            _set(conn, tid, golden_gate_state=gstate, risk_level=gate.get("risk_level", task["risk_level"]),
                 next_action=gate.get("next_action", ""))
            log.info("[GOLDEN_LATCH_GATE] proposal=%s state=%s risk=%s", proposal_id, gstate, gate.get("risk_level"))

            if gstate in ("NEEDS_OPERATOR", "APPROVED_CORE_MANUAL"):
                _set(conn, tid, status="PAUSED", stage="gate_review", progress_pct=75,
                     blocked_reason="AWAITING_OPERATOR_APPROVAL_GOLDEN_GATE",
                     next_action="operator approval at Golden Latch Gate")
                out["result"] = "AWAITING_OPERATOR"
                break
            if gstate == "REJECTED" or gstate == "BLOCKED":
                out["blocked"] += 1
                out["result"] = _block(conn, tid, f"GATE_{gstate}:{gate.get('blocked_reason','')}")
                break

            # ── apply (only APPROVED_UI_AUTO / PATCH_READY-writable) ──
            patch_id = gate.get("patch_id")
            if gstate == "APPROVED_UI_AUTO" and patch_id:
                _set(conn, tid, status="APPLYING", stage="apply", progress_pct=88, patch_id=patch_id)
                # SIGNOFF_ACTIVE_INTEGRATION_20260720: pre-application record
                # BEFORE any file changes (proposal + inspiration provenance,
                # gate state, target). Best-effort: a retrospective failure
                # must not block a gate-approved safe apply, but it is logged
                # loudly and the missing audit record is surfaced.
                try:
                    from services.build_retrospective import record_pre_application
                    record_pre_application(
                        conn, patch_id=int(patch_id), proposal_id=int(proposal_id),
                        inspiration_id=(int(primary_iid) if primary_iid else None),
                        target_file=str(gate.get("target_file") or task["file_targets"] or ""),
                        operator_state=gstate, compile_note="py_compile enforced by patch writer")
                except Exception as _pre:
                    log.error("[RETROSPECTIVE_MISSING] pre-application record failed "
                              "patch=%s: %s", patch_id, _pre)
                applied = _apply_patch(patch_id, conn)
                # link the patch to its inspirations regardless of outcome
                if intake_ids:
                    try:
                        from services.inspiration_intake_ledger import link_build_artifacts
                        for _iid in intake_ids:
                            link_build_artifacts(_iid, patch_id=int(patch_id), conn=conn)
                    except Exception as _lbp:
                        log.debug("link_build_artifacts(patch): %s", _lbp)
                if not applied.get("ok"):
                    try:
                        from services.build_retrospective import finalize_application
                        finalize_application(conn, patch_id=int(patch_id), applied_ok=False,
                                             detail=str(applied.get("reason", ""))[:400])
                    except Exception as _fin:
                        log.error("[RETROSPECTIVE_MISSING] finalize failed patch=%s: %s",
                                  patch_id, _fin)
                    out["blocked"] += 1
                    out["result"] = _block(conn, tid, f"APPLY_FAILED:{applied.get('reason','')}")
                    break
                _set(conn, tid, status="VERIFYING", stage="verify", progress_pct=95,
                     artifact_path=applied.get("file_path"))
                # post-apply verification + final retrospective status. If this
                # cannot be recorded, DO NOT silently claim a clean apply.
                _retro_status = "RETROSPECTIVE_UNAVAILABLE"
                try:
                    from services.build_retrospective import finalize_application
                    _retro_status = finalize_application(
                        conn, patch_id=int(patch_id), applied_ok=True,
                        detail=f"applied file: {applied.get('file_path','')}")
                except Exception as _fin:
                    log.error("[RETROSPECTIVE_MISSING] finalize failed patch=%s: %s",
                              patch_id, _fin)
                _record_task_progress(conn, tid,
                                      f"artefact: patch #{patch_id} applied; "
                                      f"retrospective={_retro_status}")
                _set(conn, tid, status="DONE", stage="complete", completed_at=_now(),
                     progress_pct=100, next_action="none", blocked_reason="",
                     last_error=("" if _retro_status not in
                                 ("RETROSPECTIVE_UNAVAILABLE",)
                                 else "APPLIED_BUT_RETROSPECTIVE_MISSING"))
                _archive_task_report(
                    conn, task, "DONE_APPLIED",
                    "Council proposal passed its gate, was applied locally, and entered verification.",
                    research=research, debate=debate,
                    metadata={"proposal_id": proposal_id, "patch_id": patch_id,
                              "applied_file": applied.get("file_path"),
                              "golden_gate_state": gstate},
                )
                out["advanced"] += 1
                out["result"] = "DONE_APPLIED"
                log.info("[POLARIS_PATCH_APPLY] patch=%s status=APPLIED task=%s", patch_id, tid)
            else:
                _set(conn, tid, status="PATCH_READY", stage="patch_build", progress_pct=80,
                     next_action="awaiting gate/operator for apply")
                out["result"] = "PATCH_READY_HELD"
                break

        return out
    except Exception as e:
        log.error("run_execution_cycle fatal: %s\n%s", e, traceback.format_exc())
        out["result"] = f"CYCLE_ERROR:{e}"
        return out
    finally:
        if own:
            try: conn.close()
            except Exception: pass

def _write_proposal(conn, task, research, debate, inspiration_id=None) -> Optional[int]:
    try:
        # parse TARGET / CODE
        import re
        tgt = re.search(r'TARGET:\s*([^\n]+)', debate)
        target_file = tgt.group(1).strip() if tgt else (task["file_targets"] or "")
        code = None
        if "CODE:" in debate:
            code = debate.split("CODE:", 1)[1].strip()
        # SIGNOFF_ACTIVE_INTEGRATION_20260720: additive, idempotent column for
        # stable inspiration→proposal linkage. Existing rows/columns untouched.
        try:
            pcols = {r[1] for r in conn.execute("PRAGMA table_info(polaris_proposals)")}
            if "inspiration_id" not in pcols:
                conn.execute("ALTER TABLE polaris_proposals ADD COLUMN inspiration_id INTEGER")
        except Exception as _mig:
            log.debug("proposal inspiration_id migration: %s", _mig)
        try:
            conn.execute("""INSERT INTO polaris_proposals
                (proposal_type, proposal_domain, project_key, proposal_text, suggested_action,
                 rewritten_code, axon_passed, confidence, status, created_at, inspiration_id)
                VALUES ('COUNCIL_BUILD','FORGE',?,?,?,?,0,0.7,'forge_complete',?,?)""",
                (task["title"], research[:4000], f"TARGET: {target_file}",
                 code, _now(), inspiration_id))
        except sqlite3.OperationalError:
            # older schema without the column (migration blocked): preserve
            # original write path exactly rather than failing the proposal.
            conn.execute("""INSERT INTO polaris_proposals
                (proposal_type, proposal_domain, project_key, proposal_text, suggested_action,
                 rewritten_code, axon_passed, confidence, status, created_at)
                VALUES ('COUNCIL_BUILD','FORGE',?,?,?,?,0,0.7,'forge_complete',?)""",
                (task["title"], research[:4000], f"TARGET: {target_file}",
                 code, _now()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        log.warning("write proposal failed: %s", e)
        return None

def _evaluate_gate(proposal_id, conn):
    try:
        try:
            from services.golden_latch_gate import evaluate_proposal
        except ModuleNotFoundError:
            import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),"services"))
            from golden_latch_gate import evaluate_proposal
        return evaluate_proposal(proposal_id, conn)
    except Exception as e:
        return {"golden_gate_state": "BLOCKED", "blocked_reason": f"GATE_UNAVAILABLE:{e}",
                "risk_level": "UNKNOWN", "next_action": "install golden_latch_gate"}

def _apply_patch(patch_id, conn):
    try:
        try:
            from services.polaris_patch_writer import apply_patch_artifact
        except ModuleNotFoundError:
            import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),"services"))
            from polaris_patch_writer import apply_patch_artifact
        return apply_patch_artifact(patch_id, conn)
    except Exception as e:
        return {"ok": False, "reason": f"PATCH_WRITER_UNAVAILABLE:{e}"}

def _ensure_self_repair_if_idle(conn, launch_run_id):
    """If there are zero runnable tasks AND zero done tasks ever, the pipeline may
    be broken — create a top-priority self-repair task so the council notices itself."""
    try:
        done = conn.execute("SELECT COUNT(*) FROM polaris_standing_tasks WHERE status='DONE'").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM polaris_standing_tasks").fetchone()[0]
        if total == 0:
            exists = conn.execute("SELECT 1 FROM polaris_standing_tasks WHERE title LIKE 'Council self-repair%'").fetchone()
            if not exists:
                conn.execute("""INSERT INTO polaris_standing_tasks
                    (source,domain,title,description,priority,status,stage,risk_level,created_at,updated_at,launch_run_id)
                    VALUES ('SELF_REPAIR','COUNCIL',
                    'Council self-repair: no tasks in standing queue',
                    'Execution spine found no tasks. Investigate seed sources.',1,'OPEN','seeded','MEDIUM',?,?,?)""",
                    (_now(), _now(), launch_run_id))
                conn.commit()
    except Exception as e:
        log.debug("self-repair check: %s", e)