#!/usr/bin/env python3
"""
core/council_stage_contract.py — PROOF-OF-WORK contract for standing tasks.

Answers "are they advancing or looping?" with evidence, not vibes.
Every task advance MUST cite an artifact (patch id, file, audit row, doc).
A run that produces no artifact is recorded as SPIN — and 3 consecutive
spins auto-flag the task STALLED so it stops burning cycles invisibly.

Stages (map 1:1 to the colour doctrine):
  IDLE(grey) RESEARCHING(cyan) DEBATING(blue) BUILDING(gold)
  STAGED(orange) APPLIED(green) BLOCKED(red) STRATEGIC(violet)
"""
import sqlite3, time, json

# ── V2 STAGE MODEL (COUNCIL_STAGE_VERIFIED_20260728) ─────────────────────────
# The V1 list terminated at APPLIED and had NO `VERIFIED` state, even though
# services/safe_patch_apply.py sets phase='VERIFIED' with verifier_result and
# verified_at. The verify step was therefore invisible to the council ledger,
# and COUNCIL_CANARY_MODE's documented release condition
# ("disable after DONE_APPLIED_VERIFIED") named a state the stage machine could
# not represent. Both are fixed here.
STAGES = [
    "IDLE",
    "CLAIMED",
    "RESEARCHING",
    "EVIDENCE_RECORDED",
    "DEBATING",
    "SPECIFIED",
    "BUILDING",
    "STAGED",
    "VERIFYING",
    "VERIFIED",
    "AWAITING_APPROVAL",
    "APPLIED",
    "RETROSPECTIVE",
    "BLOCKED",
    "REJECTED",
    "STRATEGIC",
]

# The canary release condition. Reaching this stage on a verified canary task
# is what durably flips the build plane out of canary-only mode.
TERMINAL_SUCCESS_STAGE = "RETROSPECTIVE"
VERIFIED_STAGE = "VERIFIED"

COLOUR = {
    "IDLE": "#888888", "CLAIMED": "#B0B0B0", "RESEARCHING": "#8EF9FF",
    "EVIDENCE_RECORDED": "#5FD3E0", "DEBATING": "#9945FF", "SPECIFIED": "#C08BFF",
    "BUILDING": "#FFD700", "STAGED": "#FFB347", "VERIFYING": "#FFA500",
    "VERIFIED": "#00E5A0", "AWAITING_APPROVAL": "#FF8C00", "APPLIED": "#14F195",
    "RETROSPECTIVE": "#7CFFB2", "BLOCKED": "#FF073A", "REJECTED": "#FF073A",
    "STRATEGIC": "#E879F9",
}

# Stages that legitimately produce no file artifact. Recording one of these
# without an artifact is NOT a spin -- claiming a task or starting research is
# real progress. Every other stage must cite an artifact or it is a spin.
NON_ARTIFACT_STAGES = {"IDLE", "CLAIMED", "RESEARCHING", "VERIFYING", "AWAITING_APPROVAL"}

SPIN_LIMIT = 3

DDL = [
"""CREATE TABLE IF NOT EXISTS council_task_stage(
 task_key TEXT PRIMARY KEY, stage TEXT DEFAULT 'IDLE', stage_entered_at REAL,
 progress_pct REAL DEFAULT 0, last_artifact_kind TEXT, last_artifact_ref TEXT,
 consecutive_spins INTEGER DEFAULT 0, total_artifacts INTEGER DEFAULT 0,
 blocked_reason TEXT, updated_at REAL)""",
"""CREATE TABLE IF NOT EXISTS council_stage_evidence(
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_key TEXT, ts REAL, stage TEXT,
 artifact_kind TEXT, artifact_ref TEXT, delta_summary TEXT, is_spin INTEGER DEFAULT 0,
 run_id TEXT, attempt_id TEXT, actor TEXT, status TEXT, blocker TEXT,
 previous_stage TEXT, created_at REAL)""",
"""CREATE INDEX IF NOT EXISTS idx_cse_task ON council_stage_evidence(task_key, ts)""",
"""CREATE INDEX IF NOT EXISTS idx_cse_attempt ON council_stage_evidence(attempt_id)""",
"""CREATE TABLE IF NOT EXISTS council_stage_write_failures(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task_key TEXT, run_id TEXT,
 phase TEXT, db_path TEXT, expected_schema TEXT, actual_columns TEXT,
 exception TEXT)""",
]

def init(conn):
    for d in DDL: conn.execute(d)
    conn.commit()

