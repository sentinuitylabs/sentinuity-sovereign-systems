"""
services/council_build_orchestrator.py

Durable autonomous build-state service for Sentinuity.

Purpose:
- Preserve the six-node council roster across launches.
- Show each council node's current model/model tier/evolution state.
- Seed and resume council build tasks instead of losing progress each launch.
- Keep high-risk code apply behind Golden Lattice approval.
- Seed Substrate Strategy Lab tables for paper-first strategy development.

This service DOES NOT apply high-risk patches. It creates the work queue,
model registry, phase state, and proof surfaces that Substrate Node should render.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time, logging
from pathlib import Path
from typing import Any, Iterable

SERVICE_NAME = "council_build_orchestrator"
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sentinuity_matrix.db"
CYCLE_SECONDS = int(os.getenv("COUNCIL_BUILD_CYCLE_SECONDS", "60"))

PHASES = [
    "DISCOVER", "DESIGN", "PATCH_READY", "TESTING", "NEEDS_APPROVAL",
    "APPROVED", "APPLIED", "POST_VERIFY", "VERIFIED", "FAILED", "ROLLED_BACK",
]

CANONICAL_COUNCIL = [
    {
        "agent_name": "POLARIS",
        "display_name": "POLARIS",
        "service_name": "polaris",
        "role": "final planner / coordinator",
        "authority_level": "planner",
        "can_apply_code": 1,
        "can_block": 0,
        "model_family": "openai",
        "default_model": "gpt-5.4-mini",
        "current_model": "gpt-5.4-mini",
        "model_tier": "signoff",
        "evolution_state": "baseline",
        "policy": {
            "low": "gpt-5.4-nano",
            "medium": "gpt-5.4-mini",
            "high": "gpt-5.5",
            "live_or_execution": "gpt-5.5",
        },
    },
    {
        "agent_name": "IVARIS",
        "display_name": "IVARIS",
        "service_name": "polaris_auxiliary",
        "role": "adversarial critic / safety reviewer",
        "authority_level": "reviewer",
        "can_apply_code": 0,
        "can_block": 1,
        "model_family": "anthropic",
        "default_model": "claude-opus",
        "current_model": "claude-opus",
        "model_tier": "critic",
        "evolution_state": "baseline",
        "policy": {
            "low": "claude-sonnet",
            "medium": "claude-opus",
            "high": "claude-opus",
            "live_or_execution": "claude-opus",
        },
    },
    {
        "agent_name": "NUGGET",
        "display_name": "NUGGET",
        "service_name": "reconnaissance_engine",
        "role": "auditor / assertion runner / telemetry compressor",
        "authority_level": "auditor",
        "can_apply_code": 0,
        "can_block": 0,
        "model_family": "nim/openai-lite",
        "default_model": "nim-nano",
        "current_model": "nim-nano",
        "model_tier": "fast_scan",
        "evolution_state": "baseline",
        "policy": {
            "low": "nim-nano",
            "medium": "nim-mini",
            "high": "nim-mini+polaris_review",
            "live_or_execution": "polaris_required",
        },
    },
    {
        "agent_name": "ORACLE",
        "display_name": "ORACLE",
        "service_name": "ws_price_oracle",
        "role": "external senses / market, wallet, price and web facts",
        "authority_level": "sensor",
        "can_apply_code": 0,
        "can_block": 0,
        "model_family": "tools/scouts",
        "default_model": "oracle-scout",
        "current_model": "oracle-scout",
        "model_tier": "sensor",
        "evolution_state": "baseline",
        "policy": {
            "low": "local_scout",
            "medium": "oracle_scout+wallet_scout",
            "high": "multi_source_oracle",
            "live_or_execution": "fresh_price_required",
        },
    },
    {
        "agent_name": "AXON",
        "display_name": "AXON",
        "service_name": "execution_engine",
        "role": "execution validator / motor output safety",
        "authority_level": "execution_validator",
        "can_apply_code": 0,
        "can_block": 1,
        "model_family": "rules+runtime",
        "default_model": "axon-runtime",
        "current_model": "axon-runtime",
        "model_tier": "runtime_guard",
        "evolution_state": "baseline",
        "policy": {
            "low": "runtime_checks",
            "medium": "runtime_checks+polaris",
            "high": "golden_lattice_required",
            "live_or_execution": "golden_lattice_required",
        },
    },
    {
        "agent_name": "RHIZA",
        "display_name": "RHIZA",
        "service_name": "symbiotic_router",
        "role": "synthesis / memory / pattern integrator",
        "authority_level": "synthesizer",
        "can_apply_code": 0,
        "can_block": 0,
        "model_family": "grok/synthesis",
        "default_model": "grok-current",
        "current_model": "grok-current",
        "model_tier": "synthesis",
        "evolution_state": "baseline",
        "policy": {
            "low": "grok-light",
            "medium": "grok-current",
            "high": "grok-current+polaris_review",
            "live_or_execution": "synthesis_only",
        },
    },
]

SUPPORT_SYSTEMS = [
    ("GOVERNOR", "sovereign_governor", "constitutional orchestrator / final system policy"),
    ("GUARDIAN", "system_guardian", "runtime health, watchdog, restart proof"),
    ("GOLDEN_LATTICE", "golden_lattice", "operator approval gate for high-risk apply"),
    ("AXIOM_NIM", "nim_doctrine", "specialist model library, not a council seat"),
]

STANDING_TASKS = [
    ("Solana main interface edge audit", "solana_interface", "solana_edge_audit", 1, "MEDIUM",
     "Every launch/periodic cycle audit the main Solana lane: latest 30/100 closes, latch freshness, price integrity, exit reasons, TAKE_PROFIT/RUNNER/DEAD_TOKEN balance, and propose one low-risk edge improvement."),
    ("Substrate Node DB-backed command layer", "substrate_node", "ui_build", 1, "MEDIUM",
     "Patch only the Substrate Node section so it renders DB-backed council queue, approvals, runner radar, velocity engine, smart-wallet convergence, and strategy lab."),
    ("Golden Lattice approval gate proof", "substrate_node", "approval_gate", 1, "HIGH",
     "Prove high-risk patches cannot apply without operator approval and show pending approvals in Substrate Node."),
    ("Runner Radar panel from shadow_runners", "substrate_node", "runner_radar", 2, "LOW",
     "Render caught/missed runners, top peak multiples, unseen/rejected monsters, and rejection reasons from shadow_runners."),
    ("Velocity Engine panel from runner_likelihood_scores", "substrate_node", "velocity_engine", 2, "LOW",
     "Render open paper positions, velocity, peak multiple, tier, recommendation, and latest score."),
    ("Smart Wallet Convergence panel", "substrate_node", "smart_wallet", 3, "MEDIUM",
     "Render wallet_entry_likelihood_signals and show observe-only if signals are empty."),
    ("Strategy Lab paper-only registry", "substrate_node", "strategy_lab", 3, "MEDIUM",
     "Show runner_ladder_bot, smart_wallet_convergence_bot, and grid_quant_bot with paper/live disabled state."),
    ("Wire runner detector into execution for paper scoring only", "execution", "runner_detector", 2, "HIGH",
     "Score open paper positions after 15 seconds and every 10 seconds; no live scale, early cut disabled by default."),
    ("Fix shadow runner peak timestamp and wallet schema", "runner_radar", "tracker_fix", 2, "MEDIUM",
     "Use actual peak timestamp; detect token_mint vs mint_address dynamically; add smart wallet counts and tide at peak."),
    ("Close smart-wallet inference loop", "smart_money", "wallet_inference", 2, "HIGH",
     "Populate wallet_entry_likelihood_signals from profiled wallet convergence before any live copy trading."),
    ("Fix six-node council stalemate resolver", "council", "stalemate_resolver", 1, "MEDIUM",
     "Use Polaris, Ivaris, Nugget, Oracle, Axon, Rhiza roles; no infinite stalemates; escalate high risk to Golden Lattice."),
    ("Pattern overlay versus underlay expectancy review", "solana_interface", "pattern_overlay_review", 1, "MEDIUM",
     "Shadow-only rolling 30/100/300 close comparison of underlay-only, overlay-approved and overlay-rejected cohorts; measure net PnL, runner rate, false-negative runners, dud/rug prevention and Melbourne-hour/cluster-entry expectancy. Never auto-apply live changes."),
    ("GMGN filter taxonomy gap analysis", "smart_money", "gmgn_filter_gap", 1, "MEDIUM",
     "Research every publicly visible GMGN token/wallet filter, map it to Sentinuity fields and on-chain sources, backtest runner loss versus dud/rug prevention, and recommend observe/paper/reject. Never create a live veto without operator approval."),
]

STRATEGIES = [
    ("runner_ladder_bot", "runner_ladder", 1, "paper", "MEDIUM",
     "Small entry, post-entry MONSTER/STRONG detection, paper-only ladder/hold logic; live scale disabled."),
    ("smart_wallet_convergence_bot", "copy_convergence", 0, "paper", "HIGH",
     "Paper-fire or observe 2+ profiled-wallet convergence after wallet signals are populated and measured."),
    ("grid_quant_bot", "grid_quant", 0, "paper", "MEDIUM",
     "Research/paper grid only for older, liquid, range-bound tokens. Not for fresh pump chaos."),
]


def connect() -> sqlite3.Connection:
    try:
        from core.schema import get_connection  # type: ignore
        return get_connection()
    except Exception:
        conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def ensure_column(conn: sqlite3.Connection, table: str, col: str, spec: str) -> None:
    if col not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")


def cfg(conn: sqlite3.Connection, key: str, value: Any, desc: str = "council build orchestrator") -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY, value TEXT, description TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO system_config(key,value,description) VALUES(?,?,?)",
        (key, str(value), desc),
    )


def heartbeat(status: str, note: str = "", work: int = 0) -> None:
    try:
        from core.schema import update_heartbeat  # type: ignore
        update_heartbeat(SERVICE_NAME, status, note, work_processed=work)
        return
    except Exception:
        pass
    try:
        with connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_heartbeat(
                    service_name TEXT PRIMARY KEY, status TEXT, note TEXT,
                    last_pulse REAL, work_processed INTEGER DEFAULT 0,
                    last_success_at REAL
                )
            """)
            conn.execute("""
                INSERT INTO system_heartbeat(service_name,status,note,last_pulse,work_processed,last_success_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(service_name) DO UPDATE SET
                    status=excluded.status,
                    note=excluded.note,
                    last_pulse=excluded.last_pulse,
                    work_processed=excluded.work_processed,
                    last_success_at=excluded.last_success_at
            """, (SERVICE_NAME, status, note, time.time(), work, time.time() if status == "alive" else None))
    except Exception:
        pass


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS council_work_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            updated_at REAL,
            source_agent TEXT,
            assigned_agent TEXT,
            target_tab TEXT,
            task_type TEXT,
            title TEXT,
            description TEXT,
            priority INTEGER DEFAULT 5,
            risk_level TEXT DEFAULT 'LOW',
            phase TEXT DEFAULT 'DISCOVER',
            status TEXT DEFAULT 'OPEN',
            files_touched TEXT,
            patch_path TEXT,
            backup_path TEXT,
            test_command TEXT,
            test_result TEXT,
            verifier_command TEXT,
            verifier_result TEXT,
            approval_required INTEGER DEFAULT 1,
            approved_by_operator INTEGER DEFAULT 0,
            applied_at REAL,
            verified_at REAL,
            rollback_path TEXT,
            result_summary TEXT,
            last_error TEXT
        )
    """)
    for col, spec in [
        ("assigned_agent", "TEXT"), ("phase", "TEXT DEFAULT 'DISCOVER'"),
        ("patch_path", "TEXT"), ("verifier_command", "TEXT"), ("verifier_result", "TEXT"),
        ("verified_at", "REAL"), ("last_error", "TEXT"), ("blocker_reason", "TEXT"),
        ("last_recovered_at", "REAL"),
    ]:
        ensure_column(conn, "council_work_queue", col, spec)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS council_stalemates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            resolved_at REAL,
            task_id INTEGER,
            agents_involved TEXT,
            disagreement TEXT,
            decision TEXT,
            resolution_rule TEXT,
            operator_required INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patch_apply_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            task_id INTEGER,
            file_path TEXT,
            backup_path TEXT,
            patch_summary TEXT,
            risk_level TEXT,
            precheck_result TEXT,
            apply_result TEXT,
            postcheck_result TEXT,
            rollback_result TEXT,
            final_status TEXT
        )
    """)
    for col, spec in [
        ("created_at", "REAL"), ("task_id", "INTEGER"), ("file_path", "TEXT"),
        ("backup_path", "TEXT"), ("patch_summary", "TEXT"), ("risk_level", "TEXT"),
        ("precheck_result", "TEXT"), ("apply_result", "TEXT"),
        ("postcheck_result", "TEXT"), ("rollback_result", "TEXT"),
        ("final_status", "TEXT"), ("patch_ref", "TEXT"), ("stage", "INTEGER"),
        ("patch_id", "INTEGER"), ("ts", "REAL"), ("action", "TEXT"),
        ("outcome", "TEXT"), ("detail", "TEXT"),
    ]:
        ensure_column(conn, "patch_apply_journal", col, spec)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS council_role_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT UNIQUE,
            display_name TEXT,
            service_name TEXT,
            role TEXT,
            authority_level TEXT,
            can_apply_code INTEGER DEFAULT 0,
            can_block INTEGER DEFAULT 0,
            can_request_operator INTEGER DEFAULT 1,
            model_family TEXT,
            default_model TEXT,
            current_model TEXT,
            model_tier TEXT,
            evolution_state TEXT,
            model_policy_json TEXT,
            last_task_id INTEGER,
            last_model_change_at REAL,
            heartbeat_status TEXT,
            heartbeat_age_sec REAL,
            notes TEXT,
            updated_at REAL
        )
    """)
    for col, spec in [
        ("display_name", "TEXT"), ("service_name", "TEXT"), ("model_family", "TEXT"),
        ("default_model", "TEXT"), ("current_model", "TEXT"), ("model_tier", "TEXT"),
        ("evolution_state", "TEXT"), ("model_policy_json", "TEXT"), ("last_task_id", "INTEGER"),
        ("last_model_change_at", "REAL"), ("heartbeat_status", "TEXT"), ("heartbeat_age_sec", "REAL"),
    ]:
        ensure_column(conn, "council_role_registry", col, spec)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS council_model_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            task_id INTEGER,
            agent_name TEXT,
            task_type TEXT,
            risk_level TEXT,
            selected_model TEXT,
            model_tier TEXT,
            evolution_direction TEXT,
            reason TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS council_model_evolution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            task_id INTEGER,
            agent_name TEXT,
            old_model TEXT,
            new_model TEXT,
            old_tier TEXT,
            new_tier TEXT,
            direction TEXT,
            reason TEXT
        )
    """)
    # Existing databases may contain older versions of these tables without
    # task_id/model fields. Migrate them before the first assignment query so a
    # live upgrade cannot stall the whole Council cycle with "no such column".
    for col, spec in [
        ("created_at", "REAL"), ("task_id", "INTEGER"),
        ("agent_name", "TEXT"), ("task_type", "TEXT"),
        ("risk_level", "TEXT"), ("selected_model", "TEXT"),
        ("model_tier", "TEXT"), ("evolution_direction", "TEXT"),
        ("reason", "TEXT"),
    ]:
        ensure_column(conn, "council_model_assignments", col, spec)
    for col, spec in [
        ("created_at", "REAL"), ("task_id", "INTEGER"),
        ("agent_name", "TEXT"), ("old_model", "TEXT"),
        ("new_model", "TEXT"), ("old_tier", "TEXT"),
        ("new_tier", "TEXT"), ("direction", "TEXT"),
        ("reason", "TEXT"),
    ]:
        ensure_column(conn, "council_model_evolution_log", col, spec)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_system_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            service_name TEXT,
            role TEXT,
            updated_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_strategy_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            strategy_type TEXT,
            enabled INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'paper',
            risk_level TEXT DEFAULT 'LOW',
            description TEXT,
            config_json TEXT,
            created_at REAL,
            updated_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_strategy_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT,
            mint_address TEXT,
            token_name TEXT,
            signal_type TEXT,
            confidence REAL,
            reason TEXT,
            suggested_action TEXT,
            mode TEXT,
            created_at REAL,
            consumed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_strategy_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT,
            signal_id INTEGER,
            position_id INTEGER,
            mint_address TEXT,
            opened_at REAL,
            closed_at REAL,
            entry_price REAL,
            exit_price REAL,
            peak_mult REAL,
            realized_pnl_usd REAL,
            outcome TEXT,
            notes TEXT
        )
    """)


    # Bootstrap observability tables so verifier can go green before the
    # long-running services complete their first cycle.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_runners (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mint_address        TEXT NOT NULL,
            token_name          TEXT,
            detected_at         REAL NOT NULL,
            entry_price_seen    REAL,
            peak_price_seen     REAL,
            peak_mult           REAL,
            time_to_peak_sec    REAL,
            tide_at_peak        TEXT,
            we_qualified        INTEGER DEFAULT 0,
            we_latched          INTEGER DEFAULT 0,
            we_opened           INTEGER DEFAULT 0,
            position_id         INTEGER,
            rejection_reason    TEXT,
            quality_reason      TEXT,
            smart_wallet_signal INTEGER DEFAULT 0,
            smart_wallet_count  INTEGER DEFAULT 0,
            elite_wallet_count  INTEGER DEFAULT 0,
            top_wallet_lead_time_sec REAL,
            classification      TEXT,
            updated_at          REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_mint ON shadow_runners(mint_address)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_detected ON shadow_runners(detected_at)")
    for col, spec in [
        ("smart_wallet_count", "INTEGER DEFAULT 0"),
        ("elite_wallet_count", "INTEGER DEFAULT 0"),
        ("top_wallet_lead_time_sec", "REAL"),
        ("tide_at_peak", "TEXT"),
    ]:
        ensure_column(conn, "shadow_runners", col, spec)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS runner_likelihood_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            mint_address TEXT,
            token_name TEXT,
            scored_at REAL,
            age_sec REAL,
            entry_price REAL,
            peak_price REAL,
            peak_mult REAL,
            velocity_per_min REAL,
            likelihood REAL,
            tier TEXT,
            recommend TEXT,
            reason TEXT,
            mode TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runner_scores_position ON runner_likelihood_scores(position_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runner_scores_scored_at ON runner_likelihood_scores(scored_at)")


def choose_model(agent: dict[str, Any], task: sqlite3.Row) -> tuple[str, str, str, str]:
    risk = str(task["risk_level"] or "LOW").upper()
    text = " ".join(str(task[k] or "") for k in ["target_tab", "task_type", "title", "description"]).lower()
    policy = agent.get("policy", {})
    if risk == "HIGH" or any(word in text for word in ["execution", "live", "wallet", "signing", "swap", "engine"]):
        selected = policy.get("live_or_execution") or policy.get("high") or agent["default_model"]
        tier = "critical"
    elif risk == "MEDIUM":
        selected = policy.get("medium") or agent["default_model"]
        tier = "signoff"
    else:
        selected = policy.get("low") or agent["default_model"]
        tier = "fast"
    default = agent.get("default_model") or selected
    direction = "baseline"
    if selected != default:
        direction = "evolved" if tier in ("critical", "signoff") else "devolved"
    reason = f"risk={risk}; task={task['task_type']}; target={task['target_tab']}"
    return selected, tier, direction, reason


def seed_roles(conn: sqlite3.Connection) -> None:
    now = time.time()
    for agent in CANONICAL_COUNCIL:
        conn.execute("""
            INSERT INTO council_role_registry(
                agent_name, display_name, service_name, role, authority_level,
                can_apply_code, can_block, can_request_operator,
                model_family, default_model, current_model, model_tier,
                evolution_state, model_policy_json, notes, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent_name) DO UPDATE SET
                display_name=excluded.display_name,
                service_name=excluded.service_name,
                role=excluded.role,
                authority_level=excluded.authority_level,
                can_apply_code=excluded.can_apply_code,
                can_block=excluded.can_block,
                can_request_operator=excluded.can_request_operator,
                model_family=excluded.model_family,
                default_model=excluded.default_model,
                model_policy_json=excluded.model_policy_json,
                notes=excluded.notes,
                updated_at=excluded.updated_at
        """, (
            agent["agent_name"], agent["display_name"], agent["service_name"], agent["role"], agent["authority_level"],
            agent["can_apply_code"], agent["can_block"], 1,
            agent["model_family"], agent["default_model"], agent["current_model"], agent["model_tier"],
            agent["evolution_state"], json.dumps(agent["policy"]),
            "Canonical six-node council seat. Model may evolve/devolve per task; name stays fixed.", now,
        ))
    for name, svc, role in SUPPORT_SYSTEMS:
        conn.execute("""
            INSERT INTO support_system_registry(name, service_name, role, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                service_name=excluded.service_name,
                role=excluded.role,
                updated_at=excluded.updated_at
        """, (name, svc, role, now))


def seed_tasks(conn: sqlite3.Connection) -> int:
    now = time.time()
    inserted = 0
    for title, target, task_type, priority, risk, desc in STANDING_TASKS:
        row = conn.execute("SELECT id FROM council_work_queue WHERE title=?", (title,)).fetchone()
        if row:
            continue
        assigned = "POLARIS"
        if "runner" in task_type or "tracker" in task_type:
            assigned = "NUGGET"
        if "wallet" in task_type:
            assigned = "ORACLE"
        if "execution" in target or "detector" in task_type:
            assigned = "AXON"
        if "stalemate" in task_type:
            assigned = "RHIZA"
        conn.execute("""
            INSERT INTO council_work_queue(
                created_at, updated_at, source_agent, assigned_agent, target_tab,
                task_type, title, description, priority, risk_level, phase,
                status, approval_required
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now, now, "council_build_orchestrator", assigned, target,
            task_type, title, desc, priority, risk, "DISCOVER", "OPEN", 1 if risk in ("HIGH", "MEDIUM") else 0,
        ))
        inserted += 1
    return inserted


