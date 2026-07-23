"""
execution_engine.py
-------------------------------------------------------------------------------
Sentinuity Unified Execution Engine - SIGNED-OFF v1.0
-------------------------------------------------------------------------------

Merges responsibilities previously split across:
  - paper_executor.py
  - position_reconciler.py
  - zombie_resolver.py

ARCHITECTURE - THREE SOURCES OF TRUTH
--------------------------------------
1. POSITION STATE     - get_open_positions() / get_position_by_id_open()
2. EXIT DECISIONS     - evaluate_exit_for_position() (live)
                        reconcile_position()          (gap/startup)
                        scan_and_resolve_zombies()    (stale feed)
3. POSITION CLOSURE   - close_position_canonical()   - ALL closes route here

No other function may write status='CLOSED' or return wallet funds.

EXIT PATH ORDER (evaluate_exit_for_position)
--------------------------------------------
  1. TIME_CUT            - negative PnL beyond discovery window
  2. TIME_CUT_STAGNANT   - flat/weakly green beyond stagnation window
  3. TRAILING_STOP       - after trail_activate threshold
  4. TAKE_PROFIT
  5. STOP_LOSS
  6. MAX_HOLD_TIME

ZOMBIE THREADING FIX (Claude sign-off patch)
---------------------------------------------
The original zombie_resolver.py blocked the main loop for up to
ZOMBIE_HITL_TIMEOUT_SECONDS (default 30s) while polling Telegram.
On pump.fun tokens where 30s is a material portion of trade lifetime,
this caused missed TP/SL fires during that window.

Fix: scan_and_resolve_zombies() now spawns a daemon thread per zombie
for HITL polling. The main loop never blocks. Thread results are
collected via _pending_zombie_results (thread-safe queue).

DB HYGIENE
------------------------------------------------------------------------------
  - All reads/writes use core.schema.get_connection() exclusively
  - Local _get_conn() removed entirely (was only needed for legacy isolation)
  - No duplicate connection setup patterns

REMOVED BEHAVIOURS (deliberate)
------------------------------------------------------------------------------
  - Duplicate sqlite3.connect() blocks from reconciler and zombie close helpers
  - close_position_reconcile() and close_at_scratch() standalone paths
    - both now route through close_position_canonical()
  - The inconsistency in zombie offline branch (fetched best price but always
    closed at scratch) is fixed: now uses best known price when available,
    otherwise scratch
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import sys
import threading
import time
import queue
from pathlib import Path
from typing import Any, Optional

# Configure logging BEFORE any service imports.
# logging.basicConfig() is a no-op if already configured - whichever module
# calls it first wins the format string for ALL loggers in the process.
# market_intelligence also calls basicConfig([MARKET_INTEL]) at module level,
# and it was being imported BEFORE this block ran, causing every log line
# including scan_for_entries() to show [MARKET_INTEL] prefix.
# Placing basicConfig here (before any service imports) ensures [EXEC_ENGINE]
# format is locked in first.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [EXEC_ENGINE] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("execution_engine")

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import DB_PATH, get_connection, get_intel_connection, update_heartbeat, get_config_value

try:
    from services.pattern_live_arming import evaluate_pattern_permission as _pattern_live_permission
    from services.live_decision_contract import (
        publish as _publish_decision_contract,
        derive_verdict as _derive_live_verdict,
    )
    _PATTERN_LIVE_ARMING_AVAILABLE = True
except Exception:
    _PATTERN_LIVE_ARMING_AVAILABLE = False
    _pattern_live_permission = None

try:
    from services.trade_lifecycle import (
        ensure_schema as _tle_ensure,
        emit_trade_opened as _tle_opened,
        emit_mark_written as _tle_mark,
        emit_trade_closed as _tle_closed,
        emit_coverage_alert as _tle_coverage_alert,
        get_trade_coverage as _tle_coverage,
        classify_exit_validity as _tle_validity,
    )
    _TLE_AVAILABLE = True
except ImportError:
    _TLE_AVAILABLE = False

try:
    from services.cognition_logger import log_cognition as _log_cog_fn
    _COGNITION_AVAILABLE = True
except Exception:
    _COGNITION_AVAILABLE = False

try:
    from services.market_intelligence import get_curve_progress as _get_curve_progress
    _CURVE_CHECK_AVAILABLE = True
except Exception:
    _CURVE_CHECK_AVAILABLE = False

# Oracle notify - used to push new mint subscriptions to the oracle process.
# NOTE: oracle_last_write_age() was previously imported here for the liveness
# gate but is now REMOVED - that gate was replaced with a direct DB read from
# sentinuity_intelligence.db (see _get_oracle_age_sec in scan_for_entries).
# The in-memory variable was always 0 in this process (separate process scope).
try:
    from services.ws_price_oracle import notify_new_mint as _oracle_notify_mint
except Exception:
    def _oracle_notify_mint(mint: str) -> None:
        pass  # no-op fallback when oracle not importable

# Price Truth Router - single authoritative price source
try:
    from services.price_router import get_execution_price as _router_exec_price
    from services.price_router import get_ui_price as _router_ui_price
    from services.price_router import get_live_liquidation_price as _router_live_liquidation_price
    _PRICE_ROUTER_AVAILABLE = True
except Exception:
    _PRICE_ROUTER_AVAILABLE = False
    def _router_exec_price(mint, entry_price, opened_at): return None
    def _router_ui_price(mint, entry_price, opened_at): return None
    def _router_live_liquidation_price(mint, quantity, entry_price, opened_at): return None

try:
    from services.live_trading import execute_live_sell as _live_sell, execute_live_buy as _live_buy
    _LIVE_TRADING_AVAILABLE = True
except Exception:
    _LIVE_TRADING_AVAILABLE = False
    def _live_sell(mint, qty, pos_id, price, emergency=False):
        return {"success": False, "error": "live_trading not available"}

# logging.basicConfig and log = ... moved above service imports - prevents [MARKET_INTEL] format collision

SERVICE_NAME       = "execution_engine"
MAX_OPEN_POSITIONS = 3

# Track positions that have already had trailing-stop-activate logged
# to prevent the message firing every evaluation cycle (~every 2s per position).
_trail_logged_positions: set = set()
POLL_INTERVAL      = 2.2        # v3.2: was 5s - faster mark cycle for meter updates
ZOMBIE_POLL_EVERY  = 6          # run zombie scan every N main cycles (~30s)

# ── PRAGMA CACHE - eliminates per-tick PRAGMA table_info(paper_positions) ──
# update_position_mark was calling PRAGMA table_info every single tick.
# Audit: 0.89ms median, 1,632ms worst-case spike.
# Cache at module load. Restart service if schema changes (rare).
_PP_COLS_CACHE: set = set()

def _get_pp_cols() -> set:
    global _PP_COLS_CACHE
    if _PP_COLS_CACHE:
        return _PP_COLS_CACHE
    try:
        import sqlite3 as _sq
        _c = _sq.connect(str(DB_PATH), timeout=3)
        _PP_COLS_CACHE = {r[1] for r in _c.execute("PRAGMA table_info(paper_positions)").fetchall()}
        _c.close()
    except Exception:
        pass
    return _PP_COLS_CACHE

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID  = int(os.getenv("TELEGRAM_OWNER_ID", "0") or "0")


def _cfg_enabled(key: str, default: str = "0") -> bool:
    try:
        return str(get_config_value(key, default)).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        return False


def _is_live_mode() -> bool:
    """Legacy compatibility only. True-dual keeps TRADING_MODE=paper."""
    try:
        return str(get_config_value("TRADING_MODE", "paper")).strip().lower() == "live"
    except Exception:
        return False


def _live_lane_armed() -> bool:
    """Independent live mirror arm; paper remains the primary runtime lane."""
    return (
        _cfg_enabled("DUAL_MODE_ENABLED")
        and _cfg_enabled("DUAL_MODE_ARMED")
        and _cfg_enabled("LIVE_TRADING_ENABLED")
        and _cfg_enabled("LIVE_MODE_B_ENABLED")
        and _cfg_enabled("LIVE_ARMED")
    )


def _live_oracle_coverage_guard(candidate_mint: str, candidate_entry_price: float,
                                candidate_opened_at: float) -> tuple[bool, str]:
    """Non-bypassable final guard immediately before an on-chain live buy.

    A candidate may score highly and pass its quote preflight while the shared
    MTM/exit-price fabric is dark.  The July 16 runtime proved that condition:
    open positions repeatedly logged NO_LIVE_PRICE and exit evaluation was
    suppressed.  New funded exposure is therefore forbidden unless:

      1. the global oracle state is not ERROR/DEAD;
      2. the durable intelligence tick stream has written recently;
      3. the candidate itself has an executable router price; and
      4. every already-open REAL position has an executable router exit price.

    Paper learning remains unaffected.  Any uncertainty fails closed for only
    the live mirror and is recorded in the executor log.
    """
    if not _PRICE_ROUTER_AVAILABLE:
        return False, "price_router_unavailable"

    try:
        _state = str(get_config_value("WS_ORACLE_STATE", "UNKNOWN")).strip().upper()
    except Exception:
        _state = "UNKNOWN"
    if _state in {"ERROR", "DEAD"}:
        return False, f"oracle_state={_state}"

    # Cross-process liveness truth comes from the intelligence DB, not the
    # oracle process's in-memory counters.
    _max_age = float(get_config_value("LIVE_ORACLE_NEW_ENTRY_MAX_AGE_SEC", 90.0))
    try:
        import sqlite3 as _sqlite3
        _intel_path = Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db"
        _ic = _sqlite3.connect(str(_intel_path), timeout=3.0)
        _row = _ic.execute("SELECT MAX(ts_ms) FROM mtm_ticks").fetchone()
        _ic.close()
        _last_tick = float(_row[0] or 0.0) / 1000.0 if _row else 0.0
        _age = time.time() - _last_tick if _last_tick > 0 else 999999.0
    except Exception as _exc:
        return False, f"oracle_tick_probe_error={type(_exc).__name__}"
    if _age > _max_age:
        return False, f"oracle_tick_age={_age:.1f}s>{_max_age:.1f}s"

    # Candidate must have fresh executable price truth at the final fire point.
    try:
        _candidate = _router_exec_price(
            candidate_mint, float(candidate_entry_price), float(candidate_opened_at)
        )
    except Exception as _exc:
        return False, f"candidate_router_error={type(_exc).__name__}"
    if not _candidate or not bool(_candidate.get("can_execute_exit")):
        _age_txt = (_candidate or {}).get("age_sec", "n/a")
        return False, f"candidate_not_executable age={_age_txt}"

    # Never increase funded exposure while an existing REAL position cannot be
    # priced for exit. This directly prevents stacking capital during the exact
    # NO_LIVE_PRICE state observed in the recent runtime.
    try:
        with get_connection() as _cov_conn:
            _real_rows = _cov_conn.execute(
                "SELECT id,mint_address,entry_price,opened_at FROM paper_positions "
                "WHERE status='OPEN' AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'"
            ).fetchall()
        for _rr in _real_rows:
            _rr_price = _router_exec_price(
                str(_rr["mint_address"]), float(_rr["entry_price"] or 0.0),
                float(_rr["opened_at"] or 0.0),
            )
            if not _rr_price or not bool(_rr_price.get("can_execute_exit")):
                _rr_age = (_rr_price or {}).get("age_sec", "n/a")
                return False, f"real_pos={int(_rr['id'])}_exit_uncovered age={_rr_age}"
    except Exception as _exc:
        return False, f"real_exit_coverage_error={type(_exc).__name__}"

    return True, f"covered tick_age={_age:.1f}s candidate_age={float(_candidate.get('age_sec') or 0.0):.1f}s"


def _position_is_real(position: dict) -> bool:
    return str(position.get("funding_mode") or "SIM").strip().upper() == "REAL"

# -- Zombie threading state ----------------------------------------------------
# Thread-safe queue: zombie threads post (position_id, action) here.
# Main loop drains it every cycle.
_zombie_result_queue: queue.Queue = queue.Queue()
# Tracks which position_ids have an active HITL thread to avoid double-spawning
_zombie_threads_active: set[int] = set()
_zombie_threads_lock   = threading.Lock()

# HITL alert cooldown (for non-threaded path)
_hitl_sent_at: dict[int, float] = {}


# -----------------------------------------------------------------------------
# SCHEMA ENSURE
# -----------------------------------------------------------------------------

def ensure_executor_schema() -> None:
    """Add non-breaking columns if absent (backward-compat with older DB)."""
    try:
        with get_connection() as conn:
            # Instrument trade lifecycle event spine
            if _TLE_AVAILABLE:
                try:
                    _tle_ensure(conn)
                except Exception:
                    pass
            # paper_positions columns
            pp_cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(paper_positions)"
            ).fetchall()}
            for col, typedef in [
                ("last_price",           "REAL"),
                ("last_marked_at",       "REAL"),
                ("highest_price_seen",   "REAL"),
                ("close_claimed_until",  "REAL"),
                ("mint_address",         "TEXT"),
                # PATCH D: freshness provenance - 'oracle'|'engine'|'fallback'
                ("mark_source",          "TEXT DEFAULT 'unknown'"),
                ("live_exec_price",      "REAL"),
                ("live_exec_pct",        "REAL"),
                ("live_exec_band",       "TEXT"),
                ("live_exec_updated_at", "REAL"),
                ("live_exec_source",     "TEXT"),
                ("live_state",           "TEXT"),
                ("buy_tx_sig",           "TEXT"),
                ("sell_tx_sig",          "TEXT"),
                ("chain_confirmed_at",   "REAL"),
                ("reconciled_at",        "REAL"),
                ("actual_entry_price",   "REAL"),
                ("actual_quantity",      "REAL"),
                ("entry_sol_spent",      "REAL"),
                ("entry_fee_sol",        "REAL"),
                ("exit_sol_received",    "REAL"),
                ("exit_fee_sol",         "REAL"),
                ("settlement_pnl_sol",   "REAL"),
                ("fill_meta_json",       "TEXT"),
                ("sim_parent_position_id", "INTEGER"),
            ]:
                if col not in pp_cols:
                    conn.execute(
                        f"ALTER TABLE paper_positions ADD COLUMN {col} {typedef}"
                    )

            # PHASE 1 schema additions - explicit try/except per column
            # so each is independently idempotent regardless of DB state.
            try:
                conn.execute(
                    "ALTER TABLE paper_positions ADD COLUMN entry_price_source TEXT DEFAULT 'qualify'"
                )
            except Exception:
                pass  # column already exists - safe to ignore

            try:
                conn.execute(
                    "ALTER TABLE paper_positions ADD COLUMN entry_price_ts REAL"
                )
            except Exception:
                pass  # column already exists - safe to ignore

            # entry_confidence - observational only, captures snapshot confidence at entry
            try:
                conn.execute(
                    "ALTER TABLE paper_positions ADD COLUMN entry_confidence REAL"
                )
            except Exception:
                pass  # column already exists - safe to ignore

            # trade_autopsies - CREATE with full schema, then add missing cols
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_autopsies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id INTEGER,
                    token_name TEXT,
                    mint_address TEXT,
                    win_loss TEXT,
                    realized_pnl_usd REAL DEFAULT 0,
                    notes TEXT,
                    created_at REAL
                )
            """)
            ta_cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(trade_autopsies)"
            ).fetchall()}
            for col, typedef in [
                ("position_id",       "INTEGER"),
                ("mint_address",      "TEXT"),
                ("win_loss",          "TEXT"),
                ("realized_pnl_usd",  "REAL DEFAULT 0"),
                ("notes",             "TEXT"),
                ("created_at",        "REAL"),
            ]:
                if col not in ta_cols:
                    conn.execute(
                        f"ALTER TABLE trade_autopsies ADD COLUMN {col} {typedef}"
                    )

            # polaris_trade_reviews - needed by Polaris feedback loop
            conn.execute("""
                CREATE TABLE IF NOT EXISTS polaris_trade_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id INTEGER,
                    token_name TEXT,
                    mint_address TEXT,
                    win_loss TEXT,
                    exit_category TEXT,
                    realized_pnl_usd REAL DEFAULT 0,
                    pnl_pct REAL DEFAULT 0,
                    hold_seconds REAL DEFAULT 0,
                    reviewed_at REAL,
                    polaris_version TEXT DEFAULT 'executor_v1'
                )
            """)

            # DEFENSIVE LAYER: wallet_write_log - every wallet mutation logged
            # with source, delta, position_id and resulting balance.
            # Enables post-mortem inspection of any wallet explosion:
            #   SELECT * FROM wallet_write_log ORDER BY id DESC LIMIT 250;
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wallet_write_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id  INTEGER,
                    delta_usd    REAL,
                    new_balance  REAL,
                    source       TEXT,
                    token_name   TEXT,
                    pnl_usd      REAL,
                    pnl_pct      REAL,
                    timestamp    REAL
                )
            """)

            # MOMENTUM GATE AUDIT - shadow measurement table
            # Phase 1: data collection only. Phase 2: hard gate when evidence supports.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS momentum_gate_audit (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id          INTEGER,
                    mint_address         TEXT,
                    token_name           TEXT,
                    qual_price           REAL,
                    latest_price         REAL,
                    move_from_qual_pct   REAL,
                    move_short_term_pct  REAL,
                    would_veto           INTEGER DEFAULT 0,
                    entered              INTEGER DEFAULT 0,
                    position_id          INTEGER,
                    created_at           REAL,
                    reviewed_at          REAL,
                    eventual_outcome     TEXT,
                    eventual_pnl_pct     REAL,
                    exit_reason          TEXT
                )
            """)

            conn.commit()
    except Exception as e:
        log.debug("ensure_executor_schema skipped: %s", e)


# -----------------------------------------------------------------------------
# COGNITION / TELEMETRY - single queue + single writer thread
# -----------------------------------------------------------------------------
# Execution thread MUST NEVER block on cognition logging.
# One Queue(maxsize=200), one daemon writer thread, one SQLite connection.
# Execution path: put_nowait() → returns in microseconds.
# Queue full → drop silently. Never block, never retry.

import queue as _queue
import threading as _threading

_COG_QUEUE: "_queue.Queue" = _queue.Queue(maxsize=200)   # explicit 200 cap
_COG_WRITER_STARTED       = False
_COG_DROPPED_COUNT        = 0    # observability counter - surfaced in UI

def _start_cognition_writer() -> None:
    """Start the single background cognition writer thread. Idempotent."""
    global _COG_WRITER_STARTED
    if _COG_WRITER_STARTED:
        return
    _COG_WRITER_STARTED = True   # set BEFORE thread start - prevents race

    def _writer_loop():
        while True:
            try:
                payload = _COG_QUEUE.get(timeout=5)
                if payload is None:   # sentinel - clean shutdown
                    break
                stage, message, token, kwargs = payload
                try:
                    _log_cog_fn(stage, message, token=token, **kwargs)
                except Exception:
                    pass
                finally:
                    _COG_QUEUE.task_done()
            except _queue.Empty:
                continue
            except Exception:
                continue

    t = _threading.Thread(target=_writer_loop, name="cognition-writer", daemon=True)
    t.start()


def _log_cognition(token: str, message: str, stage: str = "EXECUTOR", **kwargs) -> None:
    """
    Non-blocking cognition log.
    Execution thread enqueues and returns immediately (<1µs).
    Queue cap: 200. Overflow: drop + increment _COG_DROPPED_COUNT. Never block.
    """
    global _COG_DROPPED_COUNT
    if not _COGNITION_AVAILABLE:
        return
    try:
        _COG_QUEUE.put_nowait((stage, message, token, kwargs))
    except _queue.Full:
        _COG_DROPPED_COUNT += 1   # track drops for UI observability
    except Exception:
        pass


# -----------------------------------------------------------------------------
# SOURCE OF TRUTH - POSITION STATE READERS
# -----------------------------------------------------------------------------

def get_open_positions() -> list[dict]:
    """Canonical reader for all OPEN positions."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("get_open_positions failed: %s", e)
        return []


def get_position_by_id_open(position_id: int) -> Optional[dict]:
    """Race-guard read: returns position only if still OPEN."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE id=? AND status='OPEN'",
                (position_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_position_by_id_open failed pos=%s: %s", position_id, e)
        return None


def count_open_positions(funding_mode: str | None = None) -> int:
    try:
        with get_connection() as conn:
            if funding_mode:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM paper_positions "
                    "WHERE status='OPEN' AND UPPER(COALESCE(funding_mode,'SIM'))=?",
                    (str(funding_mode).upper(),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM paper_positions WHERE status='OPEN'"
                ).fetchone()
        return int(row["c"] or 0) if row else 0
    except Exception:
        return 0


def get_wallet_balance() -> float:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT wallet_balance FROM system_state WHERE id=1"
            ).fetchone()
        return float(row["wallet_balance"] or 0) if row else 0.0
    except Exception:
        return 0.0


def get_last_executor_heartbeat() -> float:
    """
    Returns last heartbeat from this service or legacy paper_executor.
    Used by reconciler to calculate gap duration on restart.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT last_pulse FROM system_heartbeat
                WHERE service_name IN ('execution_engine', 'paper_executor')
                ORDER BY last_pulse DESC LIMIT 1
                """,
            ).fetchone()
        return float(row["last_pulse"] or 0) if row else 0.0
    except Exception:
        return 0.0


# -----------------------------------------------------------------------------
# PRICE HELPERS
# -----------------------------------------------------------------------------


# ── INTEL-FIRST PRICE CACHE ────────────────────────────────────────────────────
_price_cache: dict = {}     # mint -> price
_price_cache_ts: dict = {}  # mint -> epoch of last cache write


def _get_price_intel_first(mint: str) -> "Optional[float]":
    """
    Read latest price from sentinuity_intelligence.db (mtm_ticks).
    Falls back to None - caller then uses market_snapshots.
    Cache TTL: 500ms to cut DB reads by ~80% under load.
    Schema: mtm_ticks(mint_address, price_usd, ts_ms, source)
    """
    import time
    now = time.time()

    # Cache hit
    if mint in _price_cache and (now - _price_cache_ts.get(mint, 0)) < 0.5:
        return _price_cache[mint]

    # Intel DB read - use price_usd and ts_ms (actual column names)
    try:
        intel = get_intel_connection()
        row = intel.execute(
            "SELECT price_usd, ts_ms FROM mtm_ticks "
            "WHERE mint_address=? ORDER BY ts_ms DESC LIMIT 1",
            (mint,),
        ).fetchone()
        intel.close()
        if row and row[0] is not None:
            age_sec = (now * 1000 - float(row[1])) / 1000.0
            if age_sec < 120:  # relaxed from 30s - allow up to 120s old ticks
                price = float(row[0])
                _price_cache[mint] = price
                _price_cache_ts[mint] = now
                log.debug(
                    "[MTM_INTEL] mint=%s price=%.10f age=%.0fs",
                    mint[:12], price, age_sec,
                )
                return price
            else:
                log.debug(
                    "[MTM_INTEL_STALE] mint=%s age=%.0fs (>120s threshold)",
                    mint[:12], age_sec,
                )
    except Exception as _e:
        log.warning("[MTM_INTEL_FAIL] mint=%s error=%s", mint[:12], _e)

    return None
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_mtm_price_row(mint: str) -> Optional[dict]:
    import time as _t
    _ip = _get_price_intel_first(mint)
    if _ip is not None:
        return {"observed_price": _ip, "price_updated_at": _t.time()}
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT observed_price, price_updated_at
                FROM market_snapshots
                WHERE mint_address=? AND candidate_state='mtm'
                  AND observed_price IS NOT NULL
                ORDER BY price_updated_at DESC, id DESC LIMIT 1
                """,
                (mint,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_last_known_price(mint: str) -> Optional[float]:
    """MTM-scoped price lookup. Intel DB first, fallback to market_snapshots.
    Returns only prices from WS oracle ticks (candidate_state='mtm').
    Prevents qualify-time latched prices from being used as close prices.

    SIGN-OFF FIX 6: Previously unscoped. Now intel-DB-first for freshness.
    Use get_last_known_price_unscoped() only as explicit last resort."""
    _ip = _get_price_intel_first(mint)
    if _ip is not None:
        return _ip
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT observed_price FROM market_snapshots
                WHERE mint_address=? AND candidate_state='mtm'
                  AND observed_price IS NOT NULL AND observed_price > 0
                ORDER BY price_updated_at DESC, id DESC LIMIT 1
                """,
                (mint,),
            ).fetchone()
        return float(row["observed_price"]) if row else None
    except Exception:
        return None


def get_last_known_price_unscoped(mint: str) -> Optional[float]:
    """Unscoped fallback - intel DB first, then any market_snapshots row.
    Only call when get_last_known_price() returns None AND a price is
    absolutely required. Do NOT use as primary close-price source."""
    _ip = _get_price_intel_first(mint)
    if _ip is not None:
        return _ip
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT observed_price FROM market_snapshots
                WHERE mint_address=? AND observed_price IS NOT NULL
                  AND observed_price > 0
                ORDER BY price_updated_at DESC, id DESC LIMIT 1
                """,
                (mint,),
            ).fetchone()
        return float(row["observed_price"]) if row else None
    except Exception:
        return None


def get_best_entry_price(
    mint: str,
    qualify_price: float,
    qualify_ts: float,
) -> tuple[float, str, float]:
    """
    Phase 1: Prevent stale MTM bleed from previous trades.

    Only considers prices newer than the qualification timestamp (qualify_ts).
    This ensures old MTM rows written for a previously closed position on the
    same mint cannot contaminate the entry price of a new position.

    Single-query approach: ORDER BY candidate_state='mtm' first (oracle ticks
    have highest authority), then by price_updated_at DESC. Returns the single
    best row, or falls back to qualify_price with no DB access.

    Returns: (price, source, ts)
      price  - the price to use as entry_price (always a valid float > 0)
      source - 'upgraded' if a fresher price was found, 'qualify' otherwise
      ts     - price_updated_at of the returned price (or qualify_ts if fallback)
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT observed_price, price_updated_at, candidate_state
                FROM market_snapshots
                WHERE mint_address = ?
                  AND observed_price > 0
                  AND price_updated_at >= ?
                  AND candidate_state != 'mtm'
                ORDER BY price_updated_at DESC
                LIMIT 1
                """,
                (mint, qualify_ts),
            ).fetchone()
            if row:
                _p = float(row["observed_price"])
                _t = float(row["price_updated_at"])
                if _p > 0:
                    return _p, "upgraded", _t
    except Exception as e:
        log.error("get_best_entry_price failed mint=%s: %s", mint[:16], e)

    # Fallback: always return a valid price - never None, never zero
    return qualify_price, "qualify", qualify_ts


def get_last_price_ts(mint: str) -> Optional[float]:
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT price_updated_at FROM market_snapshots
                WHERE mint_address=? AND candidate_state='mtm'
                  AND observed_price IS NOT NULL AND observed_price > 0
                ORDER BY price_updated_at DESC LIMIT 1
                """,
                (mint,),
            ).fetchone()
        return float(row["price_updated_at"]) if row else None
    except Exception:
        return None


def get_peak_price_since_open(mint: str, opened_at: float) -> Optional[float]:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(observed_price) AS peak FROM market_snapshots "
                "WHERE mint_address=? AND candidate_state='mtm' "
                "AND price_updated_at >= ? AND observed_price > 0",
                (mint, opened_at),
            ).fetchone()
        return float(row["peak"]) if row and row["peak"] is not None else None
    except Exception:
        return None


# ── ROLLING REGIME DETECTOR (SIGNOFF_LIVE_LANE_REPAIR_20260715) ──────────────
# Evidence: repeated Melbourne-time regimes (13:00-16:00 / 18:00-21:00 /
# 01:00-04:00) showed 17-33% runner rates with zero catastrophic losses.
# Hard-coding permanent hours would ossify a moving target, so regime is
# detected from a rolling window of CLOSED SIM trades using the durable
# held_peak_pct stamp. The regime NEVER relaxes a hard safety gate; it only
# contributes a bounded score bonus and is published for UI truth.
_MB_REGIME_CACHE: dict = {"ts": 0.0, "state": "STANDARD", "reason": "cold_start"}
_MB_REGIME_LOCK = threading.Lock()


def _mb_regime_snapshot() -> tuple[str, str]:
    """Return (state, reason). state in {'STANDARD','RUNNER_RICH'}."""
    now = time.time()
    with _MB_REGIME_LOCK:
        if now - _MB_REGIME_CACHE["ts"] < 60.0:
            return _MB_REGIME_CACHE["state"], _MB_REGIME_CACHE["reason"]
    state, reason = "STANDARD", "insufficient_sample"
    try:
        window_h = float(get_config_value("REGIME_WINDOW_HOURS", 3.0))
        min_trades = int(get_config_value("REGIME_MIN_TRADES", 8))
        runner_pct_floor = float(get_config_value("REGIME_RUNNER_RATE_PCT", 22.0))
        runner_peak_pct = float(get_config_value("REGIME_RUNNER_PEAK_PCT", 80.0))
        with get_connection() as conn:
            _cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(paper_positions)").fetchall()}
            if "held_peak_pct" not in _cols:
                reason = "held_peak_pct_not_stamped_yet"
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, "
                    "SUM(CASE WHEN CAST(COALESCE(held_peak_pct,0) AS REAL)>=? "
                    "THEN 1 ELSE 0 END) AS runners "
                    "FROM paper_positions WHERE status='CLOSED' "
                    "AND UPPER(COALESCE(funding_mode,'SIM'))='SIM' "
                    "AND CAST(closed_at AS REAL)>=?",
                    (runner_peak_pct, now - window_h * 3600.0),
                ).fetchone()
                n = int(row[0] or 0)
                runners = int(row[1] or 0)
                if n >= min_trades:
                    rate = runners / n * 100.0
                    if rate >= runner_pct_floor:
                        state = "RUNNER_RICH"
                        reason = (f"{runners}/{n} runners ({rate:.1f}%) >= "
                                  f"{runner_pct_floor:.0f}% over last {window_h:.0f}h")
                    else:
                        reason = (f"{runners}/{n} runners ({rate:.1f}%) < "
                                  f"{runner_pct_floor:.0f}% over last {window_h:.0f}h")
                else:
                    reason = f"only {n}/{min_trades} closed SIM trades in {window_h:.0f}h window"
        # Publish for UI truth (canonical persisted state, not a label guess).
        try:
            with get_connection() as _rc:
                for _k, _v in (("MARKET_REGIME_STATE", state),
                               ("MARKET_REGIME_REASON", reason),
                               ("MARKET_REGIME_SAMPLED_AT", f"{now:.1f}")):
                    _rc.execute(
                        "INSERT OR REPLACE INTO system_config(key,value) VALUES(?,?)",
                        (_k, _v))
                _rc.commit()
        except Exception:
            pass
    except Exception as _re:
        reason = f"detector_error:{type(_re).__name__}"
    with _MB_REGIME_LOCK:
        _MB_REGIME_CACHE.update({"ts": now, "state": state, "reason": reason})
    return state, reason


