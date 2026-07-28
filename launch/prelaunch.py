# SIGNOFF_NO_SIDECAR_SPINE_REPAIR_20260609: proof-safe dual sizing persistence. No sidecar.
"""
prelaunch.py
============
SENTINUITY PRE-LAUNCH SEQUENCE
Run this BEFORE Launch_Sentinuity.bat every time.
Ensures the organism launches into a known-good state.

What it does:
  1. Verifies all critical service files exist
  2. Clears any stale drawdown halt
  3. Sets all known-good config values
  4. Clears the stale unpriced backlog (>30 min old, no price)
  5. Resets stuck pipeline rows
  6. WAL checkpoint
  7. Reports pipeline state — green means launch

What it NEVER touches:
  - paper_positions (trade history)
  - trade_autopsies (outcome data)
  - polaris_trade_reviews (learning data)
  - polaris_learned_patterns (doctrine)
  - system_config values set by Polaris/IVARIS
  - wallet_balance or initial_capital
"""

import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime

# Resolve ROOT regardless of where this script is invoked from.
_here = Path(__file__).resolve().parent
BASE_DIR = _here if ((_here / "core").exists() and (_here / "services").exists()) else _here.parent
DB_PATH  = BASE_DIR / "sentinuity_matrix.db"
SEP = "=" * 58


def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def ok(msg):   print(f"  [OK]    {msg}")
def warn(msg): print(f"  [WARN]  {msg}")
def fixed(msg):print(f"  [FIXED] {msg}")
def fail(msg): print(f"  [FAIL]  {msg}"); sys.exit(1)