def record_run(conn, task_key, stage, artifact_kind=None, artifact_ref=None,
               delta_summary="", progress_pct=None, blocked_reason=None,
               run_id=None, actor=None, status=None, attempt_id=None):
    """Call after EVERY council/polaris task run. No artifact => SPIN.

    V2: stages in NON_ARTIFACT_STAGES (CLAIMED, RESEARCHING, VERIFYING,
    AWAITING_APPROVAL) are real progress that legitimately produces no file, so
    they are not counted as spins. Everything else must cite an artifact.
    """
    now = time.time()
    init(conn)
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; valid: {STAGES}")
    spin = 0 if (artifact_ref or stage in NON_ARTIFACT_STAGES) else 1
    prev_row = conn.execute(
        "SELECT stage FROM council_task_stage WHERE task_key=?", (task_key,)).fetchone()
    previous_stage = prev_row[0] if prev_row else None
    conn.execute("""INSERT INTO council_stage_evidence
        (task_key,ts,stage,artifact_kind,artifact_ref,delta_summary,is_spin,
         run_id,attempt_id,actor,status,blocker,previous_stage,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_key, now, stage, artifact_kind, artifact_ref, delta_summary[:400], spin,
         run_id, attempt_id, actor, status, blocked_reason, previous_stage, now))
    row = conn.execute("SELECT consecutive_spins,total_artifacts FROM council_task_stage WHERE task_key=?",(task_key,)).fetchone()
    spins = (row[0] if row else 0) + 1 if spin else 0
    arts  = (row[1] if row else 0) + (0 if spin else 1)
    final_stage = "BLOCKED" if (spins >= SPIN_LIMIT or blocked_reason) else stage
    reason = blocked_reason or (f"STALLED: {spins} runs, no artifact produced" if spins>=SPIN_LIMIT else None)
    conn.execute("""INSERT INTO council_task_stage
        (task_key,stage,stage_entered_at,progress_pct,last_artifact_kind,last_artifact_ref,
         consecutive_spins,total_artifacts,blocked_reason,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(task_key) DO UPDATE SET
          stage=excluded.stage,
          stage_entered_at=CASE WHEN council_task_stage.stage!=excluded.stage
                                THEN excluded.stage_entered_at ELSE council_task_stage.stage_entered_at END,
          progress_pct=COALESCE(excluded.progress_pct, council_task_stage.progress_pct),
          last_artifact_kind=COALESCE(excluded.last_artifact_kind, council_task_stage.last_artifact_kind),
          last_artifact_ref=COALESCE(excluded.last_artifact_ref, council_task_stage.last_artifact_ref),
          consecutive_spins=excluded.consecutive_spins,
          total_artifacts=excluded.total_artifacts,
          blocked_reason=excluded.blocked_reason,
          updated_at=excluded.updated_at""",
        (task_key, final_stage, now, progress_pct, artifact_kind, artifact_ref,
         spins, arts, reason, now))
    conn.commit()
    return {"stage": final_stage, "spin": bool(spin), "consecutive_spins": spins,
            "total_artifacts": arts, "colour": COLOUR[final_stage], "blocked_reason": reason}

def board(conn):
    """UI feed: one compact row per task. This is the debate-chamber breakdown."""
    init(conn)
    out=[]
    for r in conn.execute("""SELECT task_key,stage,progress_pct,last_artifact_kind,last_artifact_ref,
        consecutive_spins,total_artifacts,blocked_reason,updated_at FROM council_task_stage
        ORDER BY CASE stage WHEN 'BLOCKED' THEN 0 WHEN 'APPLIED' THEN 1 ELSE 2 END, updated_at DESC"""):
        d=dict(zip(["task_key","stage","progress_pct","artifact_kind","artifact_ref",
                    "spins","artifacts","blocked_reason","updated_at"],r))
        d["colour"]=COLOUR.get(d["stage"],"#888888")
        d["advancing"] = d["artifacts"]>0 and d["spins"]<SPIN_LIMIT
        out.append(d)
    return out

def recent_evidence(conn, task_key, n=8):
    init(conn)
    return [dict(zip(["ts","stage","kind","ref","delta","is_spin"],r)) for r in conn.execute(
        """SELECT ts,stage,artifact_kind,artifact_ref,delta_summary,is_spin
           FROM council_stage_evidence WHERE task_key=? ORDER BY ts DESC LIMIT ?""",(task_key,n))]

if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv)>1 else "sentinuity_matrix.db"
    c = sqlite3.connect(db); init(c)
    print("="*72); print("  COUNCIL BUILD BOARD — proof of work"); print("="*72)
    b = board(c)
    if not b: print("  (no task stages recorded yet — wire record_run() into task loops)")
    for d in b:
        flag = "ADVANCING" if d["advancing"] else ("SPINNING" if d["spins"] else "idle")
        print(f"  [{d['stage']:11}] {d['task_key'][:30]:30} {d['progress_pct'] or 0:5.0f}%  "
              f"artifacts={d['artifacts']:<3} spins={d['spins']}  {flag}")
        if d["artifact_ref"]: print(f"                 last: {d['artifact_kind']}={d['artifact_ref'][:50]}")
        if d["blocked_reason"]: print(f"                 BLOCKED: {d['blocked_reason']}")
    c.close()


# ── DURABLE CANARY RELEASE LATCH (COUNCIL_CANARY_LATCH_20260728) ─────────────
# COUNCIL_CANARY_MODE previously defaulted to "1" inside BOTH
# services/council_autobuilder.py and services/council_task_ledger.py, so the
# system could never leave canary-only mode: removing the launcher `set` line
# changed nothing, and unsetting the environment variable changed nothing.
#
# The build plane state is now DURABLE, stored in system_config, and read in
# preference to any environment default.
BUILD_PLANE_KEY = "COUNCIL_BUILD_PLANE_STATE"
CANARY_TASK_KEY = "COUNCIL_CANARY_TASK_KEY"

STATE_CANARY_REQUIRED = "CANARY_REQUIRED"
STATE_CANARY_RUNNING  = "CANARY_RUNNING"
STATE_CANARY_VERIFIED = "CANARY_VERIFIED"
STATE_BUILD_READY     = "BUILD_READY"
STATE_BLOCKED         = "BLOCKED"

BUILD_PLANE_STATES = [STATE_CANARY_REQUIRED, STATE_CANARY_RUNNING,
                      STATE_CANARY_VERIFIED, STATE_BUILD_READY, STATE_BLOCKED]


def _ensure_config(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS system_config("
                 "key TEXT PRIMARY KEY, value TEXT, description TEXT)")


def get_build_plane_state(conn) -> str:
    """Durable state. Defaults to CANARY_REQUIRED on a virgin database -- a
    fail-closed default that a verified canary can permanently clear."""
    _ensure_config(conn)
    row = conn.execute("SELECT value FROM system_config WHERE key=?",
                       (BUILD_PLANE_KEY,)).fetchone()
    val = str(row[0]).strip() if row and row[0] is not None else ""
    return val if val in BUILD_PLANE_STATES else STATE_CANARY_REQUIRED


def set_build_plane_state(conn, state: str, note: str = "") -> str:
    if state not in BUILD_PLANE_STATES:
        raise ValueError(f"unknown build plane state {state!r}")
    _ensure_config(conn)
    conn.execute(
        "INSERT INTO system_config(key,value,description) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, description=excluded.description",
        (BUILD_PLANE_KEY, state, f"council build plane @ {time.time():.0f} {note}"[:400]))
    conn.commit()
    return state


def canary_only_mode(conn) -> bool:
    """THE replacement for os.getenv('COUNCIL_CANARY_MODE','1').

    Normal backlog tasks are claimable only once the build plane is BUILD_READY.
    """
    return get_build_plane_state(conn) != STATE_BUILD_READY


def evaluate_canary_release(conn, canary_task_key: str) -> dict:
    """Atomically release the build plane IFF the canary task genuinely reached
    a VERIFIED stage and then a terminal success stage, with evidence rows to
    prove both. Returns the resulting state.

    This never trusts a flag: it reads the evidence table.
    """
    init(conn)
    # F3b: evaluate ONLY the latest attempt. Historical failures stay visible
    # but must not permanently poison a later retry.
    latest = conn.execute(
        "SELECT attempt_id FROM council_stage_evidence WHERE task_key=? "
        "AND attempt_id IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (canary_task_key,)).fetchone()
    latest_attempt = latest[0] if latest else None
    if latest_attempt is None:
        rows = conn.execute(
            "SELECT stage, artifact_ref, is_spin FROM council_stage_evidence "
            "WHERE task_key=? AND attempt_id IS NULL ORDER BY ts",
            (canary_task_key,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT stage, artifact_ref, is_spin FROM council_stage_evidence "
            "WHERE task_key=? AND attempt_id=? ORDER BY ts",
            (canary_task_key, latest_attempt)).fetchall()
    stages_seen = [r[0] for r in rows]

    verified = VERIFIED_STAGE in stages_seen
    applied = "APPLIED" in stages_seen
    terminal = TERMINAL_SUCCESS_STAGE in stages_seen
    # VERIFIED must cite an artifact -- a verification with no artifact is not
    # evidence of anything.
    verified_with_artifact = any(
        r[0] == VERIFIED_STAGE and r[1] for r in rows)

    cur_state = get_build_plane_state(conn)
    if cur_state == STATE_BUILD_READY:
        return {"state": STATE_BUILD_READY, "released": True, "reason": "already_released"}

    if "BLOCKED" in stages_seen or "REJECTED" in stages_seen:
        set_build_plane_state(conn, STATE_BLOCKED, f"canary {canary_task_key} failed")
        return {"state": STATE_BLOCKED, "released": False,
                "reason": "canary_blocked_or_rejected"}

    if verified and verified_with_artifact and applied and terminal:
        set_build_plane_state(conn, STATE_BUILD_READY,
                              f"canary {canary_task_key} verified+applied+retrospective")
        return {"state": STATE_BUILD_READY, "released": True, "reason": "canary_verified"}

    missing = [s for s, ok in (
        (VERIFIED_STAGE, verified and verified_with_artifact),
        ("APPLIED", applied),
        (TERMINAL_SUCCESS_STAGE, terminal)) if not ok]
    if stages_seen:
        set_build_plane_state(conn, STATE_CANARY_RUNNING, f"canary {canary_task_key} in progress")
    return {"state": get_build_plane_state(conn), "released": False,
            "reason": f"canary_incomplete:missing={','.join(missing)}"}