def get_mtm_prices_during_gap(mint: str, from_ts: float, to_ts: float) -> list[dict]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT observed_price, price_updated_at
                FROM market_snapshots
                WHERE mint_address=? AND candidate_state='mtm'
                  AND price_updated_at BETWEEN ? AND ?
                  AND observed_price IS NOT NULL AND observed_price > 0
                ORDER BY price_updated_at ASC
                """,
                (mint, from_ts, to_ts),
            ).fetchall()
        return [{"price": float(r["observed_price"]), "ts": float(r["price_updated_at"])}
                for r in rows]
    except Exception as e:
        log.warning("get_mtm_prices_during_gap failed mint=%s: %s", mint, e)
        return []


def fetch_dexscreener_price(mint: str) -> Optional[float]:
    """
    Direct DexScreener fallback when MTM rows are absent.
    Preserves paper_executor fix: pump tokens drop off Jupiter before DexScreener.
    """
    try:
        import requests
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=5, headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return None
        pairs = (resp.json() or {}).get("pairs") or []
        sol = [p for p in pairs if str(p.get("chainId", "")).lower() == "solana"]
        if not sol:
            return None
        best = max(sol, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        px = float(best.get("priceUsd") or 0)
        return px if px > 0 else None
    except Exception:
        return None


# -----------------------------------------------------------------------------
# POSITION MARKING / LATENCY
# -----------------------------------------------------------------------------

def update_position_mark(
    position_id: int,
    current_price: float,
    unrealized_pnl_usd: float,
    marked_at: float,
    source: str = "engine",   # 'oracle' | 'engine' | 'fallback' | 'router-stale'
    router_result: "Optional[dict]" = None,  # price_router result dict, optional
) -> None:
    """
    Write live mark to paper_positions.
    If router_result is provided, also writes live_exec_* columns:
      live_exec_price, live_exec_pct, live_exec_source, live_exec_updated_at,
      live_exec_age_sec, live_exec_confidence, live_exec_can_exit
    This makes update_position_mark the single write point for all mark data.

    EXECUTION TRUTH INVARIANT:
    unrealized_pnl_usd is only written non-zero when ALL conditions are true:
      - router_result is provided
      - router_result["can_execute_exit"] == True
      - router_result["price"] > 0
    Otherwise unrealized_pnl_usd is forced to 0.0 regardless of what caller passed.
    This is the single enforcement point - callers must not bypass it.
    """
    # Enforce execution truth: PnL=0 unless router confirms executable price
    _router_executable = (
        router_result is not None
        and router_result.get("can_execute_exit", False)
        and float(router_result.get("price", 0) or 0) > 0
    )
    if not _router_executable:
        unrealized_pnl_usd = 0.0
    try:
        with get_connection() as conn:
            cols = _get_pp_cols()  # cached - no PRAGMA per tick

            # Pre-fetch entry_price + mint_address in one SELECT
            # (used for live_exec_pct calc and TLE - avoids 2 extra SELECTs)
            _pre = conn.execute(
                "SELECT entry_price, mint_address FROM paper_positions WHERE id=?",
                (position_id,)
            ).fetchone()
            _entry_price  = float(_pre["entry_price"]  or 0) if _pre else 0.0
            _mint_address = str(_pre["mint_address"] or "")  if _pre else ""

            # ── MERGED SINGLE UPDATE ─────────────────────────────────────────
            # Base columns (always written) + live_exec_* columns (when router
            # available) merged into one UPDATE to halve write round-trips.
            _set_parts = [
                "unrealized_pnl_usd=?",
                "last_price=?",
                "last_marked_at=?",
                "highest_price_seen = CASE WHEN COALESCE(highest_price_seen,0) > ? THEN highest_price_seen ELSE ? END",
            ]
            _vals = [unrealized_pnl_usd, current_price, marked_at,
                     current_price, current_price]

            if "mark_source" in cols:
                _set_parts.append("mark_source=?")
                _vals.append(source)

            # Live exec columns - only when router has a price
            _rpct = 0.0
            if router_result is not None and router_result.get("price", 0) > 0:
                _rp  = router_result["price"]
                _rs  = router_result.get("source", source)
                _ra  = router_result.get("age_sec", 9999.0)
                _rc  = router_result.get("confidence", 0.0)
                _rce = 1 if router_result.get("can_execute_exit", False) else 0
                _rpct = (_rp - _entry_price) / _entry_price * 100.0 if _entry_price > 0 else 0.0

                for col, val in [
                    ("live_exec_price",      _rp),
                    ("live_exec_pct",        _rpct),
                    ("live_exec_source",     _rs),
                    ("live_exec_updated_at", marked_at),
                    ("live_exec_age_sec",    _ra),
                    ("live_exec_confidence", _rc),
                    ("live_exec_can_exit",   _rce),
                ]:
                    if col in cols:
                        _set_parts.append(f"{col}=?")
                        _vals.append(val)

            _vals.append(position_id)
            conn.execute(
                f"UPDATE paper_positions SET {', '.join(_set_parts)} WHERE id=?",
                tuple(_vals),
            )

            # ── MARK TAPE (instrumentation, 2026-07-08) ──────────────────
            # Append-only tape of every mark the exit manager can see.
            # Powers dwell/velocity/shelf calibration + phantom-peak forensics.
            # Rolling 48h retention. Failure here never affects marking.
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS mark_tape ("
                    " position_id INTEGER, mint TEXT, ts REAL,"
                    " price REAL, pct REAL, source TEXT)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mark_tape_pos"
                    " ON mark_tape(position_id, ts)"
                )
                _tape_pct = ((current_price - _entry_price) / _entry_price * 100.0
                             if _entry_price > 0 else 0.0)
                conn.execute(
                    "INSERT INTO mark_tape(position_id,mint,ts,price,pct,source)"
                    " VALUES(?,?,?,?,?,?)",
                    (position_id, _mint_address, marked_at,
                     float(current_price or 0), _tape_pct, str(source or "?")),
                )
                # opportunistic cheap prune (~1 in 200 marks)
                import random as _rnd
                if _rnd.random() < 0.005:
                    conn.execute("DELETE FROM mark_tape WHERE ts < ?",
                                 (time.time() - 172800,))
            except Exception:
                pass
            # ── /MARK TAPE ───────────────────────────────────────────────

            # TLE event - reuses pre-fetched entry_price + mint_address
            if _TLE_AVAILABLE and router_result and router_result.get("can_execute_exit") and router_result.get("price", 0) > 0:
                try:
                    _tle_mark(conn,
                        position_id=position_id,
                        mint=_mint_address,
                        entry_price=_entry_price,
                        router_result=router_result,
                        pct=_rpct)
                except Exception:
                    pass

            conn.commit()
    except Exception as exc:
        log.warning("update_position_mark failed pos_id=%s price=%s: %s",
                    position_id, current_price, exc)


def measure_write_latency_ms() -> float:
    try:
        start = time.monotonic()
        with get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS _latency_probe (ts INTEGER)")
            conn.execute("INSERT INTO _latency_probe VALUES (?)", (int(time.time()),))
            conn.execute("DELETE FROM _latency_probe")
            conn.commit()
        return (time.monotonic() - start) * 1000
    except Exception:
        return 9999.0


# -----------------------------------------------------------------------------
# SOURCE OF TRUTH - DRAWDOWN UPDATE
# -----------------------------------------------------------------------------

def update_drawdown_after_close(conn, pnl_usd: float, pos_size_usd: float) -> None:
    """
    Exponential drawdown memory + tiered brakes.
    Called inside the close_position_canonical transaction (conn already open).

    Tier 1 (>= threshold):        soft brake - halve position size next trade
    Tier 2 (>= threshold - 1.5):  auto-calibrate SL and position size
    Tier 3 (>= threshold - 2.0):  hard stop - DRAWDOWN_HALT_ACTIVE = 1
    """
    try:
        threshold = float(float(get_config_value("DRAWDOWN_HALT_THRESHOLD_PCT", 25.0)))
        current   = float(float(get_config_value("DRAWDOWN_ACCUMULATED_PCT",    0.0)))

        wallet_bal     = get_wallet_balance()
        portfolio_base = max(wallet_bal, pos_size_usd * 4, 1.0)
        loss_pct       = abs(pnl_usd) / portfolio_base * 100

        if pnl_usd >= 0:
            new_acc = round(current * 0.5, 4)                      # win decays memory
        else:
            new_acc = round(current * 0.65 + loss_pct, 4)          # loss compounds

        new_acc = max(0.0, min(new_acc, threshold * 2))
        conn.execute(
            "UPDATE system_config SET value=? WHERE key='DRAWDOWN_ACCUMULATED_PCT'",
            (str(round(new_acc, 4)),),
        )

        tier2 = threshold * 1.5
        tier3 = threshold * 2.0

        if new_acc >= tier3:
            conn.execute(
                "UPDATE system_config SET value='1' WHERE key='DRAWDOWN_HALT_ACTIVE'"
            )
            log.warning("DRAWDOWN TIER 3 HARD STOP - accumulated=%.2f%%", new_acc)
            _log_cognition("HEALTH",
                f"TIER 3 drawdown emergency at {new_acc:.1f}% - hard stop engaged. "
                "Capital protection law invoked.")

        elif new_acc >= tier2:
            current_sl      = float(float(get_config_value("STOP_LOSS_PCT",      10.0)))
            current_pos_pct = float(float(get_config_value("POSITION_SIZE_PCT",  5.0)))
            new_sl          = round(current_sl * 0.75, 2)
            new_pos_pct     = round(current_pos_pct * 0.5, 2)
            conn.execute("UPDATE system_config SET value=? WHERE key='STOP_LOSS_PCT'",
                         (str(new_sl),))
            conn.execute("UPDATE system_config SET value=? WHERE key='POSITION_SIZE_PCT'",
                         (str(new_pos_pct),))
            log.warning(
                "DRAWDOWN TIER 2 AUTO-CALIBRATE - %.2f%% SL %.1f%%-%.1f%% pos %.2f%%-%.2f%%",
                new_acc, current_sl, new_sl, current_pos_pct, new_pos_pct,
            )

        elif new_acc >= threshold:
            conn.execute(
                """
                INSERT INTO system_config (key, value, description) VALUES
                ('DRAWDOWN_SOFT_BRAKE', '1', 'Soft brake - position size halved')
                ON CONFLICT(key) DO UPDATE SET value='1'
                """
            )
            log.warning("DRAWDOWN TIER 1 SOFT BRAKE - accumulated=%.2f%%", new_acc)

        else:
            try:
                conn.execute(
                    "UPDATE system_config SET value='0' WHERE key='DRAWDOWN_SOFT_BRAKE'"
                )
            except Exception:
                pass

    except Exception as e:
        log.warning("update_drawdown_after_close failed: %s", e)


# -----------------------------------------------------------------------------
# SOURCE OF TRUTH - POSITION CLOSURE
# -----------------------------------------------------------------------------

def close_position_canonical(
    position_id: int,
    exit_price: float,
    exit_reason: str,
    *,
    closure_mode: str = "normal",
    force_scratch: bool = False,
    notes_prefix: str = "",
) -> bool:
    """
    THE SINGLE SOURCE OF TRUTH FOR ALL POSITION CLOSURES.

    All closes - normal, reconcile, zombie, manual, infra - route here.
    No other function may write status='CLOSED' or credit the wallet.

    closure_mode: "normal" | "reconcile" | "zombie" | "manual" | "infra"
    force_scratch: True - exit_price forced to entry_price (zombie scratch semantics)

    Returns True on success, False if position not found or already closed.
    """
    position = get_position_by_id_open(position_id)
    if not position:
        return False

    try:
        entry_price  = float(position["entry_price"]      or 0)
        pos_size_usd = float(position["position_size_usd"] or 0)
        quantity     = float(position["quantity"]          or 0)
        token_name   = str(position["token_name"]          or "UNKNOWN")
        mint         = str(position["mint_address"]        or "")
        is_real_position = _position_is_real(position)

        now = time.time()

        # A REAL-funded position must never be scratch-closed without an on-chain sell.
        if is_real_position and force_scratch:
            log.critical("[LIVE_SCRATCH_BLOCKED] REAL pos=%d requires confirmed on-chain exit; keeping OPEN", position_id)
            return False

        if force_scratch:
            exit_price = entry_price

        # ── PAPER EXIT SLIPPAGE SIMULATION ───────────────────────────────
        # Real pump.fun sells receive 1-4% less due to thin bids + slippage.
        # Apply in paper mode only, never on scratch/zombie exits.
        if not force_scratch and not is_real_position:
            try:
                _slip_exit = float(get_config_value("PAPER_SLIPPAGE_EXIT_PCT", 2.5)) / 100.0
                _fee_exit  = float(get_config_value("PAPER_FEE_PER_TX_USD", 0.10))
                exit_price = exit_price * (1.0 - _slip_exit)   # receive less on exit
                pos_size_usd = max(0.0, pos_size_usd - _fee_exit)  # deduct exit fee
            except Exception:
                pass  # never block a close on slippage calculation failure

        # CLOSE PRICE SANITY GUARD - single chokepoint for ALL closes
        # If exit_price > 1000x entry, oracle data is corrupt - force scratch exit
        if entry_price > 0 and exit_price > entry_price * 1000 and not force_scratch:
            log.warning(
                "CLOSE SANITY GUARD: corrupt exit_price=%.10f for %s "
                "(%.1fx entry=%.10f) - forcing scratch exit to protect wallet",
                exit_price, token_name, exit_price / entry_price, entry_price
            )
            exit_price = entry_price  # force scratch - no PnL credited

        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
        pnl_usd = 0.0 if force_scratch else pos_size_usd * (pnl_pct / 100.0)

        # SIGNOFF_CEILING_REMOVAL_20260715 (final directive item 4):
        # The previous SIGN-OFF FIX 1+5 clamped realized PnL to ±100% of stake.
        # PROVEN FAULT: the 3.5h audit shows two legitimate exits (+108.1% and
        # +111.5%, both RUNNER_PROFIT_LOCK with in-reason peaks 126.1%/129.5%)
        # stored as exactly $25.00 — a false 100% storage ceiling on a market
        # where longs routinely exceed +100%. Corrupt-mark protection already
        # exists upstream (the 1000x CLOSE SANITY GUARD above); the pre-write
        # cap is therefore retained ONLY as a corrupt-mark guard at
        # PNL_MAX_GAIN_MULTIPLE × stake (default 60x, matching the June-era
        # RUNNER_PNL_MAX_MULTIPLE). The downside clamp at -100% of stake stays:
        # a long cannot lose more than its stake — that bound is physical, not
        # cosmetic. All audit tables keep recording one consistent figure.
        if not force_scratch:
            try:
                _pnl_gain_mult = float(get_config_value("PNL_MAX_GAIN_MULTIPLE", 60.0))
            except Exception:
                _pnl_gain_mult = 60.0
            _pnl_gain_mult = max(1.0, _pnl_gain_mult)
            _pnl_up_cap = pos_size_usd * _pnl_gain_mult
            if pnl_usd > _pnl_up_cap:
                log.error(
                    "CORRUPT MARK CLAMP (pre-write): PnL +$%.2f exceeds %.0fx of "
                    "pos_size $%.2f for %s pos=%d. Clamping to $%.2f before DB write.",
                    pnl_usd, _pnl_gain_mult, pos_size_usd, token_name,
                    position_id, _pnl_up_cap,
                )
                pnl_usd = _pnl_up_cap
            elif pnl_usd < -pos_size_usd:
                log.error(
                    "WALLET CLAMP (pre-write): PnL -$%.2f exceeds -100%% of pos_size $%.2f "
                    "for %s pos=%d. Clamping to -$%.2f (stake floor) before DB write.",
                    abs(pnl_usd), pos_size_usd, token_name, position_id, pos_size_usd,
                )
                pnl_usd = -pos_size_usd
            # Recompute pct from the (possibly guarded) usd so all tables agree
            pnl_pct = (pnl_usd / pos_size_usd * 100.0) if pos_size_usd > 0 else 0.0

        outcome = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "SCRATCH")

        _HALT_REASON_MAP = {
            "TAKE_PROFIT":         "TAKE_PROFIT",
            "STOP_LOSS":           "STOP_LOSS",
            "TRAILING_STOP":       "TRAILING_STOP",
            "TIME_CUT_STAGNANT":   "TIME_CUT_STAGNANT",
            "TIME_CUT":            "TIME_CUT",
            "STALE_WINNER":        "STALE_WINNER",
            "MAX_HOLD_TIME":       "MAX_HOLD",
            "SAFE_RESTART":        "EXECUTOR_JAM",
            "RECONCILE_TP":        "TAKE_PROFIT",
            "RECONCILE_SL":        "STOP_LOSS",
            "RECONCILE_MAX_HOLD":  "MAX_HOLD",
            "RECONCILE_NO_DATA":   "EXECUTOR_JAM",
            "ZOMBIE":              "EXECUTOR_JAM",
            "DRAWDOWN":            "DRAWDOWN_HALT",
            "MANUAL":              "MANUAL",
        }
        reason_upper = str(exit_reason or "").upper()
        halt_reason  = "UNKNOWN"
        for key, tag in _HALT_REASON_MAP.items():
            if reason_upper.startswith(key):
                halt_reason = tag
                break

        is_infra_exit = halt_reason in {"EXECUTOR_JAM", "UNKNOWN"}

        # TRUE_DUAL_ATOMIC_EXIT_20260713:
        # REAL rows close only after confirmed on-chain sell. SIM rows never call live sell.
        _live_sig = None
        if is_real_position and not force_scratch:
            # Entry arming controls NEW risk only. It must never disable the
            # ability to liquidate risk already owned by the wallet.
            if not _LIVE_TRADING_AVAILABLE:
                log.critical("[LIVE_EXIT_BLOCKED] REAL pos=%d live_trading module unavailable; keeping OPEN", position_id)
                return False
            _emergency_exit = any(tag in reason_upper for tag in (
                "HARD_STOP", "STOP_LOSS", "MAX_HOLD", "STALE_PRICE",
                "NO_PRICE", "EMERGENCY", "RUG", "HONEYPOT"
            ))
            try:
                with get_connection() as _intent_conn:
                    _intent_conn.execute(
                        "UPDATE paper_positions SET live_state='EXIT_INTENT', "
                        "source_note=COALESCE(source_note,'')||? WHERE id=? AND status='OPEN'",
                        ("|exit_intent:" + str(exit_reason)[:160], position_id),
                    )
                    _intent_conn.commit()
            except Exception as _intent_err:
                log.error("[LIVE_EXIT_INTENT_WRITE_FAIL] pos=%d err=%s", position_id, _intent_err)
            try:
                _ls = _live_sell(mint, quantity, position_id, exit_price, emergency=_emergency_exit)
            except Exception as _le:
                log.error("[LIVE_SELL_ERROR] REAL pos=%d: %s; keeping OPEN", position_id, _le)
                return False
            if not _ls.get("success"):
                if _ls.get("confirmed") and _ls.get("tx_sig"):
                    try:
                        with get_connection() as _pending_conn:
                            _pending_conn.execute(
                                "UPDATE paper_positions SET live_state='SELL_CONFIRMED_UNRESOLVED',sell_tx_sig=?,"
                                "fill_meta_json=?,source_note=COALESCE(source_note,'')||? WHERE id=?",
                                (str(_ls.get("tx_sig")), json.dumps(_ls.get("fill_meta") or {}, sort_keys=True),
                                 "|manual_sell_reconciliation_required", position_id),
                            )
                            _pending_conn.commit()
                    except Exception as _pending_err:
                        log.critical("[LIVE_SELL_PENDING_WRITE_FAIL] pos=%d %s", position_id, _pending_err)
                log.critical("[LIVE_SELL_FAIL] REAL pos=%d error=%s; keeping unresolved/open",
                             position_id, _ls.get("error"))
                return False
            _live_sig = _ls.get("tx_sig")
            log.info("[LIVE_SELL] REAL pos=%d sig=%s", position_id, str(_live_sig or "")[:20])
            if _live_sig:
                exit_reason = f"LIVE:{str(_live_sig)[:12]}:{exit_reason}"
            # Canonical REAL settlement overwrites every mark/theoretical value.
            net_received_sol = float(_ls.get("net_sol_received") or 0.0)
            try:
                entry_spent_sol = float(position["entry_sol_spent"] or 0.0)
            except Exception:
                entry_spent_sol = 0.0
            if entry_spent_sol <= 0 or net_received_sol <= 0:
                log.critical("[LIVE_SETTLEMENT_UNRESOLVED] pos=%d entry_sol=%s exit_sol=%s; keeping unresolved",
                             position_id, entry_spent_sol, net_received_sol)
                return False
            settlement_pnl_sol = net_received_sol - entry_spent_sol
            pnl_pct = (settlement_pnl_sol / entry_spent_sol) * 100.0
            pnl_usd = pos_size_usd * (pnl_pct / 100.0)
            exit_price = float(_ls.get("actual_exit_price") or 0.0)
            if exit_price <= 0:
                exit_price = entry_price * (1.0 + pnl_pct / 100.0)
            outcome = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "SCRATCH")
            now = float(_ls.get("chain_confirmed_at") or time.time())

        with get_connection() as conn:
            # Race guard: re-check OPEN inside transaction
            still_open = conn.execute(
                "SELECT id FROM paper_positions WHERE id=? AND status='OPEN'",
                (position_id,),
            ).fetchone()
            if not still_open:
                return False

            # Preserve live_exec_pct as final_exec_pct so closed-trade reviews
            # can show what the meter was reading at close time.
            _pp_cols_close = {r["name"] for r in conn.execute(
                "PRAGMA table_info(paper_positions)"
            ).fetchall()}
            _fep_set = ", final_exec_pct=?" if "final_exec_pct" in _pp_cols_close else ""
            _fep_val = (pnl_pct,) if _fep_set else ()

            # SIGNOFF_HELD_PEAK_STAMP_20260715 (final directive item 4):
            # PROVEN FAULT: held peak read as 0.0% on all 31 audited closes while
            # real peaks (87.9-129.5%) lived only inside exit-reason strings.
            # highest_price_seen IS maintained on every mark tick; the close path
            # simply never converted it into a durable peak percentage. Stamp it
            # here, unclamped, into held_peak_pct (added schema-safely).
            try:
                if "held_peak_pct" not in _pp_cols_close:
                    conn.execute("ALTER TABLE paper_positions ADD COLUMN held_peak_pct REAL")
                    _pp_cols_close.add("held_peak_pct")
            except Exception:
                pass
            _hp_set, _hp_val = "", ()
            try:
                _hps_row = conn.execute(
                    "SELECT highest_price_seen FROM paper_positions WHERE id=?",
                    (position_id,),
                ).fetchone()
                _hps = float(_hps_row[0] or 0.0) if _hps_row else 0.0
                _peak_px = max(_hps, exit_price, 0.0)
                if entry_price > 0 and _peak_px > 0:
                    _held_peak_pct = (_peak_px - entry_price) / entry_price * 100.0
                    _peak_sets = []
                    _peak_vals = []
                    if "held_peak_pct" in _pp_cols_close:
                        _peak_sets.append("held_peak_pct=?")
                        _peak_vals.append(round(_held_peak_pct, 4))
                    # V2/report compatibility: peak_pnl_pct is the field consumed
                    # by the public audit and several UI surfaces. Keep both peak
                    # columns in exact agreement instead of hiding real peaks only
                    # inside exit_reason text.
                    if "peak_pnl_pct" in _pp_cols_close:
                        _peak_sets.append("peak_pnl_pct=?")
                        _peak_vals.append(round(_held_peak_pct, 4))
                    if _peak_sets:
                        _hp_set = ", " + ", ".join(_peak_sets)
                        _hp_val = tuple(_peak_vals)
            except Exception:
                _hp_set, _hp_val = "", ()

            conn.execute(
                f"""
                UPDATE paper_positions
                SET status='CLOSED', exit_price=?, realized_pnl_usd=?,
                    unrealized_pnl_usd=0.0, closed_at=?,
                    last_price=?, last_marked_at=?, exit_reason=?,
                    exit_category=?, win_loss=?{_fep_set}{_hp_set}
                WHERE id=?
                """,
                (exit_price, pnl_usd, now, exit_price, now, exit_reason,
                 halt_reason, outcome) + _fep_val + _hp_val + (position_id,),
            )

            if is_real_position:
                try:
                    conn.execute(
                        "UPDATE paper_positions SET live_state='SETTLED',sell_tx_sig=?,chain_confirmed_at=?,"
                        "reconciled_at=?,exit_sol_received=?,exit_fee_sol=?,settlement_pnl_sol=?,fill_meta_json=? WHERE id=?",
                        (str(_live_sig or ""), now, float(_ls.get("reconciled_at") or time.time()),
                         float(_ls.get("net_sol_received") or 0.0), float(_ls.get("fee_sol") or 0.0),
                         settlement_pnl_sol, json.dumps(_ls.get("fill_meta") or {}, sort_keys=True), position_id),
                    )
                except Exception as exc:
                    log.critical("[LIVE_SETTLEMENT_WRITE_FAIL] pos=%d error=%s", position_id, exc)
                    raise

            conn.execute(
                """
                INSERT INTO paper_executions (
                    position_id, token_name, mint_address,
                    side, price, quantity, notional_usd, value_usd, reason, timestamp
                ) VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?)
                """,
                (position_id, token_name, mint,
                 exit_price, quantity, exit_price * quantity, pnl_usd, exit_reason, now),
            )

            # pnl_usd is bounded to [-stake, +PNL_MAX_GAIN_MULTIPLE×stake] before
            # this transaction (SIGNOFF_CEILING_REMOVAL_20260715 above).
            _wallet_delta = pos_size_usd + pnl_usd

            # In live mode: skip paper tracker - real balance synced from chain
            if not is_real_position:
                conn.execute(
                    "UPDATE system_state SET wallet_balance = wallet_balance + ? WHERE id=1",
                    (_wallet_delta,),
                )

            # SIM wallet audit only. REAL wallet truth is recorded by live transaction telemetry.
            if not is_real_position:
                try:
                    conn.execute(
                        """INSERT INTO wallet_write_log
                            (position_id, delta_usd, new_balance, source, token_name, pnl_usd, pnl_pct, timestamp)
                            VALUES (?, ?, (SELECT wallet_balance FROM system_state WHERE id=1), ?, ?, ?, ?, ?)""",
                        (position_id, _wallet_delta, f"CLOSE_{closure_mode}",
                         token_name, pnl_usd, pnl_pct, now),
                    )
                except Exception:
                    pass

            autopsy_note = (
                f"{notes_prefix} exit={exit_reason} halt_reason={halt_reason} "
                f"infra={is_infra_exit} pnl_pct={pnl_pct:.2f} mode={closure_mode}"
            ).strip()

            conn.execute(
                """
                INSERT INTO trade_autopsies (
                    position_id, token_name, mint_address,
                    win_loss, realized_pnl_usd, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (position_id, token_name, mint, outcome, pnl_usd, autopsy_note, now),
            )

            # Polaris learning feed - written on every close so Intelligence
            # tab analytics and Polaris win-rate calculations are always current.
            # Wrapped in try/except so analytics failure never blocks the close.
            try:
                hold_secs = now - float(position.get("opened_at") or now)
                # Get coverage metrics for this trade to give Polaris clean signal
                _cov = {}
                if _TLE_AVAILABLE:
                    try:
                        _cov = _tle_coverage(mint,
                            float(position.get("opened_at") or now), now)
                    except Exception:
                        pass
                _exit_validity = "UNKNOWN"
                if _TLE_AVAILABLE and _cov:
                    try:
                        _exit_validity = _tle_validity(
                            exit_price, entry_price, exit_reason, _cov,
                            router_can_execute=False,  # conservative: engine knows if router was used
                        )
                    except Exception:
                        pass
                conn.execute(
                    """
                    INSERT INTO polaris_trade_reviews (
                        position_id, token_name, mint_address,
                        win_loss, exit_category, realized_pnl_usd,
                        pnl_pct, hold_seconds, reviewed_at, polaris_version,
                        tick_count_during_trade, first_tick_delay_sec,
                        max_pct_seen, min_pct_seen,
                        coverage_score, exit_validity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'executor_v1', ?, ?, ?, ?, ?, ?)
                    """,
                    (position_id, token_name, mint,
                     outcome, halt_reason, pnl_usd,
                     pnl_pct, hold_secs, now,
                     _cov.get("tick_count", 0),
                     _cov.get("first_tick_delay_sec"),
                     _cov.get("max_pct_seen"),
                     _cov.get("min_pct_seen"),
                     _cov.get("coverage_score", 0.0),
                     _exit_validity),
                )
            except Exception:
                pass  # never block a close on analytics write failure

            update_drawdown_after_close(conn, pnl_usd, pos_size_usd)

            # Veto any lingering latched snapshots for this mint
            try:
                conn.execute(
                    """
                    UPDATE market_snapshots
                    SET execution_ready=0, candidate_state='vetoed',
                        quality_reason='POST_CLOSE_VETO'
                    WHERE mint_address=? AND candidate_state IN ('latched', 'pending')
                    """,
                    (mint,),
                )
            except Exception:
                pass

            # Emit TRADE_CLOSED event with full coverage audit.
            # router_can_execute: infer from closure_mode + exit_reason.
            # "normal" mode closes are router-gated - price was executable.
            # "guardian"/"zombie"/"reconcile" may use fallback prices.
            if _TLE_AVAILABLE:
                try:
                    _pr_can_exec = (
                        closure_mode == "normal"
                        and not force_scratch
                        and exit_price != entry_price
                    )
                    _tle_closed(conn,
                        position_id=position_id, mint=mint,
                        entry_price=entry_price, exit_price=exit_price,
                        realized_pnl=pnl_usd, exit_reason=exit_reason,
                        hold_seconds=now - float(position.get("opened_at") or now),
                        opened_at=float(position.get("opened_at") or now),
                        router_can_execute=_pr_can_exec,
                    )
                except Exception:
                    pass

            conn.commit()

        # Clear trail-logged flag so future positions are not silently suppressed
        _trail_logged_positions.discard(position_id)

        log.info(
            "CLOSED pos=%d %s mode=%s PnL=%+.4f USD (%+.2f%%) reason=%s",
            position_id, outcome, closure_mode, pnl_usd, pnl_pct, exit_reason,
        )

        # Cognition messages by exit type
        if "STOP_LOSS" in exit_reason:
            _log_cognition(token_name,
                f"Circuit breaker tripped. Stop-loss at {pnl_pct:.1f}% on {token_name}. "
                f"Capital returned: ${pos_size_usd + pnl_usd:.2f}.")
        elif "TAKE_PROFIT" in exit_reason:
            _log_cognition(token_name,
                f"Take profit harvested at {pnl_pct:.1f}% on {token_name}. "
                f"Extracted ${pnl_usd:.4f} USD. Sovereign gain secured.")
        elif "TRAILING_STOP" in exit_reason:
            _log_cognition(token_name,
                f"Trailing stop executed on {token_name} at {pnl_pct:.1f}%.")
        elif closure_mode == "reconcile":
            _log_cognition(token_name,
                f"Reconciler closed {token_name} {outcome} at {pnl_pct:+.1f}% "
                f"(gap closure). Capital returned: ${pos_size_usd + pnl_usd:.2f}.",
                meta={"reason": exit_reason, "pnl_usd": round(pnl_usd, 4)})
        elif closure_mode == "zombie":
            _log_cognition(token_name,
                f"Zombie position {token_name} closed "
                f"({'at scratch' if force_scratch else 'best available price'}). "
                f"Slot freed. Reason: {exit_reason}. "
                f"Capital returned: ${pos_size_usd + pnl_usd:.2f}.",
                meta={"position_id": position_id, "pnl_usd": pnl_usd})
        else:
            _log_cognition(token_name,
                f"Position {position_id} closed {outcome}. "
                f"PnL={pnl_usd:+.4f} USD ({pnl_pct:+.2f}%). Reason: {exit_reason}")

        update_heartbeat(SERVICE_NAME, "ALIVE",
            f"closed pos={position_id} {outcome} pnl={pnl_usd:+.4f}",
            work_processed=1, last_success_at=now)

        # Update momentum gate audit row if one exists for this position
        try:
            _mg_outcome = (
                "WIN"  if pnl_pct > 0 or any(x in exit_reason for x in ("TAKE_PROFIT", "TRAIL"))
                else "LOSS"
            )
            with get_connection() as _mg_close:
                _mg_close.execute("""
                    UPDATE momentum_gate_audit
                    SET reviewed_at      = ?,
                        eventual_pnl_pct = ?,
                        eventual_outcome = ?,
                        exit_reason      = ?
                    WHERE position_id = ?
                """, (now, round(pnl_pct, 4), _mg_outcome, exit_reason, position_id))
                _mg_close.commit()
        except Exception:
            pass

        # ── TAX ALLOCATION - on profitable closes only ────────────────────────
        # Fire-and-forget: never interrupts close, never raises.
        # Allocates virtual tax reserve from realized profit.
        if pnl_usd > 0 and not force_scratch:
            try:
                from services.tax_allocator import allocate_tax as _alloc_tax
                _alloc_tax(position_id, pnl_usd, token_name)
            except Exception:
                pass  # tax allocation is observational - never blocks close

        # ── SMART MONEY OUTCOME - observational only, feeds self-learning loop ─
        # Post-commit, fire-and-forget. Never blocks or fails a close.
        try:
            from services.smart_money_metrics import record_trade_outcome as _sm_record
            # Look up stored smart_money_score for this token if available
            _sm_score = None
            try:
                with get_connection() as _smc:
                    _smr = _smc.execute(
                        "SELECT smart_money_score FROM token_metrics "
                        "WHERE token_name=? OR token_name=? "
                        "ORDER BY ts DESC LIMIT 1",
                        (mint, token_name)
                    ).fetchone()
                    if _smr: _sm_score = int(_smr[0] or 0)
            except Exception:
                pass
            if _sm_score is not None:
                _sm_record(score=_sm_score, pnl=pnl_usd)
                log.debug("smart_money outcome recorded score=%d pnl=%+.4f", _sm_score, pnl_usd)
        except Exception as e:
            log.debug("smart_money outcome record skipped: %s", e)

        # COPYTRADE OUTCOME RESTORE — observational A/B ledger only.
        try:
            try:
                from services.copytrade_influence import record_outcome as _ct_outcome
            except Exception:
                from copytrade_influence import record_outcome as _ct_outcome  # type: ignore
            _ct_mfe = _ct_mae = None
            try:
                if isinstance(position, dict):
                    _ct_mfe = position.get("peak_pct")
                    _ct_mae = position.get("max_adverse_pct") or position.get("trough_pct")
            except Exception:
                pass
            _ct_outcome(mint, position_id=position_id, pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        max_favourable_pct=float(_ct_mfe) if _ct_mfe is not None else None,
                        max_adverse_pct=float(_ct_mae) if _ct_mae is not None else None,
                        exit_reason=exit_reason)
        except Exception as _ct_err:
            log.debug("copytrade outcome record skipped: %s", _ct_err)

        # META LEARNING LOOP
        try:
            with get_connection() as _ml_conn:
                _ml_count = _ml_conn.execute(
                    "SELECT COUNT(*) FROM paper_positions WHERE status='CLOSED'"
                ).fetchone()[0]
                if _ml_count > 0 and _ml_count % 50 == 0:
                    _ml_rows = _ml_conn.execute("""
                        SELECT realized_pnl_usd, confidence
                        FROM paper_positions
                        WHERE status='CLOSED'
                        ORDER BY closed_at DESC
                        LIMIT 50
                    """).fetchall()
                    _ml_wins   = [r for r in _ml_rows if r[0] is not None and float(r[0]) > 0 and r[1] is not None]
                    _ml_losses = [r for r in _ml_rows if r[0] is not None and float(r[0]) <= 0 and r[1] is not None]
                    if _ml_wins and _ml_losses:
                        _ml_win_conf  = sum(float(r[1]) for r in _ml_wins)  / len(_ml_wins)
                        _ml_loss_conf = sum(float(r[1]) for r in _ml_losses) / len(_ml_losses)
                        if _ml_loss_conf > _ml_win_conf:
                            _ml_current = float(get_config_value("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.75))
                            _ml_new = round(min(max(_ml_current + 0.02, 0.50), 0.85), 3)
                            _ml_conn.execute(
                                "UPDATE system_config SET value=? WHERE key='SUPERVISOR_MIN_MINT_CONFIDENCE'",
                                (str(_ml_new),),
                            )
                            _ml_conn.commit()
                            log.info("META LEARNING: conf floor adjusted %.3f → %.3f (%d trades)",
                                     _ml_current, _ml_new, _ml_count)
        except Exception as _ml_err:
            log.debug("meta learning skipped: %s", _ml_err)

        return True

    except Exception as e:
        log.exception("close_position_canonical failed pos=%d: %s", position_id, e)
        return False


# Legacy shim - preserves any external call sites expecting the old signature
def close_position(position_id: int, exit_price: float, exit_reason: str) -> None:
    close_position_canonical(position_id, exit_price, exit_reason, closure_mode="normal")


def momentum_gate_stats() -> dict:
    """
    Return momentum gate shadow statistics.
    Run periodically to evaluate Phase 2 promotion readiness.

    Promotion criteria (ChatGPT directive):
      - false_negatives < 10-15%   (veto + WIN - cases we would have missed)
      - true_negatives high         (veto + LOSS - cases we correctly avoided)
      - avg_pnl_allowed > avg_pnl_vetoed
      - payoff ratio remains intact
    """
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                                         AS total,
                    SUM(CASE WHEN would_veto=1 THEN 1 ELSE 0 END)                   AS vetoed,
                    SUM(CASE WHEN would_veto=1 AND eventual_outcome='WIN'  THEN 1 ELSE 0 END) AS false_negatives,
                    SUM(CASE WHEN would_veto=1 AND eventual_outcome='LOSS' THEN 1 ELSE 0 END) AS true_negatives,
                    SUM(CASE WHEN would_veto=0 AND eventual_outcome='WIN'  THEN 1 ELSE 0 END) AS true_positives,
                    SUM(CASE WHEN would_veto=0 AND eventual_outcome='LOSS' THEN 1 ELSE 0 END) AS false_positives,
                    AVG(CASE WHEN would_veto=0 THEN eventual_pnl_pct END)            AS avg_pnl_allowed,
                    AVG(CASE WHEN would_veto=1 THEN eventual_pnl_pct END)            AS avg_pnl_vetoed
                FROM momentum_gate_audit
                WHERE reviewed_at IS NOT NULL
            """).fetchone()
            if row:
                stats = dict(row)
                total    = stats.get("total") or 0
                vetoed   = stats.get("vetoed") or 0
                fn       = stats.get("false_negatives") or 0
                fn_rate  = (fn / vetoed * 100) if vetoed > 0 else 0
                stats["false_negative_rate_pct"] = round(fn_rate, 1)
                promote  = (
                    total >= 50 and
                    fn_rate < 15 and
                    (stats.get("avg_pnl_allowed") or 0) > (stats.get("avg_pnl_vetoed") or 0)
                )
                stats["ready_for_phase_2"] = promote
                return stats
    except Exception as e:
        log.debug("momentum_gate_stats failed: %s", e)
    return {}


# -----------------------------------------------------------------------------
# ENTRY SCANNER
# -----------------------------------------------------------------------------

def scan_for_entries() -> int:
    """
    Scan market_snapshots for latched signals and open paper positions.
    Full original logic from paper_executor.py - no changes to gate order or sizing.
    Returns count of new positions opened this cycle.
    """
    opened = 0

    # Gate 0: drawdown hard halt
    halt = str(str(get_config_value("DRAWDOWN_HALT_ACTIVE", "0"))).strip()
    if halt == "1":
        pct = str(get_config_value("DRAWDOWN_ACCUMULATED_PCT", "0.0"))
        log.info("ENTRY SCAN SKIPPED - drawdown halt active (%s%%)", pct)
        return 0

    # ===============================
    # ORACLE LIVENESS - HARD FIX V2
    # ===============================
    # Reads MAX(ts_ms) from sentinuity_intelligence.db (shared between processes).
    # The old in-memory oracle_last_write_age() always returned 9999 because
    # ws_price_oracle runs in a separate process. This is the permanent fix.
    _oracle_gate_sec = float(get_config_value("ORACLE_LIVENESS_GATE_SEC", 300.0))

    def _get_oracle_age_sec():
        try:
            import sqlite3 as _sq3, os as _os, time as _time
            _idb_path = _os.path.join(
                _os.path.dirname(_os.path.dirname(__file__)),
                "sentinuity_intelligence.db"
            )
            _ic = _sq3.connect(_idb_path, timeout=2)
            _latest = _ic.execute("SELECT MAX(ts_ms) FROM mtm_ticks").fetchone()[0]
            _ic.close()
            if not _latest:
                return 9999.0
            return _time.time() - (_latest / 1000.0)
        except Exception as e:
            log.warning("[ORACLE AGE ERROR] %s", e)
            return 0.0  # fail-open -- never block trading on DB error

    _oracle_age = _get_oracle_age_sec()
    log.info("[ORACLE AGE] %.1fs (gate=%.0fs)", _oracle_age, _oracle_gate_sec)

    if _oracle_age > _oracle_gate_sec:
        _current_open = count_open_positions()
        if _current_open > 0:
            log.warning(
                "ENTRY SCAN BLOCKED - oracle stale %.1fs (gate=%.0fs) with %d open",
                _oracle_age, _oracle_gate_sec, _current_open,
            )
            return 0
    # Gate passed (or no open positions)

    # Gate 1: latency probe (cached, re-probed at most once per minute)
    limit_ms = float(float(get_config_value("EXECUTOR_WRITE_LATENCY_LIMIT_MS", 10000.0)))
    _lc = getattr(scan_for_entries, "_latency_cache", {"ms": 0.0, "ts": 0.0})
    scan_for_entries._latency_cache = _lc
    _now_ts = time.time()
    if _now_ts - _lc["ts"] > 60:
        _lc["ms"] = measure_write_latency_ms()
        _lc["ts"] = _now_ts

    latency      = float(_lc["ms"] or 0.0)
    degraded_mode = latency > limit_ms
    if degraded_mode:
        log.warning(
            "ENTRY SCAN DEGRADED - latency %.0fms > %.0fms; proceeding cautiously",
            latency, limit_ms,
        )

    # TRUE DUAL: paper capacity is independent from the REAL mirror lane.
    _paper_max = int(get_config_value("PAPER_MAX_OPEN_POSITIONS",
                                      get_config_value("EXECUTOR_MAX_OPEN_POSITIONS", MAX_OPEN_POSITIONS)))
    if count_open_positions("SIM") >= _paper_max:
        return 0
    max_pos = _paper_max

    # Gate 3: wallet balance
    balance = get_wallet_balance()
    if balance <= 0:
        log.warning("ENTRY SCAN BLOCKED - wallet balance zero or negative")
        return 0

    # Position sizing
    pos_pct      = float(float(get_config_value("POSITION_SIZE_PCT", 5.0)))
    pos_size_usd = float(float(get_config_value("PAPER_POSITION_SIZE_USD", get_config_value("POSITION_SIZE_USD", 25.0))))
    if pos_pct > 0:
        pos_size_usd = round(balance * (pos_pct / 100.0), 2)
    pos_size_usd = max(5.0, min(pos_size_usd, balance * 0.25))
    # PAPER HARD CAP: never open more than $500 per position regardless of wallet
    _max_paper = float(get_config_value("MAX_PAPER_POSITION_USD", 500.0))
    if pos_size_usd > _max_paper:
        log.warning("POSITION SIZE CAP: $%.2f capped to $%.2f", pos_size_usd, _max_paper)
        pos_size_usd = _max_paper

    soft_brake = str(str(get_config_value("DRAWDOWN_SOFT_BRAKE", "0"))).strip()
    if soft_brake == "1":
        pos_size_usd = max(5.0, round(pos_size_usd * 0.5, 2))
        log.info("SOFT BRAKE ACTIVE - position size halved to $%.2f", pos_size_usd)

    # ── HOUR GATE - evidence-based UTC hour filtering ──────────────────────
    # Built from 1,458 trade sample. Block hours: avg PnL < -0.30, WR < 15%.
    # Reduce hours: avg negative, require higher confidence floor.
    # Config keys: HOUR_GATE_ENABLED, HOUR_GATE_BLOCK_UTC, HOUR_GATE_REDUCE_UTC,
    #              HOUR_GATE_REDUCE_CONF, POSITION_SIZE_REDUCE_HOURS_USD
    import datetime as _dt
    _hour_gate_enabled = str(get_config_value("HOUR_GATE_ENABLED", "0")).strip() == "1"
    if _hour_gate_enabled:
        _utc_hour = _dt.datetime.utcnow().hour
        _block_str  = str(get_config_value("HOUR_GATE_BLOCK_UTC",  "")).strip()
        _reduce_str = str(get_config_value("HOUR_GATE_REDUCE_UTC", "")).strip()
        _block_hours  = {int(h.strip()) for h in _block_str.split(",")  if h.strip().isdigit()}
        _reduce_hours = {int(h.strip()) for h in _reduce_str.split(",") if h.strip().isdigit()}

        if _utc_hour in _block_hours:
            # DOCTRINE:
            # Paper → NEVER blocked by hour gate. Learns 24/7 no exceptions.
            # Live  → blocked in bad hours UNLESS all 5 Mode B conditions pass
            #         (exceptional signal overrides hour restriction).
            if _is_live_mode():
                # Live: hour gate blocks normal entries.
                # Mode B exceptional fire still allowed - checked later in pipeline.
                # We do NOT return 0 here for live - Mode B gate handles it.
                log.info(
                    "HOUR_GATE_LIVE utc_hour=%d - live normal entry suppressed, "
                    "Mode B exceptional fire still active",
                    _utc_hour,
                )
            else:
                # Paper: never blocked - learning continues regardless of hour
                log.debug(
                    "HOUR_GATE utc_hour=%d - paper learning continues (gate is live-only)",
                    _utc_hour,
                )

        if _utc_hour in _reduce_hours:
            _reduce_conf = float(get_config_value("HOUR_GATE_REDUCE_CONF", "0.87"))
            _reduce_size = float(get_config_value("POSITION_SIZE_REDUCE_HOURS_USD", pos_size_usd))
            log.info("HOUR_GATE_REDUCE utc_hour=%d - conf floor raised to %.2f, size $%.2f",
                     _utc_hour, _reduce_conf, _reduce_size)
            # Raise the effective min confidence for this scan cycle
            # Achieved by temporarily overriding the supervisor floor check here
            # We pass it via a scan-local variable picked up in Phase A gate below
            _hour_conf_floor_override = _reduce_conf
            pos_size_usd = max(5.0, _reduce_size)
        else:
            _hour_conf_floor_override = None
    else:
        _hour_conf_floor_override = None
    # ── END HOUR GATE ───────────────────────────────────────────────────────

    # Schema-safe confidence expression - inspect market_snapshots columns first
    # Must be defined BEFORE the SELECT query that uses _snap_conf_expr
    try:
        with get_connection() as _sc:
            _ms_cols = {r[1] for r in _sc.execute("PRAGMA table_info(market_snapshots)").fetchall()}
        if "mint_confidence" in _ms_cols:
            _snap_conf_expr = "mint_confidence"
        elif "confidence" in _ms_cols:
            _snap_conf_expr = "confidence"
        else:
            _snap_conf_expr = "NULL"
    except Exception:
        _snap_conf_expr = "NULL"

    fetch_limit = 25  # always scan full candidate pool; degraded_mode limits opens, not inspections

    try:
        with get_connection() as conn:
            rows = conn.execute(
                (
                    "SELECT id, mint_address, token_name, observed_price, price_updated_at,"
                    " COALESCE(created_at, timestamp, price_updated_at, 0) AS created_at,"
                    " " + _snap_conf_expr + " AS snap_confidence"
                    " FROM market_snapshots"
                    " WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)"
                    "   AND candidate_state=\'latched\'"
                    "   AND observed_price IS NOT NULL AND observed_price > 0"
                    "   AND COALESCE(tx_hash, \'\') NOT LIKE \'mtm:%\'"
                    "   AND (? - COALESCE(created_at, timestamp, price_updated_at, 0)) <= ?"
                    " ORDER BY"
                    "   COALESCE(created_at, timestamp, price_updated_at, 0) DESC,"
                    "   price_updated_at DESC,"
                    "   id DESC"
                    " LIMIT ?"
                ),
                (time.time(), 1800, fetch_limit),  # 1800s loose SQL prefilter - Python hard gate enforces exact max_signal_age below
            ).fetchall()
            # Note: SQL prefilter uses 10x the Python gate as a loose first pass.
            # The Python hard gate below enforces the exact max_signal_age.
            # Using 10x in SQL avoids rejecting tokens whose created_at is slightly
            # mis-timestamped while still preventing ancient backlog from hitting Python.
    except Exception as e:
        log.warning("Entry scan read failed: %s", e)
        return 0

    tp_pct        = float(float(get_config_value("TAKE_PROFIT_PCT",           25.0)))
    sl_pct        = float(float(get_config_value("STOP_LOSS_PCT",             10.0)))
    max_price_age  = float(float(get_config_value("EXECUTOR_MAX_PRICE_AGE_SEC", 300.0)))
    # 300s: DexScreener prices written at qualification time are the primary source
    # for pre-graduation pump.fun tokens. Oracle (Jupiter) cannot always refresh them.
    # 60s was killing entries because the qualify→latch→execute path takes 30-90s
    # and the price was written at qualify time. 300s gives a realistic trading window.
    # GUILLOTINE: signal age gate - independent of price freshness.
    # price_age measures how old the PRICE is; signal_age measures how old the SIGNAL is.
    # A token qualified 29 minutes ago can have a fresh oracle price but zero momentum edge.
    # Default 600s - matches SIGNAL_TIER1_MAX_AGE_SEC. Pipeline latency (discovery
    # → ingest → qualify → price → supervisor → latch) is already 30-90s minimum.
    # 15s default was killing every signal before it could reach execution.
    # EXECUTOR_MAX_SIGNAL_AGE_SEC in DB overrides this.
    max_signal_age = float(float(get_config_value("EXECUTOR_MAX_SIGNAL_AGE_SEC", 600.0)))

    mints_opened_this_batch: set = set()
    for row in rows:
        try:
            snap_id     = int(row["id"])
            mint        = str(row["mint_address"] or "")
            token_name  = str(row["token_name"] or mint or "UNKNOWN")[:20]
            # Entry price: observed_price from latched snapshot only.
            # last_price/current_price are not selected by this query - do not reference them.
            entry_price = float(row["observed_price"] or 0)
            price_age   = time.time() - float(row["price_updated_at"] or 0)
            # SIGN-OFF BUG FIX: conf and entry_conf were never extracted in scan_for_entries.
            # The INSERT at the bottom references both - causing NameError on EVERY entry attempt.
            # The outer except caught it silently, printed [EXECUTION ERROR] to stdout,
            # but left latch_claimed_until set, permanently blocking that signal for 30s.
            # This was the root cause of 10 latched signals and 0 trades.
            _raw_conf  = row["snap_confidence"]
            conf       = float(_raw_conf) if _raw_conf is not None else 0.0
            entry_conf = float(_raw_conf) if _raw_conf is not None else None

            # GUILLOTINE: signal age hard gate - fires before any other per-row logic.
            # Measures how old the SIGNAL is (time since token was discovered/qualified),
            # independent of price freshness. Old signal = momentum edge gone regardless
            # of how fresh the oracle price is. Non-bypassable capital protection gate.
            now_ts     = time.time()
            created_ts = float(row["created_at"] or 0)
            signal_age = now_ts - created_ts if created_ts > 0 else float("inf")

            if signal_age > max_signal_age:
                log.warning(
                    "EXECUTION BLOCKED - SIGNAL TOO OLD snap=%d %s "
                    "signal_age=%.2fs (max=%.0fs) - momentum edge expired",
                    snap_id, mint[:16], signal_age, max_signal_age,
                )
                with get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE market_snapshots SET execution_ready=0,
                            candidate_state='vetoed',
                            quality_reason=?
                        WHERE id=?
                        """,
                        (f"SIGNAL_TOO_OLD_AT_EXEC_{int(signal_age)}s", snap_id),
                    )
                    conn.commit()
                continue

            # BATCH DEDUP: skip if same mint already opened this scan cycle
            if mint in mints_opened_this_batch:
                log.debug("BATCH DEDUP: skip %s", mint[:16])
                continue

            if not entry_price or not mint:
                continue

            # HARD STALE GUARD - NON-BYPASSABLE (belt-and-suspenders with SQL filter above)
            # Any signal whose price is older than max_price_age is vetoed here
            # regardless of what the qualifier or supervisor wrote to quality_reason.
            # This is the final gate before capital is deployed.
            if price_age > max_price_age:
                log.warning(
                    "EXECUTION BLOCKED - STALE PRICE snap=%d %s age=%.0fs (max=%.0fs)",
                    snap_id, mint[:16], price_age, max_price_age,
                )
                with get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE market_snapshots SET execution_ready=0,
                            candidate_state='vetoed',
                            quality_reason=COALESCE(NULLIF(quality_reason,''),'STALE_PRICE_AT_EXEC')
                        WHERE id=?
                        """,
                        (snap_id,),
                    )
                    conn.commit()
                continue

            # Re-check before each open
            if count_open_positions() >= max_pos:
                break

            # Blacklist gate
            with get_connection() as conn:
                bl = conn.execute(
                    "SELECT 1 FROM mint_blacklist WHERE mint_address=?", (mint,)
                ).fetchone()
                if bl:
                    conn.execute(
                        """
                        UPDATE market_snapshots SET execution_ready=0,
                            candidate_state='vetoed', quality_reason='BLACKLISTED'
                        WHERE id=?
                        """,
                        (snap_id,),
                    )
                    conn.commit()
                    continue

            # Explicit OPEN guard: never open second position for same mint
            with get_connection() as conn:
                _open_exists = conn.execute(
                    "SELECT 1 FROM paper_positions "
                    "WHERE mint_address=? AND status='OPEN' LIMIT 1",
                    (mint,),
                ).fetchone()
                if _open_exists:
                    conn.execute(
                        "UPDATE market_snapshots SET execution_ready=0, "
                        "candidate_state='vetoed', quality_reason='OPEN_POSITION_EXISTS' WHERE id=?",
                        (snap_id,),
                    )
                    conn.commit()
                    continue

            # Reentry gate - configurable via REENTRY_COOLDOWN_SECONDS (default 300s).
            # 0 = only block if position is currently OPEN (paper trading mode).
            # >0 = block if closed within N seconds (production mode).
            # The old permanent "never trade same mint twice" gate was replaced because
            # pump.fun tokens recycle constantly and a full history ban exhausts the
            # tradeable universe after a few hundred paper trades.
            _reentry_cooldown = float(get_config_value("REENTRY_COOLDOWN_SECONDS", 0.0))
            with get_connection() as conn:
                if _reentry_cooldown > 0:
                    # Production mode: block if traded within cooldown window
                    _cutoff = time.time() - _reentry_cooldown
                    _recent = conn.execute(
                        "SELECT 1 FROM paper_positions "
                        "WHERE mint_address=? AND COALESCE(closed_at, opened_at) > ? LIMIT 1",
                        (mint, _cutoff),
                    ).fetchone()
                    if _recent:
                        conn.execute(
                            "UPDATE market_snapshots SET execution_ready=0, "
                            "candidate_state='vetoed', quality_reason='REENTRY_COOLDOWN' "
                            "WHERE id=?", (snap_id,),
                        )
                        conn.commit()
                        continue
                else:
                    # Paper trading mode (default): only block if OPEN position exists
                    _open_exists = conn.execute(
                        "SELECT 1 FROM paper_positions "
                        "WHERE mint_address=? AND status='OPEN' LIMIT 1",
                        (mint,),
                    ).fetchone()
                    if _open_exists:
                        conn.execute(
                            "UPDATE market_snapshots SET execution_ready=0, "
                            "candidate_state='vetoed', quality_reason='OPEN_POSITION_EXISTS' "
                            "WHERE id=?", (snap_id,),
                        )
                        conn.commit()
                        continue

            # Curve completion re-check - token may have graduated since qualify time.
            # Fail-open: RPC error does NOT block entry (same policy as market_intelligence).
            # This closes the window between qualify-time gate and execution-time open.
            if _CURVE_CHECK_AVAILABLE:
                try:
                    _curve = _get_curve_progress(mint)
                    if _curve.get("complete") and not _curve.get("error"):
                        log.info(
                            "CURVE GRADUATED at open time - vetoing snap=%s mint=%s",
                            snap_id, mint[:16],
                        )
                        with get_connection() as conn:
                            conn.execute(
                                """UPDATE market_snapshots SET execution_ready=0,
                                       candidate_state='vetoed',
                                       quality_reason='CURVE_COMPLETE_AT_OPEN'
                                   WHERE id=?""",
                                (snap_id,),
                            )
                            conn.commit()
                        continue
                except Exception as _ce:
                    log.debug(
                        "Curve re-check skipped (RPC err) snap=%s mint=%s: %s",
                        snap_id, mint[:16], _ce,
                    )

            now = time.time()

            # ── PHASE A TEMPORAL GATE - FINAL ENFORCEMENT BEFORE OPEN ─────
            # Re-read live snapshot. Recompute temporal truth from raw timestamps.
            # Phase A.1 thresholds (NORTH_STAR.md + sync directive):
            #   signal_age   <= 45s   (momentum clock - unchanged)
            #   price_age    <= 45s   (raised from 20s to match oracle cadence)
            #   freshness_score >= 0.85 (recomputed, not stored)
            #   active_cognition = 1
            try:
                with get_connection() as _tg_conn:
                    _tg_row = _tg_conn.execute(
                        """SELECT price_updated_at, created_at, first_seen_at,
                                  timestamp, active_cognition, freshness_score,
                                  tier, token_name
                           FROM market_snapshots WHERE id=?""",
                        (snap_id,)
                    ).fetchone()

                if _tg_row:
                    import math as _math, datetime as _dt

                    def _pts_exec(v) -> float:
                        if not v: return 0.0
                        try: return float(v)
                        except Exception: pass
                        try:
                            s = str(v).strip()
                            for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                                         "%Y-%m-%d %H:%M:%S.%f"):
                                try: return _dt.datetime.strptime(s, _fmt).timestamp()
                                except ValueError: continue
                        except Exception: pass
                        return 0.0

                    _now_gate = time.time()

                    # Signal age: use latched_at (supervisor's HOT decision timestamp)
                    # as the reservation anchor. Falls back to price_updated_at then
                    # discovery timestamps. This measures "time since supervisor approved"
                    # not "time since discovery" - eliminates double-gating pipeline latency.
                    _latch_ts = (
                        _pts_exec(_tg_row.get("latched_at"))
                        or _pts_exec(_tg_row["price_updated_at"])
                        or max(
                            _pts_exec(_tg_row["created_at"]),
                            _pts_exec(_tg_row["first_seen_at"]),
                            _pts_exec(_tg_row["timestamp"]),
                        )
                    )
                    _gate_signal_age = (_now_gate - _latch_ts) if _latch_ts > 0 else float("inf")
                    if _gate_signal_age < 0:
                        _gate_signal_age = 0  # clock skew guard - prevents freshness explosion

                    # Price age: oracle write timestamp
                    _price_ts_gate  = _pts_exec(_tg_row["price_updated_at"])
                    _gate_price_age = (_now_gate - _price_ts_gate) if _price_ts_gate > 0 else float("inf")

                    # Freshness: recomputed from signal age. Half-life 276.89s = 0.85 at 45s.
                    _gate_freshness = round(max(0.0, _math.exp(-_gate_signal_age / 276.89)), 4)

                    # active_cognition: live value
                    _gate_active = int(_tg_row["active_cognition"] or 0)
                    _gate_tier   = str(_tg_row["tier"] or "UNKNOWN")

                    # Always log temporal state for diagnostics
                    log.debug(
                        "PHASE_A_GATE snap=%d %s signal_age=%.1fs price_age=%.1fs "
                        "freshness=%.4f active=%d tier=%s",
                        snap_id, mint[:16], _gate_signal_age, _gate_price_age,
                        _gate_freshness, _gate_active, _gate_tier,
                    )

                    _phase_a_fail = None
                    # Phase A.1: signal_age gate is configurable to account for
                    # pipeline latency (discovery→qualify→latch→executor = 35-95s).
                    # Default 120s - supervisor already validated HOT at latch time.
                    # Set EXECUTOR_PHASE_A_MAX_SIGNAL_AGE in system_config to tune.
                    _phase_a_signal_max = float(get_config_value("EXECUTOR_PHASE_A_MAX_SIGNAL_AGE", 120.0))
                    # SIGN-OFF FIX - PHASE A PRICE GATE (root cause: latched signals not opening)
                    # price_updated_at is written by MI at qualify time, NOT refreshed by supervisor.
                    # Oracle only subscribes to open-position mints + mints with price_updated_at<45s.
                    # Pre-entry latched mints have no oracle subscription: price_updated_at stays
                    # at qualify time. Pipeline qualify->latch takes 20-120s, so price_age at
                    # execution is 22-125s. The old hardcoded 45s gate killed every signal where
                    # the supervisor took more than ~43s to latch (which is most of them).
                    # Fix: gate is now configurable. Default 120s matches supervisor max signal age.
                    # Set EXECUTOR_PHASE_A_MAX_PRICE_AGE in system_config to tune down if needed.
                    _phase_a_price_max = float(get_config_value("EXECUTOR_PHASE_A_MAX_PRICE_AGE", 120.0))
                    if _gate_signal_age > _phase_a_signal_max:
                        _phase_a_fail = f"SIGNAL_TOO_OLD_AT_EXEC_{int(_gate_signal_age)}s"
                    elif _gate_price_age > _phase_a_price_max:
                        # Price too stale relative to configurable limit.
                        # If this fires, raise EXECUTOR_PHASE_A_MAX_PRICE_AGE or
                        # ensure ws_price_oracle refreshes pre-entry latched mints.
                        _phase_a_fail = f"PRICE_TOO_OLD_AT_EXEC_{int(_gate_price_age)}s"
                    elif _gate_freshness < float(get_config_value("EXECUTOR_FRESHNESS_MIN", 0.20)):
                        # Freshness gate - configurable, default 0.20.
                        # Freshness = exp(-signal_age / 276.89), where signal_age = now - latched_at.
                        # With supervisor stamping latched_at at latch time: signal_age~2-5s
                        # -> freshness~0.99 -> always passes at 0.20.
                        # Default 0.20 = safety floor allowing up to ~450s signal age.
                        # Old hardcoded 0.85 = required signal_age < 45s (impossible given pipeline latency).
                        # EXECUTOR_FRESHNESS_MIN = 0.0 to fully disable freshness gate.
                        _phase_a_freshness_min = float(get_config_value("EXECUTOR_FRESHNESS_MIN", 0.20))
                        _phase_a_fail = f"FRESHNESS_LOW_AT_EXEC_{_gate_freshness:.4f}_min={_phase_a_freshness_min:.2f}"
                    elif _gate_active != 1:
                        _phase_a_fail = "ACTIVE_COGNITION_ZERO_AT_EXEC"

                    if _phase_a_fail:
                        log.warning(
                            "PHASE_A_BLOCKED snap=%d %s reason=%s "
                            "signal_age=%.1fs price_age=%.1fs freshness=%.4f "
                            "active=%d tier=%s",
                            snap_id, mint[:16], _phase_a_fail,
                            _gate_signal_age, _gate_price_age, _gate_freshness,
                            _gate_active, _gate_tier,
                        )
                        with get_connection() as _tg_veto:
                            _tg_veto.execute(
                                """UPDATE market_snapshots SET execution_ready=0,
                                       candidate_state='EXECUTOR_STALE_GATE',
                                       quality_reason=?
                                   WHERE id=?""",
                                (_phase_a_fail, snap_id),
                            )
                            _tg_veto.commit()
                        continue
                    else:
                        log.info(
                            "PHASE_A_PASS snap=%d %s signal_age=%.1fs "
                            "price_age=%.1fs freshness=%.4f tier=%s - opening",
                            snap_id, mint[:16], _gate_signal_age,
                            _gate_price_age, _gate_freshness, _gate_tier,
                        )
            except Exception as _tg_err:
                log.debug("Phase A temporal gate error snap=%d: %s", snap_id, _tg_err)

            # ── HOUR CONF OVERRIDE: apply reduce-hour confidence floor ────
            # _hour_conf_floor_override is set above if current UTC hour is in
            # HOUR_GATE_REDUCE_UTC. If confidence is below that elevated floor,
            # skip this signal without vetoing it (may still open in a good hour).
            if _hour_conf_floor_override is not None and conf < _hour_conf_floor_override:
                log.info(
                    "HOUR_GATE_CONF_SKIP snap=%d %s conf=%.3f < hour_floor=%.2f utc_hour=%d",
                    snap_id, mint[:16], conf, _hour_conf_floor_override,
                    __import__("datetime").datetime.utcnow().hour,
                )
                continue

            # ── PHASE 1: ENTRY PRICE SCOPING ──────────────────────────────
            # Compute qualify_ts from price_age (already calculated above as
            # time.time() - row["price_updated_at"]). Reconstructing it here
            # avoids re-reading the row and keeps the calculation consistent.
            _qualify_price = float(row["observed_price"] or 0)
            _qualify_ts    = now - price_age  # == float(row["price_updated_at"])

            final_price, price_source, price_ts = get_best_entry_price(
                mint,
                _qualify_price,
                _qualify_ts,
            )

            # get_best_entry_price always returns a valid price (never None/0).
            # Sanity check: if something impossible slips through, fall back hard.
            if not final_price or final_price <= 0:
                final_price  = _qualify_price
                price_source = "qualify"
                price_ts     = _qualify_ts

            if not final_price or final_price <= 0:
                print(f"[EXECUTION SKIP] Invalid price in router result for {row['mint_address']} (qualify={_qualify_price})")
                continue

            entry_price = final_price
            price_age   = now - price_ts  # recalculate age against final price ts

            # ── MOMENTUM GATE - SHADOW PHASE (data collection, no blocking) ──
            # ChatGPT directive May 14 2026: measure before committing capital.
            # MOMENTUM_GATE_SHADOW_ONLY=1 → log only, never block
            # MOMENTUM_GATE_ENABLED=1 + SHADOW_ONLY=0 → hard veto
            _mg_shadow   = int(get_config_value("MOMENTUM_GATE_SHADOW_ONLY", 1))
            _mg_enabled  = int(get_config_value("MOMENTUM_GATE_ENABLED", 0))
            _mg_qual_pct = float(get_config_value("MOMENTUM_FROM_QUAL_PCT", 2.0))
            _mg_st_pct   = float(get_config_value("MOMENTUM_SHORT_TERM_PCT", 1.5))

            _mg_qual_price  = float(_qualify_price or entry_price)
            _mg_latest      = _get_price_intel_first(mint) or entry_price
            _mg_audit_id    = None

            _mg_move_qual = (
                ((_mg_latest / _mg_qual_price) - 1.0) * 100.0
                if _mg_qual_price > 0 and _mg_latest > 0 else 0.0
            )
            # Short-term: use same value unless cached price available
            _mg_move_st = _mg_move_qual

            _mg_would_veto = int(
                _mg_move_qual < _mg_qual_pct and _mg_move_st < _mg_st_pct
            )

            # Write audit row for every candidate
            try:
                with get_connection() as _mg_conn:
                    _mg_cur = _mg_conn.execute("""
                        INSERT INTO momentum_gate_audit
                            (snapshot_id, mint_address, token_name, qual_price,
                             latest_price, move_from_qual_pct, move_short_term_pct,
                             would_veto, entered, created_at)
                        VALUES (?,?,?,?,?,?,?,?,0,?)
                    """, (snap_id, mint, token_name, _mg_qual_price,
                          _mg_latest, round(_mg_move_qual, 4),
                          round(_mg_move_st, 4), _mg_would_veto, now))
                    _mg_audit_id = _mg_cur.lastrowid
                    _mg_conn.commit()
            except Exception as _mg_err:
                log.debug("momentum_gate_audit insert failed: %s", _mg_err)

            if _mg_would_veto:
                log.info(
                    "MOMENTUM_GATE_SHADOW_VETO snap=%d %s move_qual=%.2f%% move_st=%.2f%%",
                    snap_id, mint[:16], _mg_move_qual, _mg_move_st,
                )
                _log_cognition(token_name,
                    f"MOMENTUM_GATE_SHADOW_VETO: move_from_qual={_mg_move_qual:.2f}% "
                    f"threshold={_mg_qual_pct}% - would have blocked entry")
                # Hard gate: only activate if ENABLED and NOT shadow-only
                if _mg_enabled and not _mg_shadow:
                    log.warning("MOMENTUM_GATE_HARD_VETO snap=%d %s - skipping entry",
                                snap_id, mint[:16])
                    continue
            # ── END MOMENTUM GATE ──────────────────────────────────────────

            # ENTRY VIABILITY FILTER (LATENCY EDGE)
            if _qualify_price > 0 and entry_price > 0:
                pump_pct = ((entry_price - _qualify_price) / _qualify_price)
                ceiling = float(get_config_value("ENTRY_VIABILITY_CEILING", 0.25))
                if pump_pct > ceiling:
                    log.warning(
                        "LATE ENTRY BLOCKED %s pump=%.2f (ceiling=%.2f)",
                        mint[:8], pump_pct, ceiling,
                    )
                    continue

            quantity = (pos_size_usd / entry_price) if entry_price > 0 else 0
            # QUANTITY GUARD: reject absurd quantities from micro-priced tokens
            if quantity > 1_000_000_000:
                log.warning("QUANTITY GUARD: snap=%d qty=%.0f exceeds 1B "
                             "(entry=%.12f) skipping", snap_id, quantity, entry_price)
                continue

            # CLAIM this snapshot for execution (prevents guardian from resetting it
            # as orphaned while we are in the process of opening the position).
            try:
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE market_snapshots SET latch_claimed_until=? WHERE id=?",
                        (now + 30, snap_id),
                    )
                    conn.commit()
            except Exception:
                pass  # non-blocking - open proceeds regardless

            log.info(
                "EXECUTION_ATTEMPT snap=%d %s price=%.10f conf=%.3f "
                "signal_age=%.1fs price_age=%.1fs size=$%.2f",
                snap_id, token_name, entry_price, (conf or 0),
                signal_age, price_age, pos_size_usd,
            )
            print(f"[EXECUTION ATTEMPT] mint={row['mint_address']} price={entry_price}")

            # ── MODE B - CONVICTION-SCORED LIVE GATE (live only) ──────────
            # 2026-07-14 sign-off repair:
            #   * candidate-specific evidence decides live admission;
            #   * smart-wallet evidence is additive, never mandatory;
            #   * recent PAPER losses are a bounded score penalty, never a veto;
            #   * the old hard 30-180s signal-age window is removed;
            #   * price/oracle/liquidity/impact protections remain hard safety gates.
            # Paper mode still learns independently and is never blocked here.
            _mode_b_live_pass = False
            _mb_live_score = 0.0
            # SIGNOFF_LIVE_LANE_REPAIR_20260715: candidate-specific oracle
            # authority + evidence-led curve reserve. Defined unconditionally so
            # the live fire path can read them regardless of arming state.
            _mb_half_size = False
            _mb_oracle_authority = "GLOBAL_HEALTHY"
            _mb_curve_band = "UNKNOWN"
            _mb_regime_state, _mb_regime_reason = "STANDARD", "not_evaluated"
            _mb_preflight_reason = None
            if _live_lane_armed():
                _mb_reasons = []
                _mb_hard_blocks = []

                # Copytrade bonus is stripped from the base confidence before live
                # evaluation so the same evidence is not counted twice.
                _mb_conf_eval = float(conf or 0)
                try:
                    with get_connection() as _mb_ct:
                        _ct_row = _mb_ct.execute(
                            "SELECT copytrade_bonus FROM copytrade_influence_ledger "
                            "WHERE token_mint=? AND decision='BONUS_APPLIED' AND ts>=? "
                            "ORDER BY ts DESC LIMIT 1",
                            (mint, time.time() - 900),
                        ).fetchone()
                    if _ct_row and float(_ct_row[0] or 0) > 0:
                        _mb_conf_eval = max(0.0, _mb_conf_eval - float(_ct_row[0]))
                except Exception:
                    pass

                # Confidence: hard safety floor plus graduated conviction score.
                _mb_conf_hard_floor = float(get_config_value("LIVE_CONFIDENCE_HARD_FLOOR", 0.65))
                if _mb_conf_eval < _mb_conf_hard_floor:
                    _mb_hard_blocks.append(
                        f"conf={_mb_conf_eval:.3f}<{_mb_conf_hard_floor:.3f}"
                    )
                _mb_live_score += max(0.0, min(35.0, (_mb_conf_eval - 0.50) / 0.45 * 35.0))

                # Smart money is optional evidence. Missing/NOISE contributes zero,
                # while verified RUNNER evidence reduces the required score.
                _mb_tier = "UNAVAILABLE"
                _mb_sm_bonus = 0.0
                try:
                    from services.smart_money_metrics import get_score as _mb_sm_score
                    _mb_sm = _mb_sm_score(mint) or {}
                    _mb_tier = str(_mb_sm.get("tier", "NOISE")).upper()
                    if _mb_tier == "ELITE_RUNNER":
                        _mb_sm_bonus = 15.0
                    elif _mb_tier == "RUNNER":
                        _mb_sm_bonus = 10.0
                except Exception:
                    _mb_tier = "UNAVAILABLE"
                _mb_live_score += _mb_sm_bonus

                # Tide remains a hard infrastructure safety gate. Oracle ERROR/DEAD
                # remain unconditional hard blocks (infrastructure is genuinely
                # down). Oracle STALLED is no longer an unconditional global
                # capital veto: evidence shows the best six-hour window
                # (2026-07-14 13:00-19:00, +$420.22, 19 runners) ran with the
                # global hot-set marked STALLED 32.8% of samples, and four
                # 82-83% runners at adjusted confidence 0.89 were blocked partly
                # by the global flag. STALLED is deferred to a candidate-specific
                # oracle-authority resolution below, after candidate price age,
                # liquidity and route evidence are known.
                _mb_tide = "UNKNOWN"
                _mb_density = 0.0
                _mb_oracle = "UNKNOWN"
                _mb_oracle_stalled = False
                try:
                    _mb_tide = str(get_config_value("MARKET_TIDE_STATE", "NORMAL")).strip().upper()
                    _mb_density = float(get_config_value("MARKET_TIDE_DENSITY", "0") or 0)
                    _mb_oracle = str(get_config_value("WS_ORACLE_STATE", "UNKNOWN")).strip().upper()
                    if _mb_tide == "EXTREME" or _mb_density >= 200:
                        _mb_hard_blocks.append(f"tide=EXTREME({_mb_density:.0f}/min)")
                    if _mb_oracle in ("ERROR", "DEAD", "NETWORK_OUTAGE"):
                        _mb_hard_blocks.append(f"oracle={_mb_oracle}")
                    elif _mb_oracle == "STALLED":
                        if int(get_config_value("LIVE_ORACLE_CANDIDATE_AUTHORITY", 1)):
                            _mb_oracle_stalled = True  # resolved candidate-side below
                        else:
                            _mb_hard_blocks.append("oracle=STALLED")
                except Exception:
                    pass

                # Fresh-price scoring and hard maximum age. Signal age is now a
                # graduated quality measure, not a narrow 30-180 second veto.
                _mb_launch_age = float(signal_age or 0.0)
                _mb_price_age = float(price_age or 999999.0)
                _mb_max_price_age = float(get_config_value("LIVE_MAX_PRICE_AGE_SEC", 90.0))
                _mb_max_signal_age = float(get_config_value("LIVE_MAX_SIGNAL_AGE_SEC", 900.0))
                if _mb_price_age > _mb_max_price_age:
                    _mb_hard_blocks.append(
                        f"price_age={_mb_price_age:.0f}s>{_mb_max_price_age:.0f}s"
                    )
                elif _mb_price_age <= 30:
                    _mb_live_score += 20.0
                elif _mb_price_age <= 60:
                    _mb_live_score += 15.0
                else:
                    _mb_live_score += 8.0

                if _mb_launch_age > _mb_max_signal_age:
                    _mb_hard_blocks.append(
                        f"signal_age={_mb_launch_age:.0f}s>{_mb_max_signal_age:.0f}s"
                    )
                elif 20 <= _mb_launch_age <= 240:
                    _mb_live_score += 15.0
                elif _mb_launch_age <= 600:
                    _mb_live_score += 10.0
                else:
                    _mb_live_score += 4.0

                # Entry momentum is candidate-specific evidence. A modest positive
                # move helps; a severe reversal is a hard block.
                _mb_momentum = float(locals().get("_mg_move_qual", 0.0) or 0.0)
                if _mb_momentum >= 5.0:
                    _mb_live_score += 12.0
                elif _mb_momentum >= 2.0:
                    _mb_live_score += 9.0
                elif _mb_momentum >= 0.0:
                    _mb_live_score += 5.0
                elif _mb_momentum <= -8.0:
                    _mb_hard_blocks.append(f"momentum={_mb_momentum:.1f}%")
                else:
                    _mb_live_score -= 5.0

                # Live liquidity/impact protection is evaluated before wallet fire.
                _mb_curve_sol = 0.0
                _mb_rt_impact = None
                try:
                    _mb_sol_usd = float(get_config_value("SOL_PRICE_USD", 150.0))
                    with get_connection() as _mb_liq_conn:
                        # SIGN-OFF 2026-07-15: market_snapshots has no
                        # snapshot_timestamp column in the live schema. SQLite
                        # resolves every COALESCE identifier before execution,
                        # so the old ORDER BY raised OperationalError for every
                        # Mode B candidate even though price_updated_at existed.
                        #
                        # Resolve the newest available timestamp column from the
                        # actual schema. This preserves the liquidity gate; it
                        # does not weaken or bypass it.
                        _mb_ms_cols = {
                            str(_r["name"] if hasattr(_r, "keys") else _r[1])
                            for _r in _mb_liq_conn.execute(
                                "PRAGMA table_info(market_snapshots)"
                            ).fetchall()
                        }
                        _mb_order_col = next(
                            (
                                _c for _c in (
                                    "price_updated_at",
                                    "updated_at",
                                    "created_at",
                                    "timestamp",
                                    "id",
                                )
                                if _c in _mb_ms_cols
                            ),
                            None,
                        )
                        if not _mb_order_col:
                            raise sqlite3.OperationalError(
                                "market_snapshots has no usable recency column"
                            )
                        _mb_liq = _mb_liq_conn.execute(
                            "SELECT curve_sol_reserves FROM market_snapshots "
                            "WHERE mint_address=? AND curve_sol_reserves>0 "
                            f"ORDER BY {_mb_order_col} DESC LIMIT 1",
                            (mint,),
                        ).fetchone()
                    _mb_curve_sol = float(_mb_liq[0] or 0.0) if _mb_liq else 0.0
                    _mb_curve_floor = float(get_config_value("LIVE_SHADOW_CURVE_MIN_SOL", 2.0))
                    # SIGNOFF_LIVE_LANE_REPAIR_20260715: the 2 SOL floor becomes an
                    # evidence-led risk band instead of an automatic veto — three
                    # of the four missed 82-83% runners sat at 0.31/0.33/1.31 SOL.
                    # The soft lane ships DISABLED (LIVE_SOFT_CURVE_RESERVE=0)
                    # until replay_gate_variants.py proves superior expected value
                    # on the operator's own trade history. When enabled it uses
                    # half live size, an absolute curve minimum, a tighter
                    # round-trip impact cap, and a mandatory executable-route
                    # preflight (enforced in the oracle-authority resolution).
                    _mb_soft_curve = bool(int(get_config_value("LIVE_SOFT_CURVE_RESERVE", 0)))
                    _mb_curve_abs_min = float(get_config_value("LIVE_CURVE_ABS_MIN_SOL", 0.30))
                    # SIGNOFF_ROUTE_AUTHORITY_20260716:
                    # A fresh executable Jupiter quote is the canonical proof that
                    # the requested live notional can actually route. Curve reserve
                    # telemetry remains valuable supporting evidence, but missing or
                    # sub-floor curve data must not veto an otherwise executable
                    # route. Curve-gap candidates are forced to half-size and must
                    # pass the bounded Jupiter preflight below. No route still means
                    # fail closed.
                    _mb_route_authority = bool(int(get_config_value(
                        "LIVE_JUPITER_ROUTE_AUTHORITY", 1)))
                    _mb_curve_gap_requires_route = False
                    if _mb_curve_sol <= 0:
                        _mb_curve_band = "MISSING_ROUTE_REQUIRED"
                        if _mb_route_authority:
                            _mb_half_size = True
                            _mb_curve_gap_requires_route = True
                        else:
                            _mb_hard_blocks.append("curve=missing")
                    elif _mb_curve_sol < _mb_curve_floor and not (
                        _mb_soft_curve and _mb_curve_sol >= _mb_curve_abs_min
                    ):
                        if _mb_route_authority:
                            _mb_curve_band = "SUB_FLOOR_ROUTE_REQUIRED"
                            _mb_half_size = True
                            _mb_curve_gap_requires_route = True
                        else:
                            _mb_curve_band = "SUB_FLOOR_BLOCKED"
                            _mb_hard_blocks.append(
                                f"curve={_mb_curve_sol:.2f}SOL<{_mb_curve_floor:.2f}SOL"
                            )
                    else:
                        if _mb_curve_sol < _mb_curve_floor:
                            _mb_curve_band = "SUB_FLOOR_HALF_SIZE"
                            _mb_half_size = True
                        elif _mb_curve_sol >= 8.0:
                            _mb_curve_band = "DEEP"
                        elif _mb_curve_sol >= 4.0:
                            _mb_curve_band = "HEALTHY"
                        else:
                            _mb_curve_band = "FLOOR"
                        _mb_reserve_usd = _mb_curve_sol * _mb_sol_usd
                        _mb_impact = float(pos_size_usd) / (_mb_reserve_usd + float(pos_size_usd)) * 100.0
                        _mb_rt_impact = _mb_impact * 2.0
                        _mb_max_rt = float(get_config_value("MAX_ROUND_TRIP_SLIPPAGE_PCT", 8.0))
                        if _mb_half_size:
                            # Half-size cohort: model impact at half notional and
                            # cap it tighter than the standard lane.
                            _mb_impact_h = (float(pos_size_usd) * 0.5) / (
                                _mb_reserve_usd + float(pos_size_usd) * 0.5) * 100.0
                            _mb_rt_impact = _mb_impact_h * 2.0
                            _mb_max_rt = min(
                                _mb_max_rt,
                                float(get_config_value("LIVE_SOFT_CURVE_MAX_RT_PCT", 6.0)),
                            )
                        if _mb_rt_impact > _mb_max_rt:
                            _mb_hard_blocks.append(
                                f"rt_impact={_mb_rt_impact:.1f}%>{_mb_max_rt:.1f}%"
                            )
                        elif _mb_curve_band == "DEEP":
                            _mb_live_score += 18.0
                        elif _mb_curve_band == "HEALTHY":
                            _mb_live_score += 14.0
                        elif _mb_curve_band == "FLOOR":
                            _mb_live_score += 10.0
                        else:  # SUB_FLOOR_HALF_SIZE — thinner reserve earns less
                            _mb_live_score += 6.0
                except Exception as _mb_liq_err:
                    _mb_liq_name = type(_mb_liq_err).__name__
                    _mb_liq_msg = str(_mb_liq_err).replace("|", "/")[:120]
                    _mb_hard_blocks.append(
                        f"liquidity_check={_mb_liq_name}"
                        + (f":{_mb_liq_msg}" if _mb_liq_msg else "")
                    )

                # ── CANDIDATE-SPECIFIC ORACLE AUTHORITY ──────────────────────
                # (SIGNOFF_LIVE_LANE_REPAIR_20260715)
                # When the GLOBAL hot-set is STALLED, the candidate may still be
                # inside the empirically profitable envelope (hot-age p90 <=44.5s,
                # any-feed age p90 <=10.9s, writes/min p10 >=8.6, from the audited
                # 14-day history). Continuation is permitted only when ALL of:
                #   1. candidate's own price age is fresh (<= override max);
                #   2. oracle envelope telemetry is recent and inside bounds;
                #   3. a fresh executable Jupiter route exists (read-only
                #      preflight: route validity, entry impact, wallet usable) —
                #      this simultaneously proves RPC health and sellability
                #      direction at live size.
                # Stale candidate price, collapsed write cadence, missing route,
                # RPC failure and excessive impact all remain hard blocks. The
                # SUB_FLOOR_HALF_SIZE curve cohort requires the same executable-
                # route preflight even when the oracle is HEALTHY.
                _mb_need_preflight = bool(
                    _mb_oracle_stalled or _mb_half_size
                    or locals().get("_mb_curve_gap_requires_route", False)
                )
                if _mb_oracle_stalled:
                    _mb_ov_max_price_age = float(
                        get_config_value("LIVE_ORACLE_OVERRIDE_MAX_PRICE_AGE_SEC", 30.0))
                    _mb_env_hot_max = float(
                        get_config_value("LIVE_ORACLE_HOT_AGE_MAX_SEC", 44.5))
                    _mb_env_any_max = float(
                        get_config_value("LIVE_ORACLE_ANY_AGE_MAX_SEC", 10.9))
                    _mb_env_wpm_min = float(
                        get_config_value("LIVE_ORACLE_WPM_MIN", 8.6))
                    _mb_env_max_stale = float(
                        get_config_value("LIVE_ORACLE_TELEMETRY_MAX_AGE_SEC", 60.0))
                    _mb_env_fail = []
                    try:
                        _t_hot = float(get_config_value("WS_ORACLE_HOT_AGE_SEC", 999999.0))
                        _t_any = float(get_config_value("WS_ORACLE_ANY_AGE_SEC", 999999.0))
                        _t_wpm = float(get_config_value("WS_ORACLE_WPM", 0.0))
                        _t_at = float(get_config_value("WS_ORACLE_SAMPLED_AT", 0.0))
                    except Exception:
                        _t_hot, _t_any, _t_wpm, _t_at = 999999.0, 999999.0, 0.0, 0.0
                    if time.time() - _t_at > _mb_env_max_stale:
                        _mb_env_fail.append(f"telemetry_age>{_mb_env_max_stale:.0f}s")
                    if _mb_price_age > _mb_ov_max_price_age:
                        _mb_env_fail.append(
                            f"cand_price_age={_mb_price_age:.0f}s>{_mb_ov_max_price_age:.0f}s")
                    if _t_hot > _mb_env_hot_max:
                        _mb_env_fail.append(f"hot_age={_t_hot:.0f}s>{_mb_env_hot_max:.0f}s")
                    if _t_any > _mb_env_any_max:
                        _mb_env_fail.append(f"any_age={_t_any:.0f}s>{_mb_env_any_max:.0f}s")
                    if _t_wpm < _mb_env_wpm_min:
                        _mb_env_fail.append(f"wpm={_t_wpm:.0f}<{_mb_env_wpm_min:.0f}")
                    if _mb_env_fail:
                        _mb_oracle_authority = "STALLED_OUTSIDE_ENVELOPE"
                        _mb_hard_blocks.append(
                            "oracle=STALLED[" + ",".join(_mb_env_fail) + "]")
                        _mb_need_preflight = _mb_half_size  # override dead; only curve cohort still needs it
                    else:
                        _mb_oracle_authority = "CANDIDATE_ENVELOPE_OK"
                if _mb_need_preflight and not _mb_hard_blocks:
                    # Read-only route check. Never signs or submits. Only reached
                    # for candidates that have survived every other hard gate, so
                    # API usage stays bounded.
                    try:
                        _mb_live_notional = float(get_config_value("LIVE_POSITION_SIZE_USD", 0.0))
                        if _mb_half_size:
                            _mb_live_notional *= 0.5
                        if _mb_live_notional <= 0:
                            _mb_preflight_reason = "live_size_unset"
                            _mb_hard_blocks.append("preflight=live_size_unset")
                        else:
                            from services.live_trading import preflight_live_buy as _mb_pf
                            _mb_pf_res = _mb_pf(mint, _mb_live_notional) or {}
                            _mb_preflight_reason = str(_mb_pf_res.get("reason", "unknown"))
                            if not _mb_pf_res.get("viable"):
                                _mb_hard_blocks.append(
                                    f"preflight={_mb_preflight_reason}")
                                if _mb_oracle_stalled:
                                    _mb_oracle_authority = "STALLED_NO_ROUTE"
                            else:
                                _mb_pf_impact = _mb_pf_res.get("price_impact_pct")
                                if _mb_pf_impact is not None:
                                    try:
                                        _mb_rt_impact = float(_mb_pf_impact) * 2.0
                                    except Exception:
                                        pass
                                if locals().get("_mb_curve_gap_requires_route", False):
                                    # Route truth replaces absent/stale curve
                                    # telemetry, but only at half-size.
                                    _mb_oracle_authority = "JUPITER_ROUTE_AUTHORITY"
                                    _mb_live_score += float(get_config_value(
                                        "LIVE_ROUTE_AUTHORITY_SCORE", 10.0))
                                if _mb_oracle_stalled:
                                    _mb_oracle_authority = "CANDIDATE_OVERRIDE"
                                    # Global stall is still mild negative evidence.
                                    _mb_live_score -= float(
                                        get_config_value("LIVE_ORACLE_STALL_SCORE_PENALTY", 5.0))
                    except Exception as _mb_pf_err:
                        _mb_preflight_reason = f"{type(_mb_pf_err).__name__}"
                        _mb_hard_blocks.append(f"preflight={_mb_preflight_reason}")
                        if _mb_oracle_stalled:
                            _mb_oracle_authority = "STALLED_PREFLIGHT_ERROR"
                # ── END CANDIDATE-SPECIFIC ORACLE AUTHORITY ──────────────────

                # Recent paper losses are context only. Cap the penalty so a strong
                # independent candidate can still clear live. Real daily-loss limits
                # remain enforced later at wallet submission.
                _mb_losses = 0
                try:
                    with get_connection() as _mb_conn:
                        _mb_losses = int(_mb_conn.execute(
                            "SELECT COUNT(*) FROM paper_positions "
                            "WHERE status='CLOSED' "
                            "AND CAST(COALESCE(realized_pnl_usd,0) AS REAL) < "
                            "    -MAX(0.25, CAST(COALESCE(position_size_usd,25) AS REAL)*0.04) "
                            "AND UPPER(COALESCE(exit_reason,'')) NOT LIKE 'GUARDIAN_STALE%' "
                            "AND CAST(closed_at AS REAL)>=?",
                            (time.time() - 7200,),
                        ).fetchone()[0] or 0)
                except Exception:
                    _mb_losses = 0
                _mb_loss_penalty = min(
                    float(get_config_value("LIVE_CLUSTER_MAX_PENALTY", 12.0)),
                    max(0.0, (_mb_losses - 2) * float(get_config_value("LIVE_CLUSTER_PENALTY_PER_LOSS", 2.0))),
                )
                _mb_live_score -= _mb_loss_penalty

                # Rolling regime contributes bounded confidence only. It may
                # never override freshness, route, impact, sellability,
                # authority, wallet reserve or reconciliation safety — those all
                # live in _mb_hard_blocks and the fire path, untouched here.
                try:
                    _mb_regime_state, _mb_regime_reason = _mb_regime_snapshot()
                    if _mb_regime_state == "RUNNER_RICH":
                        _mb_live_score += min(
                            6.0,
                            max(0.0, float(get_config_value(
                                "REGIME_RUNNER_RICH_SCORE_BONUS", 4.0))),
                        )
                except Exception:
                    _mb_regime_state, _mb_regime_reason = "STANDARD", "detector_unavailable"

                _mb_threshold = float(get_config_value(
                    "LIVE_SAFE_SCORE_MIN_WITH_SM" if _mb_sm_bonus > 0 else "LIVE_SAFE_SCORE_MIN_NO_SM",
                    66.0 if _mb_sm_bonus > 0 else 74.0,
                ))
                _mb_live_score = max(0.0, min(100.0, _mb_live_score))
                if _mb_live_score < _mb_threshold:
                    _mb_reasons.append(
                        f"score={_mb_live_score:.1f}<{_mb_threshold:.1f}"
                    )
                _mb_reasons.extend(_mb_hard_blocks)

                # Authoritative audit row. Existing databases are migrated in place.
                try:
                    _mb_verdict = "BLOCKED" if _mb_reasons else "PASS"
                    _mb_reason_text = " | ".join(_mb_reasons) if _mb_reasons else (
                        f"LIVE_SAFE_SCORE={_mb_live_score:.1f}>={_mb_threshold:.1f}"
                    )
                    with get_connection() as _mb_audit:
                        _mb_audit.execute(
                            """CREATE TABLE IF NOT EXISTS mode_b_decision_ledger (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                snapshot_id INTEGER, position_id INTEGER,
                                mint_address TEXT, token_name TEXT, evaluated_at REAL,
                                verdict TEXT, reasons TEXT, adjusted_confidence REAL,
                                confidence_floor REAL, smart_money_tier TEXT,
                                tide_state TEXT, tide_density REAL, oracle_state TEXT,
                                signal_age_sec REAL, cluster_losses_2h INTEGER,
                                live_armed INTEGER DEFAULT 0,
                                live_safe_score REAL, score_threshold REAL,
                                cluster_penalty REAL, curve_sol_reserves REAL,
                                round_trip_impact_pct REAL
                            )"""
                        )
                        _mb_cols = {r[1] for r in _mb_audit.execute(
                            "PRAGMA table_info(mode_b_decision_ledger)"
                        ).fetchall()}
                        for _col, _typ in (
                            ("live_safe_score", "REAL"),
                            ("score_threshold", "REAL"),
                            ("cluster_penalty", "REAL"),
                            ("curve_sol_reserves", "REAL"),
                            ("round_trip_impact_pct", "REAL"),
                            # SIGNOFF_LIVE_LANE_REPAIR_20260715 forensic columns
                            ("price_age_sec", "REAL"),
                            ("oracle_authority", "TEXT"),
                            ("oracle_hot_age_sec", "REAL"),
                            ("oracle_any_age_sec", "REAL"),
                            ("oracle_wpm", "REAL"),
                            ("curve_band", "TEXT"),
                            ("regime_state", "TEXT"),
                            ("regime_reason", "TEXT"),
                            ("half_size", "INTEGER"),
                            ("preflight_reason", "TEXT"),
                        ):
                            if _col not in _mb_cols:
                                _mb_audit.execute(
                                    f"ALTER TABLE mode_b_decision_ledger ADD COLUMN {_col} {_typ}"
                                )
                        try:
                            _mb_t_hot = float(get_config_value("WS_ORACLE_HOT_AGE_SEC", -1.0))
                            _mb_t_any = float(get_config_value("WS_ORACLE_ANY_AGE_SEC", -1.0))
                            _mb_t_wpm = float(get_config_value("WS_ORACLE_WPM", -1.0))
                        except Exception:
                            _mb_t_hot = _mb_t_any = _mb_t_wpm = -1.0
                        _mb_audit.execute(
                            """INSERT INTO mode_b_decision_ledger (
                                snapshot_id,mint_address,token_name,evaluated_at,verdict,reasons,
                                adjusted_confidence,confidence_floor,smart_money_tier,tide_state,
                                tide_density,oracle_state,signal_age_sec,cluster_losses_2h,live_armed,
                                live_safe_score,score_threshold,cluster_penalty,curve_sol_reserves,
                                round_trip_impact_pct,
                                price_age_sec,oracle_authority,oracle_hot_age_sec,
                                oracle_any_age_sec,oracle_wpm,curve_band,regime_state,
                                regime_reason,half_size,preflight_reason
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                      ?,?,?,?,?,?,?,?,?,?)""",
                            (snap_id,mint,token_name,time.time(),_mb_verdict,_mb_reason_text,
                             float(_mb_conf_eval),float(_mb_conf_hard_floor),_mb_tier,
                             _mb_tide,float(_mb_density),_mb_oracle,float(_mb_launch_age),
                             int(_mb_losses),1,float(_mb_live_score),float(_mb_threshold),
                             float(_mb_loss_penalty),float(_mb_curve_sol or 0.0),
                             _mb_rt_impact,
                             float(_mb_price_age),str(_mb_oracle_authority),
                             _mb_t_hot,_mb_t_any,_mb_t_wpm,str(_mb_curve_band),
                             str(_mb_regime_state),str(_mb_regime_reason)[:200],
                             int(bool(_mb_half_size)),
                             (str(_mb_preflight_reason)[:160] if _mb_preflight_reason else None)),
                        )
                        _mb_audit.commit()
                except Exception as _mb_audit_err:
                    log.debug("mode-b decision ledger skipped snap=%s: %s", snap_id, _mb_audit_err)

                if _mb_reasons:
                    log.warning(
                        "LIVE_SAFE_BLOCKED snap=%d %s score=%.1f/%.1f sm=%s "
                        "loss_penalty=%.1f reasons=%s",
                        snap_id, token_name, _mb_live_score, _mb_threshold, _mb_tier,
                        _mb_loss_penalty, " | ".join(_mb_reasons),
                    )
                    try:
                        with get_connection() as _mb_veto:
                            _mb_veto.execute(
                                "UPDATE market_snapshots SET latch_claimed_until=NULL WHERE id=?",
                                (snap_id,),
                            )
                            _mb_veto.commit()
                    except Exception:
                        pass
                else:
                    _mode_b_live_pass = True
                    log.info(
                        "LIVE_SAFE_PASS snap=%d %s score=%.1f/%.1f sm=%s "
                        "curve=%.2fSOL rt=%s firing=$%.2f",
                        snap_id, token_name, _mb_live_score, _mb_threshold, _mb_tier,
                        _mb_curve_sol,
                        (f"{_mb_rt_impact:.1f}%" if _mb_rt_impact is not None else "n/a"),
                        min(
                            float(get_config_value("LIVE_POSITION_SIZE_USD", 0.0)),
                            float(get_config_value("LIVE_MAX_TOTAL_EXPOSURE_USD", 0.0)),
                        ),
                    )
            # ── END MODE B LIVE GATE ───────────────────────────────────────

            # ── PAPER SLIPPAGE SIMULATION ─────────────────────────────────
            # Real pump.fun buys via Jupiter cost 0.5-2.5% slippage + fees.
            # Apply in paper mode only so paper results reflect live reality.
            if True:  # paper/SIM lane always models slippage
                _slip_entry = float(get_config_value("PAPER_SLIPPAGE_ENTRY_PCT", 1.5)) / 100.0
                _fee_entry  = float(get_config_value("PAPER_FEE_PER_TX_USD", 0.10))
                entry_price = entry_price * (1.0 + _slip_entry)   # pay more on entry
                pos_size_usd = pos_size_usd + _fee_entry           # deduct tx fee
                quantity = pos_size_usd / entry_price if entry_price > 0 else quantity

            with get_connection() as conn:
                # SIGNOFF_ACCEPTED_ENTRY_ATOMIC_20260716:
                # core.schema connections use isolation_level=None (autocommit).
                # An explicit transaction prevents a partial SIM row from surviving
                # if any required bookkeeping step fails before the live mirror.
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    """
                    INSERT INTO paper_positions (
                        token_name, mint_address, status,
                        entry_price, quantity, position_size_usd,
                        take_profit_pct, stop_loss_pct,
                        realized_pnl_usd, unrealized_pnl_usd, opened_at,
                        last_price, last_marked_at,
                        entry_price_source, entry_price_ts,
                        confidence, entry_confidence, strategy_version,
                        funding_mode, money_source, execution_source, mode
                    ) VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, ?, ?, ?, ?, ?,
                              'SIM', 'SIM_EQUITY', 'PAPER_ENGINE', 'paper')
                    """,
                    (token_name, mint, entry_price, quantity, pos_size_usd,
                     tp_pct, sl_pct, now, entry_price, now,
                     # Tag tide state in source so FLOOD trades are filterable
                     f"{price_source}|tide={str(get_config_value('MARKET_TIDE_STATE','NORMAL')).upper()}",
                     price_ts, conf, entry_conf,
                     str(get_config_value("ACTIVE_STRATEGY_VERSION", "UNVERSIONED"))),
                )
                position_id = cur.lastrowid

                # COPYTRADE ADVISORY RESTORE — SIM annotation only.
                #
                # This code executes immediately after the canonical SIM position
                # is inserted. A REAL position does not exist yet; it is created
                # later only after Mode B passes and the on-chain buy confirms.
                # The former undefined live-capital guard aborted the entry path
                # before TRUE_DUAL_LIVE_MIRROR and prevented approved candidates
                # from reaching execute_live_buy().
                #
                # Copytrade annotation remains observational: it cannot create,
                # approve, size or bypass an entry, and failure never blocks the
                # paper entry or the independent live mirror.
                try:
                    try:
                        from services.copytrade_entry_influence import mark_copytrade_influence as _ct_mark
                    except Exception:
                        from copytrade_entry_influence import mark_copytrade_influence as _ct_mark  # type: ignore
                    _ct_mark(conn, position_id, mint)
                except Exception as _ct_tag_err:
                    log.debug("copytrade advisory tag skipped pos=%s: %s", position_id, _ct_tag_err)
                mints_opened_this_batch.add(mint)
                try:
                    cur.execute(
                        "UPDATE mode_b_decision_ledger SET position_id=? "
                        "WHERE id=(SELECT id FROM mode_b_decision_ledger "
                        "WHERE snapshot_id=? AND mint_address=? ORDER BY id DESC LIMIT 1)",
                        (position_id, snap_id, mint),
                    )
                except Exception:
                    pass

                # ── LIVE SHADOW STAMP (2026-07-10) ─────────────────────────
                # Annotation only. Records the curve depth this trade opened
                # into, the constant-product impact a live order would pay,
                # and whether the live lane WOULD have fired. Never blocks.
                try:
                    _sol_usd = float(get_config_value("SOL_PRICE_USD", 150.0))
                    _snap = cur.execute(
                        "SELECT curve_sol_reserves, curve_progress_pct, price_updated_at "
                        "FROM market_snapshots WHERE mint_address=? AND curve_sol_reserves>0 "
                        "AND price_updated_at<=? ORDER BY price_updated_at DESC LIMIT 1",
                        (mint, now),
                    ).fetchone()
                    _cr = float(_snap["curve_sol_reserves"]) if _snap else 0.0
                    _cp = (_snap["curve_progress_pct"] if _snap else None)
                    _snap_ts = float(_snap["price_updated_at"]) if _snap else None

                    _sz = float(pos_size_usd or 0.0)
                    if _cr > 0 and _sz > 0:
                        _res_usd = _cr * _sol_usd
                        _imp = _sz / (_res_usd + _sz) * 100.0
                        _rt = _imp * 2.0
                    else:
                        _imp = None; _rt = None

                    _live_curve_floor = float(get_config_value("LIVE_SHADOW_CURVE_MIN_SOL", 2.0))
                    _max_rt = float(get_config_value("MAX_ROUND_TRIP_SLIPPAGE_PCT", 8.0))
                    _max_age = float(get_config_value("LIVE_MAX_PRICE_AGE_SEC", 90.0))
                    _conf_floor = float(get_config_value("LIVE_CONFIDENCE_FLOOR", 0.0))
                    _age = (now - float(price_ts)) if price_ts else None

                    if _cr <= 0:
                        _verdict, _live = "PAPER_ONLY_MISSING_CURVE", 0
                    elif _cr < _live_curve_floor:
                        _verdict, _live = ("PAPER_ONLY_THIN_CURVE_%.2fSOL" % _cr), 0
                    elif _rt is not None and _rt > _max_rt:
                        _verdict, _live = ("BLOCKED_THIN_CURVE_%.1fpct_rt" % _rt), 0
                    elif _age is not None and _age > _max_age:
                        _verdict, _live = ("BLOCKED_STALE_%.0fs" % _age), 0
                    elif _conf_floor > 0 and float(entry_conf or 0) < _conf_floor:
                        _verdict, _live = "PAPER_ONLY_LOW_CONF", 0
                    else:
                        _verdict, _live = "LIVE_CANDIDATE", 1

                    cur.execute(
                        "UPDATE paper_positions SET entry_curve_sol_reserves=?, "
                        "entry_curve_progress_pct=?, entry_market_snapshot_ts=?, "
                        "entry_liquidity_source='snapshot_at_open', "
                        "est_entry_impact_pct=?, est_exit_impact_pct=?, "
                        "est_round_trip_impact_pct=?, would_be_live_eligible=?, "
                        "curve_gate_reason=? WHERE id=?",
                        (_cr or None, _cp, _snap_ts, _imp, _imp, _rt, _live, _verdict, position_id),
                    )
                    # Append-only audit ledger: independent from paper admission and live execution.
                    try:
                        _modeled_gas = float(get_config_value("LIVE_MODELED_GAS_USD", 0.02))
                        cur.execute(
                            "INSERT INTO live_shadow_ledger (position_id,mint_address,token_name,scored_at,verdict,reason,would_be_live_eligible,curve_sol_reserves,curve_progress_pct,price_age_sec,entry_impact_pct,exit_impact_pct,round_trip_impact_pct,modeled_gas_usd,position_size_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (position_id,mint,token_name,now,_verdict,_verdict,_live,_cr or None,_cp,_age,_imp,_imp,_rt,_modeled_gas,_sz),
                        )
                    except Exception as _ledger_err:
                        log.debug("live shadow ledger skipped pos=%s: %s", position_id, _ledger_err)
                    log.info("LIVE_SHADOW pos=%d verdict=%s curve=%.2fSOL rt_impact=%s",
                             position_id, _verdict, _cr,
                             ("%.1f%%" % _rt) if _rt is not None else "n/a")
                except Exception as _ls_err:
                    try:
                        log.debug("live shadow stamp skipped pos=%s: %s", position_id, _ls_err)
                    except Exception:
                        pass
                # ── /LIVE SHADOW STAMP ─────────────────────────────────────
                log.info("OPENED pos=%d token=%s conf=%s",
                         position_id, token_name,
                         (f"{entry_conf:.3f}" if entry_conf is not None else "NULL"))

                # Update momentum audit row - mark entered=1 with position_id
                if _mg_audit_id:
                    try:
                        conn.execute(
                            "UPDATE momentum_gate_audit SET entered=1, position_id=? WHERE id=?",
                            (position_id, _mg_audit_id),
                        )
                    except Exception:
                        pass

                conn.execute(
                    """
                    INSERT INTO paper_executions (
                        position_id, token_name, mint_address,
                        side, price, quantity, notional_usd, value_usd, reason, timestamp
                    ) VALUES (?, ?, ?, 'BUY', ?, ?, ?, ?, 'ENTRY', ?)
                    """,
                    (position_id, token_name, mint,
                     entry_price, quantity, pos_size_usd, pos_size_usd, now),
                )

                # TRUE DUAL: SIM lane always uses simulated paper accounting.
                if True:
                    conn.execute(
                        "UPDATE system_state SET wallet_balance = wallet_balance - ? WHERE id=1",
                        (pos_size_usd,),
                    )

                # Log entry wallet debit
                try:
                    conn.execute(
                        """INSERT INTO wallet_write_log
                            (position_id, delta_usd, new_balance, source, token_name, pnl_usd, pnl_pct, timestamp)
                            VALUES (?, ?, (SELECT wallet_balance FROM system_state WHERE id=1), 'ENTRY', ?, 0.0, 0.0, ?)""",
                        (position_id, -pos_size_usd, token_name, now),
                    )
                except Exception:
                    pass  # never block an entry on log failure

                conn.execute(
                    "UPDATE market_snapshots SET execution_ready=2, candidate_state='executed', latched=0 WHERE id=?",
                    (snap_id,),
                )
                # Emit TRADE_OPENED event into lifecycle spine
                if _TLE_AVAILABLE:
                    try:
                        _tle_opened(conn,
                            position_id=position_id, mint=mint,
                            entry_price=entry_price, pos_size_usd=pos_size_usd,
                            source=price_source)
                    except Exception:
                        pass
                conn.commit()

            # PATTERN_PERSISTENCE_LIVE_ARMING_20260715:
            # Reconstruct causal state from closes that existed before this candidate
            # opened. Pattern permission is additive and can never bypass Mode B or
            # any wallet/quote/freshness/sell/reconciliation safety control.
            _pattern_required = _cfg_enabled("PATTERN_LIVE_ARMING_REQUIRED", "1")
            _pattern_perm = None
            _pattern_live_pass = not _pattern_required
            _pattern_size_multiplier = 1.0
            if _pattern_required:
                if _PATTERN_LIVE_ARMING_AVAILABLE and _pattern_live_permission is not None:
                    try:
                        with get_connection() as _pat_conn:
                            _pattern_perm = _pattern_live_permission(
                                _pat_conn, candidate_entry_ts=now,
                                window_sec=float(get_config_value("PATTERN_LIVE_WINDOW_SEC", 900.0)),
                            )
                        # Hard invariant: DORMANT/WATCHING telemetry can never
                        # authorise funded execution, even if a stale/legacy
                        # PatternPermission implementation incorrectly exposes
                        # armed=True. Two independent realised successes are the
                        # minimum authority boundary; ARMED/CONFIRMED are the only
                        # capital-permission states.
                        _pattern_state_norm = str(_pattern_perm.state or "").upper()
                        _pattern_confirmations = int(_pattern_perm.confirmations or 0)
                        _pattern_size_multiplier = float(_pattern_perm.size_multiplier or 0.0)
                        _pattern_live_pass = bool(
                            _pattern_perm.armed
                            and _pattern_state_norm in {"ARMED", "CONFIRMED"}
                            and _pattern_confirmations >= 2
                            and _pattern_size_multiplier > 0.0
                        )
                        if not _pattern_live_pass:
                            _pattern_size_multiplier = 0.0
                        log.info("[PATTERN_GATE] state=%s confirmations=%d multiplier=%.2f pass=%s reason=%s",
                                 _pattern_perm.state, _pattern_perm.confirmations,
                                 _pattern_size_multiplier, _pattern_live_pass, _pattern_perm.reason)
                    except Exception as _pat_err:
                        _pattern_live_pass = False
                        log.error("[PATTERN_GATE_ERROR] %s", _pat_err)
                else:
                    _pattern_live_pass = False
                    log.error("[PATTERN_GATE_UNAVAILABLE] required module unavailable; live mirror blocked")

            try:
                with get_connection() as _pat_audit:
                    _cols = {r[1] for r in _pat_audit.execute("PRAGMA table_info(mode_b_decision_ledger)").fetchall()}
                    for _c,_t in (("pattern_state","TEXT"),("pattern_confirmations","INTEGER"),
                                  ("pattern_size_multiplier","REAL"),("pattern_reason","TEXT")):
                        if _c not in _cols:
                            _pat_audit.execute(f"ALTER TABLE mode_b_decision_ledger ADD COLUMN {_c} {_t}")
                    _pat_audit.execute(
                        "UPDATE mode_b_decision_ledger SET pattern_state=?,pattern_confirmations=?,"
                        "pattern_size_multiplier=?,pattern_reason=? WHERE position_id=?",
                        ((_pattern_perm.state if _pattern_perm else ("BYPASSED" if not _pattern_required else "UNAVAILABLE")),
                         int(_pattern_perm.confirmations if _pattern_perm else 0),
                         float(_pattern_size_multiplier),
                         (_pattern_perm.reason if _pattern_perm else ("not_required" if not _pattern_required else "module_unavailable")),
                         position_id),
                    )
                    _pat_audit.commit()
            except Exception as _pat_audit_err:
                log.debug("pattern audit stamp skipped pos=%s: %s", position_id, _pat_audit_err)

            # SIGNOFF_LIVE_LEDGER_20260716 — EXECUTOR DECISION CONTRACT.
            # The executor publishes its OWN verdict here, at the exact point it
            # decides. ui/live_gate_constellation.py reads this record; it no
            # longer recomputes FINAL FIRE from raw tables. One decision, one
            # verdict, one truth. Never raises — a publish failure makes the UI
            # show UNAVAILABLE, which is correct, rather than an invented state.
            try:
                _ldc_lane_armed = bool(_LIVE_TRADING_AVAILABLE and _live_lane_armed())
                # SIZING_GATE_V2_20260721: resolve the operator notional BEFORE the
                # verdict is derived, so FIRE_PATH_OPEN can never be published
                # with zero or missing live size / exposure cap (V2 review
                # blocker 2). Mirrors the fail-closed check at the live-mirror
                # boundary; parallel caps (pattern multiplier, curve half-size)
                # follow the mirror's non-multiplicative doctrine exactly.
                _ldc_req_size = float(get_config_value("LIVE_POSITION_SIZE_USD", 0.0) or 0.0)
                _ldc_exp_cap = float(get_config_value("LIVE_MAX_TOTAL_EXPOSURE_USD", 0.0) or 0.0)
                _ldc_sizing_ok = _ldc_req_size > 0.0 and _ldc_exp_cap > 0.0
                _ldc_mult = 1.0
                if _pattern_required:
                    _ldc_mult = min(_ldc_mult, max(0.0, min(1.0, float(_pattern_size_multiplier))))
                if _mb_half_size:
                    _ldc_mult = min(_ldc_mult, 0.5)
                _ldc_would_fire = (min(_ldc_req_size, _ldc_exp_cap) * _ldc_mult) if _ldc_sizing_ok else 0.0
                _ldc_gates = [
                    {"name": "LIVE_LANE_ARMED", "state": "PASS" if _ldc_lane_armed else "BLOCK",
                     "current": ("armed" if _ldc_lane_armed else
                                 ("live_trading module unavailable" if not _LIVE_TRADING_AVAILABLE
                                  else "disarmed by config")),
                     "contract": "DUAL_MODE_ENABLED+DUAL_MODE_ARMED+LIVE_TRADING_ENABLED+LIVE_MODE_B_ENABLED+LIVE_ARMED"},
                    {"name": "MODE_B", "state": "PASS" if _mode_b_live_pass else "BLOCK",
                     "current": ("passed" if _mode_b_live_pass else "mode B refused this candidate")
                                + (" (half size)" if _mb_half_size else ""),
                     "contract": "services/execution_engine.py mode B decision"},
                    {"name": "PATTERN", "state": "PASS" if _pattern_live_pass else "BLOCK",
                     "current": (f"{_pattern_perm.state} · {_pattern_perm.confirmations} confirms · "
                                 f"{_pattern_size_multiplier:.2f}x" if _pattern_perm
                                 else ("not required" if not _pattern_required else "unavailable")),
                     "contract": "capital authority: 2 independent SIM closes arm; 0.5x until 3 verified positive-net live canaries earn 1.0x"},
                    {"name": "SIZING", "state": ("PASS" if _ldc_sizing_ok else "BLOCK"),
                     "current": (f"would fire ${_ldc_would_fire:.2f} "
                                 f"(size=${_ldc_req_size:.2f} cap=${_ldc_exp_cap:.2f} mult={_ldc_mult:.2f}x)"
                                 if _ldc_sizing_ok else
                                 f"live size/exposure cap unresolved "
                                 f"(size={_ldc_req_size} cap={_ldc_exp_cap}); rerun launcher interview"),
                     "contract": "LIVE_POSITION_SIZE_USD>0 and LIVE_MAX_TOTAL_EXPOSURE_USD>0, "
                                 "stamped by the launcher interview"},
                ]
                _ldc_verdict, _ldc_blocker = _derive_live_verdict(
                    lane_armed=_ldc_lane_armed,
                    hard_gates=_ldc_gates,
                    flow_ready=True,   # a candidate is in hand at this point
                )
                _publish_decision_contract(
                    verdict=_ldc_verdict,
                    gates=_ldc_gates,
                    blocker=_ldc_blocker,
                    next_event=("on-chain buy submission" if _ldc_verdict == "FIRE_PATH_OPEN"
                                else ("await the next independent realised confirmation"
                                      if (not _pattern_live_pass and _mode_b_live_pass)
                                      else "resolve the failing gate above")),
                    lane_armed=_ldc_lane_armed,
                    pattern_state=(_pattern_perm.state if _pattern_perm else
                                   ("BYPASSED" if not _pattern_required else "UNAVAILABLE")),
                    pattern_armed=bool(_pattern_live_pass),
                    pattern_multiplier=float(_pattern_size_multiplier),
                    pattern_reason=(_pattern_perm.reason if _pattern_perm else None),
                    size_multiplier=_ldc_mult,
                    would_fire_usd=_ldc_would_fire,
                    candidate_mint=mint,
                    position_id=position_id,
                    authored_by="execution_engine.scan_for_entries",
                )
            except Exception as _ldc_err:
                log.debug("decision contract publish skipped pos=%s: %s",
                          position_id, _ldc_err)

            # TRUE_DUAL_LIVE_MIRROR_20260713:
            # Paper trade is already committed. A separate REAL row is created only
            # after Mode B and the causal pattern gate pass and the on-chain buy confirms.
            if _LIVE_TRADING_AVAILABLE and _live_lane_armed() and _mode_b_live_pass and _pattern_live_pass:
                try:
                    # Operator-owned live amount. Launch_Sentinuity.bat stamps this
                    # from the startup interview; no dollar amount is hard-wired here.
                    _requested_live_size = float(get_config_value("LIVE_POSITION_SIZE_USD", 0.0))
                    _exposure_cap = float(get_config_value("LIVE_MAX_TOTAL_EXPOSURE_USD", 0.0))
                    if _requested_live_size <= 0 or _exposure_cap <= 0:
                        raise RuntimeError(
                            "live size/exposure cap missing or non-positive; rerun launcher interview"
                        )
                    _base_live_size = min(_requested_live_size, _exposure_cap)
                    _effective_live_multiplier = 1.0
                    if _pattern_required:
                        _effective_live_multiplier = min(
                            _effective_live_multiplier,
                            max(0.0, min(1.0, _pattern_size_multiplier)),
                        )
                    # A curve half-size restriction and a pattern half-size restriction
                    # are parallel caps, not multiplicative penalties. Half + half stays
                    # half; it must not silently become quarter size.
                    if _mb_half_size:
                        _effective_live_multiplier = min(_effective_live_multiplier, 0.5)
                    _live_size = _base_live_size * _effective_live_multiplier
                    log.info("[LIVE_SIZE_POLICY] pattern=%s curve_half=%s multiplier=%.2f firing=$%.2f",
                             (_pattern_perm.state if _pattern_perm else ("BYPASSED" if not _pattern_required else "UNKNOWN")),
                             bool(_mb_half_size), _effective_live_multiplier, _live_size)
                    _live_max = int(get_config_value("LIVE_MAX_OPEN_POSITIONS", 1))
                    with get_connection() as _lc:
                        _real_open = int(_lc.execute(
                            "SELECT COUNT(*) FROM paper_positions WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' AND (status='OPEN' OR COALESCE(live_state,'') IN ('BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED','OPEN_REAL','SELL_TRIGGERED','SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED'))"
                        ).fetchone()[0] or 0)
                        _real_exposure = float(_lc.execute(
                            "SELECT COALESCE(SUM(position_size_usd),0) FROM paper_positions WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' AND (status='OPEN' OR COALESCE(live_state,'') IN ('BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED','OPEN_REAL','SELL_TRIGGERED','SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED'))"
                        ).fetchone()[0] or 0.0)
                    _daily_limit = float(
                        get_config_value("LIVE_DAILY_LOSS_LIMIT_USD", _exposure_cap)
                    )
                    if _daily_limit <= 0:
                        _daily_limit = _exposure_cap
                    with get_connection() as _risk_conn:
                        _day_loss = abs(float(_risk_conn.execute(
                            "SELECT COALESCE(SUM(CASE WHEN realized_pnl_usd<0 THEN realized_pnl_usd ELSE 0 END),0) "
                            "FROM paper_positions WHERE status='CLOSED' "
                            "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' AND closed_at>=?",
                            (time.time()-86400,),
                        ).fetchone()[0] or 0.0))
                        _last_real = _risk_conn.execute(
                            "SELECT realized_pnl_usd FROM paper_positions WHERE status='CLOSED' "
                            "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' ORDER BY closed_at DESC LIMIT 1"
                        ).fetchone()
                        _last_real_loss = bool(_last_real and float(_last_real[0] or 0.0) < 0)
                    if _day_loss >= _daily_limit:
                        log.critical("[LIVE_RISK_HALT] day_loss=$%.2f limit=$%.2f; no new live buy",
                                     _day_loss, _daily_limit)
                    elif _real_open >= _live_max or _real_exposure + _live_size > _exposure_cap + 1e-9:
                        log.warning("[LIVE_MIRROR_BLOCKED] open=%d/%d exposure=$%.2f cap=$%.2f",
                                    _real_open, _live_max, _real_exposure, _exposure_cap)
                    else:
                        # Use the candidate's authoritative price timestamp, not the
                        # newly-created SIM row timestamp. Requiring a tick strictly after
                        # `now` made a fresh qualified candidate look like NO_DATA (age=9999)
                        # during the few milliseconds before the oracle wrote its next tick.
                        # `price_ts` is the timestamp of the exact price that qualified this
                        # candidate; the guard still enforces its own freshness threshold.
                        _coverage_reference_ts = float(price_ts or now)
                        _coverage_ok, _coverage_reason = _live_oracle_coverage_guard(
                            mint, entry_price, _coverage_reference_ts
                        )
                        if not _coverage_ok:
                            log.critical(
                                "[LIVE_ORACLE_COVERAGE_BLOCK] SIM pos=%d mint=%s reason=%s; "
                                "paper remains OPEN, no funded exposure added",
                                position_id, mint[:16], _coverage_reason,
                            )
                            try:
                                with get_connection() as _cov_audit:
                                    _cov_audit.execute(
                                        "UPDATE mode_b_decision_ledger SET pattern_reason="
                                        "COALESCE(pattern_reason,'') || ? WHERE position_id=?",
                                        (f"|live_oracle_coverage_block:{_coverage_reason}", position_id),
                                    )
                                    _cov_audit.commit()
                            except Exception:
                                pass
                            _lr = {"success": False, "error": _coverage_reason}
                        else:
                            log.info(
                                "[LIVE_ORACLE_COVERAGE_PASS] SIM pos=%d mint=%s %s",
                                position_id, mint[:16], _coverage_reason,
                            )
                            _lr = _live_buy(mint, _live_size, entry_price, position_id)
                        if _lr.get("success"):
                            _live_qty = float(_lr["actual_qty"])
                            _live_entry_price = float(_lr["actual_price"])
                            _actual_cost_usd = float(_lr.get("actual_cost_usd") or _live_size)
                            _sig = str(_lr.get("tx_sig") or "")
                            _chain_opened_at = float(_lr.get("chain_confirmed_at") or time.time())
                            _reconciled_at = float(_lr.get("reconciled_at") or time.time())
                            _fill_json = json.dumps(_lr.get("fill_meta") or {}, sort_keys=True, separators=(",", ":"))
                            with get_connection() as _real_conn:
                                _real_cur = _real_conn.execute(
                                    """
                                    INSERT INTO paper_positions (
                                        token_name,mint_address,status,entry_price,quantity,position_size_usd,
                                        take_profit_pct,stop_loss_pct,realized_pnl_usd,unrealized_pnl_usd,
                                        opened_at,last_price,last_marked_at,entry_price_source,entry_price_ts,
                                        confidence,entry_confidence,strategy_version,funding_mode,money_source,
                                        execution_source,mode,source_note,live_state,buy_tx_sig,
                                        chain_confirmed_at,reconciled_at,actual_entry_price,actual_quantity,
                                        entry_sol_spent,entry_fee_sol,fill_meta_json,sim_parent_position_id,
                                        highest_price_seen
                                    ) VALUES (?,?, 'OPEN',?,?,?,?,?,0.0,0.0,?,?,?,?,?,?,?, ?,
                                              'REAL','REAL_WALLET','REAL_TX','live',?,?,?,?,?,?,?,?,?,?,?,?)
                                    """,
                                    (token_name,mint,_live_entry_price,_live_qty,_actual_cost_usd,tp_pct,sl_pct,
                                     _chain_opened_at,_live_entry_price,_chain_opened_at,f"live_tx:{_sig[:20]}",
                                     _chain_opened_at,conf,entry_conf,
                                     str(get_config_value("ACTIVE_STRATEGY_VERSION", "UNVERSIONED")),
                                     f"dual_mirror_of_sim_position={position_id}","OPEN_REAL",_sig,
                                     _chain_opened_at,_reconciled_at,_live_entry_price,_live_qty,
                                     float(_lr.get("net_spent_sol") or 0.0),float(_lr.get("fee_sol") or 0.0),
                                     _fill_json,position_id,_live_entry_price),
                                )
                                _real_id = int(_real_cur.lastrowid)
                                _real_conn.execute(
                                    "INSERT INTO paper_executions (position_id,token_name,mint_address,side,price,quantity,notional_usd,value_usd,reason,timestamp) "
                                    "VALUES (?,?,?,'BUY',?,?,?,?,?,?)",
                                    (_real_id,token_name,mint,_live_entry_price,_live_qty,_actual_cost_usd,_actual_cost_usd,
                                     f"LIVE_RECONCILED_ENTRY:{_sig[:20]}",_chain_opened_at),
                                )
                                _real_conn.commit()
                            log.info("[LIVE_BUY] REAL pos=%d mirror_of=%d sig=%s actual_size=$%.2f",
                                     _real_id, position_id, _sig[:20], _actual_cost_usd)
                        elif _lr.get("confirmed"):
                            # Chain succeeded but fill metadata is unresolved. Persist a
                            # non-trading state so restart/reconciliation cannot lose funds.
                            _sig = str(_lr.get("tx_sig") or "")
                            with get_connection() as _real_conn:
                                _real_conn.execute(
                                    """INSERT INTO paper_positions
                                    (token_name,mint_address,status,entry_price,quantity,position_size_usd,
                                     take_profit_pct,stop_loss_pct,opened_at,last_price,last_marked_at,
                                     entry_price_source,entry_price_ts,confidence,entry_confidence,
                                     strategy_version,funding_mode,money_source,execution_source,mode,
                                     source_note,live_state,buy_tx_sig,fill_meta_json,sim_parent_position_id)
                                    VALUES (?,?,'BUY_CONFIRMED_UNRESOLVED',0,0,?, ?,?,0,0,0,?,?,?, ?,?,
                                            'REAL','REAL_WALLET','REAL_TX','live',?,?,?,?,?)""",
                                    (token_name,mint,_live_size,tp_pct,sl_pct,f"live_tx:{_sig[:20]}",0.0,
                                     conf,entry_conf,str(get_config_value("ACTIVE_STRATEGY_VERSION", "UNVERSIONED")),
                                     f"manual_reconciliation_required;mirror={position_id}",
                                     "BUY_CONFIRMED_UNRESOLVED",_sig,
                                     json.dumps(_lr.get("fill_meta") or {}, sort_keys=True),position_id),
                                )
                                _real_conn.commit()
                            log.critical("[LIVE_BUY_UNRESOLVED] sig=%s mint=%s; live lane must remain blocked",
                                         _sig[:20], mint[:16])
                        else:
                            log.warning("[LIVE_BUY_FAIL] SIM pos=%d error=%s; paper remains OPEN, no REAL row",
                                        position_id, _lr.get("error"))
                except Exception as _le:
                    log.error("[LIVE_BUY_ERROR] SIM pos=%d: %s; paper remains OPEN, no REAL row",
                              position_id, _le)
                    # SIZING_GATE_V2_20260721: a candidate must never disappear after
                    # FIRE_PATH_OPEN. Republish the executor contract as
                    # BLOCKED with the exact mirror error so the UI shows a
                    # visible, actionable reason instead of a stale verdict.
                    try:
                        _publish_decision_contract(
                            verdict="BLOCKED",
                            gates=[{"name": "LIVE_MIRROR", "state": "BLOCK",
                                    "current": str(_le)[:200],
                                    "contract": "resolved notional -> preflight -> "
                                                "execute_live_buy must complete or report"}],
                            blocker=f"LIVE_MIRROR: {str(_le)[:160]}",
                            next_event="resolve the live-mirror error above",
                            lane_armed=True,
                            candidate_mint=mint,
                            position_id=position_id,
                            authored_by="execution_engine.live_mirror_exception",
                        )
                    except Exception:
                        pass

            # IMMEDIATE ORACLE SUBSCRIPTION - fired AFTER commit so the position
            # row exists in paper_positions when the oracle's _get_open_mints()
            # poll next runs. Previously this fired before commit so the 1s fallback
            # poll could miss the mint on its first cycle if _force_subscribe()
            # failed silently (WS not yet connected). Moving post-commit closes
            # the race: position is in DB before oracle is asked to track it.
            try:
                _oracle_notify_mint(mint)
            except Exception:
                pass  # never block - oracle will pick up via 1s poll if this fails

            # PRICE BOOTSTRAP: immediately attempt a router price read so the meter
            # has a live value within ~1s of open rather than waiting for the oracle's
            # next write cycle (up to 2s for open mints after Patch A).
            # If router returns nothing, live_exec_* stays NULL - do NOT fake with
            # entry_price, that would show 0.00% which looks identical to no data.
            try:
                # Pass opened_at (= now at this point) so Intel DB filter ts_ms >= opened_at*1000
                # accepts ticks from this moment forward. Using `now` is correct here
                # since the position was just committed - opened_at == now.
                _boot_pr = _router_exec_price(mint, entry_price, now) if _PRICE_ROUTER_AVAILABLE else None
                if _boot_pr and _boot_pr.get("price", 0) > 0 and _boot_pr.get("can_execute_exit"):
                    _boot_pnl = pos_size_usd * (
                        (_boot_pr["price"] - entry_price) / entry_price
                    ) if entry_price > 0 else 0.0
                    update_position_mark(
                        position_id, _boot_pr["price"], _boot_pnl, now,
                        source=_boot_pr.get("source", "bootstrap"),
                        router_result=_boot_pr,
                    )
                    log.info(
                        "PRICE_BOOTSTRAP pos=%d %s price=%.10f pct=%.4f%%",
                        position_id, token_name, _boot_pr["price"],
                        (_boot_pr["price"] - entry_price) / entry_price * 100,
                    )
            except Exception:
                pass  # never block entry on bootstrap failure

            log.info(
                "ENTRY OPENED pos=%d %s @ %.8f size=$%.2f TP=%.0f%% SL=%.0f%%",
                position_id, token_name, entry_price, pos_size_usd, tp_pct, sl_pct,
            )
            # PHASE 1 ENTRY_AUDIT - structured log for price truth validation.
            # Confirm in logs after deployment:
            #   source=qualify  → qualify-time price used (no fresher price found)
            #   source=upgraded → fresher oracle/resolver price used at entry
            # Identical entry prices across tokens = source=qualify + genuinely
            # same bonding-curve stage for all qualifying signals.
            log.info(
                "ENTRY_AUDIT mint=%s qualify=%.8f final=%.8f source=%s "
                "price_age=%.2fs signal_age=%.2fs",
                mint[:16],
                _qualify_price,
                final_price,
                price_source,
                price_age,
                signal_age,
            )
            _log_cognition(token_name,
                f"Deployed ${pos_size_usd:.2f} into {token_name} at ${entry_price:.8f}. "
                f"Position active. TP: {tp_pct:.0f}% | SL: {sl_pct:.0f}%.")
            update_heartbeat(SERVICE_NAME, "ALIVE",
                f"Entry opened pos={position_id} {token_name} @ {entry_price:.8f}",
                work_processed=1, last_success_at=now)

            opened += 1
            if degraded_mode:
                break

        except Exception as e:
            # Full structured log - visible in execution_engine.log, not just stdout.
            # snap_id and mint are extracted early in the loop so always defined here.
            log.exception(
                "EXECUTION_FAILURE snap=%s mint=%s: %s",
                snap_id, str(mint or "")[:12], e
            )
            # Release claim so signal isn't blocked for 30s on next cycle.
            # Uses snap_id (int) not row["id"] to avoid a second NameError if row is corrupt.
            try:
                with get_connection() as _rel_conn:
                    _rel_conn.execute(
                        "UPDATE market_snapshots SET latch_claimed_until=NULL WHERE id=?",
                        (snap_id,)
                    )
                    _rel_conn.commit()
            except Exception:
                pass
            continue

    return opened


# -----------------------------------------------------------------------------
# LIVE EXIT EVALUATION
# -----------------------------------------------------------------------------

def check_open_positions() -> None:
    """Evaluate all open positions for exit conditions."""
    for position in get_open_positions():
        try:
            # PATCH C - post-entry coverage audit: warn if oracle has no ticks
            # for a position older than 5s. Proof panel for oracle coverage gaps.
            _pos_age = time.time() - float(position.get("opened_at") or 0)
            if _pos_age > 5.0:
                try:
                    _mint_chk = str(position.get("mint_address") or "")
                    _opened_ms = float(position.get("opened_at") or 0) * 1000
                    _iconn = get_intel_connection()
                    _tick_count = _iconn.execute(
                        "SELECT COUNT(*) FROM mtm_ticks WHERE mint_address=? AND ts_ms>=?",
                        (_mint_chk, _opened_ms),
                    ).fetchone()[0]
                    _iconn.close()
                    if _tick_count == 0:
                        log.warning(
                            "[NO_POST_ENTRY_TICKS] pos=%d %s age=%.0fs "
                            "- oracle has ZERO ticks since open. Meter will be blind.",
                            position["id"], _mint_chk[:16], _pos_age,
                        )
                        if _TLE_AVAILABLE:
                            try:
                                with get_connection() as _tle_conn:
                                    emit_coverage_alert = _tle_coverage_alert
                                    emit_coverage_alert(_tle_conn,
                                        position_id=position["id"],
                                        mint=_mint_chk, age_sec=_pos_age)
                                    _tle_conn.commit()
                            except Exception:
                                pass
                except Exception:
                    pass  # never block exit eval on audit failure
            # POSITION LIFECYCLE INTELLIGENCE (PLI)
            try:
                from services.position_lifecycle_intelligence import get_lifecycle_action as _pli_action
                _pli_conf      = float(position.get("confidence") or 0)
                _pli_peak_conf = float(position.get("peak_confidence", _pli_conf) or _pli_conf)
                _pli_substrate = str(get_config_value("SUBSTRATE_MACRO_REGIME", "NEUTRAL"))
                _pli_vol       = float(position.get("volume_acceleration") or 1.0)
                _pli_result    = _pli_action(
                    position       = position,
                    current_alpha  = _pli_conf,
                    peak_alpha     = _pli_peak_conf,
                    substrate_state= _pli_substrate,
                    volatility     = _pli_vol,
                )
                _pli_act = _pli_result.get("action", "HOLD")
                if _pli_act == "EXIT":
                    _pli_price = float(position.get("last_price") or position.get("entry_price") or 0)
                    if _pli_price > 0:
                        close_position_canonical(
                            int(position["id"]), _pli_price, "LIFECYCLE_EXIT",
                            closure_mode="normal",
                        )
                        continue
                elif _pli_act in ("SCALE_IN", "PARTIAL_PROFIT"):
                    log.info("PLI %s pos=%d reason=%s",
                             _pli_act, position["id"], _pli_result.get("reason", ""))
                if _pli_result.get("new_trailing_stop"):
                    try:
                        with get_connection() as _pli_conn:
                            _pli_conn.execute(
                                "UPDATE paper_positions SET trail_stop_price=? WHERE id=? AND status='OPEN'",
                                (_pli_result["new_trailing_stop"], position["id"]),
                            )
                            _pli_conn.commit()
                    except Exception:
                        pass
            except Exception as _pli_err:
                log.debug("PLI skipped pos=%s: %s", position.get("id"), _pli_err)

            evaluate_exit_for_position(position)
        except Exception as e:
            log.warning("Exit eval failed pos=%s: %s", position.get("id"), e)

def get_best_live_exec_price(
    mint: str,
    entry_price: float,
    opened_at: float,
) -> tuple[float, str, float]:
    """
    Returns (price, source, age_sec) using the FRESHEST valid post-entry price.
    Rule: freshest timestamp wins - NOT most extreme move.
    Rejects: price<=0, price>1000x entry, rows older than opened_at.
    """
    now      = time.time()
    best_price  = 0.0
    best_ts     = 0.0
    best_source = "none"
    max_sane    = entry_price * 1000.0 if entry_price > 0 else 1.0

    # ── a) MTM rows (candidate_state='mtm') after opened_at ──────────────────
    try:
        _ip = _get_price_intel_first(mint)
        if _ip and 0 < _ip < max_sane:
            # Intel ticks don't have a per-row ts we can compare to opened_at
            # Use now as ts - they're always recent by design
            if now > best_ts:
                best_price  = _ip
                best_ts     = now
                best_source = "intel-mtm"
    except Exception:
        pass

    try:
        with get_connection() as _c:
            _row = _c.execute(
                """SELECT observed_price, price_updated_at
                   FROM market_snapshots
                   WHERE mint_address=?
                     AND candidate_state='mtm'
                     AND observed_price > 0
                     AND price_updated_at > ?
                   ORDER BY price_updated_at DESC LIMIT 1""",
                (mint, opened_at),
            ).fetchone()
        if _row:
            _p  = float(_row["observed_price"])
            _ts = float(_row["price_updated_at"] or 0)
            if 0 < _p < max_sane and _ts > best_ts:
                best_price  = _p
                best_ts     = _ts
                best_source = "mtm"
    except Exception:
        pass

    # ── b) Unscoped row after opened_at ───────────────────────────────────────
    try:
        with get_connection() as _c:
            _row = _c.execute(
                """SELECT observed_price, price_updated_at
                   FROM market_snapshots
                   WHERE mint_address=?
                     AND observed_price > 0
                     AND price_updated_at > ?
                   ORDER BY price_updated_at DESC LIMIT 1""",
                (mint, opened_at),
            ).fetchone()
        if _row:
            _p  = float(_row["observed_price"])
            _ts = float(_row["price_updated_at"] or 0)
            if 0 < _p < max_sane and _ts > best_ts:
                best_price  = _p
                best_ts     = _ts
                best_source = "unscoped"
    except Exception:
        pass

    # ── c) DexScreener only if DB data is stale (>30s) ───────────────────────
    db_age = now - best_ts if best_ts > 0 else 9999
    if db_age > 30:
        try:
            _dex = fetch_dexscreener_price(mint)
            if _dex and 0 < _dex < max_sane:
                # DexScreener CDN cache ~30-60s stale - treat as 45s old
                _dex_ts = now - 45.0
                if _dex_ts > best_ts:
                    best_price  = _dex
                    best_ts     = _dex_ts
                    best_source = "dex-stale"
        except Exception:
            pass

    age_sec = now - best_ts if best_ts > 0 else 9999.0
    return (best_price, best_source, age_sec)


# === 0708_NATIVE_LILYPAD_SUB100_HARVEST_HELPERS ===
def _lp_bool(_v: object, _default: bool = False) -> bool:
    try:
        if _v is None:
            return _default
        return str(_v).strip().lower() not in ("0", "false", "off", "no", "none", "")
    except Exception:
        return _default


def _lp_float(_key: str, _default: float) -> float:
    try:
        return float(float(get_config_value(_key, _default)))
    except Exception:
        return float(_default)


def _lp_cfg_bool(_key: str, _default: bool = False) -> bool:
    try:
        return _lp_bool(get_config_value(_key, "1" if _default else "0"), _default)
    except Exception:
        return _default


def _lilypad_ensure_tables() -> None:
    """Create/migrate persistent Lilypad state and audit tables safely."""
    try:
        with get_connection() as _conn:
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS lilypad_harvest_state (
                    position_id INTEGER PRIMARY KEY,
                    mint TEXT,
                    opened_at REAL,
                    first_40_ts REAL,
                    first_50_ts REAL,
                    first_60_ts REAL,
                    first_75_ts REAL,
                    first_100_ts REAL,
                    last_high_pct REAL DEFAULT -999.0,
                    last_high_ts REAL,
                    max_seen_pct REAL DEFAULT -999.0,
                    last_seen_pct REAL,
                    last_seen_ts REAL,
                    plateau_count INTEGER DEFAULT 0,
                    plateau_anchor_pct REAL,
                    previous_seen_pct REAL,
                    previous_seen_ts REAL,
                    status TEXT DEFAULT 'TRACKING',
                    decision TEXT,
                    exit_fired INTEGER DEFAULT 0,
                    source TEXT,
                    updated_at REAL
                )
            """)
            # Existing databases may already have the earlier Lilypad table.
            _have = {r[1] for r in _conn.execute(
                "PRAGMA table_info(lilypad_harvest_state)"
            ).fetchall()}
            _wanted = {
                "plateau_count": "INTEGER DEFAULT 0",
                "plateau_anchor_pct": "REAL",
                "previous_seen_pct": "REAL",
                "previous_seen_ts": "REAL",
            }
            for _col, _typ in _wanted.items():
                if _col not in _have:
                    _conn.execute(
                        f"ALTER TABLE lilypad_harvest_state ADD COLUMN {_col} {_typ}"
                    )
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS lilypad_harvest_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    position_id INTEGER,
                    mint TEXT,
                    event_type TEXT,
                    level INTEGER,
                    pnl_pct REAL,
                    max_seen_pct REAL,
                    last_high_age_sec REAL,
                    hold_sec REAL,
                    price REAL,
                    source TEXT,
                    reason TEXT,
                    shadow_only INTEGER DEFAULT 0
                )
            """)
            _conn.commit()
    except Exception as _e:
        try:
            log.debug("lilypad table ensure skipped: %s", _e)
        except Exception:
            pass

def _lilypad_event(position_id: int, mint: str, event_type: str, level: int | None,
                   pnl_pct: float, max_seen_pct: float, last_high_age_sec: float | None,
                   hold_sec: float, price: float, source: str, reason: str,
                   shadow_only: bool = False) -> None:
    try:
        with get_connection() as _conn:
            _conn.execute("""
                INSERT INTO lilypad_harvest_events
                (ts, position_id, mint, event_type, level, pnl_pct, max_seen_pct,
                 last_high_age_sec, hold_sec, price, source, reason, shadow_only)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (time.time(), int(position_id), str(mint), str(event_type), level,
                  float(pnl_pct), float(max_seen_pct),
                  None if last_high_age_sec is None else float(last_high_age_sec),
                  float(hold_sec), float(price), str(source or "")[:80], str(reason or "")[:300],
                  1 if shadow_only else 0))
            _conn.commit()
    except Exception as _e:
        try:
            log.debug("lilypad event skipped pos=%s: %s", position_id, _e)
        except Exception:
            pass