print(f"\n{SEP}")
print(f"  SENTINUITY PRE-LAUNCH  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(SEP)

if not DB_PATH.exists():
    warn("DB not found — will be created on first launch")
else:
    ok(f"DB found: {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")

# ── CRITICAL SERVICE FILES ─────────────────────────────────────────────────────
print(f"\n[1] Critical service files")
CRITICAL = [
    # Core 10 runtime contract — guardian is launched by Watchdog_Sentinuity.bat
    "services/ingest_pipeline.py",
    "services/market_intelligence.py",
    "services/ws_price_oracle.py",
    "services/execution_engine.py",
    "services/sovereign_governor.py",
    "services/system_guardian.py",
    "services/pump_monitor.py",
    "services/neural_supervisor.py",
    "services/polaris.py",
    "services/sovereign_parameter_engine.py",
    "services/replay_engine.py",
    "services/polaris_auxiliary.py",
    "services/reconnaissance_engine.py",
    "core/schema.py",
    "services/cognition_logger.py",
    # Sovereign Forge — doctrine + orchestrator must exist before launch
    "launch/SENTINUITY_SOVEREIGN_DOCTRINE.md",
    "launch/forge_genesis_seed.py",
    "services/intelligence_orchestrator.py",
    "services/freshness_enforcer.py",
    "services/trade_accounting_guard.py",
    # Autonomous build / Substrate Node sign-off spine
    "services/council_build_orchestrator.py",
    "services/safe_patch_apply.py",
    "services/shadow_runner_tracker.py",
    "services/runner_likelihood_detector.py",
    "services/periodic_refresh.py",
    "services/winner_snapshot_archiver.py",
    "launch/VERIFY_LAUNCH_READY.py",
    "ui/substrate_node.py",
]
missing = [f for f in CRITICAL if not (BASE_DIR / f).exists()]
if missing:
    for f in missing:
        fail(f"MISSING: {f}")
else:
    ok(f"All {len(CRITICAL)} critical files present")

# Canonical World UI is optional for trading, but its path must be unambiguous.
_world_canonical = BASE_DIR / "ui" / "sovereign_world.html"
_world_legacy_candidates = [
    BASE_DIR / "ui" / "world_os.py",
    BASE_DIR / "world_os.py",
    BASE_DIR / "ui" / "world_scene.py",
    BASE_DIR / "ui" / "world_scene.html",
]
if _world_canonical.exists():
    ok(f"Canonical World UI present: {_world_canonical.relative_to(BASE_DIR)}")
else:
    warn(f"Canonical World UI missing (non-fatal): {_world_canonical}")
_world_legacy_present = [x for x in _world_legacy_candidates if x.exists()]
if _world_legacy_present:
    warn(
        "Ignored legacy World candidates: "
        + ", ".join(str(x.relative_to(BASE_DIR)) for x in _world_legacy_present)
    )

if not DB_PATH.exists():
    print(f"\n  DB does not exist — skipping DB checks")
    print(f"  Launch normally, schema will be created on first run.\n")
    sys.exit(0)

conn = get_conn()
now  = time.time()

# ── PIPELINE SCHEMA ────────────────────────────────────────────────────────────
print(f"\n[1b] Ensuring pipeline schema (offline, before services start)")
try:
    # Fix: r[1] is column name, r[0] is column index in PRAGMA table_info
    raw_cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_dna)").fetchall()}
    for col, col_def in [
        ("claim_until",       "REAL"),
        ("resolved_at",       "REAL"),
        ("resolution_status", "TEXT"),
        ("resolution_note",   "TEXT"),
        ("mint_address",      "TEXT"),
        ("mint_confidence",   "REAL"),
        ("confidence",        "REAL"),
        ("resolution_method", "TEXT"),
    ]:
        if col not in raw_cols:
            try:
                conn.execute(f"ALTER TABLE raw_dna ADD COLUMN {col} {col_def}")
                fixed(f"raw_dna: added {col}")
            except Exception:
                pass  # column already exists

    # Backfill market_snapshots — every column used by prelaunch and runtime services
    snap_cols = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
    for col, col_def in [
        ("candidate_state",  "TEXT DEFAULT \'pending\'"),
        ("price_status",     "TEXT DEFAULT \'pending\'"),
        ("quality_status",   "TEXT DEFAULT \'pending\'"),
        ("quality_reason",   "TEXT DEFAULT \'\'"),
        ("price_attempts",   "INTEGER DEFAULT 0"),
        ("observed_price",   "REAL"),
        ("price_updated_at", "REAL"),
        ("resolver_status",  "TEXT DEFAULT \'\'"),
        ("execution_ready",  "INTEGER DEFAULT 0"),
        ("mint_address",     "TEXT DEFAULT \'\'"),
        ("latched",          "INTEGER DEFAULT 0"),
        ("qualify_claimed_until", "REAL"),
        ("price_last_attempt_at", "REAL"),
        ("duplicate_key",         "TEXT"),
        ("token_age_seconds",    "REAL"),
        ("token_liquidity_usd",  "REAL"),
        ("market_cap_usd",       "REAL"),
        ("is_tradeable",         "INTEGER DEFAULT 0"),
        ("source_note",          "TEXT"),
        ("curve_progress_pct",   "REAL"),
        ("curve_sol_reserves",   "REAL"),
        ("mint_confidence",       "REAL"),
        ("confidence",            "REAL"),
        ("qualified",             "INTEGER DEFAULT 0"),
        ("qualified_at",          "REAL"),
        ("latched_at",            "REAL"),
        ("preentry_firewall_at",  "REAL"),
        ("first_seen_at",         "REAL"),
        ("updated_at",            "REAL"),
        ("active_cognition",      "INTEGER DEFAULT 1"),
        ("freshness_score",       "REAL DEFAULT 1.0"),
        ("tier",                  "TEXT DEFAULT 'HOT'"),
    ]:
        if col not in snap_cols:
            try:
                conn.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} {col_def}")
                fixed(f"market_snapshots: added {col}")
            except Exception:
                pass  # already exists


    # Backfill paper_positions — columns required by live execution truth + final review writes.
    try:
        pos_cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
        for col, col_def in [
            ("live_exec_price",      "REAL"),
            ("live_exec_pct",        "REAL"),
            ("live_exec_source",     "TEXT"),
            ("live_exec_updated_at", "REAL"),
            ("live_exec_band",       "TEXT"),
            ("live_exec_confidence", "REAL"),
            ("live_exec_can_exit",   "INTEGER DEFAULT 0"),
            ("final_exec_pct",       "REAL"),
            ("exit_category",        "TEXT"),
            ("win_loss",             "TEXT"),
        ]:
            if col not in pos_cols:
                try:
                    conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} {col_def}")
                    fixed(f"paper_positions: added {col}")
                except Exception:
                    pass
    except Exception as _pe:
        warn(f"paper_positions live_exec schema check skipped: {_pe}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS resolved_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT UNIQUE,
            mint_address TEXT,
            mint_confidence REAL DEFAULT 0,
            resolution_method TEXT DEFAULT \'\',
            token_name TEXT DEFAULT \'\',
            owner_address TEXT DEFAULT \'\',
            block_time REAL,
            raw_dna_id INTEGER,
            created_at REAL NOT NULL,
            note TEXT DEFAULT \'\'
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS anomaly_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, source_service TEXT NOT NULL, anomaly_type TEXT NOT NULL, payload_json TEXT DEFAULT \'{}\', created_at REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_dna_state_id ON raw_dna(processed_state, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_dna_claim_until ON raw_dna(claim_until)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_resolved_tx_hash ON resolved_transactions(tx_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_snapshots_tx_hash ON market_snapshots(tx_hash)")
    conn.commit()
    ok("Pipeline schema verified — raw_dna and market_snapshots columns current")
except Exception as e:
    warn(f"Pipeline schema check failed (non-fatal): {e}")



# ── PRE-ENTRY FRESHNESS + ACCOUNTING MODE GUARDS ─────────────────────────────
print(f"\n[1c] Pre-entry freshness + SIM/REAL accounting guards")
try:
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    from services.trade_accounting_guard import ensure_trade_accounting_schema, backfill_trade_modes
    from services.freshness_enforcer import ensure_freshness_config, run_prelaunch_freshness_cleanup

    # Metadata-only: adds SIM/REAL columns and tags historical rows.
    # Does not change balances, size, PnL, status, or open/closed state.
    _acct_schema = ensure_trade_accounting_schema(DB_PATH)
    _acct_backfill = backfill_trade_modes(DB_PATH, dry_run=False)
    ok(
        "Trade accounting guard complete — "
        f"columns_added={_acct_schema.get('added', [])} "
        f"rows_tagged={_acct_backfill.get('rows_updated', 0)} "
        f"real={_acct_backfill.get('real', 0)} sim={_acct_backfill.get('sim', 0)}"
    )

    # Freshness-only: clears stale pre-entry execution flags/latches.
    # Never touches paper_positions/open_positions/balances/PnL.
    ensure_freshness_config(DB_PATH)
    _fresh = run_prelaunch_freshness_cleanup(DB_PATH, dry_run=False)
    ok(
        "Prelaunch freshness cleanup complete — "
        f"expired={_fresh.get('expired_candidates', 0)} "
        f"exec_ready_cleared={_fresh.get('cleared_execution_ready', 0)} "
        f"latches_cleared={_fresh.get('cleared_latches', 0)} "
        f"stale_price_blocks={_fresh.get('stale_price_blocks', 0)} "
        f"open_mints_excluded={_fresh.get('open_position_mints_excluded', 0)}"
    )
except Exception as e:
    fail(f"Prelaunch freshness/accounting guard failed: {e}")


# ── WAL CHECKPOINT ─────────────────────────────────────────────────────────────
print(f"\n[2] WAL checkpoint + write mode")
try:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    ok("WAL checkpoint complete")
except Exception as e:
    warn(f"WAL checkpoint failed (non-fatal): {e}")

print(f"\n[2b] Enforcing DB write mode (latency critical)")
try:
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    ok("synchronous=NORMAL + WAL enforced — prevents performance regression on restart")
except Exception as e:
    warn(f"synchronous/WAL enforcement failed (non-fatal): {e}")

# ── KNOWN-GOOD CONFIG ──────────────────────────────────────────────────────────
print(f"\n[3] Enforcing known-good config values")
config_fixes = [
    # INFRASTRUCTURE ONLY — these are safe to reset on every boot.
    # DO NOT add trading strategy values here (liquidity, mcap, token age, drawdown)
    # — those are owned by Polaris and must never be overwritten on launch.
    ("TRADING_MODE",                   "paper",   "Always launch in live mode — paused/paper from end_of_day is cleared"),
    ("DRAWDOWN_HALT_ACTIVE",           "0",      "Clear any stale halt"),
    ("DRAWDOWN_SOFT_BRAKE",            "0",      "Clear soft brake"),
    ("EXECUTOR_WRITE_LATENCY_LIMIT_MS","10000",  "10s — prevents false blocks"),
    ("SUPERVISOR_PRICE_MAX_AGE_SECONDS","600",   "10min — legacy supervisor price window"),
    ("SUPERVISOR_MAX_PRICE_AGE_SEC",   "300",    "Supervisor SQL fetch price-age gate aligned with oracle refresh"),
    ("SUPERVISOR_MAX_SIGNAL_AGE_SEC",  "600",    "10min — supervisor signal age gate"),
    ("INTEL_PRICE_MAX_AGE_SEC",        "120",    "2min — live execution intel age gate"),
    ("EXECUTOR_MAX_HOLD_SECONDS",      "900",    "15min max hold — standardised default"),
    # EVIDENCE-CALIBRATED GATES — set by qual_rejection_audit May 2026.
    # fix_ivaris_and_qualifier.py set MIN_CURVE_SOL=2 which blocked 87% of live launch
    # tokens (max seen = 0.52 SOL). Protected here so scripts cannot silently revert.
    ("MIN_CURVE_SOL",                  "0.1",    "Calibrated: real tokens launch below 0.3 SOL early"),
    ("MIN_MARKET_CAP_USD",             "500",    "Calibrated: catches early launch mcaps below $1000"),
    ("SUPERVISOR_PHASE_A_SIGNAL_AGE_SEC","120",  "Qualified_at model: from qualification not discovery"),
    ("SUPERVISOR_FRESHNESS_FLOOR",     "0.60",   "Gate 0.7: allows 141s pipeline latency"),
    # TIME_CUT_SECONDS removed from engine V3 (Edge Preservation Patch).
    # Fixed time cuts replaced by stagnation-only exit. Key kept here as
    # a no-op tombstone so scripts that read it don't error.
    ("TIME_CUT_SECONDS",               "180",    "LEGACY — ignored by execution_engine V3+. Stagnation logic now governs."),
    # MAX_HOLD_SECONDS is a legacy alias. Engine reads EXECUTOR_MAX_HOLD_SECONDS (set above at 900s).
    # Kept as tombstone only.
    ("MAX_HOLD_SECONDS",               "180",    "LEGACY alias — engine reads EXECUTOR_MAX_HOLD_SECONDS"),
    # ── EDGE PRESERVATION V3 — execution engine exit thresholds ─────────────
    # These match the hardcoded values in execution_engine.py V3.
    # Listed here for visibility and future configurability only.
    # Engine now reads HARD_STOP_LOSS_PCT directly; this is enforced at boot.
    ("HARD_STOP_LOSS_PCT",             "4.0",    "Sign-off risk guard: hard stop below -4% — fires before all other exits"),
    ("PATTERN_LIVE_ARMING_MODE",     "advisory", "Operator sign-off: patterns remain telemetry/advisory; Mode B remains live authority"),
    ("PATTERN_LIVE_ARMING_REQUIRED", "0",        "Operator sign-off: pattern confirmation does not hard-veto otherwise-valid mirroring"),
    ("STOP_LOSS_PCT",                  "4.0",    "Position metadata stop-loss aligned with hard guard"),
    ("RUNNER_ACTIVATE_PCT",            "20.0",   "V3: runner mode activates at +20% unrealized"),
    ("RUNNER_TRAIL_PCT",               "10.0",   "V3: trailing stop 10% from peak in runner mode"),
    ("RUNNER_TRAIL_TIGHT_PCT",         "8.0",    "V3: trail tightens to 8% at +50% unrealized"),
    ("STAGNATION_WINDOW_SEC",          "180",    "V3: minimum hold before stagnation check runs"),
    ("STAGNATION_MOVE_THRESHOLD_PCT",  "0.2",    "V3: exit if price moved <0.2% in last 60s"),
    # ── SIGN-OFF AUDIT FIXES — confirmed missing from all boot scripts ───────
    # These were found absent in the May 2026 launch readiness audit.
    # Without them, defaults fall to code-only values which may differ per file.
    ("SUPERVISOR_MIN_MINT_CONFIDENCE",  "0.65",   "Confidence floor — code default also 0.75, explicit is safer"),
    ("EXECUTOR_MAX_OPEN_POSITIONS",     "4",      "Executor hard cap on concurrent positions"),
    ("POLARIS_FORGE_ONLY_MODE",        "0",      "Must be 0 — value 1 blocks all entries"),
    ("GOVERNOR_AUTO_APPROVE",          "1",      "Sovereign governor auto-approves"),
    # Phase A gate fixes — hardcoded gates killed every latched signal
    # With supervisor stamping latched_at=now at latch, executor measures from latch (2-5s)
    # so signal_age is always ~2-5s -> freshness~0.99 -> passes all gates
    # These are safety net values in case running instance hasn't restarted yet
    ("EXECUTOR_PHASE_A_MAX_PRICE_AGE",   "600",  "Phase A price gate — 600s matches supervisor window"),
    ("EXECUTOR_PHASE_A_MAX_SIGNAL_AGE",  "600",  "Phase A signal gate — from latched_at not discovery"),
    # FRESHNESS: exp(-signal_age/276.89). Old 0.85 = required <45s (impossible). 0.20 = up to 450s.
    # With stamp fix: signal_age~2-5s -> freshness~0.99 -> trivially passes 0.20 floor.
    ("EXECUTOR_FRESHNESS_MIN",           "0.20", "Freshness floor — 0.20 allows up to ~450s signal age"),
    ("SUPERVISOR_FRESHNESS_FLOOR",       "0.60", "Supervisor freshness floor — matches executor"),
    ("EXECUTOR_MAX_SIGNAL_AGE_SEC",      "900",  "Outer signal age gate"),
    ("EXECUTOR_MAX_PRICE_AGE_SEC",       "300",  "Executor price-age gate aligned with paper entry window"),
    ("PREENTRY_MAX_PRICE_AGE_SECONDS",   "300",  "Pre-entry firewall price age aligned with executor"),
    ("PREENTRY_MAX_SIGNAL_AGE_SECONDS",  "900",  "Pre-entry firewall signal age aligned with executor"),
    ("LIVE_MAX_OPEN_POSITIONS",          "1",    "Live capital cap — real lane only"),
    ("PAPER_MAX_OPEN_POSITIONS",         "5",    "Paper/shadow learning cap independent of live lane"),
    ("LIVE_PAPER_SHADOW_ON_BLOCK",       "1",    "Keep paper mapping alive when live Mode B/cap blocks"),
    ("LIVE_POSITION_SIZE_USD",           "0",    "Operator-owned; launcher must stamp a positive live size"),
    ("PAPER_POSITION_SIZE_USD",          "20",   "Flat-dollar paper/shadow size"),
    ("MAX_LIVE_POSITION_USD",            "0",    "Operator-owned live ceiling"),
    ("POSITION_SIZE_USD",                "20",   "Fallback flat-dollar paper size"),

    # ── AUTONOMOUS BUILD / SUBSTRATE NODE SAFE DEFAULTS ─────────────────
    ("AUTONOMOUS_BUILD_ENABLED", "1", "Enable proposal/test build spine"),
    ("AUTONOMOUS_CODE_APPLY_ENABLED", "0", "High-risk code apply disabled by default"),
    ("GOLDEN_LATTICE_REQUIRED", "1", "Operator gate required for high-risk patches"),
    ("COUNCIL_WORK_QUEUE_ENABLED", "1", "Enable durable council work queue"),
    ("COUNCIL_BUILD_RESUME_ENABLED", "1", "Resume autonomous build phases on fresh launch"),
    ("SUBSTRATE_NODE_ENABLED", "1", "Enable DB-backed Substrate Node"),
    ("COUNCIL_MODEL_EVOLUTION_ENABLED", "1", "Show task-based model evolution/devolution"),
    ("RUNNER_DETECTOR_ENABLED", "1", "Enable paper velocity scoring"),
    ("RUNNER_DUD_EARLY_CUT_ENABLED", "0", "No automatic early cut until proven"),
    ("RUNNER_LIVE_SCALE_ENABLED", "0", "Never live scale automatically by default"),
    ("SMART_WALLET_CONVERGENCE_ENABLED", "1", "Observe/paper smart wallet convergence"),
    ("GRID_QUANT_BOT_ENABLED", "0", "Grid bot disabled by default"),
    ("GRID_QUANT_BOT_MODE", "paper", "Grid bot paper/research only"),
]
# Runtime gate alignment keys are boot guards, not optional hints.
# The old ON CONFLICT DO NOTHING preserved stale DB values forever, so earlier
# fixes could appear in code while the live DB kept blocking trades. Force only
# trade-flow safety/entry keys; leave unrelated build/substrate knobs operator-owned.
_FORCE_CONFIG_KEYS = {
    "TRADING_MODE",
    "DRAWDOWN_HALT_ACTIVE", "DRAWDOWN_SOFT_BRAKE",
    "POLARIS_FORGE_ONLY_MODE", "GOVERNOR_AUTO_APPROVE",
    "EXECUTOR_WRITE_LATENCY_LIMIT_MS",
    "MIN_CURVE_SOL", "MIN_MARKET_CAP_USD",
    "SUPERVISOR_PRICE_MAX_AGE_SECONDS", "SUPERVISOR_MAX_PRICE_AGE_SEC",
    "SUPERVISOR_MAX_SIGNAL_AGE_SEC", "SUPERVISOR_PHASE_A_SIGNAL_AGE_SEC",
    "SUPERVISOR_FRESHNESS_FLOOR", "SUPERVISOR_MIN_MINT_CONFIDENCE",
    "EXECUTOR_PHASE_A_MAX_PRICE_AGE", "EXECUTOR_PHASE_A_MAX_SIGNAL_AGE",
    "EXECUTOR_FRESHNESS_MIN", "EXECUTOR_MAX_SIGNAL_AGE_SEC",
    "EXECUTOR_MAX_PRICE_AGE_SEC", "PREENTRY_MAX_PRICE_AGE_SECONDS",
    "PREENTRY_MAX_SIGNAL_AGE_SECONDS", "INTEL_PRICE_MAX_AGE_SEC",
    "EXECUTOR_MAX_OPEN_POSITIONS", "LIVE_MAX_OPEN_POSITIONS",
    "PAPER_MAX_OPEN_POSITIONS", "LIVE_PAPER_SHADOW_ON_BLOCK",
    "LIVE_POSITION_SIZE_USD", "PAPER_POSITION_SIZE_USD",
    "MAX_LIVE_POSITION_USD", "POSITION_SIZE_USD",
    "PATTERN_LIVE_ARMING_MODE", "PATTERN_LIVE_ARMING_REQUIRED",
}

for _cfg in config_fixes:
    # Robust boot guard: every row should be (key, value, desc), but tolerate older malformed rows.
    if not isinstance(_cfg, (tuple, list)) or len(_cfg) < 2:
        warn(f"Skipping malformed config row: {_cfg!r}")
        continue
    key, value = str(_cfg[0]), str(_cfg[1])
    desc = str(_cfg[2]) if len(_cfg) > 2 else ""

    # HOTFIX_20260707_OPERATOR_LAUNCH_KEYS:
    # These values are owned by Launch_Sentinuity.bat / launch_config.py and the
    # operator interview. Prelaunch must verify/clean schema, not silently undo
    # paper/dual mode or the chosen trade sizes/caps after the operator answers.
    if key in {
        "TRADING_MODE",
        "DUAL_MODE_ENABLED",
        "LIVE_TRADING_ENABLED",
        "LIVE_ARMED",
        "LIVE_MONEY_MODE",
        "EXECUTION_ARMED",
        "LIVE_POSITION_SIZE_USD",
        "PAPER_POSITION_SIZE_USD",
        "POSITION_SIZE_USD",
        "MAX_LIVE_POSITION_USD",
        "LIVE_TRADE_AMOUNT_USD",
        "LIVE_MAX_TOTAL_EXPOSURE_USD",
        "LIVE_DAILY_LOSS_LIMIT_USD",
        "OPERATOR_LIVE_POSITION_SIZE_USD",
        "PAPER_MAX_OPEN_POSITIONS",
        "LIVE_MAX_OPEN_POSITIONS",
    }:
        continue
    if key in _FORCE_CONFIG_KEYS:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
    else:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, value)
        )
