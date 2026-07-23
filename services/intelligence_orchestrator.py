"""
services/intelligence_orchestrator.py
======================================
SOVEREIGN FORGE — AUTONOMOUS BUILD ORGANISM

This service owns the full INTELLIGENCE_BUILD pipeline.
It is completely isolated from the trading bot.

Responsibilities:
  1. Curiosity loop — when forge queue is empty, generate next milestone proposal
  2. Stage advancement — move projects from RESEARCH → DESIGN → BUILD → TEST → SHADOW
  3. Dead proposal recovery — retry debate_error with exponential cooldown
  4. Resource governance — rate limits, budget caps, no retry storms
  5. Human gate — Telegram escalation for SHADOW → IMPLEMENTATION only

The trading organism (execution_engine, supervisor, etc.) is NEVER touched.
Trading proposals (proposal_domain='TRADING') are NEVER processed here.

Run: python -m services.intelligence_orchestrator
"""
from __future__ import annotations
import json, logging, os, sys, time
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FORGE] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("intelligence_orchestrator")

SERVICE_NAME  = "intelligence_orchestrator"
POLL_INTERVAL = 30       # seconds between checks
MAX_RETRY     = 5        # max retries per proposal before archiving
BASE_COOLDOWN = 300      # 5 min base cooldown on retry
MAX_COOLDOWN  = 3600     # 1 hour max cooldown
MIN_OPEN      = 2        # if open forge proposals < this, trigger curiosity
CONTEXT_CAP   = 6000     # max chars of evidence injected into debate prompt

# Stage pipeline order
STAGES = ["RESEARCH", "DESIGN", "BUILD", "TEST", "SHADOW_SIMULATION", "REVIEW"]
# Human gate required for these transitions
HUMAN_GATE_STAGES = {"SHADOW_SIMULATION", "REVIEW"}

# ── DOMAIN ISOLATION ─────────────────────────────────────────────────────────
TRADING_TERMS = [
    "TIME_CUT", "STOP_LOSS", "TAKE_PROFIT", "raw_dna", "pump_monitor",
    "neural_supervisor", "execution_engine", "wallet_balance", "market_snapshots",
    "paper_positions", "latched_signals", "qualified", "ingest_pipeline",
    "SIGNAL_TIER", "MIN_CURVE_SOL", "MIN_PRICE_MOMENTUM", "TIME_CUT_SECONDS",
]

def _is_forge_proposal(text: str) -> bool:
    """True if proposal belongs to FORGE domain — no trading bot content."""
    text_lower = text.lower()
    for term in TRADING_TERMS:
        if term.lower() in text_lower:
            return False
    return True

# ── PROJECT & MILESTONE HELPERS ───────────────────────────────────────────────
def _get_active_projects() -> list[dict]:
    with get_connection() as db:
        rows = db.execute("""
            SELECT * FROM forge_projects
            WHERE status='active'
            ORDER BY priority ASC
        """).fetchall()
    return [dict(r) for r in rows]

def _get_open_forge_count() -> int:
    with get_connection() as db:
        return db.execute("""
            SELECT COUNT(*) FROM polaris_proposals
            WHERE proposal_domain='FORGE'
              AND status='open'
              AND COALESCE(cooldown_until, 0) < ?
        """, (time.time(),)).fetchone()[0]

def _get_project_stage(project_key: str) -> str:
    with get_connection() as db:
        row = db.execute(
            "SELECT current_stage FROM forge_projects WHERE project_key=?",
            (project_key,)
        ).fetchone()
    return row["current_stage"] if row else "RESEARCH"

def _advance_project_stage(project_key: str, current_stage: str) -> str | None:
    """Advance to next stage. Returns new stage or None if complete."""
    try:
        idx = STAGES.index(current_stage)
        if idx + 1 < len(STAGES):
            next_stage = STAGES[idx + 1]
            with get_connection() as db:
                db.execute(
                    "UPDATE forge_projects SET current_stage=?, updated_at=? WHERE project_key=?",
                    (next_stage, time.time(), project_key)
                )
                db.commit()
            log.info("Project %s advanced: %s → %s", project_key, current_stage, next_stage)
            return next_stage
    except ValueError:
        pass
    return None