def _lilypad_sub100_decision(position_id: int, mint: str, opened_at: float,
                             entry_price: float, current_price: float, pnl_pct: float,
                             hold_s: float, source: str, price_age_sec: float) -> str | None:
    """
    Full-position Lilypad hybrid exit for trusted sub-100 percent runners.

    Sign-off behaviour:
      * +100 percent graduates permanently to the monster-runner pathway.
      * +50 to +99.9 percent uses a three-mark plateau test.
      * A meaningful fast retracement exits on the second trusted mark instead
        of waiting for the third mark.
      * Any genuine new high resets the plateau sequence.
      * No partial selling and no entry/live-gate changes.
    """
    try:
        if not _lp_cfg_bool("LILYPAD_SUB100_HARVEST_ENABLED", True):
            return None
        if _lp_cfg_bool("LIVE_MONEY_MODE", False) or _lp_cfg_bool("LIVE_TRADING_ENABLED", False):
            return None
        if entry_price <= 0 or current_price <= 0:
            return None

        _lilypad_ensure_tables()
        _now = time.time()
        _source = str(source or "")
        _src_l = _source.lower()

        if _lp_cfg_bool("LILYPAD_BLOCK_SUSPECT_SOURCES", True):
            _bad_terms = ("fallback", "gate_blocked", "dex-stale", "stale",
                          "wss_fail", "stall_recovery")
            if any(_term in _src_l for _term in _bad_terms):
                _lilypad_event(position_id, mint, "SKIP_SUSPECT_SOURCE", None,
                               pnl_pct, pnl_pct, None, hold_s, current_price,
                               _source, f"suspect_source={_source}", False)
                return None

        _max_price_age = _lp_float("LILYPAD_MAX_PRICE_AGE_SEC", 90.0)
        try:
            if float(price_age_sec) > _max_price_age:
                _lilypad_event(position_id, mint, "SKIP_STALE_PRICE", None,
                               pnl_pct, pnl_pct, None, hold_s, current_price,
                               _source,
                               f"price_age={price_age_sec:.1f}s max={_max_price_age:.1f}s",
                               False)
                return None
        except Exception:
            pass

        if hold_s < _lp_float("LILYPAD_MIN_HOLD_SEC", 8.0):
            return None

        _new_high_epsilon = _lp_float("LILYPAD_NEW_HIGH_EPSILON_PCT", 0.25)
        _plateau_band = _lp_float("LILYPAD_PLATEAU_BAND_PP", 5.0)
        _plateau_marks_needed = max(3, int(_lp_float("LILYPAD_PLATEAU_MARKS", 3.0)))
        _shadow = _lp_cfg_bool("LILYPAD_SUB100_SHADOW_ONLY", False)
        _decision = None
        _event_type = None
        _level = None

        with get_connection() as _conn:
            _row = _conn.execute(
                "SELECT * FROM lilypad_harvest_state WHERE position_id=?",
                (int(position_id),),
            ).fetchone()
            if not _row:
                _conn.execute("""
                    INSERT OR IGNORE INTO lilypad_harvest_state
                    (position_id, mint, opened_at, last_high_pct, last_high_ts,
                     max_seen_pct, last_seen_pct, last_seen_ts, plateau_count,
                     plateau_anchor_pct, previous_seen_pct, previous_seen_ts,
                     status, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TRACKING', ?, ?)
                """, (int(position_id), str(mint), float(opened_at),
                      float(pnl_pct), _now, float(pnl_pct), float(pnl_pct), _now,
                      1 if pnl_pct >= 50.0 else 0,
                      float(pnl_pct) if pnl_pct >= 50.0 else None,
                      None, None, _source, _now))
                _conn.commit()
                _row = _conn.execute(
                    "SELECT * FROM lilypad_harvest_state WHERE position_id=?",
                    (int(position_id),),
                ).fetchone()

            _first40 = _row["first_40_ts"]
            _first50 = _row["first_50_ts"]
            _first60 = _row["first_60_ts"]
            _first75 = _row["first_75_ts"]
            _first100 = _row["first_100_ts"]
            _last_high_pct = float(_row["last_high_pct"] if _row["last_high_pct"] is not None else -999.0)
            _last_high_ts = float(_row["last_high_ts"] if _row["last_high_ts"] is not None else _now)
            _max_seen_pct = float(_row["max_seen_pct"] if _row["max_seen_pct"] is not None else -999.0)
            _last_seen_pct = None if _row["last_seen_pct"] is None else float(_row["last_seen_pct"])
            _last_seen_ts = None if _row["last_seen_ts"] is None else float(_row["last_seen_ts"])
            _plateau_count = int(_row["plateau_count"] or 0)
            _plateau_anchor = None if _row["plateau_anchor_pct"] is None else float(_row["plateau_anchor_pct"])
            _status = str(_row["status"] or "TRACKING")

            if pnl_pct >= 40.0 and _first40 is None:
                _first40 = _now
                _lilypad_event(position_id, mint, "FIRST_40", 40, pnl_pct,
                               max(_max_seen_pct, pnl_pct), 0.0, hold_s,
                               current_price, _source, "first confirmed +40", _shadow)
            if pnl_pct >= 50.0 and _first50 is None:
                _first50 = _now
                _lilypad_event(position_id, mint, "FIRST_50", 50, pnl_pct,
                               max(_max_seen_pct, pnl_pct), 0.0, hold_s,
                               current_price, _source, "first confirmed +50", _shadow)
            if pnl_pct >= 60.0 and _first60 is None:
                _first60 = _now
                _lilypad_event(position_id, mint, "FIRST_60", 60, pnl_pct,
                               max(_max_seen_pct, pnl_pct), 0.0, hold_s,
                               current_price, _source, "first confirmed +60", _shadow)
            if pnl_pct >= 75.0 and _first75 is None:
                _first75 = _now
                _lilypad_event(position_id, mint, "FIRST_75", 75, pnl_pct,
                               max(_max_seen_pct, pnl_pct), 0.0, hold_s,
                               current_price, _source, "first confirmed +75", _shadow)

            # Monster graduation is permanent for this position.
            if pnl_pct >= 100.0:
                if _first100 is None:
                    _first100 = _now
                    _lilypad_event(position_id, mint, "GRADUATED_100", 100,
                                   pnl_pct, max(_max_seen_pct, pnl_pct), 0.0,
                                   hold_s, current_price, _source,
                                   "crossed +100; hand to monster trailing", _shadow)
                _conn.execute("""
                    UPDATE lilypad_harvest_state
                    SET first_40_ts=?, first_50_ts=?, first_60_ts=?, first_75_ts=?,
                        first_100_ts=?, last_high_pct=?, last_high_ts=?,
                        max_seen_pct=?, previous_seen_pct=last_seen_pct,
                        previous_seen_ts=last_seen_ts, last_seen_pct=?, last_seen_ts=?,
                        plateau_count=0, plateau_anchor_pct=NULL,
                        status='GRADUATED_100', decision='MONSTER_TRAILING',
                        source=?, updated_at=?
                    WHERE position_id=?
                """, (_first40, _first50, _first60, _first75, _first100,
                      max(_last_high_pct, pnl_pct), _now, max(_max_seen_pct, pnl_pct),
                      float(pnl_pct), _now, _source, _now, int(position_id)))
                _conn.commit()
                return None

            if _status == "GRADUATED_100" or _first100 is not None:
                return None

            _is_new_high = pnl_pct > (_last_high_pct + _new_high_epsilon)
            if _is_new_high:
                _last_high_pct = float(pnl_pct)
                _last_high_ts = _now
                _max_seen_pct = max(_max_seen_pct, float(pnl_pct))
                _plateau_count = 1 if pnl_pct >= 50.0 else 0
                _plateau_anchor = float(pnl_pct) if pnl_pct >= 50.0 else None
                _lilypad_event(position_id, mint, "NEW_HIGH", None, pnl_pct,
                               _max_seen_pct, 0.0, hold_s, current_price,
                               _source, "new sub-100 high; plateau reset", _shadow)
            else:
                _max_seen_pct = max(_max_seen_pct, float(pnl_pct))
                if _max_seen_pct >= 50.0 and _last_seen_pct is not None:
                    if _max_seen_pct >= 75.0:
                        _level = 75
                        _fast_drawdown = _lp_float("LILYPAD_75_FAST_DRAWDOWN_PP", 10.0)
                        _protected_floor = _lp_float("LILYPAD_75_EXIT_FLOOR_PCT", 60.0)
                    else:
                        _level = 50
                        _fast_drawdown = _lp_float("LILYPAD_50_FAST_DRAWDOWN_PP", 8.0)
                        _protected_floor = _lp_float("LILYPAD_50_EXIT_FLOOR_PCT", 42.0)

                    _drawdown = max(0.0, _max_seen_pct - float(pnl_pct))
                    _near_peak = float(pnl_pct) >= (_max_seen_pct - _plateau_band)
                    _not_higher = float(pnl_pct) <= (_last_seen_pct + _new_high_epsilon)

                    # Fast-drop override: the second trusted mark is enough when
                    # the reversal is already meaningful.
                    if _drawdown >= _fast_drawdown or pnl_pct <= _protected_floor:
                        _event_type = "FAST_DROP_EXIT"
                        _decision = (
                            f"LILYPAD_FAST_DROP_EXIT_{_level}pct_"
                            f"peak_{_max_seen_pct:.1f}pct_drawdown_{_drawdown:.1f}pp_"
                            f"pnl_{pnl_pct:.1f}pct"
                        )
                    elif _near_peak and _not_higher:
                        _plateau_count = max(1, _plateau_count) + 1
                        if _plateau_anchor is None:
                            _plateau_anchor = _max_seen_pct
                        if _plateau_count >= _plateau_marks_needed:
                            _event_type = "PLATEAU_EXIT"
                            _decision = (
                                f"LILYPAD_PLATEAU_EXIT_{_level}pct_"
                                f"marks_{_plateau_count}_peak_{_max_seen_pct:.1f}pct_"
                                f"pnl_{pnl_pct:.1f}pct"
                            )
                    else:
                        # A move more than the plateau band below the peak but
                        # smaller than the fast-drop threshold remains under
                        # observation for the next trusted mark.
                        _plateau_count = 0
                        _plateau_anchor = None

            _last_high_age = max(0.0, _now - _last_high_ts)
            _conn.execute("""
                UPDATE lilypad_harvest_state
                SET first_40_ts=?, first_50_ts=?, first_60_ts=?, first_75_ts=?,
                    first_100_ts=?, last_high_pct=?, last_high_ts=?, max_seen_pct=?,
                    previous_seen_pct=last_seen_pct, previous_seen_ts=last_seen_ts,
                    last_seen_pct=?, last_seen_ts=?, plateau_count=?,
                    plateau_anchor_pct=?, status=?, decision=?, source=?, updated_at=?
                WHERE position_id=?
            """, (_first40, _first50, _first60, _first75, _first100,
                  _last_high_pct, _last_high_ts, _max_seen_pct, float(pnl_pct),
                  _now, int(_plateau_count), _plateau_anchor,
                  "EXIT_READY" if _decision else "TRACKING", _decision,
                  _source, _now, int(position_id)))
            _conn.commit()

        if _decision:
            _lilypad_event(position_id, mint,
                           "WOULD_EXIT" if _shadow else _event_type,
                           _level, pnl_pct, _max_seen_pct, _last_high_age,
                           hold_s, current_price, _source, _decision, _shadow)
            if _shadow:
                return None
            return _decision

    except Exception as _e:
        try:
            log.debug("lilypad decision skipped pos=%s: %s", position_id, _e)
        except Exception:
            pass
    return None