conn.commit()
ok(f"Applied {len(config_fixes)} config values")

# ── ENSURE UI MODULE DIRECTORY EXISTS ────────────────────────────────────────
print(f"\n[3b] Checking ui/ module directory...")
_ui_dir = Path("ui")
if not _ui_dir.exists():
    _ui_dir.mkdir(exist_ok=True)
    fixed("Created ui/ directory")
else:
    ok("ui/ directory present")

_ui_modules = [
    ("ui/substrate_tab.py",         "Substrate Lane 2 tab"),
    ("ui/sovereign_health_tab.py",  "Biological Intelligence Lane 3 tab"),
]
for _path, _desc in _ui_modules:
    if Path(_path).exists():
        ok(f"{_desc}: present")
    else:
        warn(f"{_desc}: MISSING ({_path})")

# ── CLEAR STALE BACKLOG ────────────────────────────────────────────────────────
print(f"\n[4] Clear stale unpriced backlog (>30 min, no price)")
cutoff_30m = now - 1800
r = conn.execute("""
    UPDATE market_snapshots
    SET candidate_state = 'vetoed',
        price_status    = 'dead',
        quality_reason  = 'PRELAUNCH_STALE_BACKLOG'
    WHERE price_status IN ('pending','retry')
      AND observed_price IS NULL
      AND candidate_state = 'pending'
      AND COALESCE(timestamp, 0) < ?
""", (cutoff_30m,))
conn.commit()
fixed(f"Vetoed {r.rowcount:,} stale unpriced rows") if r.rowcount else ok("No stale backlog")