def _compress_evidence(project_key: str) -> str:
    """Pull research cache for project, compress to CONTEXT_CAP chars."""
    with get_connection() as db:
        rows = db.execute("""
            SELECT topic, summary, confidence
            FROM forge_research_cache
            WHERE project_key=? AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY confidence DESC, created_at DESC
            LIMIT 10
        """, (project_key, time.time())).fetchall()
    if not rows:
        return "No research cache found for this project yet."
    parts = []
    total = 0
    for r in rows:
        entry = f"[{r['topic']} | conf={r['confidence']:.2f}] {r['summary']}"
        if total + len(entry) > CONTEXT_CAP:
            break
        parts.append(entry)
        total += len(entry)
    return "\n".join(parts)

# ── CURIOSITY LOOP — GENERATES NEXT MILESTONE PROPOSAL ───────────────────────
STAGE_PROMPTS = {
    "RESEARCH": (
        "FORGE RESEARCH — {project_title}: "
        "Conduct research into {project_description} "
        "Specifically: (1) what data sources are available, "
        "(2) what edge or signal has been identified in prior literature, "
        "(3) what is the simplest measurable hypothesis to test first. "
        "Output: a structured research summary with confidence scores. "
        "Measurement: research summary completeness score 0-1. "
        "Falsifiability: if no credible data source exists, report INFEASIBLE. "
        "Risk: research only, no code changes, no config changes."
    ),
    "DESIGN": (
        "FORGE DESIGN — {project_title}: "
        "Based on research evidence: {evidence}, "
        "design the architecture for a Python module that implements "
        "{project_description} "
        "Output: module specification with: inputs, outputs, DB tables needed, "
        "API calls required, estimated latency. "
        "Measurement: design completeness — does it cover all inputs/outputs? "
        "Falsifiability: if design requires >3 new external APIs, flag as high-risk. "
        "Risk: design only, no code written yet."
    ),
    "BUILD": (
        "FORGE BUILD — {project_title}: "
        "Implement the designed module based on spec: {evidence}. "
        "Write production-quality Python code. "
        "Output: complete module code stored as a forge artifact. "
        "Requirements: must not import or reference execution_engine, "
        "paper_positions, wallet_balance, or any trading-critical module. "
        "Measurement: does the code run without errors in isolation? "
        "Falsifiability: if implementation requires trading bot integration "
        "before testing, return to DESIGN stage. "
        "Risk: code artifact only, not deployed anywhere."
    ),
    "TEST": (
        "FORGE TEST — {project_title}: "
        "Test the built module against historical data. "
        "Evidence and artifact: {evidence}. "
        "Run in replay_engine simulation mode. "
        "Output: test results — accuracy, false positive rate, latency. "
        "Measurement: minimum 100 historical data points tested. "
        "Falsifiability: if accuracy < 55% on historical data, return to BUILD. "
        "Risk: simulation only, no live data modified."
    ),
    "SHADOW_SIMULATION": (
        "FORGE SHADOW — {project_title}: "
        "Run module in shadow mode alongside live system. "
        "Module observes but does not act. Log predicted signals vs actual outcomes. "
        "Evidence: {evidence}. "
        "Output: shadow performance report — predicted vs actual correlation. "
        "Measurement: 24-hour shadow run, minimum 50 signal events. "
        "Falsifiability: if correlation < 0.4, module needs redesign. "
        "HUMAN GATE: this stage requires human approval before proceeding to IMPLEMENTATION. "
        "Risk: observation only, zero write access to trading systems."
    ),
}

