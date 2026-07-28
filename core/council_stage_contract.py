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

STAGES = ["IDLE","RESEARCHING","DEBATING","BUILDING","STAGED","APPLIED","BLOCKED","STRATEGIC"]
COLOUR = {"IDLE":"#888888","RESEARCHING":"#8EF9FF","DEBATING":"#9945FF","BUILDING":"#FFD700",
          "STAGED":"#FFB347","APPLIED":"#14F195","BLOCKED":"#FF073A","STRATEGIC":"#E879F9"}
SPIN_LIMIT = 3

DDL = [
"""CREATE TABLE IF NOT EXISTS council_task_stage(
 task_key TEXT PRIMARY KEY, stage TEXT DEFAULT 'IDLE', stage_entered_at REAL,
 progress_pct REAL DEFAULT 0, last_artifact_kind TEXT, last_artifact_ref TEXT,
 consecutive_spins INTEGER DEFAULT 0, total_artifacts INTEGER DEFAULT 0,
 blocked_reason TEXT, updated_at REAL)""",
"""CREATE TABLE IF NOT EXISTS council_task_evidence(
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_key TEXT, ts REAL, stage TEXT,
 artifact_kind TEXT, artifact_ref TEXT, delta_summary TEXT, is_spin INTEGER DEFAULT 0)""",
"""CREATE INDEX IF NOT EXISTS idx_cte_task ON council_task_evidence(task_key, ts)""",
]

def init(conn):
    for d in DDL: conn.execute(d)
    conn.commit()

def record_run(conn, task_key, stage, artifact_kind=None, artifact_ref=None,
               delta_summary="", progress_pct=None, blocked_reason=None):
    """Call after EVERY council/polaris task run. No artifact => SPIN."""
    now = time.time()
    init(conn)
    spin = 0 if artifact_ref else 1
    conn.execute("""INSERT INTO council_task_evidence
        (task_key,ts,stage,artifact_kind,artifact_ref,delta_summary,is_spin)
        VALUES(?,?,?,?,?,?,?)""",
        (task_key, now, stage, artifact_kind, artifact_ref, delta_summary[:400], spin))
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
           FROM council_task_evidence WHERE task_key=? ORDER BY ts DESC LIMIT ?""",(task_key,n))]

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