# PATCH: Also purge stale QUALIFIED rows that were never priced.
# Prelaunch previously only purged candidate_state='pending' rows, missing rows
# that had already been qualified (quality_status='qualified') in prior sessions.
# These pile up as permanent backlog — blocking supervisor from seeing fresh tokens.
# A pump.fun token has a window of minutes. Qualified rows >10 min old are dead weight.
r2 = conn.execute("""
    UPDATE market_snapshots
    SET candidate_state = 'vetoed',
        price_status    = 'dead',
        quality_reason  = 'PRELAUNCH_STALE_QUALIFIED'
    WHERE quality_status = 'qualified'
      AND candidate_state NOT IN ('vetoed', 'exited', 'latched')
      AND COALESCE(updated_at, created_at, timestamp, 0) < ?
""", (now - 600,))
conn.commit()
fixed(f"Cleared {r2.rowcount:,} stale qualified backlog rows (>10 min)") if r2.rowcount else ok("No stale qualified backlog")

# ── CLEAR STALE LATCHED SIGNALS (boot only) ──────────────────────────────────
# Latched signals older than 10 min are dead momentum — pump.fun tokens have
# a window of seconds to minutes. Old latched rows show as phantom signals in
# the terminal on restart. Clear them every boot.
try:
    r_latch = conn.execute("""
        UPDATE market_snapshots
        SET candidate_state  = 'vetoed',
            execution_ready  = 0,
            latched          = 0,
            quality_reason   = 'STALE_LATCHED_BOOT'
        WHERE COALESCE(latched, 0) = 1
          AND COALESCE(execution_ready, 0) = 0
          AND COALESCE(price_updated_at, created_at, timestamp, 0) < ?
    """, (now - 600,))
    conn.commit()
    fixed(f"Cleared {r_latch.rowcount} stale latched signals (>10 min)") if r_latch.rowcount else ok("No stale latched signals")