# === END_0708_NATIVE_LILYPAD_SUB100_HARVEST_HELPERS ===



# =============================================================================
# SENTINUITY SIGNOFF 20260709 — RUNNER PROFIT LOCK HELPERS
# =============================================================================

def _runner_profit_lock_enabled() -> bool:
    try:
        return str(get_config_value("PAPER_RUNNER_PROFIT_LOCK_ENABLED", "1")).strip().lower() not in ("0", "false", "off", "no")
    except Exception:
        return True


def _runner_profit_lock_floor_pct(peak_pct: float) -> float | None:
    """
    Paper runner protection ladder.

    Once a position has proven a major run, max-hold/stagnation should not be
    allowed to recycle it flat/negative. These are conservative paper stop-fill
    floors based on the observed high-water mark.
    """
    try:
        peak_pct = float(peak_pct or 0.0)
    except Exception:
        peak_pct = 0.0
    # 2026-07-13 sign-off: preserve the proven monster pathway, but stop
    # ordinary 75-149% poppers from recycling to the old +25/+40 floors.
    # A peak is only trusted after the exit evaluator recorded it in mark_tape.
    if peak_pct >= 400.0:
        return float(get_config_value("RUNNER_LOCK_FLOOR_400_PCT", 125.0))
    if peak_pct >= 250.0:
        return float(get_config_value("RUNNER_LOCK_FLOOR_250_PCT", 90.0))
    if peak_pct >= 150.0:
        return float(get_config_value("RUNNER_LOCK_FLOOR_150_PCT", 60.0))
    if peak_pct >= 100.0:
        absolute = float(get_config_value("RUNNER_LOCK_FLOOR_100_PCT", 75.0))
        drawdown = float(get_config_value("RUNNER_LOCK_MAX_GIVEBACK_100_149_PP", 18.0))
        return max(absolute, peak_pct - drawdown)
    if peak_pct >= 75.0:
        absolute = float(get_config_value("RUNNER_LOCK_FLOOR_75_PCT", 60.0))
        drawdown = float(get_config_value("RUNNER_LOCK_MAX_GIVEBACK_75_99_PP", 10.0))
        return max(absolute, peak_pct - drawdown)
    return None