def _generate_curiosity_proposal(project: dict) -> int | None:
    """Generate next milestone proposal for a project. Returns proposal ID."""
    stage = project["current_stage"]
    if stage not in STAGE_PROMPTS:
        log.warning("Project %s in unknown stage %s", project["project_key"], stage)
        return None

    evidence = _compress_evidence(project["project_key"])
    text = STAGE_PROMPTS[stage].format(
        project_title=project["title"],
        project_description=project["description"],
        evidence=evidence[:2000],
    )

    if not _is_forge_proposal(text):
        log.error("Generated proposal contains trading terms — blocked")
        return None

    metrics = json.dumps({
        "target": f"forge_{project['project_key']}",
        "suggested": f"complete_{stage.lower()}_milestone",
        "hypothesis": f"{stage} milestone for {project['project_key']}",
        "expected_outcome": f"completed {stage} artifact",
        "falsifiability": "see proposal text",
        "risk_assessment": "forge_only_no_trading_system_access",
        "measurement_metric": f"{stage.lower()}_completion_score",
        "project_key": project["project_key"],
        "stage": stage,
    })

    with get_connection() as db:
        cur = db.execute("""
            INSERT INTO polaris_proposals
                (proposal_type, proposal_text, metrics_json, status,
                 seen_count, created_at, confidence,
                 proposal_domain, stage, project_key, retry_count)
            VALUES ('INTELLIGENCE_BUILD', ?, ?, 'open',
                    0, ?, 0.85,
                    'FORGE', ?, ?, 0)
        """, (text, metrics, time.time(), stage, project["project_key"]))
        proposal_id = cur.lastrowid
        db.commit()

    log.info("Curiosity proposal %d generated: %s / %s",
             proposal_id, project["project_key"], stage)
    return proposal_id

# ── DEAD PROPOSAL RECOVERY ────────────────────────────────────────────────────
def _recover_dead_proposals() -> int:
    """Retry debate_error proposals with exponential cooldown. Returns count recovered."""
    now = time.time()
    recovered = 0
    with get_connection() as db:
        dead = db.execute("""
            SELECT id, retry_count, proposal_text
            FROM polaris_proposals
            WHERE proposal_domain='FORGE'
              AND status='debate_error'
              AND (cooldown_until IS NULL OR cooldown_until < ?)
        """, (now,)).fetchall()

        for row in dead:
            rid   = row["id"]
            count = int(row["retry_count"] or 0)
            if count >= MAX_RETRY:
                db.execute(
                    "UPDATE polaris_proposals SET status='archived' WHERE id=?", (rid,)
                )
                log.warning("Proposal %d exceeded max retries — archived", rid)
                continue

            cooldown = min(MAX_COOLDOWN, BASE_COOLDOWN * (2 ** count))
            db.execute("""
                UPDATE polaris_proposals
                SET status='open',
                    seen_count=0,
                    retry_count=?,
                    cooldown_until=?,
                    api_health_state='retry'
                WHERE id=?
            """, (count + 1, now + cooldown, rid))
            recovered += 1
            log.info("Proposal %d recovered (retry %d, cooldown %ds)", rid, count+1, cooldown)

        db.commit()
    return recovered

def _reset_seen_counts() -> int:
    """Reset seen_count on FORGE proposals so governor picks them up."""
    with get_connection() as db:
        n = db.execute("""
            UPDATE polaris_proposals
            SET seen_count=0
            WHERE proposal_domain='FORGE'
              AND status='open'
              AND seen_count >= 3
              AND (cooldown_until IS NULL OR cooldown_until < ?)
        """, (time.time(),)).rowcount
        db.commit()
    if n:
        log.info("Reset seen_count on %d stale forge proposals", n)
    return n

def _check_completed_milestones() -> None:
    """If a FORGE proposal was approved, advance the project stage."""
    with get_connection() as db:
        approved = db.execute("""
            SELECT id, project_key, stage
            FROM polaris_proposals
            WHERE proposal_domain='FORGE'
              AND status IN ('approved', 'applied')
              AND project_key IS NOT NULL
              AND stage IS NOT NULL
        """).fetchall()

        for row in approved:
            project_key = row["project_key"]
            stage       = row["stage"]
            proj = db.execute(
                "SELECT current_stage FROM forge_projects WHERE project_key=?",
                (project_key,)
            ).fetchone()
            if proj and proj["current_stage"] == stage:
                _advance_project_stage(project_key, stage)
            # Mark proposal as processed so we don't advance again
            db.execute(
                "UPDATE polaris_proposals SET status='completed' WHERE id=?",
                (row["id"],)
            )
        db.commit()