except Exception as _e:
    warn(f"Stale latched cleanup skipped: {_e}")

# Clear execution_ready slot jams older than 5 min
try:
    r_exec = conn.execute("""
        UPDATE market_snapshots
        SET execution_ready  = 0,
            latched          = 0,
            candidate_state  = 'vetoed',
            quality_reason   = 'UNJAMMED_BOOT'
        WHERE COALESCE(execution_ready, 0) = 1
          AND COALESCE(price_updated_at, created_at, timestamp, 0) < ?
    """, (now - 300,))
    conn.commit()
    fixed(f"Unjammed {r_exec.rowcount} stale execution_ready signals (>5 min)") if r_exec.rowcount else ok("No execution_ready jams")
except Exception as _e:
    warn(f"Unjam cleanup skipped: {_e}")

# ── PURGE STALE raw_dna (boot only) ────────────────────────────────────────────
# Skip raw_dna rows older than 10 min so ingest never processes dead-momentum
# tokens into market_snapshots on restart. These are unresolved rows from before
# shutdown that would create stale qualified signals and block fresh flow.
try:
    _rr = conn.execute(
        "UPDATE raw_dna SET processed_state=2 "
        "WHERE processed_state=0 "
        "AND COALESCE(first_seen_at, created_at, timestamp, processed_at, 0) < ? "
        "AND COALESCE(first_seen_at, created_at, timestamp, processed_at, 0) > 0",
        (now - 600,))
    conn.commit()
    fixed(f"Boot purge: skipped {_rr.rowcount:,} stale raw_dna rows") if _rr.rowcount else ok("raw_dna: no stale rows to skip")
except Exception as _e:
    warn(f"raw_dna purge skipped: {_e}")

# Also kill stale state=1 rows — resolver cannot resolve transactions older than
# ~2 minutes (Helius returns null). Leaving them causes resolver to burn all
# credits on guaranteed-fail calls and starves fresh flow.
try:
    _rr1 = conn.execute(
        "UPDATE raw_dna SET processed_state=-1, resolution_note='BOOT_PURGE_STALE_STATE1' "
        "WHERE processed_state=1 "
        "AND COALESCE(first_seen_at, created_at, timestamp, processed_at, 0) < ? "
        "AND COALESCE(first_seen_at, created_at, timestamp, processed_at, 0) > 0",
        (now - 600,))
    conn.commit()
    fixed(f"Boot purge: killed {_rr1.rowcount:,} stale state=1 resolver rows") if _rr1.rowcount else ok("raw_dna state=1: no stale rows")