def _runner_profit_lock_columns(conn) -> None:
    """Best-effort telemetry columns for exit-quality learning."""
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
        wanted = {
            "runner_protected": "INTEGER DEFAULT 0",
            "runner_peak_pct": "REAL",
            "runner_lock_floor_pct": "REAL",
            "runner_lock_price": "REAL",
            "exit_gap_from_peak_pct": "REAL",
            "exit_quality_tag": "TEXT",
        }
        for col, typ in wanted.items():
            if col not in have:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} {typ}")
    except Exception:
        pass


def _runner_profit_lock_decision(position_id: int, entry_price: float, current_price: float, position: dict) -> dict | None:
    """
    Return a protective close decision for PAPER mode if a runner has already
    proven a high-water mark but has fallen back to/through the configured
    profit-lock floor.

    This is intentionally paper-only style protection. It avoids the observed
    failure mode where MAX_HOLD_TIME closes a position flat/negative after the
    database had already recorded a +90% to +448% high-water mark.
    """
    if not _runner_profit_lock_enabled():
        return None
    try:
        entry_price = float(entry_price or 0.0)
        current_price = float(current_price or 0.0)
    except Exception:
        return None
    if entry_price <= 0 or current_price <= 0:
        return None

    # Prefer the persisted high-water mark. Fallback to current mark.
    try:
        # TRUSTED PEAK: highest_price_seen is an unfiltered MAX(observed_price).
        # Prefer the peak the exit manager actually observed (mark_tape).
        _pl_px, _pl_pct, _pl_src, _pl_ts = _trusted_peak_from_tape(
            int(position.get("id") or 0), float(position.get("entry_price") or 0.0))
        peak_price = float(_pl_px) if _pl_px and _pl_px > 0 else \
            float(position.get("highest_price_seen") or 0.0)
    except Exception:
        peak_price = 0.0
    if peak_price <= 0:
        peak_price = current_price
    if peak_price < current_price:
        peak_price = current_price

    peak_pct = ((peak_price - entry_price) / entry_price) * 100.0
    floor_pct = _runner_profit_lock_floor_pct(peak_pct)
    if floor_pct is None:
        return None

    floor_price = entry_price * (1.0 + floor_pct / 100.0)
    current_pct = ((current_price - entry_price) / entry_price) * 100.0

    # Store telemetry as soon as runner protection becomes active.
    try:
        with get_connection() as _conn:
            _runner_profit_lock_columns(_conn)
            _conn.execute(
                "UPDATE paper_positions SET runner_protected=1, runner_peak_pct=?, "
                "runner_lock_floor_pct=?, runner_lock_price=? WHERE id=?",
                (peak_pct, floor_pct, floor_price, position_id),
            )
            _conn.commit()
    except Exception:
        pass

    if current_pct > floor_pct:
        return None

    # If the engine only wakes after the floor was crossed, fill at the floor
    # in PAPER research mode. This models a protective stop that should have
    # fired instead of waiting for max-hold. Real-live remains not signed off.
    assume_stop_fill = str(get_config_value("PAPER_RUNNER_LOCK_ASSUME_STOP_FILL", "1")).strip().lower() not in ("0", "false", "off", "no")
    exit_price = max(current_price, floor_price) if assume_stop_fill else current_price
    exit_pct = ((exit_price - entry_price) / entry_price) * 100.0
    gap_pct = max(0.0, peak_pct - exit_pct)
    return {
        "exit_price": exit_price,
        "peak_pct": peak_pct,
        "floor_pct": floor_pct,
        "exit_pct": exit_pct,
        "gap_pct": gap_pct,
        "reason": f"RUNNER_PROFIT_LOCK_peak_{peak_pct:.1f}pct_floor_{floor_pct:.1f}pct_exit_{exit_pct:.1f}pct",
    }