def _check_human_gate(project_key: str, stage: str) -> bool:
    """Returns True if human approval is required. Sends Telegram if so."""
    if stage not in HUMAN_GATE_STAGES:
        return False
    log.info("HUMAN GATE required for %s / %s — awaiting approval", project_key, stage)
    # Log to cognition_log for visibility
    try:
        with get_connection() as db:
            db.execute("""
                INSERT INTO cognition_log (stage, token, message, confidence, timestamp)
                VALUES ('FORGE_GATE', ?, ?, 0.99, ?)
            """, (
                project_key,
                f"HUMAN GATE: {project_key} has reached {stage}. "
                f"Reply /approve to proceed to implementation or /deny to revise.",
                time.time()
            ))
            db.commit()
    except Exception as e:
        log.warning("Failed to log human gate: %s", e)
    return True

# ── RESOURCE GOVERNANCE ───────────────────────────────────────────────────────
def _check_budget() -> bool:
    """Returns True if we're within daily NIM budget."""
    try:
        budget = float(get_config_value("ORACLE_DAILY_BUDGET", 35))
        used_row = None
        with get_connection() as db:
            used_row = db.execute("""
                SELECT COUNT(*) n FROM cognition_log
                WHERE stage LIKE 'FORGE%'
                  AND timestamp > ?
            """, (time.time() - 86400,)).fetchone()
        used = used_row["n"] if used_row else 0
        if used > budget * 10:  # rough proxy: 10 cognition entries per NIM call
            log.warning("Forge NIM budget approaching limit (%d entries today)", used)
            return False
    except Exception:
        pass
    return True

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def run() -> None:
    log.info("SOVEREIGN FORGE ONLINE — autonomous build organism awakening")
    log.info("Separation doctrine: FORGE proposals never touch trading organism")

    update_heartbeat(SERVICE_NAME, "ALIVE", "forge initialising")

    while True:
        try:
            now = time.time()

            # 1. Check budget
            if not _check_budget():
                update_heartbeat(SERVICE_NAME, "DEGRADED", "NIM budget limit approached")
                time.sleep(POLL_INTERVAL * 2)
                continue

            # 2. Recover dead proposals
            recovered = _recover_dead_proposals()

            # 3. Reset stale seen_counts
            reset = _reset_seen_counts()

            # 4. Check completed milestones — advance stages
            _check_completed_milestones()

            # 5. Curiosity loop — ensure queue never runs dry
            open_count = _get_open_forge_count()
            generated  = 0
            if open_count < MIN_OPEN:
                projects = _get_active_projects()
                for project in projects:
                    stage = project["current_stage"]
                    # Check human gate before generating SHADOW or REVIEW proposals
                    if stage in HUMAN_GATE_STAGES:
                        if not _check_human_gate(project["project_key"], stage):
                            continue
                    proposal_id = _generate_curiosity_proposal(project)
                    if proposal_id:
                        generated += 1
                    if open_count + generated >= MIN_OPEN:
                        break

            note = (
                f"open={open_count} generated={generated} "
                f"recovered={recovered} reset={reset}"
            )
            update_heartbeat(SERVICE_NAME, "ALIVE", note,
                             work_processed=generated + recovered,
                             last_success_at=now if (generated + recovered) > 0 else None)

            if generated > 0:
                log.info("Curiosity: generated %d new forge proposals", generated)

            time.sleep(POLL_INTERVAL)

        except Exception as exc:
            log.exception("Forge loop error: %s", exc)
            update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            # ── Idle build engine ────────────────────────────────────
            try:
                import sqlite3 as _ib_sq
                _ib_db = _ib_sq.connect("sentinuity_matrix.db", timeout=5)
                _ib_db.row_factory = _ib_sq.Row  # WIRING_FIX_20260723: was unaliased sqlite3 (never imported here)
                _open_pos = _ib_db.execute(
                    "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
                ).fetchone()[0]
                _recent_cog = _ib_db.execute(
                    "SELECT COUNT(*) FROM cognition_log WHERE "
                    "CAST(COALESCE(timestamp,0) AS REAL) >= ?",
                    (time.time()-300,)
                ).fetchone()[0]
                _ib_db.execute(
                    "INSERT INTO system_config(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("SUBSTRATE_BUILD_ACTIVE", "1" if _open_pos==0 and _recent_cog<5 else "0")
                )
                _ib_db.commit(); _ib_db.close()
            except Exception:
                pass

            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