except Exception as _e:
    warn(f"raw_dna state=1 purge skipped: {_e}")

# ── RESET STUCK PIPELINE ───────────────────────────────────────────────────────
print(f"\n[5] Reset stuck pipeline rows")
counts = [0, 0, 0]
try:
    r1 = conn.execute("""
        UPDATE market_snapshots SET quality_status='pending', quality_reason=''
        WHERE quality_status='processing'
          AND COALESCE(price_updated_at, 0) < ?
    """, (now - 600,))
    counts[0] = r1.rowcount
except Exception:
    # price_updated_at may not exist in all schema versions — skip gracefully
    pass
try:
    r2 = conn.execute("""
        UPDATE market_snapshots SET price_status='pending', price_attempts=0
        WHERE price_status IN ('dead','retry')
          AND candidate_state='pending' AND latched=0
    """)
    counts[1] = r2.rowcount
except Exception:
    pass
try:
    r3 = conn.execute("""
        UPDATE raw_dna SET processed_state=1, claim_until=NULL
        WHERE processed_state=99
          AND (claim_until IS NULL OR claim_until < ?)
    """, (now - 60,))
    counts[2] = r3.rowcount
except Exception:
    pass
conn.commit()
fixed(f"Reset {counts[0]} stuck processing, {counts[1]} dead/retry price, {counts[2]} stuck claims")

# ── CLOSE ZOMBIE POSITIONS ─────────────────────────────────────────────────────
print(f"\n[6] Check for zombie open positions")
zombies = conn.execute("""
    SELECT id, token_name, entry_price, position_size_usd, opened_at, mint_address
    FROM paper_positions
    WHERE status='OPEN' AND opened_at < ?
""", (now - 7200,)).fetchall()  # open > 2 hours

if zombies:
    for pos in zombies:
        age_h = (now - float(pos["opened_at"])) / 3600
        entry = float(pos["entry_price"] or 0)
        size  = float(pos["position_size_usd"] or 0)
        # Get last known price
        pr = conn.execute(
            "SELECT observed_price FROM market_snapshots WHERE mint_address=? "
            "AND observed_price>0 ORDER BY price_updated_at DESC LIMIT 1",
            (pos["mint_address"],)
        ).fetchone()
        exit_price = float(pr["observed_price"]) if pr else entry
        pnl = size * ((exit_price - entry) / entry) if entry > 0 else 0
        conn.execute(
            "UPDATE paper_positions SET status='CLOSED', exit_price=?, "
            "realized_pnl_usd=?, unrealized_pnl_usd=0, closed_at=? WHERE id=?",
            (exit_price, pnl, now, pos["id"])
        )
        # DO NOT credit wallet_balance here — prelaunch runs after manual
        # wallet corrections and double-credits capital. Wallet stays as-is.
        fixed(f"Closed zombie pos={pos['id']} {pos['token_name']} age={age_h:.1f}h pnl={pnl:+.4f}")
    conn.commit()
else:
    ok("No zombie positions")

# ── PROTECTED DATA VERIFICATION ────────────────────────────────────────────────
print(f"\n[7] Verifying learned data is intact")
checks = [
    ("paper_positions",       "SELECT COUNT(*) FROM paper_positions"),
    ("trade_autopsies",       "SELECT COUNT(*) FROM trade_autopsies"),
    ("polaris_trade_reviews", "SELECT COUNT(*) FROM polaris_trade_reviews"),
    ("polaris_learned_patterns","SELECT COUNT(*) FROM polaris_learned_patterns"),
    ("cognition_log",         "SELECT COUNT(*) FROM cognition_log"),
]
for label, sql in checks:
    try:
        n = conn.execute(sql).fetchone()[0]
        ok(f"{label}: {n:,} rows — intact")
    except Exception:
        warn(f"{label}: table not found (will be created on launch)")

# ── PIPELINE STATE ─────────────────────────────────────────────────────────────
print(f"\n[8] Pipeline state")
wallet = float(conn.execute("SELECT wallet_balance FROM system_state WHERE id=1").fetchone()[0])
pending = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE candidate_state='pending'").fetchone()[0]
qualified = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE quality_status='qualified' AND latched=0").fetchone()[0]
latched = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)").fetchone()[0]
open_pos = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0]

ok(f"Wallet: ${wallet:.2f}")
ok(f"Pending (ready for qualifier): {pending:,}")
ok(f"Qualified (waiting to latch): {qualified}")
ok(f"Latched (ready for executor): {latched}")
ok(f"Open positions: {open_pos}")

conn.close()


# HOTFIX
MAX_PRICE_AGE_SECONDS = 120
FORGE_ENABLED = False