def _runner_profit_lock_apply_exit_quality(position_id: int, decision: dict) -> None:
    try:
        with get_connection() as _conn:
            _runner_profit_lock_columns(_conn)
            _conn.execute(
                "UPDATE paper_positions SET runner_protected=1, runner_peak_pct=?, "
                "runner_lock_floor_pct=?, exit_gap_from_peak_pct=?, exit_quality_tag=? WHERE id=?",
                (
                    float(decision.get("peak_pct") or 0.0),
                    float(decision.get("floor_pct") or 0.0),
                    float(decision.get("gap_pct") or 0.0),
                    "RUNNER_PROFIT_LOCK",
                    position_id,
                ),
            )
            _conn.commit()
    except Exception:
        pass
# =============================================================================
# END SENTINUITY SIGNOFF 20260709 — RUNNER PROFIT LOCK HELPERS
# =============================================================================


# ── TRUSTED PEAK + NON-WIDENING TRAIL LATCH (2026-07-11, directive D/E) ──────
# `highest_price_seen` and get_peak_price_since_open() both derive from an
# unfiltered MAX(observed_price) over market_snapshots. One poisoned tick sets
# the peak permanently. mark_tape records every price the exit evaluator
# ACTUALLY acted on, with its source. That is the only defensible basis for a
# trailing stop: you cannot trail from a price you never saw.