def seed_strategies(conn: sqlite3.Connection) -> None:
    now = time.time()
    for name, typ, enabled, mode, risk, desc in STRATEGIES:
        conn.execute("""
            INSERT INTO substrate_strategy_registry(
                name, strategy_type, enabled, mode, risk_level, description,
                config_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                strategy_type=excluded.strategy_type,
                mode=excluded.mode,
                risk_level=excluded.risk_level,
                description=excluded.description,
                config_json=excluded.config_json,
                updated_at=excluded.updated_at
        """, (name, typ, enabled, mode, risk, desc, "{}", now, now))


def update_heartbeats_for_roles(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "system_heartbeat"):
        return
    now = time.time()
    for row in conn.execute("SELECT agent_name, service_name FROM council_role_registry").fetchall():
        hb = conn.execute(
            "SELECT status, last_pulse FROM system_heartbeat WHERE service_name=?",
            (row["service_name"],),
        ).fetchone()
        status = "MISSING_SERVICE"
        age = None
        if hb:
            status = hb["status"] or "unknown"
            age = now - float(hb["last_pulse"] or 0)
        conn.execute("""
            UPDATE council_role_registry
            SET heartbeat_status=?, heartbeat_age_sec=?, updated_at=?
            WHERE agent_name=?
        """, (status, age, now, row["agent_name"]))