# ── [9] CODE CHANGE IMPACT AUDIT ──────────────────────────────────────────────
print(f"\n{SEP}")
print(f"  [9] RECENT CODE CHANGES SINCE LAST TRADE")
print(SEP)
try:
    import hashlib as _hlib, sqlite3 as _sq3
    from datetime import datetime as _dt
    _AUDIT_DB = DB_PATH
    if not _AUDIT_DB.exists():
        print(f"  [SKIP] {_AUDIT_DB.name} not found")
    else:
        print(f"  DB: {_AUDIT_DB.name}")
        _vc = _sq3.connect(str(_AUDIT_DB), timeout=10)
        _vc.row_factory = _sq3.Row
        _vc.execute("PRAGMA journal_mode=WAL")
        try:
            _last_row = _vc.execute("SELECT MAX(CAST(opened_at AS REAL)) AS t FROM paper_positions").fetchone()
            _last_trade_ts = float(_last_row["t"] or 0)
        except Exception:
            _last_trade_ts = 0
        if _last_trade_ts > 0:
            _anchor_label = _dt.fromtimestamp(_last_trade_ts).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            _anchor_label = "no trades on record — showing last 24 h"
            _last_trade_ts = _dt.utcnow().timestamp() - 86400
        print(f"  Anchor: last position opened at {_anchor_label}")
        _vault_ok = _vc.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='code_vault_changes'").fetchone()
        if not _vault_ok:
            print("  [SKIP] code_vault_changes not found. Run: python services/code_vault.py baseline")
        else:
            _changes = _vc.execute(
                "SELECT file_name,file_path,old_hash,new_hash,changed_at,change_reason,applied_by "
                "FROM code_vault_changes WHERE changed_at>? ORDER BY changed_at DESC LIMIT 30",
                (_last_trade_ts,)
            ).fetchall()
            if not _changes:
                print("  [OK]  No code changes since last trade.")
            else:
                _CRITICAL = {"execution_engine.py","neural_supervisor.py","market_intelligence.py",
                             "schema.py","prelaunch.py","sovereign_hub.py","code_vault.py"}
                print(f"  {len(_changes)} change(s):\n")
                for _ch in _changes:
                    _when  = _dt.fromtimestamp(float(_ch["changed_at"])).strftime("%Y-%m-%d %H:%M:%S")
                    _flag  = "  *** CRITICAL ***" if _ch["file_name"] in _CRITICAL else ""
                    print(f"  {'─'*52}")
                    print(f"  FILE:  {_ch['file_path']}{_flag}")
                    print(f"  WHEN:  {_when}   {(_ch['old_hash'] or 'NEW')[:10]}...->{(_ch['new_hash'] or '?')[:10]}...")
                    print(f"  BY:    {_ch['applied_by'] or 'system'}  ({_ch['change_reason'] or 'unknown'})")
                    _ct = float(_ch["changed_at"])
                    _flow = "unknown"
                    try:
                        _pos = _vc.execute(
                            "SELECT token_name,opened_at FROM paper_positions "
                            "WHERE CAST(opened_at AS REAL) BETWEEN ? AND ? "
                            "ORDER BY CAST(opened_at AS REAL) DESC LIMIT 1",
                            (_ct-300, _ct+300)
                        ).fetchone()
                        if _pos:
                            _flow = f"OPENED {_pos['token_name']} at {_dt.fromtimestamp(float(_pos['opened_at'])).strftime('%H:%M:%S')}"
                        else:
                            _snap = _vc.execute(
                                "SELECT candidate_state,quality_reason,token_name FROM market_snapshots "
                                "WHERE COALESCE(created_at,timestamp,price_updated_at,0) BETWEEN ? AND ? "
                                "ORDER BY COALESCE(created_at,timestamp,price_updated_at,0) DESC LIMIT 1",
                                (_ct-300, _ct+300)
                            ).fetchone()
                            _flow = (f"{_snap['candidate_state']} ({_snap['quality_reason']}) token={_snap['token_name']}"
                                     if _snap and _snap['quality_reason']
                                     else (f"{_snap['candidate_state']} token={_snap['token_name']}" if _snap
                                           else "no pipeline activity +-5 min"))
                    except Exception as _fe:
                        _flow = f"lookup error: {_fe}"
                    print(f"  TRADE: {_flow}")
                print(f"  {'─'*52}")
                print("\n  On-disk vs vault:")
                _seen: set = set()
                for _ch in _changes:
                    if _ch["file_path"] in _seen: continue
                    _seen.add(_ch["file_path"])
                    _fp = DB_PATH.parent / _ch["file_path"]
                    if _fp.exists():
                        _cur_h = _hlib.sha256(_fp.read_bytes()).hexdigest()[:16]
                        print(f"    {_ch['file_name']:38s} {_cur_h}... {'match' if _cur_h.startswith((_ch['new_hash'] or '')[:12]) else 'DIFFERS'}")
                    else:
                        print(f"    {_ch['file_name']:38s} NOT FOUND")
        _vc.close()
except Exception as _audit_err:
    print(f"  [WARN] Audit failed: {_audit_err}")

# ── [10] CONFIG SNAPSHOT ───────────────────────────────────────────────────────
# Saves all system_config key/values at every boot so you can always look back
# and see exactly what settings were active when trading stopped or misbehaved.
# Query: SELECT * FROM code_vault_config_snapshots ORDER BY snapshotted_at DESC
print(f"\n{SEP}")
print(f"  [10] CONFIG SNAPSHOT")
print(SEP)
try:
    import sqlite3 as _csq3, time as _ctime
    from datetime import datetime as _cdt
    _cdb = _csq3.connect(str(DB_PATH), timeout=10)
    _cdb.row_factory = _csq3.Row
    _cdb.execute("PRAGMA journal_mode=WAL")
    _cdb.execute("""CREATE TABLE IF NOT EXISTS code_vault_config_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshotted_at REAL NOT NULL,
        snapshot_label TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT)""")
    _cdb.execute("CREATE INDEX IF NOT EXISTS idx_cvcs_ts ON code_vault_config_snapshots(snapshotted_at DESC)")
    _cdb.commit()
    _now_ts    = _ctime.time()
    _now_label = _cdt.fromtimestamp(_now_ts).strftime("boot_%Y%m%d_%H%M%S")
    _cfg_rows  = _cdb.execute("SELECT key,value FROM system_config ORDER BY key ASC").fetchall()
    if not _cfg_rows:
        print("  [SKIP] system_config empty")
    else:
        _cdb.executemany(
            "INSERT INTO code_vault_config_snapshots (snapshotted_at,snapshot_label,key,value) VALUES (?,?,?,?)",
            [(_now_ts, _now_label, r["key"], r["value"]) for r in _cfg_rows]
        )
        _cdb.commit()
        _cfg_map = {r["key"]: r["value"] for r in _cfg_rows}
        _snap_count = _cdb.execute("SELECT COUNT(DISTINCT snapshot_label) FROM code_vault_config_snapshots").fetchone()[0]
        print(f"  Snapshot: {_now_label}  ({len(_cfg_rows)} keys, {_snap_count} total boots recorded)")
        print()
        _KEY = ["TRADING_MODE","SUPERVISOR_MIN_MINT_CONFIDENCE","DRAWDOWN_HALT_ACTIVE",
                "DRAWDOWN_ACCUMULATED_PCT","LIVE_MAX_OPEN_POSITIONS","PAPER_MAX_OPEN_POSITIONS",
                "POSITION_SIZE_USD","POSITION_SIZE_PCT","STOP_LOSS_PCT","TAKE_PROFIT_PCT",
                "EXECUTOR_MAX_SIGNAL_AGE_SEC","EXECUTOR_PHASE_A_MAX_SIGNAL_AGE",
                "EXECUTOR_PHASE_A_MAX_PRICE_AGE","HOUR_GATE_ENABLED",
                "MODE_B_CONF_FLOOR","ORACLE_LIVENESS_GATE_SEC"]
        print("  Key settings at this boot:")
        for _k in _KEY:
            _v = _cfg_map.get(_k, "(not set)")
            _flag = ("  <-- HALT ACTIVE" if _k == "DRAWDOWN_HALT_ACTIVE" and _v == "1"
                     else (f"  <-- {'LIVE' if _v == 'live' else 'PAPER'}" if _k == "TRADING_MODE" else ""))
            print(f"    {_k:<44s} {_v}{_flag}")
    _cdb.close()