def _trusted_peak_from_tape(position_id, entry_price):
    """(price, pct, source, ts) of the highest mark the evaluator observed."""
    try:
        excl = str(get_config_value("TRUSTED_PEAK_EXCLUDE_SOURCES", "") or "")
        bad = tuple(s.strip() for s in excl.split(",") if s.strip())
        with get_connection() as _c:
            if bad:
                q = ("SELECT price, pct, source, ts FROM mark_tape WHERE position_id=? "
                     "AND source NOT IN (%s) ORDER BY price DESC LIMIT 1"
                     % ",".join("?" * len(bad)))
                r = _c.execute(q, (position_id, *bad)).fetchone()
            else:
                r = _c.execute("SELECT price, pct, source, ts FROM mark_tape "
                               "WHERE position_id=? ORDER BY price DESC LIMIT 1",
                               (position_id,)).fetchone()
        if not r or not r[0]:
            return None, None, None, None
        px = float(r[0])
        pct = float(r[1]) if r[1] is not None else (
            ((px - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0)
        return px, pct, str(r[2] or ""), float(r[3] or 0.0)
    except Exception:
        return None, None, None, None


def _resolve_latched_trail(position_id, trusted_peak_pct,
                           base_trail, tight_trail, tight_thresh):
    """Advance the runner state on TRUSTED peak only. The trail never widens."""
    runner_thresh = 20.0
    state = None
    prev_trail = None
    try:
        with get_connection() as _c:
            r = _c.execute("SELECT runner_state, active_trail_pct FROM paper_positions "
                           "WHERE id=?", (position_id,)).fetchone()
        if r:
            state = r[0]
            prev_trail = float(r[1]) if r[1] is not None else None
    except Exception:
        pass

    tp = float(trusted_peak_pct or 0.0)
    if tp >= tight_thresh:
        state = "TIGHT_RUNNER"
    elif tp >= runner_thresh and state != "TIGHT_RUNNER":
        state = state or "RUNNER"

    trail = tight_trail if state == "TIGHT_RUNNER" else base_trail
    # NEVER WIDEN. This is the whole point.
    if prev_trail is not None and trail > prev_trail:
        trail = prev_trail

    tightened = (prev_trail is None) or (trail < prev_trail - 1e-9)
    try:
        now_ts = time.time()
        with get_connection() as _c:
            _c.execute(
                "UPDATE paper_positions SET runner_state=?, active_trail_pct=?, "
                "runner_peak_tier=?, "
                "runner_state_latched_at=COALESCE(runner_state_latched_at, ?), "
                "trail_last_tightened_at=CASE WHEN ? THEN ? ELSE trail_last_tightened_at END "
                "WHERE id=?",
                (state, trail,
                 ("TIGHT" if state == "TIGHT_RUNNER" else ("RUNNER" if state else None)),
                 now_ts if state else None,
                 1 if tightened else 0, now_ts, position_id))
            _c.commit()
    except Exception:
        pass
    return trail
# ── /TRUSTED PEAK + LATCH ────────────────────────────────────────────────────

def evaluate_exit_for_position(position: dict) -> None:
    """
    Canonical live exit evaluator.
    Full original logic from paper_executor._evaluate_exit - no gate reordering.

    Exit evaluation order:
      1. TIME_CUT           - negative PnL after discovery window
      2. TIME_CUT_STAGNANT  - flat/weakly green after stagnation window
      3. TRAILING_STOP      - after trail_activate, based on peak
      4. TAKE_PROFIT
      5. STOP_LOSS
      6. MAX_HOLD_TIME
    """
    position_id  = int(position["id"])
    mint         = str(position["mint_address"] or "")
    token_name   = str(position["token_name"] or mint or "UNKNOWN")[:20]
    entry_price  = float(position["entry_price"]      or 0)
    tp_pct       = float(position["take_profit_pct"]  or 25.0)
    sl_pct       = float(position["stop_loss_pct"]    or 10.0)
    opened_at    = float(position["opened_at"]        or 0)
    pos_size_usd = float(position["position_size_usd"] or 50.0)

    if not mint or entry_price <= 0:
        return

    # ── PRICE TRUTH ROUTER - single authoritative price read ────────────────
    # Uses price_router.get_execution_price() which enforces:
    #   - ts >= opened_at (no pre-entry MTM bleed)
    #   - Intel DB first, MTM snapshot second, unscoped fallback
    #   - NEVER DexScreener in execution mode
    #   - can_execute_exit=False when price is stale (>120s)
    _is_real_eval = _position_is_real(position)
    if _PRICE_ROUTER_AVAILABLE and _is_real_eval:
        _pr = _router_live_liquidation_price(
            mint, float(position.get("quantity") or 0.0), entry_price, opened_at
        )
    else:
        _pr = _router_exec_price(mint, entry_price, opened_at) if _PRICE_ROUTER_AVAILABLE else None

    if _pr is None or _pr["price"] <= 0:
        # Router has no valid price - update last_price for DB continuity but
        # do NOT overwrite live_exec_price/live_exec_pct with fallback.
        # Leaving live_exec fields at their last real value is more honest than
        # writing entry_price which makes the meter show 0% and hides real movement.
        _fallback_price = float(position.get("last_price") or 0)
        if _fallback_price <= 0:
            _fallback_price = float(position.get("entry_price") or 0)
        if _fallback_price > 0:
            # Write price/timestamp only - no router_result so live_exec_* untouched
            update_position_mark(position_id, _fallback_price, 0.0,
                                 time.time(), source="fallback")
            log.warning(
                "NO_LIVE_PRICE pos=%d %s - mark updated with last known %.10f, "
                "exits suppressed until oracle recovers.",
                position_id, token_name, _fallback_price,
            )
        else:
            hold_s = time.time() - opened_at
            _log_cognition(token_name,
                f"MTM coverage lost for {token_name}. No price available. "
                f"Hold: {hold_s:.0f}s. Awaiting oracle recovery.")
        if _is_real_eval:
            _no_price_grace = float(get_config_value("LIVE_NO_PRICE_EXIT_GRACE_SEC", 45.0))
            if (time.time() - opened_at) >= _no_price_grace:
                log.critical("[LIVE_EMERGENCY_NO_PRICE_EXIT] pos=%d mint=%s grace=%.0fs",
                             position_id, mint[:16], _no_price_grace)
                close_position_canonical(
                    position_id,
                    _fallback_price if _fallback_price > 0 else entry_price,
                    "LIVE_EMERGENCY_NO_PRICE",
                    closure_mode="normal",
                )
        return

    current_price = _pr["price"]
    price_age     = _pr["age_sec"]
    _pr_can_exit  = _pr["can_execute_exit"]
    _pr_warning   = _pr["warning"]

    log.debug("[PRICE_ROUTER] %s price=%.10f age=%.1fs src=%s can_exit=%s",
              token_name, current_price, price_age, _pr["source"], _pr_can_exit)

    # -- PRICE STALENESS KILL-SWITCH ------------------------------------------
    # SIGN-OFF FIX 3: The outer gate previously used a hardcoded literal 300
    # while stale_kill was read from config (default also 300). If an operator
    # raised STALE_PRICE_FORCE_CLOSE_SECONDS > 300, the outer gate still fired
    # at 300s and returned early - skipping SL for the entire gap window.
    # Fix: read stale_kill once and use it for the outer gate too.
    stale_kill = float(float(get_config_value("STALE_PRICE_FORCE_CLOSE_SECONDS", 300.0)))
    _stale_warn_threshold = min(stale_kill, 300.0)   # begin warning at 300s max
    # EDGE_RESTORE_PAPER_LAST_TRUSTED_MARK_20260723:
    # A post-entry paper mark does not become economically meaningless merely
    # because the transport stopped refreshing it at the live-lane freshness
    # boundary.  The previous unconditional stale branch returned before HARD
    # STOP, runner-profit-lock, trailing and MAX_HOLD, allowing Guardian to
    # become the only closer.  Keep REAL strict, but let SIM/PAPER evaluate the
    # latest post-entry router mark for a bounded recovery window.
    _paper_last_mark_max_age = max(
        _stale_warn_threshold,
        float(get_config_value("PAPER_EXIT_LAST_TRUSTED_MARK_MAX_AGE_SEC", 900.0)),
    )
    _paper_bounded_last_mark = bool(
        (not _is_real_eval) and current_price > 0 and price_age <= _paper_last_mark_max_age
    )

    if current_price <= 0 or (price_age > _stale_warn_threshold and not _paper_bounded_last_mark):
        hold_s     = time.time() - opened_at
        max_hold_s = float(float(get_config_value("EXECUTOR_MAX_HOLD_SECONDS", 900.0)))

        # DexScreener removed from execution path - CDN-cached data is UI-only.
        # If current_price is 0 here, the stale_kill guard below will handle it.
        if current_price <= 0:
            log.debug("EXEC: current_price=0 for %s, stale kill will evaluate", token_name)

        if current_price <= 0 or price_age > stale_kill:
            # SIGN-OFF FIX 10: Re-read last_price fresh from DB rather than using the
            # pre-loop position snapshot, which can be several seconds stale when the
            # oracle has been writing concurrently.
            try:
                with get_connection() as _conn:
                    _fresh_row = _conn.execute(
                        "SELECT last_price FROM paper_positions WHERE id=? AND status='OPEN'",
                        (position_id,),
                    ).fetchone()
                last_known = float(_fresh_row["last_price"] or 0) if _fresh_row else 0.0
            except Exception:
                last_known = float(position.get("last_price") or 0)

            # GATE: only close if router has a fresh executable exit price.
            # Never close using last_known / emergency_exit / entry_price fallback -
            # those produce fake tiny PnL from carried stale marks.
            # If router is unavailable or stale, mark GATE_BLOCKED and keep open.
            _router_exit_price = None
            if _PRICE_ROUTER_AVAILABLE and _pr is not None:
                if _pr.get("can_execute_exit") and _pr.get("price", 0) > 0:
                    _router_exit_price = float(_pr["price"])

            if hold_s >= max_hold_s:
                if _router_exit_price is None:
                    log.warning(
                        "MAX_HOLD_TIME pos=%d %s hold=%.0fs - router not executable, "
                        "keeping open (GATE_BLOCKED, will retry next cycle)",
                        position_id, token_name, hold_s,
                    )
                    update_position_mark(position_id,
                        last_known if last_known > 0 else entry_price,
                        0.0, time.time(), source="gate_blocked")
                    return
                close_position_canonical(position_id, _router_exit_price,
                    f"MAX_HOLD_TIME_{hold_s:.0f}s", closure_mode="normal")
                return

            if price_age > stale_kill or (current_price <= 0 and hold_s >= stale_kill):
                if _router_exit_price is None:
                    log.warning(
                        "STALE_PRICE pos=%d %s age=%.0fs - router not executable, "
                        "keeping open (GATE_BLOCKED, will retry next cycle)",
                        position_id, token_name, price_age,
                    )
                    _log_cognition(token_name,
                        f"STALE PRICE - oracle dark {price_age:.0f}s. Router not executable. "
                        f"Keeping open. Will force-close when router confirms fresh price.")
                    update_position_mark(position_id,
                        last_known if last_known > 0 else entry_price,
                        0.0, time.time(), source="gate_blocked")
                    return
                _log_cognition(token_name,
                    f"STALE PRICE KILL-SWITCH: oracle dark {price_age:.0f}s "
                    f"(limit {stale_kill:.0f}s). Closing at router live price "
                    f"${_router_exit_price:.8f}. Capital protected from silent drift.")
                close_position_canonical(position_id, _router_exit_price,
                    f"STALE_PRICE_FORCE_CLOSE_{price_age:.0f}s", closure_mode="normal")
                return

            _log_cognition(token_name,
                f"MTM price for {token_name} is stale ({price_age:.0f}s). "
                f"Hold: {hold_s:.0f}s. Force-close in "
                f"{max(0, stale_kill - price_age):.0f}s if oracle stays dark.")
            # Carry last_known price forward so UI shows the price badge (not silence),
            # but zero PnL - carried price is not executable truth.
            _last = last_known if last_known > 0 else entry_price
            if _last > 0:
                update_position_mark(position_id, _last, 0.0,
                                     time.time(), source="fallback")
            return

    # ── PRICE ROUTER EXECUTION GATE ─────────────────────────────────────────
    # If router says price is too stale to trust for exit decisions, skip
    # TP/SL evaluation. Stale price MUST NOT trigger exits.
    # EXCEPTION: if position is significantly profitable (>2x take_profit_pct),
    # allow exit regardless of staleness - never hold a big winner hostage.
    if _PRICE_ROUTER_AVAILABLE and not _pr_can_exit:
        _tp_pct = float(float(get_config_value("TAKE_PROFIT_PCT", 25.0)))
        _cur_pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        _override_stale = _cur_pnl_pct >= (_tp_pct * 1.5)  # 1.5x TP = force exit
        if _override_stale:
            log.warning(
                "[PRICE_ROUTER] %s stale but pnl=%.1f%% >= %.1f%% override threshold - allowing exit",
                token_name, _cur_pnl_pct, _tp_pct * 1.5,
            )
            # Allow fall-through to TP/SL evaluation
        else:
            if _is_real_eval:
                _stale_exit_grace = float(get_config_value("LIVE_STALE_EXIT_GRACE_SEC", 45.0))
                if (time.time() - opened_at) >= _stale_exit_grace:
                    log.critical(
                        "[LIVE_EMERGENCY_STALE_EXIT] pos=%d %s age=%.1fs warning=%s",
                        position_id, token_name, price_age, _pr_warning,
                    )
                    close_position_canonical(
                        position_id, current_price if current_price > 0 else entry_price,
                        f"LIVE_EMERGENCY_STALE_PRICE_{price_age:.0f}s",
                        closure_mode="normal",
                    )
                    return
            if _paper_bounded_last_mark:
                log.warning(
                    "[PAPER_LAST_TRUSTED_MARK] pos=%d %s age=%.1fs <= %.1fs; "
                    "evaluating HARD_STOP/RUNNER/TRAIL/MAX_HOLD from post-entry mark",
                    position_id, token_name, price_age, _paper_last_mark_max_age,
                )
                # Fall through.  This mark came from price_router and is scoped
                # post-entry; it may close PAPER only.  REAL never enters here.
            else:
                log.warning(
                    "[PRICE_ROUTER] %s price stale (%.1fs) - skipping paper TP/SL. Warning: %s",
                    token_name, price_age, _pr_warning,
                )
                update_position_mark(position_id, current_price, 0.0, time.time(), source="router-stale")
                return

    # EXIT PRICE SANITY GUARD: reject if current_price > 1000x entry
    # Catches bad oracle data from ALL price sources before fake TP fires
    if entry_price > 0 and current_price > entry_price * 1000:
        log.warning(
            "EXIT PRICE GUARD: rejected price=%.10f for %s "
            "(%.1fx entry) - bad oracle data, skipping cycle",
            current_price, token_name, current_price / entry_price
        )
        return

    pnl_pct    = ((current_price - entry_price) / entry_price) * 100
    hold_s     = time.time() - opened_at
    max_hold_s = float(float(get_config_value("EXECUTOR_MAX_HOLD_SECONDS", 900.0)))

    trail_activate = float(float(get_config_value("TRAIL_ACTIVATE_PCT",  10.0)))
    trail_pct      = float(float(get_config_value("TRAIL_STOP_PCT",      15.0)))

    # ═══════════════════════════════════════════════════════════════════════════
    # EDGE PRESERVATION PATCH V3 - DIRECTIVE COMPLIANT EXIT ORDER
    # Order: 1.HARD_STOP  2.RUNNER  3.STAGNATION  4.MAX_HOLD
    # TIME_CUT (fixed 180s) removed. TAKE_PROFIT removed (runner harvests wins).
    # ═══════════════════════════════════════════════════════════════════════════

    # -- Write live MTM mark FIRST - dashboard sees true state before any exit --
    unreal = pos_size_usd * (pnl_pct / 100.0)
    update_position_mark(
        position_id, current_price, unreal, time.time(),
        source=_pr["source"] if (_PRICE_ROUTER_AVAILABLE and _pr) else "engine",
        router_result=_pr if _PRICE_ROUTER_AVAILABLE else None,
    )
    log.debug(
        "[LIVE_EXEC_PRICE] pos=%d update_position_mark handled live_exec write",
        position_id,
    )

    # highest_price_seen - update every tick before exit evaluation
    # Defaults protect against missing data never triggering false exits
    try:
        # TRUSTED PEAK BASIS: prefer the highest mark the evaluator actually saw.
        # get_peak_price_since_open() is MAX(observed_price) with no source filter.
        _tp_price, _tp_pct, _tp_src, _tp_ts = _trusted_peak_from_tape(
            position_id, entry_price)
        if _tp_price and _tp_price > 0:
            peak_price = _tp_price
            try:
                with get_connection() as _tc:
                    _tc.execute(
                        "UPDATE paper_positions SET trusted_peak_price=?, "
                        "trusted_peak_pct=?, trusted_peak_at=?, trusted_peak_source=? "
                        "WHERE id=?",
                        (_tp_price, _tp_pct, _tp_ts, _tp_src, position_id))
                    _tc.commit()
            except Exception:
                pass
        else:
            peak_price = get_peak_price_since_open(mint, opened_at) or current_price
    except Exception:
        peak_price = current_price
    if peak_price <= 0:
        peak_price = current_price

    # volume_acceleration default: 1.0 if unavailable
    try:
        _vol_acc = float(position.get("volume_acceleration") or 1.0)
    except Exception:
        _vol_acc = 1.0

    # price_change_last_60s default: 0.0 if unavailable
    try:
        _p60 = float(position.get("price_change_last_60s") or 0.0)
    except Exception:
        _p60 = 0.0

    # -- 1. HARD STOP LOSS - NON-NEGOTIABLE, fires before everything -----------
    # Config-driven hard stop. Signed-off operator intent is 4%.
    # Keep this explicit and auditable: system_config wins; 4.0 is the safe default.
    try:
        _hard_stop_pct = abs(float(get_config_value("HARD_STOP_LOSS_PCT", 4.0)))
    except Exception:
        _hard_stop_pct = 4.0
    if not math.isfinite(_hard_stop_pct) or _hard_stop_pct <= 0.0:
        _hard_stop_pct = 4.0
    if pnl_pct <= -_hard_stop_pct:
        # SOURCE_CONSENSUS_HARDSTOP_GUARD (ported from 2026-06-26 infra).
        # Paper only: ask the price-integrity contract whether this catastrophic
        # mark came from a suspect/outlier source. If so, DEFER the close and let
        # the next fresh mark confirm - this is what stops a single poison tick
        # closing a position at -37%/-71% unconfirmed. Trusted collapses close.
        _hs_defer = False
        _stop_policy = None
        try:
            if _is_real_eval:
                raise RuntimeError("real_position_hard_stop_never_defers")
            from services.price_integrity_contract import paper_hard_stop_exit_policy, ensure_integrity_columns
            try:
                with get_connection() as _mig:
                    ensure_integrity_columns(_mig)   # schema-adaptive: adds missing integrity cols once
            except Exception:
                pass
            def _fg(_k, _d=None):
                try:
                    return position.get(_k, _d)
                except Exception:
                    return _d
            _stop_policy = paper_hard_stop_exit_policy(
                is_live_mode=False,
                entry_price=entry_price,
                current_price=current_price,
                pnl_pct=pnl_pct,
                hard_stop_pct=_hard_stop_pct,
                opened_at=_fg("opened_at"),
                price_integrity_status=_fg("price_integrity_status"),
                price_integrity_reason=_fg("price_integrity_reason"),
                first_mark_source=_fg("first_mark_source"),
                entry_price_source=_fg("entry_price_source"),
                same_mint_spread_pct=_fg("same_mint_price_spread_pct"),
                entry_vs_first_mark_pct=_fg("entry_vs_first_mark_pct"),
                price_source=_fg("mark_source") or "engine",
                price_age_sec=_fg("entry_price_age_sec"),
                guard_count=_fg("unstable_price_guard_count", 0),
                catastrophic_gap_pct=float(get_config_value("PAPER_HARD_STOP_CATASTROPHIC_GAP_PCT", 25.0)),
                same_mint_spread_max_pct=float(get_config_value("PRICE_INTEGRITY_SAME_MINT_SPREAD_MAX_PCT", 10.0)),
                cap_enabled=str(get_config_value("PAPER_HARD_STOP_CAP_ENABLED", "1")).strip().lower()
                    not in ("0", "false", "off", "no"),
            )
            if bool(_stop_policy.get("defer_close")):
                _hs_defer = True
                try:
                    with get_connection() as _gc:
                        _gc.execute(
                            "UPDATE paper_positions SET "
                            "unstable_price_guard_count = COALESCE(unstable_price_guard_count,0) + 1, "
                            "price_integrity_status='UNSTABLE', "
                            "outlier_rejected=1, "
                            "price_integrity_reason=COALESCE(price_integrity_reason,'') || '|HARD_STOP_DEFERRED:' || ? "
                            "WHERE id=?",
                            (str(_stop_policy.get("audit_reason") or "DEFER_CLOSE")[:400], position_id),
                        )
                        _gc.commit()
                except Exception:
                    pass
                log.warning(
                    "HARD_STOP_DEFERRED_SOURCE_CONSENSUS pos=%d token=%s raw_pnl=%.2fpct current=%.10f reason=%s",
                    position_id, token_name, pnl_pct, current_price,
                    str(_stop_policy.get("audit_reason") or "defer_close"),
                )
        except Exception as _hs_err:
            log.debug("hard-stop consensus guard skipped pos=%d: %s", position_id, _hs_err)
        if _hs_defer:
            return  # do NOT close on this suspect mark; next fresh mark decides
        # Paper must consume the price-integrity policy result. The previous
        # integration calculated a configured stop-floor fill and then discarded
        # it, persisting a later raw mark such as -24%/-53%. Real positions never
        # receive a synthetic fill and continue to settle from chain truth.
        _hard_stop_exit_price = current_price
        _hard_stop_exit_reason = f"HARD_STOP_LOSS_{pnl_pct:.1f}pct"
        if not _is_real_eval and isinstance(_stop_policy, dict):
            try:
                _candidate_stop_price = float(_stop_policy.get("exit_price") or current_price)
                if math.isfinite(_candidate_stop_price) and _candidate_stop_price > 0.0:
                    _hard_stop_exit_price = _candidate_stop_price
                _hard_stop_exit_reason = str(
                    _stop_policy.get("exit_reason") or _hard_stop_exit_reason
                )
            except Exception:
                _hard_stop_exit_price = current_price

        close_position_canonical(
            position_id,
            _hard_stop_exit_price,
            _hard_stop_exit_reason,
            closure_mode="normal",
        )
        log.warning(
            "HARD_STOP_EXECUTED pos=%d real=%s trigger_pct=%.4f threshold_pct=%.4f "
            "trigger_price=%.12g persisted_exit_price=%.12g reason=%s",
            position_id,
            bool(_is_real_eval),
            pnl_pct,
            _hard_stop_pct,
            current_price,
            _hard_stop_exit_price,
            _hard_stop_exit_reason,
        )
        _log_cognition(token_name,
            f"HARD STOP: {token_name} hit -{_hard_stop_pct:.0f}% floor "
            f"at {pnl_pct:.2f}%. Tail risk contained. Capital protected.")
        return


    # === 0708_NATIVE_LILYPAD_SUB100_HARVEST_INLINE ===
    # Native full-exit harvester for sub-100% poppers that stop making new highs.
    # IMPORTANT: +100% graduation bypasses this forever and hands to monster trailing.
    try:
        _lp_reason = _lilypad_sub100_decision(
            position_id=position_id,
            mint=mint,
            opened_at=opened_at,
            entry_price=entry_price,
            current_price=current_price,
            pnl_pct=pnl_pct,
            hold_s=hold_s,
            source=_pr["source"] if (_PRICE_ROUTER_AVAILABLE and _pr) else "engine",
            price_age_sec=price_age,
        )
        if _lp_reason:
            close_position_canonical(position_id, current_price, _lp_reason, closure_mode="normal")
            _log_cognition(token_name,
                f"LILYPAD HARVEST: {token_name} full exit at {pnl_pct:.2f}% "
                f"before sub-100 round-trip. Reason: {_lp_reason}")
            return
    except Exception as _lp_err:
        try:
            log.debug("lilypad inline skipped pos=%d: %s", position_id, _lp_err)
        except Exception:
            pass
    # === END_0708_NATIVE_LILYPAD_SUB100_HARVEST_INLINE ===


    # === SENTINUITY 20260709 RUNNER PROFIT LOCK ===
    # Protect proven high-water runners before stagnation/MAX_HOLD can recycle
    # them flat/negative. This directly addresses the 24h audit where rows with
    # +90% to +448% peak were later closed by MAX_HOLD_TIME near flat/negative.
    try:
        _rpl_decision = _runner_profit_lock_decision(
            position_id=position_id,
            entry_price=entry_price,
            current_price=current_price,
            position=position,
        )
        if _rpl_decision:
            _runner_profit_lock_apply_exit_quality(position_id, _rpl_decision)
            close_position_canonical(
                position_id,
                float(_rpl_decision["exit_price"]),
                str(_rpl_decision["reason"]),
                closure_mode="normal",
            )
            _log_cognition(token_name,
                f"RUNNER PROFIT LOCK: {token_name} protected at "
                f"+{float(_rpl_decision['exit_pct']):.1f}% after peak "
                f"+{float(_rpl_decision['peak_pct']):.1f}%. Floor "
                f"+{float(_rpl_decision['floor_pct']):.1f}%.")
            return
    except Exception as _rpl_err:
        try:
            log.debug("runner profit lock skipped pos=%d: %s", position_id, _rpl_err)
        except Exception:
            pass
    # === END SENTINUITY 20260709 RUNNER PROFIT LOCK ===

    # -- 2. RUNNER MODE - priority above all time-based logic -----------------
    # Activates at +20% unrealized. Switches to trailing stop only.
    # Trail: 10% from peak. Tightens to 8% if PnL >= +50%.
    # No fixed TP overrides this once active.
    _runner_activate_pct = 20.0
    _runner_trail_pct    = 10.0
    _runner_tight_pct    = 8.0   # tightens at +50%
    _runner_tight_thresh = 50.0
    if pnl_pct >= _runner_activate_pct:
        if position_id not in _trail_logged_positions:
            _log_cognition(token_name,
                f"RUNNER MODE: {token_name} at +{pnl_pct:.1f}%. "
                f"Trailing stop engaged. Floor: {_runner_trail_pct:.0f}% below peak "
                f"(tightens to {_runner_tight_pct:.0f}% at +{_runner_tight_thresh:.0f}%).")
            _trail_logged_positions.add(position_id)
        # NON-WIDENING LATCH: the tier advances on TRUSTED peak, never on current
        # pnl, and once tightened it can never loosen. Previously a +168% runner
        # falling back through +50% had its trail widened 8% -> 10% mid-collapse.
        if str(get_config_value("RUNNER_TRAIL_LATCH_ENABLED", "1")).strip().lower() \
                not in ("0", "false", "off", "no"):
            _tpk_pct = _tp_pct if ("_tp_pct" in dir() and _tp_pct is not None) else pnl_pct
            try:
                _tpk_pct = max(float(_tpk_pct or 0.0), float(pnl_pct or 0.0))
            except Exception:
                _tpk_pct = pnl_pct
            _active_trail = _resolve_latched_trail(
                position_id, _tpk_pct, _runner_trail_pct,
                _runner_tight_pct, _runner_tight_thresh)
        else:
            _active_trail = _runner_tight_pct if pnl_pct >= _runner_tight_thresh else _runner_trail_pct
        runner_stop_price = peak_price * (1 - _active_trail / 100)
        if current_price <= runner_stop_price:
            _peak_pct = ((peak_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            close_position_canonical(position_id, current_price,
                f"TRAILING_STOP_{pnl_pct:.1f}pct_peak_{_peak_pct:.1f}pct",
                closure_mode="normal")
            _log_cognition(token_name,
                f"RUNNER HARVESTED: {token_name} trail stop at "
                f"{pnl_pct:.2f}%. Peak was +{_peak_pct:.1f}%. "
                f"Trail: {_active_trail:.0f}% from peak.")
            return
        # Runner mode: do not evaluate any other exit - let it run
        return

    # -- 3. STAGNATION EXIT - only after 180s, only on true price death --------
    # Replaces TIME_CUT. Time alone NEVER triggers exit.
    # SAFETY: price_change_last_60s not written by pipeline → default is 0.0
    # When 0.0, we CANNOT confirm stagnation - treat as price moving (safe).
    # Missing data must NEVER trigger false exits.
    _stagnation_window = 300.0  # raised - winners run 3-5 min
    _stagnation_move_threshold = 0.2
    if hold_s >= _stagnation_window:
        _p60_was_written = _p60 != 0.0  # 0.0 = default = unwritten = unknown
        _price_moving    = (not _p60_was_written) or (abs(_p60) >= _stagnation_move_threshold)
        _volume_expanding = _vol_acc > 1.0
        _real_winner = pnl_pct > 0.5 and _volume_expanding

        if _real_winner or _price_moving:
            log.debug(
                "STAGNATION_HELD pos=%d %s hold=%.0fs pnl=%.2f%% "
                "moving=%s winner=%s vol_acc=%.2f p60=%.4f written=%s",
                position_id, token_name, hold_s, pnl_pct,
                _price_moving, _real_winner, _vol_acc, _p60, _p60_was_written,
            )
        else:
            close_position_canonical(position_id, current_price,
                f"TIME_CUT_STAGNANT_{hold_s:.0f}s_pnl_{pnl_pct:.2f}pct",
                closure_mode="normal")
            _log_cognition(token_name,
                f"STAGNATION EXIT: {token_name} held {hold_s:.0f}s. "
                f"Price moved {abs(_p60):.4f}% in last 60s (floor 0.2%). "
                f"Dead trade cleared. PnL: {pnl_pct:.2f}%.")
            return

    # -- 4. MAX HOLD - failsafe only, never triggers for live trades -----------
    if hold_s >= max_hold_s:
        close_position_canonical(position_id, current_price,
            f"MAX_HOLD_TIME_{hold_s:.0f}s", closure_mode="normal")
        return

    # ── Legacy trailing stop for positions between TRAIL_ACTIVATE and RUNNER --
    # Handles positions that activated old trail but haven't reached +20% yet.
    # Preserves continuity for in-flight positions during directive rollout.
    if pnl_pct >= trail_activate and pnl_pct < _runner_activate_pct:
        if position_id not in _trail_logged_positions:
            _log_cognition(token_name,
                f"Trailing stop activated at {pnl_pct:.1f}% on {token_name}. "
                f"Floor: {trail_pct:.0f}% below peak.")
            _trail_logged_positions.add(position_id)
        try:
            legacy_trail_price = peak_price * (1 - trail_pct / 100)
            if current_price <= legacy_trail_price:
                _peak_pct = ((peak_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                close_position_canonical(position_id, current_price,
                    f"TRAILING_STOP_{pnl_pct:.1f}pct_peak_{_peak_pct:.1f}pct",
                    closure_mode="normal")
                return
        except Exception:
            pass

    # SIGNAL DECAY EXIT
    try:
        _sd_conf = float(position.get("confidence") or 0)
        _sd_peak = float(position.get("peak_confidence", _sd_conf))
        _sd_threshold = float(get_config_value("SIGNAL_DECAY_THRESHOLD", 0.25))
        if _sd_peak > 0 and _sd_conf > 0 and (_sd_peak - _sd_conf) > _sd_threshold:
            close_position_canonical(
                position_id,
                current_price,
                "SIGNAL_DECAY_EXIT",
                closure_mode="normal",
            )
            return
    except Exception:
        pass


# -----------------------------------------------------------------------------
# RECONCILIATION (startup gap handling)
# -----------------------------------------------------------------------------

def reconcile_position(pos: dict, gap_start: float, gap_end: float) -> str:
    """
    Replay MTM ticks from the outage window against a position's TP/SL/MAX_HOLD.
    All closes route through close_position_canonical(closure_mode='reconcile').
    Returns outcome label for summary dict.
    """
    position_id = int(pos["id"])
    mint        = str(pos["mint_address"] or "")
    entry_price = float(pos["entry_price"] or 0)
    opened_at   = float(pos["opened_at"]   or 0)
    tp_pct      = float(pos["take_profit_pct"] or 25.0)
    sl_pct      = float(pos["stop_loss_pct"]   or 10.0)

    if not mint or entry_price <= 0:
        return "skipped"

    max_hold_s = float(float(get_config_value("EXECUTOR_MAX_HOLD_SECONDS", 900.0)))
    now        = time.time()
    hold_s     = now - opened_at

    # MAX_HOLD check first - position may be expired regardless of price
    if hold_s >= max_hold_s:
        last_price = get_last_known_price(mint) or get_last_known_price_unscoped(mint)
        exit_price = last_price if last_price else entry_price
        close_position_canonical(position_id, exit_price,
            f"RECONCILE_MAX_HOLD_{hold_s:.0f}s",
            closure_mode="reconcile", notes_prefix="RECONCILED")
        return "closed_maxhold"

    prices = get_mtm_prices_during_gap(mint, gap_start, gap_end)

    if not prices:
        # OFFLINE_NON_POISONING_20260713 (Claude audit):
        # No tick history covers the gap, so the true exit is UNOBSERVABLE.
        # The previous logic closed at the last known (possibly restart-time)
        # price as a normal RECONCILE_TP/SL WIN or LOSS, which poisons realised
        # PnL, win rate, and every model trained on outcomes. Per the offline
        # doctrine, unreconstructable gaps must close under a non-training
        # category with zero PnL. force_scratch=True makes this a SCRATCH row
        # (excluded from win/loss) and is hard-blocked for REAL rows, which
        # therefore stay OPEN for chain reconciliation — exactly as required.
        gap_duration = gap_end - gap_start
        if gap_duration > 300:
            last_price = get_last_known_price(mint) or get_last_known_price_unscoped(mint)
            if last_price:
                pnl_pct = ((last_price - entry_price) / entry_price) * 100
                if pnl_pct >= tp_pct or pnl_pct <= -sl_pct:
                    log.warning(
                        "OFFLINE_OUTCOME_UNKNOWN pos=%d %s gap=%.0fs no tick history; "
                        "stale mark suggests %+0.1f%% but exit is unobservable — "
                        "scratch-closing outside training data (no fabricated PnL)",
                        position_id, mint[:16], gap_duration, pnl_pct,
                    )
                    close_position_canonical(position_id, entry_price,
                        f"OFFLINE_OUTCOME_UNKNOWN_gap{gap_duration:.0f}s",
                        closure_mode="reconcile", notes_prefix="OFFLINE_UNOBSERVED",
                        force_scratch=True)
                    return "closed_nodata"
        return "held"

    for tick in prices:
        price   = float(tick["price"])
        ts      = float(tick["ts"])
        pnl_pct = ((price - entry_price) / entry_price) * 100

        if pnl_pct >= tp_pct:
            close_position_canonical(position_id, price,
                f"RECONCILE_TP_{pnl_pct:.1f}pct",
                closure_mode="reconcile", notes_prefix="RECONCILED")
            return "closed_tp"

        if pnl_pct <= -sl_pct:
            close_position_canonical(position_id, price,
                f"RECONCILE_SL_{pnl_pct:.1f}pct",
                closure_mode="reconcile", notes_prefix="RECONCILED")
            return "closed_sl"

        hold_at_tick = ts - opened_at
        if hold_at_tick >= max_hold_s:
            close_position_canonical(position_id, price,
                f"RECONCILE_MAX_HOLD_{hold_at_tick:.0f}s",
                closure_mode="reconcile", notes_prefix="RECONCILED")
            return "closed_maxhold"

    return "held"


def run_reconciliation() -> dict:
    now        = time.time()
    last_pulse = get_last_executor_heartbeat()
    gap        = now - last_pulse if last_pulse > 0 else 0

    if gap < 30:
        return {"gap_seconds": gap, "positions_checked": 0, "skipped": True}

    positions = get_open_positions()
    if not positions:
        return {"gap_seconds": gap, "positions_checked": 0, "closed": 0}

    results: dict[str, int] = {
        "closed_tp": 0, "closed_sl": 0, "closed_maxhold": 0,
        "closed_nodata": 0, "held": 0, "skipped": 0,
    }

    for pos in positions:
        try:
            outcome = reconcile_position(pos, last_pulse, now)
            results[outcome] = results.get(outcome, 0) + 1
        except Exception as e:
            log.error("Reconciliation failed pos=%s: %s", pos.get("id"), e)
            results["skipped"] += 1

    closed = (results["closed_tp"] + results["closed_sl"]
              + results["closed_maxhold"] + results["closed_nodata"])

    if closed > 0:
        _log_cognition("SYSTEM",
            f"Startup reconciliation resolved {closed} position(s) that triggered "
            f"during the {gap:.0f}s service gap. Organism integrity restored.",
            meta={"gap_seconds": round(gap), "results": results})

    return {
        "gap_seconds":       round(gap),
        "positions_checked": len(positions),
        "closed":            closed,
        "results":           results,
    }


# -----------------------------------------------------------------------------
# ZOMBIE RESOLUTION - THREADED (Claude sign-off patch)
# -----------------------------------------------------------------------------
#
# DESIGN: scan_and_resolve_zombies() never blocks the main loop.
# When a zombie is detected, a daemon thread is spawned to handle
# the Telegram HITL poll (up to ZOMBIE_HITL_TIMEOUT_SECONDS).
# The thread posts its result to _zombie_result_queue.
# drain_zombie_results() is called every main cycle to process outcomes.
#
# Thread lifecycle:
#   spawned  - polls Telegram for operator response
#   response - posts (position_id, "release"|"hold"|"timeout"|"tg_offline") to queue
#   main     - drains queue, calls close_position_canonical or mark_hold
# -----------------------------------------------------------------------------

def _tg_post(method: str, payload: dict, timeout: int = 10) -> Optional[dict]:
    if not BOT_TOKEN:
        return None
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload, timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Telegram API error (%s): %s", method, exc)
        return None


def _tg_available() -> bool:
    result = _tg_post("getMe", {}, timeout=5)
    return bool(result and result.get("ok"))


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _send_zombie_hitl_message(pos: dict) -> Optional[int]:
    position_id  = int(pos["id"])
    token_name   = str(pos.get("token_name") or "UNKNOWN")
    opened_at    = float(pos.get("opened_at") or 0)
    entry_price  = float(pos.get("entry_price") or 0)
    pos_size     = float(pos.get("position_size_usd") or 0)
    last_price_t = pos.get("last_price_ts") or 0

    jammed_s    = int(time.time() - opened_at)
    no_price_s  = int(time.time() - last_price_t) if last_price_t else jammed_s
    hitl_timeout = int(int(get_config_value("ZOMBIE_HITL_TIMEOUT_SECONDS", 30)))

    text = (
        f"*ZOMBIE POSITION DETECTED*\n\n"
        f"Token: `{token_name}`\n"
        f"Position ID: `{position_id}`\n"
        f"Entry price: `${entry_price:.8f}`\n"
        f"Size: `${pos_size:.2f}`\n\n"
        f"*No price feed for:* `{_fmt_duration(no_price_s)}`\n"
        f"*Jammed for:* `{_fmt_duration(jammed_s)}`\n\n"
        f"Auto-releases at scratch in *{hitl_timeout}s* if no response."
    )
    keyboard = {"inline_keyboard": [[
        {"text": "RELEASE (close at scratch)", "callback_data": f"zombie_release:{position_id}"},
        {"text": "HOLD (keep open)",           "callback_data": f"zombie_hold:{position_id}"},
    ]]}
    result = _tg_post("sendMessage", {
        "chat_id": OWNER_ID, "text": text,
        "parse_mode": "Markdown", "reply_markup": keyboard,
    })
    return result["result"]["message_id"] if result and result.get("ok") else None


def _poll_for_zombie_response(position_id: int, message_id: int, timeout_s: int) -> str:
    """
    Blocks (in its own thread) polling Telegram for operator response.
    Returns "release", "hold", or "timeout".
    """
    deadline = time.time() + timeout_s
    offset   = None
    while time.time() < deadline:
        params: dict[str, Any] = {"timeout": 2, "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        result = _tg_post("getUpdates", params, timeout=10)
        if result and result.get("ok"):
            for upd in result.get("result", []):
                offset = upd["update_id"] + 1
                cq     = upd.get("callback_query")
                if not cq:
                    continue
                data  = cq.get("data", "")
                cq_id = cq["id"]
                if f":{position_id}" not in data:
                    continue
                _tg_post("answerCallbackQuery", {"callback_query_id": cq_id})
                if data.startswith("zombie_release:"):
                    _tg_post("editMessageText", {
                        "chat_id": OWNER_ID, "message_id": message_id,
                        "text": f"RELEASE confirmed for pos {position_id}. Closing at scratch.",
                    })
                    return "release"
                if data.startswith("zombie_hold:"):
                    _tg_post("editMessageText", {
                        "chat_id": OWNER_ID, "message_id": message_id,
                        "text": f"HOLD confirmed for pos {position_id}. Slot kept open.",
                    })
                    return "hold"
        time.sleep(2)
    return "timeout"


def _zombie_hitl_thread(pos: dict, message_id: int) -> None:
    """
    Daemon thread: polls Telegram, posts result to _zombie_result_queue.
    Never calls close_position_canonical directly - that stays on the main thread.
    """
    position_id  = int(pos["id"])
    hitl_timeout = int(int(get_config_value("ZOMBIE_HITL_TIMEOUT_SECONDS", 30)))

    try:
        response = _poll_for_zombie_response(position_id, message_id, hitl_timeout)
        _zombie_result_queue.put((position_id, response, pos))
    except Exception as e:
        log.warning("Zombie HITL thread failed pos=%d: %s", position_id, e)
        _zombie_result_queue.put((position_id, "timeout", pos))
    finally:
        with _zombie_threads_lock:
            _zombie_threads_active.discard(position_id)


def drain_zombie_results() -> int:
    """
    Called by the main loop every cycle.
    Drains _zombie_result_queue and executes any pending closes/holds.
    Returns number of positions resolved.
    """
    resolved = 0
    while not _zombie_result_queue.empty():
        try:
            position_id, response, pos = _zombie_result_queue.get_nowait()
        except queue.Empty:
            break

        mint = str(pos.get("mint_address") or "")

        if response == "hold":
            cooldown = float(float(get_config_value("HITL_COOLDOWN_SECONDS", 120)))
            _hitl_sent_at[position_id] = time.time()
            _log_cognition("SYSTEM",
                f"Operator confirmed HOLD for position {position_id}. "
                f"Zombie resolver standing down for {int(cooldown)}s.")
            continue

        # response == "release" or "timeout"
        if response == "timeout":
            reason = f"ZOMBIE_AUTO_CLOSE_HITL_TIMEOUT"
        else:
            reason = "ZOMBIE_HITL_OPERATOR_RELEASE"

        # Use best known price if available, else scratch
        best_exit = get_last_known_price(mint) or get_last_known_price_unscoped(mint)
        if best_exit and best_exit > 0 and response == "timeout":
            ok = close_position_canonical(position_id, best_exit, reason,
                closure_mode="zombie", force_scratch=False, notes_prefix="ZOMBIE_CLOSE")
        else:
            ok = close_position_canonical(position_id,
                float(pos.get("entry_price") or 0), reason,
                closure_mode="zombie", force_scratch=True, notes_prefix="ZOMBIE_CLOSE")

        if ok:
            resolved += 1

    return resolved


def scan_and_resolve_zombies() -> int:
    """
    Detect zombie positions (no price feed for > ZOMBIE_PRICE_STALE_SECONDS).
    Spawns a daemon thread per zombie for HITL polling - never blocks main loop.
    For Telegram-offline cases, closes immediately on the calling thread.
    Returns count of positions resolved THIS cycle (offline-path only;
    threaded resolutions are counted via drain_zombie_results).
    """
    positions = get_open_positions()
    if not positions:
        return 0

    stale_threshold = float(float(get_config_value("ZOMBIE_PRICE_STALE_SECONDS", 300)))
    resolved = 0

    for pos in positions:
        position_id = int(pos["id"])
        mint        = str(pos.get("mint_address") or "")
        token_name  = str(pos.get("token_name") or "UNKNOWN")

        if not mint:
            continue

        last_price_ts = get_last_price_ts(mint)
        pos["last_price_ts"] = last_price_ts

        no_price_for = (
            time.time() - last_price_ts
            if last_price_ts
            else time.time() - float(pos.get("opened_at") or 0)
        )

        if no_price_for < stale_threshold:
            continue

        # Cooldown guard - don't re-alert within HITL_COOLDOWN_SECONDS
        cooldown  = float(float(get_config_value("HITL_COOLDOWN_SECONDS", 120)))
        last_sent = _hitl_sent_at.get(position_id, 0)
        if (time.time() - last_sent) <= cooldown:
            continue

        # Don't spawn duplicate thread for same position
        with _zombie_threads_lock:
            if position_id in _zombie_threads_active:
                continue

        _hitl_sent_at[position_id] = time.time()

        _log_cognition(token_name,
            f"Zombie detected: {token_name} has had no price feed for "
            f"{_fmt_duration(int(no_price_for))}. HITL resolution initiated.",
            meta={"position_id": position_id, "no_price_for_seconds": int(no_price_for)})

        tg_online  = _tg_available()
        message_id = None
        if tg_online and BOT_TOKEN and OWNER_ID:
            message_id = _send_zombie_hitl_message(pos)

        if message_id:
            # -- THREADED PATH: spawn daemon, main loop never blocks --------
            with _zombie_threads_lock:
                _zombie_threads_active.add(position_id)
            t = threading.Thread(
                target=_zombie_hitl_thread,
                args=(pos, message_id),
                daemon=True,
                name=f"zombie-hitl-{position_id}",
            )
            t.start()
            log.info("Zombie HITL thread spawned for pos=%d (%s)", position_id, token_name)

        else:
            # -- OFFLINE PATH: TG unavailable - do NOT close, just log ----
            # Closing at entry price when TG is offline destroys PnL.
            # The normal exit evaluator (TIME_CUT / MAX_HOLD / SL) will
            # handle this position correctly. Only close here if held
            # far beyond max hold time with genuinely no price at all.
            hold_s     = time.time() - float(pos.get("opened_at") or 0)
            max_hold_s = float(float(get_config_value("EXECUTOR_MAX_HOLD_SECONDS", 900.0)))
            best_exit  = get_last_known_price(mint) or get_last_known_price_unscoped(mint)

            if hold_s > max_hold_s * 2 and not best_exit:
                # Only force-close if 2x over max hold AND no price ever seen
                reason = "ZOMBIE_AUTO_CLOSE_NO_PRICE_MAXHOLD"
                ok = close_position_canonical(position_id,
                    float(pos.get("entry_price") or 0), reason,
                    closure_mode="zombie", force_scratch=True,
                    notes_prefix="ZOMBIE_CLOSE")
                if ok:
                    resolved += 1
            else:
                _log_cognition(token_name,
                    f"Zombie detected but TG offline - holding position {token_name}. "
                    f"Normal exit logic will handle. Hold: {hold_s:.0f}s")

    return resolved


# -----------------------------------------------------------------------------
# MAIN LOOP
# -----------------------------------------------------------------------------


# ─────────────────────────────────────────────────────────────────────────────
# SAME-EYES EXECUTION MONITOR - read-only dry-run of scan_for_entries
# Mirrors EXACT gate sequence of scan_for_entries with zero writes.
# Returns list of decision dicts for each candidate. Call from UI or debug only.
# ─────────────────────────────────────────────────────────────────────────────
def dry_run_entry_scan(limit: int = 30) -> list:
    """
    Dry-run entry scanner - executor-aligned diagnostic view.

    Mirrors:
    - Gate order
    - Gate logic
    - Entry decision outcomes
    - Config reads (all via get_config_value)
    - degraded_mode fetch behaviour (mirrors fetch_limit = 1 if degraded)

    Intentional differences:
    - Omits SQL price filter (observed_price > 0) to expose hidden candidates
    - Those rows are labeled: BLOCKED_NO_PRICE [not visible to executor SQL]
    - Annotates degraded-mode on WOULD_ENTER instead of silently limiting

    ZERO writes: no INSERT, UPDATE, DELETE, or wallet changes.
    This is a SAME-EYES tool, not a byte-for-byte executor clone.
    """
    results = []
    now = time.time()

    def _decision(row_dict, decision, block_reason, **extra):
        d = {
            "snapshot_id":          row_dict.get("id"),
            "mint_address":         str(row_dict.get("mint_address") or "")[:20],
            "token_name":           str(row_dict.get("token_name") or "")[:18],
            "observed_price":       row_dict.get("observed_price"),
            "upgraded_entry_price": extra.get("upgraded_entry_price"),
            "created_at":           row_dict.get("created_at"),
            "price_updated_at":     row_dict.get("price_updated_at"),
            "signal_age_sec":       extra.get("signal_age_sec"),
            "price_age_sec":        extra.get("price_age_sec"),
            "confidence":           row_dict.get("mint_confidence") or row_dict.get("confidence"),
            "decision":             decision,
            "block_reason":         block_reason,
            "compact_reason":       block_reason,
        }
        return d

    # ── Read same config as executor ─────────────────────────────────────────
    halt            = str(get_config_value("DRAWDOWN_HALT_ACTIVE",        "0")).strip()
    soft_brake      = str(get_config_value("DRAWDOWN_SOFT_BRAKE",         "0")).strip()
    max_pos         = int(get_config_value("EXECUTOR_MAX_OPEN_POSITIONS",  MAX_OPEN_POSITIONS))
    max_price_age   = float(get_config_value("EXECUTOR_MAX_PRICE_AGE_SEC", 300.0))
    max_signal_age  = float(get_config_value("EXECUTOR_MAX_SIGNAL_AGE_SEC", 600.0))
    _oracle_gate_sec = float(get_config_value("ORACLE_LIVENESS_GATE_SEC",  300.0))
    conf_floor      = float(get_config_value("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.65))
    balance         = get_wallet_balance()

    # ── Pre-scan global gates ────────────────────────────────────────────────
    if halt == "1":
        return [{"snapshot_id": None, "mint_address": "-", "token_name": "GLOBAL",
                 "decision": "BLOCKED", "block_reason": "BLOCKED_DRAWDOWN_HALT",
                 "compact_reason": "BLOCKED_DRAWDOWN_HALT",
                 "observed_price": None, "upgraded_entry_price": None,
                 "created_at": None, "price_updated_at": None,
                 "signal_age_sec": None, "price_age_sec": None, "confidence": None}]

    # Oracle liveness
    _oracle_age = 0.0
    try:
        import sqlite3 as _sq3, os as _os
        _idb = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                             "sentinuity_intelligence.db")
        _ic = _sq3.connect(_idb, timeout=2)
        _latest = _ic.execute("SELECT MAX(ts_ms) FROM mtm_ticks").fetchone()[0]
        _ic.close()
        _oracle_age = (now - _latest / 1000.0) if _latest else 9999.0
    except Exception:
        _oracle_age = 0.0  # fail-open

    oracle_blocked = _oracle_age > _oracle_gate_sec and count_open_positions() > 0

    if balance <= 0:
        return [{"snapshot_id": None, "mint_address": "-", "token_name": "GLOBAL",
                 "decision": "BLOCKED", "block_reason": "BLOCKED_NO_BALANCE",
                 "compact_reason": "BLOCKED_NO_BALANCE",
                 "observed_price": None, "upgraded_entry_price": None,
                 "created_at": None, "price_updated_at": None,
                 "signal_age_sec": None, "price_age_sec": None, "confidence": None}]

    open_count = count_open_positions()

    # ── Degraded mode - read from real executor latency cache ──────────────────
    _lc           = getattr(scan_for_entries, "_latency_cache", {"ms": 0.0, "ts": 0.0})
    _latency      = float(_lc.get("ms") or 0.0)
    _limit_ms     = float(get_config_value("EXECUTOR_WRITE_LATENCY_LIMIT_MS", 10000.0))
    degraded_mode = _latency > _limit_ms
    # Mirror real executor fetch_limit: always 25 (degraded_mode limits opens, not inspections)
    real_fetch_limit = 25

    # ── Fetch candidates ────────────────────────────────────────────────────
    # NOTE: intentionally omits "AND observed_price IS NOT NULL AND observed_price > 0"
    # from the real executor SQL. Purpose: diagnostic expanded view - surfaces candidates
    # the executor would never fetch. Those rows are labeled BLOCKED_NO_PRICE below.
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, mint_address, token_name, observed_price, price_updated_at,
                       COALESCE(created_at, timestamp, price_updated_at, 0) AS created_at,
                       mint_confidence, confidence, latch_claimed_until,
                       latched, execution_ready, candidate_state, quality_reason
                FROM market_snapshots
                WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)
                  AND candidate_state='latched'
                  AND COALESCE(tx_hash, '') NOT LIKE 'mtm:%'
                  AND (? - COALESCE(created_at, timestamp, price_updated_at, 0)) <= ?
                ORDER BY
                    COALESCE(created_at, timestamp, price_updated_at, 0) DESC,
                    price_updated_at DESC,
                    id DESC
                LIMIT ?
                """,
                (now, 1800, limit),
            ).fetchall()
    # Apply real fetch_limit AFTER fetching (diagnostic fetches full limit, then slices)
    # so all candidates are visible in output but WOULD_ENTER is annotated for degraded.
    # This matches executor behaviour: only real_fetch_limit candidates can enter.
    except Exception as e:
        return [{"snapshot_id": None, "mint_address": "ERROR", "token_name": str(e)[:40],
                 "decision": "BLOCKED", "block_reason": "BLOCKED_DB_ERROR",
                 "compact_reason": str(e)[:40],
                 "observed_price": None, "upgraded_entry_price": None,
                 "created_at": None, "price_updated_at": None,
                 "signal_age_sec": None, "price_age_sec": None, "confidence": None}]

    seen_mints: set = set()

    for row in rows:
        row_dict     = dict(row)
        snap_id      = row_dict.get("id")
        mint         = str(row_dict.get("mint_address") or "")
        token_name   = str(row_dict.get("token_name") or mint or "")[:18]
        obs_price    = float(row_dict.get("observed_price") or 0)
        price_upd_at = float(row_dict.get("price_updated_at") or 0)
        created_ts   = float(row_dict.get("created_at") or 0)
        price_age    = now - price_upd_at if price_upd_at > 0 else 9999.0
        signal_age   = now - created_ts   if created_ts   > 0 else float("inf")
        conf         = float(row_dict.get("mint_confidence") or row_dict.get("confidence") or 0.0)
        # entry_confidence: raw snapshot confidence, NULL if not present (observational only)
        _raw_conf    = row_dict.get("snap_confidence")
        entry_conf   = float(_raw_conf) if _raw_conf is not None else None
        latch_until  = float(row_dict.get("latch_claimed_until") or 0)
        row_dict["signal_age_sec"] = round(signal_age, 1)
        row_dict["price_age_sec"]  = round(price_age,  1)

        def _d(decision, reason):
            return _decision(row_dict, decision, reason,
                             signal_age_sec=round(signal_age, 1),
                             price_age_sec=round(price_age, 1))

        # Global oracle gate
        if oracle_blocked:
            results.append(_d("BLOCKED", "BLOCKED_ORACLE_STALE")); continue

        # Global max positions
        if open_count >= max_pos:
            results.append(_d("BLOCKED", "BLOCKED_MAX_POSITIONS")); continue

        # Latch claimed
        if latch_until > now:
            results.append(_d("BLOCKED", "BLOCKED_LATCH_CLAIMED")); continue

        # No price
        if obs_price <= 0:
            results.append(_d("BLOCKED", "BLOCKED_NO_PRICE [not visible to executor SQL]")); continue

        # No price timestamp
        if price_upd_at <= 0:
            results.append(_d("BLOCKED", "BLOCKED_NO_PRICE_TIMESTAMP")); continue

        # Signal too old
        if signal_age > max_signal_age:
            results.append(_d("BLOCKED", f"BLOCKED_SIGNAL_TOO_OLD ({signal_age:.0f}s > {max_signal_age:.0f}s)")); continue

        # Batch dedup
        if mint in seen_mints:
            results.append(_d("BLOCKED", "BLOCKED_BATCH_DEDUP")); continue

        # Price stale
        if price_age > max_price_age:
            results.append(_d("BLOCKED", f"BLOCKED_PRICE_STALE ({price_age:.0f}s > {max_price_age:.0f}s)")); continue

        # Duplicate open
        try:
            with get_connection() as conn:
                if conn.execute("SELECT 1 FROM paper_positions WHERE mint_address=? AND status='OPEN' LIMIT 1",
                                (mint,)).fetchone():
                    results.append(_d("BLOCKED", "BLOCKED_DUPLICATE_OPEN_POSITION")); continue
        except Exception:
            pass

        # No-reentry
        try:
            with get_connection() as conn:
                if conn.execute("SELECT 1 FROM paper_positions WHERE mint_address=? LIMIT 1",
                                (mint,)).fetchone():
                    results.append(_d("BLOCKED", "BLOCKED_NO_REENTRY")); continue
        except Exception:
            pass

        # Blacklist
        try:
            with get_connection() as conn:
                if conn.execute("SELECT 1 FROM mint_blacklist WHERE mint_address=? LIMIT 1",
                                (mint,)).fetchone():
                    results.append(_d("BLOCKED", "BLOCKED_BLACKLISTED")); continue
        except Exception:
            pass

        # Curve graduated
        if _CURVE_CHECK_AVAILABLE:
            try:
                _curve = _get_curve_progress(mint)
                if _curve.get("complete") and not _curve.get("error"):
                    results.append(_d("BLOCKED", "BLOCKED_CURVE_INVALID")); continue
            except Exception:
                pass

        # Entry price upgrade
        _qualify_ts = now - price_age
        try:
            final_price, price_source, price_ts = get_best_entry_price(mint, obs_price, _qualify_ts)
            if not final_price or final_price <= 0:
                final_price = obs_price
                price_source = "qualify-fallback"
        except Exception as _ge:
            results.append(_d("BLOCKED", f"BLOCKED_ENTRY_PRICE_UNAVAILABLE ({_ge})")); continue

        # Quantity sanity
        pos_pct      = float(get_config_value("POSITION_SIZE_PCT", 5.0))
        pos_size_usd = balance * (pos_pct / 100.0) if pos_pct > 0 else float(get_config_value("POSITION_SIZE_USD", 25.0))
        if soft_brake == "1":
            pos_size_usd *= 0.5
        quantity = pos_size_usd / final_price if final_price > 0 else 0
        if quantity > 1_000_000_000:
            results.append(_d("BLOCKED", "BLOCKED_QUANTITY_ABSURD")); continue

        seen_mints.add(mint)
        open_count += 1  # optimistic increment so subsequent rows see correct count

        # ── ADMISSION FILTER (Edge Preservation Directive V3) ─────────────────
        # Signal age = freshness of INFORMATION, not token age.
        # A 5-min old token re-priced 8s ago = PRIME signal.
        # A 30s old token with no update for 200s = STALE signal.
        _adm_conf_floor     = max(conf_floor, 0.65)
        _adm_min_exec_pct   = 2.0

        # Signal age = now - MAX(price_updated_at, qualified_at, created_at)
        _sig_price_ts  = float(row_dict.get("price_updated_at") or 0)
        _sig_qual_ts   = float(row_dict.get("qualified_at") or 0)
        _sig_create_ts = float(row_dict.get("created_at") or 0)
        _sig_latest    = max(_sig_price_ts, _sig_qual_ts, _sig_create_ts)
        _true_signal_age = (now - _sig_latest) if _sig_latest > 0 else signal_age

        # Adaptive entry cutoff: hard cap at 120s BUT allow up to 180s
        # if freshness_score indicates the signal is still explosive
        # freshness_score = 1 - (signal_age / 600), range 0.0-1.0
        _freshness_score = max(0.0, 1.0 - (_true_signal_age / 600.0))
        _adm_max_signal_age = 120.0 if _freshness_score < 0.6 else 180.0

        _adm_live_exec_pct = float(row_dict.get("live_exec_pct") or 0.0)
        _adm_fail = None
        if conf < _adm_conf_floor:
            _adm_fail = f"ADMISSION_BLOCKED_LOW_CONF ({conf:.3f} < {_adm_conf_floor:.2f})"
        elif _true_signal_age > _adm_max_signal_age:
            _adm_fail = (f"ADMISSION_BLOCKED_SIGNAL_AGE "
                        f"({_true_signal_age:.0f}s > {_adm_max_signal_age:.0f}s "
                        f"fresh={_freshness_score:.2f})")
        elif _adm_live_exec_pct < _adm_min_exec_pct and _adm_live_exec_pct > 0:
            _adm_fail = f"ADMISSION_BLOCKED_LOW_EXEC_PCT ({_adm_live_exec_pct:.2f}% < {_adm_min_exec_pct:.1f}%)"
        if _adm_fail:
            open_count -= 1
            seen_mints.discard(mint)
            results.append(_d("BLOCKED", _adm_fail))
            log.debug("ADMISSION_FILTER: %s %s", mint[:16], _adm_fail)
            continue

        _dec = "WOULD_ENTER" + (" [DEGRADED: executor opens max 1 this cycle]" if degraded_mode else "")
        r = _decision(row_dict, _dec, "WOULD_ENTER",
                      signal_age_sec=round(signal_age, 1),
                      price_age_sec=round(price_age, 1),
                      upgraded_entry_price=final_price)
        r["upgraded_entry_price"] = final_price
        r["compact_reason"] = f"src={price_source} age={round(now-price_ts,0):.0f}s"
        results.append(r)

    return results


def print_same_eyes_report() -> None:
    """Print the same-eyes execution monitor to stdout. Zero writes."""
    import sys
    results = dry_run_entry_scan(limit=30)
    print("\\n═══ SAME-EYES EXECUTION MONITOR ═══")
    print(f"{'MINT':<16} {'SIG_AGE':>7} {'PRC_AGE':>7} {'CONF':>6}  DECISION")
    print("─" * 65)
    for r in results:
        mint     = str(r.get("mint_address") or "?")[:15]
        sig_age  = f"{r['signal_age_sec']:.0f}s" if r.get("signal_age_sec") else "?"
        prc_age  = f"{r['price_age_sec']:.0f}s"  if r.get("price_age_sec")  else "?"
        conf_val = r.get("confidence")
        conf_str = f"{float(conf_val):.2f}" if conf_val is not None else "  ?"
        decision = str(r.get("block_reason") or r.get("decision") or "?")[:40]
        print(f"{mint:<16} {sig_age:>7} {prc_age:>7} {conf_str:>6}  {decision}")
    print("═" * 65)
    enters = sum(1 for r in results if r.get("decision") == "WOULD_ENTER")
    blocked = len(results) - enters
    print(f"SUMMARY: {enters} WOULD_ENTER  {blocked} BLOCKED  ({len(results)} candidates)")


def run() -> None:
    ensure_executor_schema()
    # Start cognition writer thread before first log call - avoids lazy-init
    # race condition where two threads could both see _COG_WRITER_STARTED=False.
    if _COGNITION_AVAILABLE:
        _start_cognition_writer()
    log.info("EXECUTION ENGINE ONLINE - entry + exit + reconciliation + zombie protection")
    update_heartbeat(SERVICE_NAME, "ALIVE", "Execution engine online")

    # Startup gap reconciliation
    try:
        recon = run_reconciliation()
        if recon.get("closed", 0) > 0:
            log.info("RECONCILER: closed %d from %.0fs gap",
                     recon["closed"], recon["gap_seconds"])
        else:
            log.info("RECONCILER: %d positions checked - no gap triggers (gap=%.0fs)",
                     recon.get("positions_checked", 0), recon.get("gap_seconds", 0))
    except Exception as e:
        log.warning("RECONCILER startup failed (non-fatal): %s", e)

    cycle       = 0
    zombie_tick = 0
    _mg_stat_tick = 0

    while True:
        try:
            cycle       += 1
            zombie_tick += 1
            _mg_stat_tick += 1

            # Core trading cycle
            scan_for_entries()
            check_open_positions()

            # Momentum gate periodic stats - every 20 cycles
            if _mg_stat_tick >= 20:
                _mg_stat_tick = 0
                try:
                    _mgs = momentum_gate_stats()
                    if _mgs.get("total", 0) >= 10:
                        log.info(
                            "MOMENTUM_GATE_SHADOW_STATS total=%d vetoed=%d "
                            "false_neg=%d(%.1f%%) true_neg=%d "
                            "avg_pnl_allowed=%.2f avg_pnl_vetoed=%.2f phase2_ready=%s",
                            _mgs.get("total", 0), _mgs.get("vetoed", 0),
                            _mgs.get("false_negatives", 0),
                            _mgs.get("false_negative_rate_pct", 0),
                            _mgs.get("true_negatives", 0),
                            _mgs.get("avg_pnl_allowed") or 0,
                            _mgs.get("avg_pnl_vetoed") or 0,
                            _mgs.get("ready_for_phase_2", False),
                        )
                        if _mgs.get("ready_for_phase_2"):
                            log.warning(
                                "MOMENTUM_GATE PHASE 2 READY - "
                                "false_neg_rate=%.1f%% consider enabling MOMENTUM_GATE_ENABLED=1",
                                _mgs.get("false_negative_rate_pct", 0)
                            )
                except Exception:
                    pass

            # Drain zombie thread results every cycle (non-blocking)
            drained = drain_zombie_results()
            if drained > 0:
                log.info("ZOMBIE DRAIN: resolved %d position(s)", drained)

            # Zombie scan every ZOMBIE_POLL_EVERY cycles
            if zombie_tick >= ZOMBIE_POLL_EVERY:
                zombie_tick = 0
                offline_resolved = scan_and_resolve_zombies()
                if offline_resolved > 0:
                    log.info("ZOMBIE OFFLINE: resolved %d position(s)", offline_resolved)

            # Periodic heartbeat with pipeline state
            if cycle % 12 == 0:
                with get_connection() as conn:
                    open_count    = conn.execute(
                        "SELECT COUNT(*) AS c FROM paper_positions WHERE status='OPEN'"
                    ).fetchone()["c"]
                    latched_count = conn.execute(
                        "SELECT COUNT(*) AS c FROM market_snapshots "
                        "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)"
                    ).fetchone()["c"]
                    # Diagnostic: how many exec_ready rows pass the executor SQL query
                    _now_d = time.time()
                    exec_query_count = conn.execute("""
                        SELECT COUNT(*) FROM market_snapshots
                        WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)
                          AND candidate_state='latched'
                          AND observed_price IS NOT NULL AND observed_price > 0
                          AND (? - COALESCE(created_at,timestamp,price_updated_at,0)) <= 1800
                    """, (_now_d,)).fetchone()[0]
                    # Count per-block-reason for the exec_ready rows that DON'T pass
                    wrong_state = conn.execute(
                        "SELECT COUNT(*) FROM market_snapshots "
                        "WHERE COALESCE(execution_ready,0) IN (1,2) AND candidate_state!='latched'"
                    ).fetchone()[0]
                    no_price = conn.execute(
                        "SELECT COUNT(*) FROM market_snapshots "
                        "WHERE COALESCE(execution_ready,0) IN (1,2) AND (observed_price IS NULL OR observed_price=0)"
                    ).fetchone()[0]
                _note = (
                    f"open_positions={open_count} latched_signals={latched_count} "
                    f"exec_query_matches={exec_query_count}"
                )
                if wrong_state:
                    _note += f" BLOCKED_wrong_state={wrong_state}"
                if no_price:
                    _note += f" BLOCKED_no_price={no_price}"
                if latched_count > 0 and exec_query_count == 0:
                    _note += " ← PICKUP_MISMATCH_RUN_audit_executor_pickup.py"
                update_heartbeat(SERVICE_NAME, "ALIVE", _note)

        except Exception as e:
            log.exception("Execution engine loop error: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])

        time.sleep(POLL_INTERVAL)



# ─────────────────────────────────────────────────────────────────────────────
# SAME-EYES EXECUTION MONITOR - dry-run path, ZERO writes
# Mirrors scan_for_entries() gate-for-gate using the same config reads,
# the same SQL query, and the same helper calls.
# Returns a list of dicts - one per candidate - with decision and block_reason.
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run()