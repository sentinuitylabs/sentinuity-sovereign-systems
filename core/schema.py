from __future__ import annotations

import sqlite3
import time
import random
from pathlib import Path
from typing import Optional, Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sentinuity_matrix.db"


# ──────────────────────────────────────────────────────────────────────────────
# CONNECTION LAYER (HARDENED — PRODUCTION SAFE)
# ──────────────────────────────────────────────────────────────────────────────


LOCK_RETRY_MAX_SEC = 30.0
LOCK_RETRY_BASE_SEC = 0.04


def _is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in text or "database table is locked" in text
    )


def _retry_locked(call, *, max_wait_sec: float = LOCK_RETRY_MAX_SEC):
    """Retry transient SQLite writer contention without masking real SQL errors.

    Sentinuity is a multi-process organism with many short writers. WAL permits
    concurrent readers but still has one writer at a time. This helper waits for
    that writer instead of turning ordinary contention into dropped execution,
    mark, ingest, Council or telemetry work.
    """
    deadline = time.monotonic() + max(0.1, float(max_wait_sec))
    delay = LOCK_RETRY_BASE_SEC
    while True:
        try:
            return call()
        except Exception as exc:
            if not _is_lock_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay + random.random() * min(delay, 0.05))
            delay = min(delay * 1.7, 0.75)


class ResilientCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        parent = super().execute
        return _retry_locked(lambda: parent(sql, parameters))

    def executemany(self, sql, seq_of_parameters):
        parent = super().executemany
        return _retry_locked(lambda: parent(sql, seq_of_parameters))

    def executescript(self, sql_script):
        parent = super().executescript
        return _retry_locked(lambda: parent(sql_script))


class ResilientConnection(sqlite3.Connection):
    def cursor(self, factory=ResilientCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        return self.cursor().executescript(sql_script)

    def commit(self):
        parent = super().commit
        return _retry_locked(parent)


def _configure_connection(conn: sqlite3.Connection, *, busy_ms: int = 30000) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    # Do not force PRAGMA journal_mode=WAL on every connection: changing/querying
    # journal state during a writer storm can itself contend. Only transition when
    # the database is not already WAL.
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if mode != "wal":
            conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={int(max(1000, busy_ms))}")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_intel_connection(timeout: float = 8.0):
    """
    Dedicated connection to sentinuity_intelligence.db.
    Used for high-frequency MTM ticks — isolated from execution DB
    to eliminate SQLite write contention on the price path.
    """
    db_path = Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db"
    conn = sqlite3.connect(
        str(db_path), timeout=max(float(timeout), 30.0), check_same_thread=False,
        isolation_level=None, factory=ResilientConnection,
    )
    return _configure_connection(conn, busy_ms=30000)

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
        check_same_thread=False,
        factory=ResilientConnection,
    )
    return _configure_connection(conn, busy_ms=30000)


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
            CREATE TABLE IF NOT EXISTS live_shadow_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER, mint_address TEXT, token_name TEXT,
                scored_at REAL, verdict TEXT, reason TEXT,
                would_be_live_eligible INTEGER DEFAULT 0,
                curve_sol_reserves REAL, curve_progress_pct REAL, price_age_sec REAL,
                entry_impact_pct REAL, exit_impact_pct REAL, round_trip_impact_pct REAL,
                modeled_gas_usd REAL, position_size_usd REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_live_shadow_position ON live_shadow_ledger(position_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_live_shadow_scored ON live_shadow_ledger(scored_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mode_b_decision_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER, position_id INTEGER,
                mint_address TEXT, token_name TEXT, evaluated_at REAL,
                verdict TEXT, reasons TEXT, adjusted_confidence REAL,
                confidence_floor REAL, smart_money_tier TEXT,
                tide_state TEXT, tide_density REAL, oracle_state TEXT,
                signal_age_sec REAL, cluster_losses_2h INTEGER,
                live_armed INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mode_b_decision_time ON mode_b_decision_ledger(evaluated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mode_b_decision_mint ON mode_b_decision_ledger(mint_address,evaluated_at)")

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
            conn.commit()
    except Exception:
        pass  # Never block launch on cleanup failure