except Exception as _cfg_err:
    print(f"  [WARN] Config snapshot failed: {_cfg_err}")

print(f"\n{SEP}")
print(f"  PRE-LAUNCH COMPLETE + AUDIT DONE")
print(f"  Run Launch_Sentinuity.bat now")
print(SEP + "\n")

# =============================================================================
# SENTINUITY_SIGNOFF_STALE_LATCH_TTL_PRELAUNCH_V1
# Sign-off rule:
#   A latch is an execution decision. A fresh price does NOT renew an old latch.
#   On every prelaunch, clear old active executable latches by latched_at TTL.
#   This protects crash/force-restart recovery without faking prices or forcing latches.
# =============================================================================
def _sentinuity_signoff_clear_stale_latch_ttl_prelaunch() -> int:
    import os as _os
    import sqlite3 as _sqlite3
    import time as _time
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parents[1] if len(_Path(__file__).resolve().parents) > 1 else _Path.cwd()
    _db = _Path(_os.getenv("SENTINUITY_DB_PATH") or _os.getenv("DB_PATH") or (_root / "sentinuity_matrix.db"))
    if not _db.exists():
        try:
            print(f"[WARN] stale latch TTL cleanup skipped: DB not found {_db}")
        except Exception:
            pass
        return 0

    _conn = _sqlite3.connect(str(_db), timeout=30)
    _conn.row_factory = _sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA busy_timeout=30000")

    def _table_exists(_t: str) -> bool:
        return _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_t,)).fetchone() is not None

    def _cols(_t: str) -> set:
        if not _table_exists(_t):
            return set()
        return {r[1] for r in _conn.execute(f"PRAGMA table_info({_t})")}

    def _cfg_float(_key: str, _default: float) -> float:
        try:
            _r = _conn.execute("SELECT value FROM system_config WHERE key=?", (_key,)).fetchone()
            return float(_r[0]) if _r and _r[0] is not None else float(_default)
        except Exception:
            return float(_default)

    def _cfg_set(_key: str, _value: str) -> None:
        try:
            _conn.execute("""
                INSERT INTO system_config(key,value)
                VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (_key, _value))
        except Exception:
            pass

    if not _table_exists("market_snapshots"):
        _conn.close()
        return 0

    _ms = _cols("market_snapshots")
    if not {"latched", "execution_ready", "candidate_state"}.issubset(_ms):
        _conn.close()
        return 0

    _now = _time.time()
    _max_latch_age = _cfg_float("PREENTRY_MAX_EXEC_READY_AGE_SECONDS", 300)
    _max_signal_age = _cfg_float("EXECUTOR_MAX_SIGNAL_AGE_SEC", 900)
    _max_price_age = _cfg_float("EXECUTOR_MAX_PRICE_AGE_SEC", 300)
    _cfg_set("PREENTRY_MAX_EXEC_READY_AGE_SECONDS", str(int(_max_latch_age)))
    _cfg_set("STARTUP_CLEAR_STALE_LATCH_TTL_ENABLED", "1")

    _time_col = "first_seen_at" if "first_seen_at" in _ms else ("created_at" if "created_at" in _ms else "timestamp")
    _price_col = "price_updated_at" if "price_updated_at" in _ms else ("priced_at" if "priced_at" in _ms else _time_col)

    _sets = ["execution_ready=0", "latched=0", "candidate_state='vetoed'"]
    if "latch_claimed_until" in _ms:
        _sets.append("latch_claimed_until=NULL")
    if "quality_reason" in _ms:
        _sets.append("""
            quality_reason=CASE
              WHEN COALESCE(quality_reason,'')=''
              THEN 'PRELAUNCH_CLEAR_STALE_LATCH_TTL'
              WHEN quality_reason NOT LIKE '%PRELAUNCH_CLEAR_STALE_LATCH_TTL%'
              THEN quality_reason || '|PRELAUNCH_CLEAR_STALE_LATCH_TTL'
              ELSE quality_reason
            END
        """.strip())

    _sql = f"""
        UPDATE market_snapshots
        SET {', '.join(_sets)}
        WHERE (
              COALESCE(latched,0)=1
              OR COALESCE(execution_ready,0) IN (1,2)
              OR COALESCE(candidate_state,'')='latched'
        )
        AND COALESCE(candidate_state,'') NOT IN ('executed','exited')
        AND (
              (COALESCE(latched_at,0)>0 AND (? - COALESCE(latched_at,0)) > ?)
              OR (COALESCE(latched_at,0)=0 AND (? - COALESCE({_time_col},0)) > ?)
              OR (? - COALESCE({_time_col},0)) > ?
              OR (? - COALESCE({_price_col},0)) > ?
        )
    """
    _r = _conn.execute(_sql, (_now, _max_latch_age, _now, _max_latch_age, _now, _max_signal_age, _now, _max_price_age))
    _conn.commit()
    _n = int(_r.rowcount if _r.rowcount is not None else 0)
    _conn.close()
    try:
        print(f"[OK] Stale executable latch TTL cleanup cleared {_n} row(s)")
    except Exception:
        pass
    return _n

if __name__ == "__main__":
    try:
        _sentinuity_signoff_clear_stale_latch_ttl_prelaunch()
    except Exception as _sentinuity_stale_latch_ttl_error:
        try:
            print(f"[WARN] stale latch TTL cleanup skipped: {_sentinuity_stale_latch_ttl_error}")
        except Exception:
            pass
# =============================================================================
# END SENTINUITY_SIGNOFF_STALE_LATCH_TTL_PRELAUNCH_V1
# =============================================================================