def assign_models_for_open_tasks(conn: sqlite3.Connection) -> int:
    now = time.time()
    agents = {a["agent_name"]: a for a in CANONICAL_COUNCIL}
    tasks = conn.execute("""
        SELECT id, assigned_agent, task_type, risk_level, target_tab, title, description
        FROM council_work_queue
        WHERE status='OPEN' AND phase NOT IN ('VERIFIED','ROLLED_BACK')
        ORDER BY priority ASC, id ASC
        LIMIT 25
    """).fetchall()
    count = 0
    for task in tasks:
        agent = agents.get(str(task["assigned_agent"] or "POLARIS"), agents["POLARIS"])
        selected, tier, direction, reason = choose_model(agent, task)
        exists = conn.execute(
            "SELECT 1 FROM council_model_assignments WHERE task_id=? AND agent_name=? AND selected_model=? LIMIT 1",
            (task["id"], agent["agent_name"], selected),
        ).fetchone()
        if not exists:
            conn.execute("""
                INSERT INTO council_model_assignments(
                    created_at, task_id, agent_name, task_type, risk_level,
                    selected_model, model_tier, evolution_direction, reason
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (now, task["id"], agent["agent_name"], task["task_type"], task["risk_level"], selected, tier, direction, reason))
            count += 1
        prev = conn.execute(
            "SELECT current_model, model_tier FROM council_role_registry WHERE agent_name=?",
            (agent["agent_name"],),
        ).fetchone()
        if prev and (prev["current_model"] != selected or prev["model_tier"] != tier):
            conn.execute("""
                INSERT INTO council_model_evolution_log(
                    created_at, task_id, agent_name, old_model, new_model,
                    old_tier, new_tier, direction, reason
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (now, task["id"], agent["agent_name"], prev["current_model"], selected, prev["model_tier"], tier, direction, reason))
        conn.execute("""
            UPDATE council_role_registry
            SET current_model=?, model_tier=?, evolution_state=?, last_task_id=?,
                last_model_change_at=?, updated_at=?
            WHERE agent_name=?
        """, (selected, tier, direction, task["id"], now, now, agent["agent_name"]))
    return count


def resume_stale_tasks(conn: sqlite3.Connection) -> int:
    now = time.time()
    changed = 0
    # TESTING tasks older than 20 min without verifier progress become PATCH_READY.
    for row in conn.execute("""
        SELECT id, phase, updated_at, risk_level
        FROM council_work_queue
        WHERE status='OPEN' AND phase IN ('TESTING','APPLIED','POST_VERIFY')
    """).fetchall():
        age = now - float(row["updated_at"] or now)
        if age > 1200:
            next_phase = "PATCH_READY" if row["phase"] == "TESTING" else "FAILED"
            conn.execute("""
                UPDATE council_work_queue
                SET phase=?, updated_at=?, last_error=?
                WHERE id=?
            """, (next_phase, now, f"resumed from stale {row['phase']} after restart/timeout", row["id"]))
            changed += 1
    # High-risk PATCH_READY tasks cannot silently apply; route to approval if tests already pass.
    for row in conn.execute("""
        SELECT id, risk_level, phase, test_result, approved_by_operator
        FROM council_work_queue
        WHERE status='OPEN' AND risk_level='HIGH' AND phase='PATCH_READY'
    """).fetchall():
        if row["test_result"] and "PASS" in str(row["test_result"]).upper() and not int(row["approved_by_operator"] or 0):
            conn.execute("UPDATE council_work_queue SET phase='NEEDS_APPROVAL', updated_at=? WHERE id=?", (now, row["id"]))
            changed += 1
    return changed


def set_safe_config(conn: sqlite3.Connection) -> None:
    safe = {
        "AUTONOMOUS_BUILD_ENABLED": "1",
        "AUTONOMOUS_CODE_APPLY_ENABLED": "0",
        "GOLDEN_LATTICE_REQUIRED": "1",
        "COUNCIL_WORK_QUEUE_ENABLED": "1",
        "COUNCIL_BUILD_RESUME_ENABLED": "1",
        "COUNCIL_EXECUTION_ENABLED": "1",
        "COUNCIL_MAX_TASKS_PER_CYCLE": "1",
        "COUNCIL_TRANSIENT_BLOCK_RETRY_SECONDS": "900",
        "SOLANA_EDGE_AUDIT_STANDING_ENABLED": "1",
        "SUBSTRATE_NODE_ENABLED": "1",
        "COUNCIL_MODEL_EVOLUTION_ENABLED": "1",
        "COUNCIL_STALEMATE_RESOLVER_ENABLED": "1",
        "RUNNER_DETECTOR_ENABLED": "1",
        "RUNNER_DUD_EARLY_CUT_ENABLED": "0",
        "RUNNER_LIVE_SCALE_ENABLED": "0",
        "SMART_WALLET_CONVERGENCE_ENABLED": "1",
        "SMART_WALLET_LIVE_ENABLED": "0",
        "GRID_QUANT_BOT_ENABLED": "0",
        "GRID_QUANT_BOT_MODE": "paper",
    }
    for k, v in safe.items():
        cfg(conn, k, v)


def sync_global_standing_tasks(conn: sqlite3.Connection) -> int:
    """Mirror the launch-critical council tasks into the generic standing_tasks table
    so the UI/standing-task list shows them even before the full council queue renders."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS standing_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_key TEXT UNIQUE,
                title TEXT,
                description TEXT,
                priority INTEGER DEFAULT 5,
                status TEXT DEFAULT 'ACTIVE',
                owner TEXT,
                domain TEXT,
                acceptance_criteria TEXT,
                next_run_after REAL,
                last_run REAL,
                run_count INTEGER DEFAULT 0,
                last_outcome TEXT,
                created_at REAL,
                updated_at REAL
            )
        """)
        now = time.time()
        rows = [
            (
                "solana_edge_audit_recurring",
                "Recurring Solana edge audit",
                "Every launch and periodically: audit Solana paper/live interface, latest 30/100 closes, latch ordering, price freshness, price integrity, exit reasons, runner capture, and propose one edge improvement.",
                1,
                "ACTIVE",
                "POLARIS",
                "SOLANA",
                "Report blocker/idea/proposal in council queue; high-risk patches require Golden Lattice/operator approval.",
            ),
            (
                "council_autonomous_build_health",
                "Council autonomous build health check",
                "Continuously verify model/search availability, duplicate proposal loops, DB-busy blockers, patch journal, verifier results, and NEEDS-YOU surfacing.",
                2,
                "ACTIVE",
                "NUGGET",
                "COUNCIL",
                "Blocked reasons must be visible; low-risk apply requires backup + compile/verifier; high-risk requires operator approval.",
            ),
        ]
        inserted = 0
        for task_key, title, desc, priority, status, owner, domain, acceptance in rows:
            existed = conn.execute("SELECT 1 FROM standing_tasks WHERE task_key=? LIMIT 1", (task_key,)).fetchone()
            conn.execute("""
                INSERT INTO standing_tasks(task_key,title,description,priority,status,owner,domain,acceptance_criteria,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_key) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    priority=excluded.priority,
                    status=excluded.status,
                    owner=excluded.owner,
                    domain=excluded.domain,
                    acceptance_criteria=excluded.acceptance_criteria,
                    updated_at=excluded.updated_at
            """, (task_key, title, desc, priority, status, owner, domain, acceptance, now, now))
            if not existed:
                inserted += 1
        return inserted
    except Exception as e:
        logging.warning("sync_global_standing_tasks failed: %s", e)
        return 0


