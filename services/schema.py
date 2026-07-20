
from __future__ import annotations
import sqlite3
import time
from pathlib import Path
from typing import Optional, Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sentinuity_matrix.db"


# ──────────────────────────────────────────────────────────────────────────────
# CONNECTION LAYER (HARDENED — PRODUCTION SAFE)
# ──────────────────────────────────────────────────────────────────────────────


def get_intel_connection(timeout: float = 8.0):
    """
    Dedicated connection to sentinuity_intelligence.db.
    Used for high-frequency MTM ticks — isolated from execution DB
    to eliminate SQLite write contention on the price path.
    """
    db_path = Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db"
    conn = sqlite3.connect(str(db_path), timeout=timeout, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn

def get_connection() -> sqlite3.Connection:
    """
    Single source of truth for DB connections.

    Guarantees:
    - WAL mode (concurrent readers + writer)
    - busy timeout (prevents immediate lock failures)
    - autocommit (prevents long-lived write locks)
    - row factory for dict-style access
    """

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level=None,  # autocommit mode (critical for lock avoidance)
    )

    conn.row_factory = sqlite3.Row

    # Performance + concurrency safety
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    return conn


# ──────────────────────────────────────────────────────────────────────────────
# DB INIT (SAFE — NO HEAVY ALTER HERE)
# ──────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """
    Lightweight init only.

    IMPORTANT:
    - No ALTER TABLE here (prevents lock contention at startup)
    - Only CREATE IF NOT EXISTS (safe, non-blocking)
    """

    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_dna (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                tx_hash TEXT,
                program TEXT,
                instruction TEXT,
                token TEXT,
                amount REAL,
                entropy REAL,
                logs TEXT,
                processed INTEGER DEFAULT 0,
                processed_at REAL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                raw_dna_id INTEGER,
                tx_hash TEXT,
                token_name TEXT,
                confidence_score REAL,
                entropy REAL,
                buy_velocity REAL,
                cluster_id INTEGER,
                logic_breakdown TEXT,
                latched INTEGER DEFAULT 0,
                executed INTEGER DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY,
                wallet_balance REAL DEFAULT 1000.0,
                initial_capital REAL DEFAULT 1000.0
            )
        """)

        conn.execute(
            "INSERT OR IGNORE INTO system_state (id, wallet_balance, initial_capital) "
            "VALUES (1, 1000.0, 1000.0)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mint_blacklist (
                mint_address   TEXT PRIMARY KEY,
                reason         TEXT,
                blacklisted_at REAL
            )
        """)

        # ── PERFORMANCE INDEXES — added 2025-05 ─────────────────────────────
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ms_state     ON market_snapshots(candidate_state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ms_latched   ON market_snapshots(latched, execution_ready)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pp_status    ON paper_positions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pp_status_ts ON paper_positions(status, opened_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clog_stage   ON cognition_log(stage, timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ptr_reviewed ON polaris_trade_reviews(reviewed_at DESC)")

        conn.commit()

        # ── PAPER SLIPPAGE CONFIG DEFAULTS ────────────────────────────────
        # Inserted once — never overwrite if already set by operator
        _slip_defaults = [
            ("PAPER_SLIPPAGE_ENTRY_PCT", "1.5"),
            ("PAPER_SLIPPAGE_EXIT_PCT",  "2.5"),
            ("PAPER_FEE_PER_TX_USD",     "0.10"),
        ]
        for _k, _v in _slip_defaults:
            conn.execute(
                "INSERT OR IGNORE INTO system_config (key, value) VALUES (?, ?)",
                (_k, _v)
            )
        conn.commit()


def ensure_hub_compat_schema() -> None:
    """Safe additive schema for the current sovereign hub and executor surfaces.

    Only creates missing tables / columns expected by the latest hub.
    Never drops or rewrites existing data.

    PATCH F1 (CRITICAL): Removed recursive self-call on the first line of the
    try block. The original code called ensure_hub_compat_schema() before
    doing anything, producing a RecursionError that was silently swallowed by
    the outer except Exception: pass. This function had therefore never
    successfully executed on any previous run. All tables and columns it was
    supposed to create were absent from production DBs.

    Also adds latch_claimed_until to market_snapshots — required by the
    scan_for_entries claim-lock in execution_engine.py. Without it, the
    UPDATE targeting that column silently fails and the lock is never set,
    allowing the guardian to reset an actively-opening snapshot mid-open.
    """
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS polaris_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_hash TEXT UNIQUE,
                    proposal_type TEXT,
                    proposal_text TEXT,
                    suggested_action TEXT,
                    confidence REAL DEFAULT 0.0,
                    metrics_json TEXT,
                    status TEXT DEFAULT 'open',
                    created_at REAL,
                    last_seen_at REAL,
                    seen_count INTEGER DEFAULT 1
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS debate_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    speaker         TEXT,
                    action          TEXT,
                    message         TEXT,
                    content_json    TEXT,
                    logged_at       REAL,
                    thinking_state  TEXT,
                    verdict_type    TEXT,
                    transcript_json TEXT,
                    approved_by     TEXT,
                    proposal_id     INTEGER,
                    grok_narrative  TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS patch_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    applied_at REAL,
                    proposal_type TEXT,
                    action TEXT,
                    param_key TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    outcome TEXT,
                    brave_confirmed INTEGER DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS polaris_trade_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    outcome TEXT,
                    pnl REAL,
                    confidence REAL,
                    notes TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    win_loss TEXT,
                    exit_category TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    duration REAL,
                    signal_id INTEGER,
                    realized_pnl_usd REAL,
                    reviewed_at TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cognition_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    stage TEXT,
                    token TEXT,
                    message TEXT,
                    confidence REAL DEFAULT 0.0
                )
            """)

            def _ensure_column(table: str, column: str, coltype: str) -> None:
                cols = {r["name"] for r in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

            # Hub / pricing compatibility
            try:
                _ensure_column("paper_positions", "mark_source",           "TEXT DEFAULT 'unknown'")
                _ensure_column("paper_positions", "live_exec_price",       "REAL")
                _ensure_column("paper_positions", "live_exec_pct",         "REAL")
                _ensure_column("paper_positions", "live_exec_source",      "TEXT")
                _ensure_column("paper_positions", "live_exec_updated_at",  "REAL")
                _ensure_column("paper_positions", "live_exec_age_sec",     "REAL")
                _ensure_column("paper_positions", "live_exec_confidence",  "REAL")
                _ensure_column("paper_positions", "live_exec_can_exit",    "INTEGER DEFAULT 0")
                _ensure_column("paper_positions", "final_exec_pct",        "REAL")
                _ensure_column("paper_positions", "exit_category",         "TEXT")
                _ensure_column("paper_positions", "win_loss",              "TEXT")
            except Exception:
                pass

            # PATCH F1: latch_claimed_until — required by scan_for_entries claim-lock.
            # Was never created because ensure_hub_compat_schema never ran successfully.
            # Without this column the UPDATE in scan_for_entries silently fails and the
            # guardian can reset a snapshot that is mid-open.
            try:
                _ensure_column("market_snapshots", "latch_claimed_until", "REAL")
                _ensure_column("market_snapshots", "confidence",           "REAL DEFAULT 0.0")
                _ensure_column("market_snapshots", "mint_confidence",      "REAL DEFAULT 0.0")
                # Permanent age semantics contract — explicit ages, no overloaded token_age_seconds.
                _ensure_column("market_snapshots", "token_age_seconds",        "REAL")  # legacy alias for token_birth_age_seconds
                _ensure_column("market_snapshots", "signal_discovered_at",     "REAL")
                _ensure_column("market_snapshots", "signal_age_seconds",       "REAL")
                _ensure_column("market_snapshots", "token_birth_at",           "REAL")
                _ensure_column("market_snapshots", "token_birth_age_seconds",  "REAL")
                _ensure_column("market_snapshots", "price_age_seconds",        "REAL")
                # ── TX_RESOLVER v2 calibration columns ──────────────────────
                # Added 2026-05-24. Allows neural_supervisor to persist the
                # full resolver output back to each snapshot so downstream
                # consumers (UI, executor, governor, shadow tracker) can read
                # raw vs calibrated confidence and runner conviction directly.
                # The neural_supervisor PRAGMA-checks for these before writing,
                # so they are optional, but adding them unlocks the new fields
                # in the hub UI and reporting tools.
                _ensure_column("market_snapshots", "raw_confidence",        "REAL")
                _ensure_column("market_snapshots", "calibrated_confidence", "REAL")
                _ensure_column("market_snapshots", "runner_conviction",     "REAL")
                _ensure_column("market_snapshots", "runner_tier",           "TEXT")
                _ensure_column("market_snapshots", "confidence_source",     "TEXT")
            except Exception:
                pass

            # Trade review compatibility columns expected by latest hub/executor
            for col, coltype in [
                ("hold_seconds",          "REAL DEFAULT 0"),
                ("entry_mint_confidence", "REAL DEFAULT 0"),
                ("position_id",           "INTEGER"),
                ("token_name",            "TEXT"),
                ("mint_address",          "TEXT"),
                ("pnl_pct",               "REAL DEFAULT 0"),
                ("polaris_version",       "TEXT DEFAULT 'unknown'"),
            ]:
                try:
                    _ensure_column("polaris_trade_reviews", col, coltype)
                except Exception:
                    pass

            # debate_log extensibility — adds columns required by sovereign_governor
            # _write_debate_turn() and _write_approved_status(). Without these the
            # INSERT fails silently (caught by except in _write_debate_turn), leaving
            # debate_log permanently empty and the chamber blank.
            for col, coltype in [
                ("thinking_state",  "TEXT"),
                ("verdict_type",    "TEXT"),
                ("transcript_json", "TEXT"),
                ("approved_by",     "TEXT"),
                ("proposal_id",     "INTEGER"),
                # legacy columns — safe no-ops on DBs that already have them
                ("action",          "TEXT"),
                ("message",         "TEXT"),
                ("content_json",    "TEXT"),
                ("logged_at",       "REAL"),
                ("grok_narrative",  "TEXT"),   # narrative synthesis per debate turn
            ]:
                try:
                    _ensure_column("debate_log", col, coltype)
                except Exception:
                    pass

            for col, coltype in [
                ("applied_at",      "REAL"),
                ("proposal_type",   "TEXT"),
                ("action",          "TEXT"),
                ("param_key",       "TEXT"),
                ("old_value",       "TEXT"),
                ("new_value",       "TEXT"),
                ("outcome",         "TEXT"),
                ("brave_confirmed", "INTEGER DEFAULT 0"),
            ]:
                try:
                    _ensure_column("patch_history", col, coltype)
                except Exception:
                    pass

            # ── FORGE TABLES ─────────────────────────────────────────────
            # forge_projects and forge_research_cache are queried by
            # intelligence_orchestrator and provider_firewall but were never
            # created anywhere in the codebase. Safe idempotent creation only.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS forge_projects (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_key   TEXT    UNIQUE NOT NULL,
                    title         TEXT    NOT NULL,
                    description   TEXT,
                    status        TEXT    DEFAULT 'active',
                    priority      INTEGER DEFAULT 10,
                    current_stage TEXT    DEFAULT 'RESEARCH',
                    created_at    REAL,
                    updated_at    REAL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS forge_research_cache (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_key TEXT,
                    topic       TEXT,
                    summary     TEXT,
                    source      TEXT,
                    confidence  REAL DEFAULT 0.5,
                    created_at  REAL,
                    expires_at  REAL
                )
            """)

            # ── market_snapshots temporal tiering columns ─────────────────
            # neural_supervisor._ensure_temporal_schema() defines these but
            # is never called at startup. Adding here so they exist before
            # the supervisor's first query cycle.
            for col, coltype in [
                ("tier",              "TEXT DEFAULT 'COLD'"),
                ("freshness_score",   "REAL DEFAULT 0.0"),
                ("active_cognition",  "INTEGER DEFAULT 1"),
                ("last_cognition_at", "REAL"),
                ("latched_at",        "REAL"),   # Phase A: timestamp when supervisor made latch decision
            ]:
                try:
                    _ensure_column("market_snapshots", col, coltype)
                except Exception:
                    pass

            # ── polaris_proposals FORGE column migrations ─────────────────
            # Six columns used by intelligence_orchestrator and
            # sovereign_governor were never added via _ensure_column.
            # DEFAULT 'TRADING' on proposal_domain preserves all existing rows.
            for col, coltype in [
                ("proposal_domain",  "TEXT DEFAULT 'TRADING'"),
                ("stage",            "TEXT"),
                ("project_key",      "TEXT"),
                ("retry_count",      "INTEGER DEFAULT 0"),
                ("cooldown_until",   "REAL"),
                ("api_health_state", "TEXT"),
            ]:
                try:
                    _ensure_column("polaris_proposals", col, coltype)
                except Exception:
                    pass

            # ── Exceptional Live Runner Escalation tables ─────────────────
            # Owned by runner_likelihood_detector.py. Created here at startup
            # so they are always available before execution_engine runs.
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS live_escalation_ledger (
                        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at                  REAL,
                        mint_address                TEXT,
                        token_name                  TEXT,
                        token_symbol                TEXT,
                        source_snapshot_id          INTEGER,
                        paper_position_id           INTEGER,
                        strict_gate_result          TEXT,
                        legacy_gate_result          TEXT,
                        relaxed_gate_result         TEXT,
                        runner_score                REAL,
                        runner_score_pct            REAL,
                        confidence                  REAL,
                        raw_confidence              REAL,
                        calibrated_confidence       REAL,
                        freshness_score             REAL,
                        price_freshness_seconds     REAL,
                        token_age_seconds           REAL,
                        signal_age_seconds          REAL,
                        curve_progress_pct          REAL,
                        liquidity_usd               REAL,
                        wallet_convergence_score    REAL,
                        smart_wallet_count          INTEGER,
                        elite_wallet_count          INTEGER,
                        first_tick_delay_sec        REAL,
                        entry_latency_sec           REAL,
                        live_escalation_state       TEXT,
                        escalation_reason           TEXT,
                        veto_reason                 TEXT,
                        executed_live               INTEGER DEFAULT 0,
                        live_position_id            INTEGER,
                        live_entry_price            REAL,
                        live_exit_price             REAL,
                        live_realized_pnl_usd       REAL,
                        live_realized_pnl_pct       REAL,
                        max_favorable_excursion_pct REAL,
                        max_adverse_excursion_pct   REAL,
                        exit_reason                 TEXT,
                        reviewed_at                 REAL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_lel_mint ON live_escalation_ledger(mint_address, created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_lel_state ON live_escalation_ledger(live_escalation_state, created_at DESC)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS legacy_cluster_candidates (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at      REAL,
                        mint_address    TEXT,
                        token_name      TEXT,
                        token_symbol    TEXT,
                        snapshot_id     INTEGER,
                        strict_gate     TEXT,
                        legacy_gate     TEXT,
                        relaxed_gate    TEXT,
                        confidence      REAL,
                        liquidity_usd   REAL,
                        volume_5m_usd   REAL,
                        runner_tier     TEXT,
                        runner_score    REAL,
                        maturity_stage  TEXT,
                        did_run         INTEGER DEFAULT 0,
                        peak_pct        REAL,
                        reject_reason   TEXT,
                        notes           TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_lcc_mint ON legacy_cluster_candidates(mint_address, created_at DESC)")
            except Exception:
                pass

            conn.commit()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# HEARTBEAT SYSTEM
# ──────────────────────────────────────────────────────────────────────────────

def update_heartbeat(
    service_name: str,
    status: str,
    note: str = "",
    work_processed: int = 0,
    last_success_at=None,
) -> None:
    """
    Column-safe heartbeat write.

    Discovers actual columns in system_heartbeat at runtime and only
    writes columns that exist. Older DB versions lack note /
    work_processed / last_success_at — writing missing columns caused
    a silent exception that swallowed every heartbeat write entirely.
    """
    import time as _t
    now = _t.time()
    try:
        with get_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS system_heartbeat "
                "(service_name TEXT PRIMARY KEY, status TEXT, note TEXT, "
                "last_pulse REAL, work_processed INTEGER DEFAULT 0, "
                "last_success_at REAL, restart_claimed_until REAL DEFAULT 0)"
            )
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(system_heartbeat)"
            ).fetchall()}
            fields  = ["service_name", "status", "last_pulse"]
            vals    = [service_name, status, now]
            updates = ["status=excluded.status", "last_pulse=excluded.last_pulse"]
            if "note" in cols:
                fields.append("note"); vals.append(note)
                updates.append("note=excluded.note")
            if "work_processed" in cols:
                fields.append("work_processed"); vals.append(work_processed)
                updates.append("work_processed=excluded.work_processed")
            if "last_success_at" in cols:
                fields.append("last_success_at"); vals.append(last_success_at)
                updates.append("last_success_at=excluded.last_success_at")
            f_str = ", ".join(fields)
            p_str = ", ".join("?" for _ in fields)
            u_str = ", ".join(updates)
            conn.execute(
                "INSERT INTO system_heartbeat (" + f_str + ") "
                "VALUES (" + p_str + ") "
                "ON CONFLICT(service_name) DO UPDATE SET " + u_str,
                vals,
            )
            conn.commit()
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG ACCESS
# ──────────────────────────────────────────────────────────────────────────────

def get_config_value(key: str, default: Any = None) -> Any:
    """
    Safe config fetch.

    Never blocks system if table missing.
    """

    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = ?",
                (key,)
            ).fetchone()

            if row:
                return row["value"]

    except Exception:
        pass

    return default

# ─────────────────────────────────────────────────────────────────────────────
# POLARIS HELPERS (required by polaris.py)
# ─────────────────────────────────────────────────────────────────────────────

def insert_polaris_proposal(
    proposal_type: str,
    proposal_text: str,
    suggested_action: str = "",
    confidence: float = 0.0,
    metrics: Any = None,
) -> bool:
    """Insert a Polaris proposal. Returns True if new, False if duplicate."""
    import json as _json
    import hashlib as _hashlib

    metrics = metrics or {}
    phash = _hashlib.sha256(
        f"{proposal_type}|{proposal_text}|{suggested_action}".encode()
    ).hexdigest()

    try:
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM polaris_proposals WHERE proposal_hash = ?",
                (phash,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE polaris_proposals SET last_seen_at=?, seen_count=seen_count+1 "
                    "WHERE proposal_hash=?",
                    (time.time(), phash)
                )
                return False

            conn.execute("""
                INSERT INTO polaris_proposals (
                    proposal_hash, proposal_type, proposal_text, suggested_action,
                    confidence, metrics_json, status, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """, (
                phash, proposal_type, proposal_text, suggested_action,
                confidence, _json.dumps(metrics), time.time(), time.time()
            ))
            return True
    except Exception:
        return False


def queue_improvement(source: str, category: str, payload: Any) -> bool:
    """Queue an improvement suggestion from any service."""
    import json as _json

    payload = payload or {}
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS improvement_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    payload_json TEXT,
                    created_at REAL
                )
            """)
            conn.execute(
                "INSERT INTO improvement_queue (source, category, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (source, category, _json.dumps(payload), time.time())
            )
            return True
    except Exception:
        return False


def startup_cleanup() -> None:
    """
    Safe startup cleanup — resets stuck pipeline rows.
    Called by Launch_Sentinuity.bat Phase 2 before services start.
    """
    import time as _time
    now = _time.time()
    try:
        with get_connection() as conn:
            # ── SELF-HEAL: critical operational tables ─────────────────────
            # paper_positions missing causes 'no such table' in execution_engine.
            # Create minimally if not present — schema migration fills columns later.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint_address  TEXT,
                    token_name    TEXT,
                    status        TEXT DEFAULT 'OPEN',
                    opened_at     REAL,
                    closed_at     REAL,
                    entry_price   REAL,
                    position_size_usd REAL DEFAULT 0,
                    realized_pnl_usd  REAL DEFAULT 0
                )
            """)
            # market_snapshots missing would crash entire pipeline
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint_address    TEXT,
                    token_name      TEXT,
                    candidate_state TEXT DEFAULT 'pending',
                    latched         INTEGER DEFAULT 0,
                    execution_ready INTEGER DEFAULT 0,
                    is_tradeable    INTEGER DEFAULT 0,
                    created_at      REAL,
                    updated_at      REAL
                )
            """)
            # raw_dna missing would crash ingest pipeline
            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw_dna (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    processed_state INTEGER DEFAULT 0,
                    claim_until     REAL,
                    created_at      REAL
                )
            """)

            # ── RESET stuck pipeline rows ──────────────────────────────────
            # Reset stuck resolver claims
            conn.execute("""
                UPDATE raw_dna SET processed_state=1, claim_until=NULL
                WHERE processed_state=99
                  AND (claim_until IS NULL OR claim_until < ?)
            """, (now - 60,))
            
            # Reset stuck market_snapshots processing rows
            try:
                conn.execute("""
                    UPDATE market_snapshots SET quality_status='pending', quality_reason=''
                    WHERE quality_status='processing'
                """)
            except Exception:
                pass  # column may not exist in all schema versions
            
            # ── CRITICAL FIX 2026-05-25: Clear ALL stale candidates from previous session ───
            # Old version only caught pending/priced/retry/qualified. stale_prelaunch
            # rows survived and starved the qualifier with 204 stale rows.
            # Now: also catches stale_prelaunch, SIGNAL_STALE quality_reason,
            # AGE CONTRACT: token birth age is NOT startup-staleness. Window 10 min (was 30).
            # MTM, executed, vetoed, exited preserved for accounting.
            try:
                # PRAGMA check to use schema-safe columns
                _ms_cols = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
                _has_price_status     = "price_status"     in _ms_cols
                _has_active_cognition = "active_cognition" in _ms_cols
                _has_qcu              = "qualify_claimed_until" in _ms_cols
                _has_lcu              = "latch_claimed_until"   in _ms_cols
                _has_ecu              = "execution_claimed_until" in _ms_cols

                # Build extra SET clauses dynamically
                _extra_sets = []
                if _has_price_status:     _extra_sets.append("price_status='dead'")
                if _has_active_cognition: _extra_sets.append("active_cognition=0")
                if _has_qcu:              _extra_sets.append("qualify_claimed_until=NULL")
                if _has_lcu:              _extra_sets.append("latch_claimed_until=NULL")
                if _has_ecu:              _extra_sets.append("execution_claimed_until=NULL")
                _extra_sql = (", " + ", ".join(_extra_sets)) if _extra_sets else ""

                # AGE CONTRACT: never purge purely by token birth age.
                # Operational staleness uses created_at / first_seen_at / updated_at only.

                expired = conn.execute(f"""
                    UPDATE market_snapshots
                    SET candidate_state='expired_stale',
                        quality_status='rejected',
                        quality_reason='STARTUP_CLEANUP_EXPIRED',
                        execution_ready=0,
                        latched=0
                        {_extra_sql}
                    WHERE (
                        candidate_state IN ('pending', 'priced', 'retry', 'qualified', 'stale_prelaunch')
                        OR quality_reason LIKE 'SIGNAL_STALE_%'
                        OR COALESCE(created_at, first_seen_at, updated_at, 0) < ?
                    )
                    AND candidate_state NOT IN ('executed', 'vetoed', 'exited', 'mtm')
                """, (now - 600,)).rowcount  # 10 minutes (tightened from 30)
                if expired:
                    print(f"[STARTUP_CLEANUP] Expired {expired} stale candidate(s) from previous session")
            except Exception as _cleanup_err:
                print(f"[STARTUP_CLEANUP] market_snapshots cleanup error: {_cleanup_err}")

            # ── RAW_DNA cleanup — schema-safe (no updated_at reference) ─────
            # Old script crashed on raw_dna because it referenced updated_at column
            # that does not exist. Build timestamp expression from columns that do.
            try:
                _rd_cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_dna)").fetchall()}
                _ts_candidates = []
                for c in ("first_seen_at", "created_at", "processed_at", "detected_at", "timestamp"):
                    if c in _rd_cols:
                        _ts_candidates.append(f"COALESCE({c}, 0)")
                if _ts_candidates:
                    _ts_expr = " + ".join(_ts_candidates) if len(_ts_candidates) == 1 else f"MAX({', '.join(_ts_candidates)})"
                    # Use first column directly (avoids MAX() arity issues in older sqlite)
                    _ts_expr = _ts_candidates[0]
                    
                    # Build raw_dna SET clauses dynamically
                    _rd_sets = ["processed_state = -1"]
                    if "resolution_note" in _rd_cols:
                        _rd_sets.append("resolution_note='STARTUP_CLEANUP_STALE_RAW_DNA'")
                    if "claim_until" in _rd_cols:
                        _rd_sets.append("claim_until=NULL")
                    if "claimed_until" in _rd_cols:
                        _rd_sets.append("claimed_until=NULL")
                    _rd_set_sql = ", ".join(_rd_sets)
                    
                    raw_purged = conn.execute(f"""
                        UPDATE raw_dna
                        SET {_rd_set_sql}
                        WHERE processed_state IN (0, 1, 99)
                          AND {_ts_expr} > 0
                          AND {_ts_expr} < ?
                    """, (now - 600,)).rowcount
                    if raw_purged:
                        print(f"[STARTUP_CLEANUP] Expired {raw_purged} stale raw_dna row(s)")
            except Exception as _rd_err:
                print(f"[STARTUP_CLEANUP] raw_dna cleanup error: {_rd_err}")
            
            conn.commit()
    except Exception:
        pass  # Never block launch on cleanup failure