def run_once() -> dict[str, int]:
    with connect() as conn:
        ensure_tables(conn)
        set_safe_config(conn)
        seed_roles(conn)
        tasks = seed_tasks(conn)
        seed_strategies(conn)
        standing_synced = sync_global_standing_tasks(conn)
        update_heartbeats_for_roles(conn)
        model_rows = assign_models_for_open_tasks(conn)
        resumed = resume_stale_tasks(conn)
        conn.commit()
        stats = {"tasks_inserted": tasks, "standing_synced": standing_synced, "model_assignments": model_rows, "tasks_resumed": resumed}
    # SIGNOFF_COUNCIL_EXECUTION_SPINE_20260618: after setup, actually EXECUTE.
    # Previously run_once only seeded/assigned/resumed and idled — the council
    # never debated, proposed, gated, or applied. The spine is the missing engine.
    try:
        from services.council_execution_spine import run_execution_cycle
        try:
            from core.schema import get_config_value as _gcv
        except Exception:
            try:
                from schema import get_config_value as _gcv
            except Exception:
                _gcv = lambda k, d=None: d
        if str(_gcv("COUNCIL_EXECUTION_ENABLED", "1")).strip() not in ("0", "false", "False", ""):
            _maxt = 1
            try: _maxt = int(_gcv("COUNCIL_MAX_TASKS_PER_CYCLE", 1))
            except Exception: _maxt = 1
            exec_out = run_execution_cycle(max_tasks_per_cycle=_maxt)
            stats["execution"] = exec_out
            logging.info("[COUNCIL_EXECUTION] %s", exec_out)
    except Exception as _ee:
        logging.warning("[COUNCIL_EXECUTION] spine error: %s", _ee)
        stats["execution_error"] = str(_ee)
    # SIGNOFF_ACTIVE_INTEGRATION_20260720: after execution, sweep journal rows
    # into retrospectives and retry quarantined inspiration intakes. Both are
    # best-effort audit infrastructure: failure is recorded in the heartbeat
    # note, never allowed to break the build cycle.
    retro_created, quarantine_recovered = 0, 0
    try:
        from services.build_retrospective import run_once as _retro_sweep
        retro_created = int(_retro_sweep() or 0)
    except Exception as _re:
        logging.warning("[RETROSPECTIVE_SWEEP] failed: %s", _re)
        stats["retrospective_error"] = str(_re)
    try:
        from services.inspiration_intake_ledger import retry_quarantine
        quarantine_recovered = int(retry_quarantine() or 0)
    except Exception as _qe:
        logging.debug("[INTAKE_QUARANTINE_RETRY] %s", _qe)
    stats["retrospectives_created"] = retro_created
    stats["quarantine_recovered"] = quarantine_recovered

    execution = stats.get("execution") or {}
    exec_result = execution.get("result", stats.get("execution_error", "not_run"))
    exec_advanced = int(execution.get("advanced", 0) or 0)
    exec_blocked = int(execution.get("blocked", 0) or 0)
    exec_normalized = int(execution.get("normalized", 0) or 0)
    note = (f"tasks+{tasks} models+{model_rows} resumed+{resumed} "
            f"normalized+{exec_normalized} advanced+{exec_advanced} "
            f"blocked+{exec_blocked} retro+{retro_created} result={exec_result}")
    heartbeat("alive", note, tasks + model_rows + resumed + exec_normalized + exec_advanced)
    return stats


def print_status() -> None:
    with connect() as conn:
        ensure_tables(conn)
        print("=== SIX-NODE COUNCIL ===")
        for r in conn.execute("""
            SELECT agent_name, service_name, role, current_model, model_tier,
                   evolution_state, heartbeat_status, heartbeat_age_sec
            FROM council_role_registry ORDER BY id
        """).fetchall():
            age = "?" if r["heartbeat_age_sec"] is None else f"{float(r['heartbeat_age_sec']):.0f}s"
            print(f"{r['agent_name']:<8} svc={r['service_name']:<24} model={r['current_model']:<26} tier={r['model_tier']:<10} evo={r['evolution_state']:<10} hb={r['heartbeat_status']} age={age}")
        print("\n=== OPEN TASKS ===")
        for r in conn.execute("""
            SELECT id, phase, status, priority, risk_level, assigned_agent, target_tab, title
            FROM council_work_queue
            WHERE status='OPEN'
            ORDER BY priority ASC, id ASC
            LIMIT 30
        """).fetchall():
            print(f"#{r['id']:<3} {r['phase']:<15} risk={r['risk_level']:<6} agent={r['assigned_agent']:<8} tab={r['target_tab']:<16} {r['title']}")
        print("\n=== STRATEGIES ===")
        for r in conn.execute("SELECT name, enabled, mode, risk_level FROM substrate_strategy_registry ORDER BY id").fetchall():
            print(dict(r))


def run() -> None:
    heartbeat("starting", "council build orchestrator booting")
    while True:
        try:
            run_once()
        except Exception as exc:
            heartbeat("warn", f"error: {exc}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sentinuity council build orchestrator")
    parser.add_argument("--once", action="store_true", help="create/seed/resume once then exit")
    parser.add_argument("--status", action="store_true", help="print current council build status")
    args = parser.parse_args()
    if args.status:
        run_once()
        print_status()
    elif args.once:
        print(run_once())
    else:
        run()