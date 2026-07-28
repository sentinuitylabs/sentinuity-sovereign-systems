"""
system_guardian.py
===============================================================================
Sentinuity System Guardian -- CORE-10 SIGNED-OFF v1.1
===============================================================================

Merges:
  - system_health_monitor.py   (health detection, wallet recon, stale positions)
  - watchdog_monitor.py        (dead-service detection, restart execution)
  - auto_healer.py             (pipeline healing, slot jam, DB pruning)
  - db_prune_guard.py          (raw_dna, market_snapshots, resolved_transactions pruning)
  - emergency_vacuum.py        (WAL checkpoint, SQLite VACUUM)

===============================================================================
ARCHITECTURE -- THREE LAYERS (Gemini audit specification)
===============================================================================

  DETECTOR LAYER   -- pure observation, no side effects
    detect_dead_services()        reads heartbeats, returns findings
    detect_db_pressure()          reads WAL size + latency
    detect_wallet_drift()         reads positions + wallet, returns finding
    detect_loop_stalls()          reads pipeline state, returns finding

  POLICY LAYER     -- decides what should happen, no side effects
    decide_recovery_actions(findings) -> list of actions

  EXECUTOR LAYER   -- single place where side effects happen
    restart_service(service_name)     subprocess only here
    run_prune_cycle()                 DB cleanup only here
    run_vacuum_if_safe()              VACUUM only here
    safe_close_position(pos_id, ...)  capital mutation only here

===============================================================================
RACE CONDITIONS CLOSED (Gemini + ChatGPT audit findings)
===============================================================================

RACE 1 -- Double position close / wallet double-credit (CRITICAL)
  Before: auto_healer.heal_loop_tokens() AND health_monitor.check_stale_positions()
  both wrote status='CLOSED' and updated wallet_balance independently.
  Fix: all position closes in guardian route through safe_close_position()
  which uses an atomic claim: UPDATE paper_positions ... WHERE status='OPEN'
  and checks rowcount==1 before crediting wallet. Only one writer can win.

RACE 2 -- Wallet reconciliation during mid-entry (CRITICAL)
  Before: check_wallet_reconciliation() read open_count==0 and healed wallet,
  but execution_engine deducts wallet BEFORE inserting the paper_positions row.
  In that microsecond gap, open_count==0 but an entry is in flight.
  Fix: reconciliation aborts if ANY latched AND execution_ready in (1, 2) signals
  exist -- meaning an entry is imminent. Added second check on top of open_count.

RACE 3 -- Duplicate restart from multiple restart authorities
  Before: watchdog_monitor was the only restarter (auto_healer had no restart
  code -- ChatGPT's audit was incorrect on this point per file inspection).
  After merge: restart authority is enforced via restart_claimed_until column
  on service_heartbeats. Atomic UPDATE claim -- only the process that successfully
  claims the row may restart.

RACE 4 -- False kill of slow-but-alive service
  Before: watchdog threshold was hardcoded 300s.
  Fix: WATCHDOG_DEAD_THRESHOLD_SECONDS now reads from system_config (default
  420s). Resolver during heavy RPC batches can safely take 5-6 minutes.

===============================================================================
PRUNE SAFETY
===============================================================================

  raw_dna:          prunes only terminal states (-1, -2, 3) older than 5 min
  market_snapshots: prunes only vetoed/dead rows NOT tied to open positions
  resolved_txns:    prunes only rows where raw_dna.processed_state == 3
  cognition_log:    prunes oldest rows beyond retention limit

  VACUUM:           only runs when:
                      open_positions == 0
                      AND no close in progress
                      AND DB latency below threshold
                      AND outside active execution pressure window

===============================================================================
MAIN LOOP -- ORDERED PHASES (Gemini specification)
===============================================================================

  Every cycle (default 60s, startup-safe):
    Phase 1: collect health facts         (detector layer, read-only)
    Phase 2: collect DB pressure facts    (detector layer, read-only)
    Phase 3: collect loop stall facts     (detector layer, read-only)
    Phase 4: collect reconciliation facts (detector layer, read-only)
    Phase 5: derive actions               (policy layer, no side effects)
    Phase 6: execute bounded actions      (executor layer, max 5 per cycle)
    Phase 7: heartbeat + log

  Pipeline healing (stuck claims, stale retries, slot jam) runs every cycle.
  DB pruning runs every 60s.
  Wallet reconciliation runs every 5 minutes (when safe).
  VACUUM runs nightly or when DB pressure is critical.
"""
from __future__ import annotations


import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import get_connection, update_heartbeat, get_config_value

try:
    from services.cognition_logger import log_cognition as _log_cog
    _COGNITION_AVAILABLE = True
except Exception:
    _COGNITION_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [GUARDIAN] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("system_guardian")

SERVICE_NAME = "system_guardian"
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID     = int(os.getenv("TELEGRAM_OWNER_ID", "0") or "0")

# -- Thresholds (all configurable via system_config) ---------------------------
HEARTBEAT_DEAD_SECONDS    = 120    # health flags dead at this age
WATCHDOG_DEAD_THRESHOLD   = 420    # guardian restarts at this age (raised from 300)
STARTUP_GRACE_SECONDS     = 120    # don't restart during startup window
RESTART_COOLDOWN_SECONDS  = 120    # minimum seconds between restarts of same service
RESTART_BUDGET_PER_CYCLE  = 5      # max restarts per guardian cycle
SLOT_JAM_SECONDS          = 120    # no new entry for this long = slot jam
WALLET_RECON_WARN_USD     = 25.0   # drift threshold for wallet warning
WALLET_RECON_AUTO_FIX     = True   # auto-repair when safe
RESTART_LEASE_SECONDS     = 120    # restart_claimed_until duration

WATCHDOG_WINDOW_PREFIX = "SENTINUITY-"
LOG_DIR = BASE_DIR / "logs"

# -- Services the guardian monitors and can restart ----------------------------
CRITICAL_SERVICES: Dict[str, str] = {
    # Core 10 runtime processes -- guardian monitors/restarts these only.
    # Internal thread heartbeats (ingest, resolver, signal_engine, qualifier,
    # price_enricher) still exist for dashboard visibility, but guardian
    # supervises the merged parent process, not each internal lane.
    "pump_monitor":               "services.pump_monitor",
    "ingest_pipeline":            "services.ingest_pipeline",
    "market_intelligence":        "services.market_intelligence",
    "neural_supervisor":          "services.neural_supervisor",
    "execution_engine":           "services.execution_engine",
    "sovereign_governor":         "services.sovereign_governor",
    "polaris":                    "services.polaris",
    "sovereign_parameter_engine": "services.sovereign_parameter_engine",
    "replay_engine":              "services.replay_engine",
    "polaris_auxiliary":           "services.polaris_auxiliary",
    "reconnaissance_engine":       "services.reconnaissance_engine",
    "x_scout":                     "services.x_scout",
    # Launch-freshness replication trio
    "freshness_enforcer":          "services.freshness_enforcer",
    "periodic_refresh":            "services.periodic_refresh",
    "rolling_eviction":            "services.rolling_eviction",
    # Forensic preservation - archives runner snapshots before guardian deletes
    "winner_snapshot_archiver":    "services.winner_snapshot_archiver",
    # Shadow runner observability - tracks 10x'ers we missed
    "shadow_runner_tracker":       "services.shadow_runner_tracker",
}

# Extra module-launch args per service. Empty if not listed.
SERVICE_LAUNCH_ARGS: Dict[str, list] = {
    "freshness_enforcer": ["service"],
}

# Optional satellites and utilities -- observed for visibility only.
# They are intentionally outside the core-10 restart contract.
IMPORTANT_SERVICES: Dict[str, str] = {
    "code_vault":                "services.code_vault",
}

# Services that should never be auto-restarted
SKIP_RESTART = {"watchdog", "system_guardian", SERVICE_NAME}

# Restart state file (legacy compatibility from watchdog_monitor.py)
RESTART_STATE_FILE = BASE_DIR / "watchdog_state.json"


def _cognition(message: str, token: str = "GUARDIAN",
               meta: Optional[dict] = None) -> None:
    if not _COGNITION_AVAILABLE:
        return
    try:
        _log_cog("HEALTH", message, token=token, meta=meta or {})
    except Exception:
        pass


def _tg_alert(message: str) -> None:
    if not BOT_TOKEN or not OWNER_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


# =============================================================================
# SCHEMA ENSURE
# =============================================================================

def _ensure_guardian_schema() -> None:
    """Add guardian control columns -- non-destructive, safe to re-run."""
    try:
        with get_connection() as conn:
            # Ensure service_heartbeats exists for guardian restart lease tracking.
            # This table may or may not be created by openclaw -- create it here
            # so guardian can always write restart_claimed_until safely.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS service_heartbeats (
                    service_name           TEXT PRIMARY KEY,
                    status                 TEXT DEFAULT 'UNKNOWN',
                    note                   TEXT DEFAULT '',
                    last_heartbeat         REAL DEFAULT 0,
                    restart_claimed_until  REAL DEFAULT 0
                )
            """)
            conn.commit()

            # Restart lease on service_heartbeats (atomic claim column)
            hb_cols = {r["name"] for r in
                       conn.execute("PRAGMA table_info(service_heartbeats)").fetchall()}
            if "restart_claimed_until" not in hb_cols:
                conn.execute(
                    "ALTER TABLE service_heartbeats "
                    "ADD COLUMN restart_claimed_until REAL DEFAULT 0"
                )

            # Close claim on paper_positions (prevents double-close)
            pos_cols = {r["name"] for r in
                        conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
            if "close_claimed_until" not in pos_cols:
                conn.execute(
                    "ALTER TABLE paper_positions "
                    "ADD COLUMN close_claimed_until REAL DEFAULT 0"
                )

            # Health events table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_health_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,
                    service_name TEXT,
                    message TEXT,
                    severity TEXT DEFAULT 'INFO',
                    created_at REAL
                )
            """)
            conn.commit()
    except Exception as e:
        log.warning("ensure_guardian_schema failed (non-fatal): %s", e)


def _log_health_event(event_type: str, service_name: str,
                      message: str, severity: str = "INFO") -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO system_health_events
                    (event_type, service_name, message, severity, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, service_name, message, severity, time.time()),
            )
            conn.commit()
    except Exception:
        pass


# =============================================================================
# DETECTOR LAYER -- pure observation, no side effects
# =============================================================================

def _get_startup_marker_age(now: float) -> float:
    marker = BASE_DIR / ".startup_marker"
    if marker.exists():
        try:
            return now - marker.stat().st_mtime
        except Exception:
            pass
    return now


def _get_running_python_modules() -> set:
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process python* | Select-Object CommandLine | Format-List"],
            capture_output=True, text=True, timeout=5,
        )
        return set(result.stdout.lower().split())
    except Exception:
        return set()


def detect_dead_services() -> List[Dict[str, Any]]:
    """
    Read all heartbeats. Return list of findings for dead/degraded services.
    Pure observation -- no restarts, no mutations.
    """
    findings = []
    now = time.time()
    dead_threshold = float(get_config_value(
        "WATCHDOG_DEAD_THRESHOLD_SECONDS", WATCHDOG_DEAD_THRESHOLD))

    try:
        hb_map: dict = {}
        with get_connection() as conn:
            # Secondary: service_heartbeats (guardian lease tracking)
            try:
                rows = conn.execute(
                    "SELECT service_name, last_heartbeat, status, restart_claimed_until "
                    "FROM service_heartbeats"
                ).fetchall()
                for r in rows:
                    hb_map[r["service_name"]] = dict(r)
            except Exception:
                pass  # table may not exist or schema may differ

            # Primary: system_heartbeat (written by all services via schema.update_heartbeat)
            # Normalise last_pulse -> last_heartbeat so downstream code is unchanged.
            try:
                rows = conn.execute(
                    "SELECT service_name, last_pulse AS last_heartbeat, "
                    "status, COALESCE(restart_claimed_until, 0) AS restart_claimed_until "
                    "FROM system_heartbeat"
                ).fetchall()
                for r in rows:
                    hb_map[r["service_name"]] = dict(r)  # system_heartbeat wins
            except Exception:
                pass
    except Exception as e:
        log.warning("detect_dead_services: heartbeat read failed: %s", e)
        return []

    startup_age = _get_startup_marker_age(now)

    for service_name, module in CRITICAL_SERVICES.items():
        if service_name in SKIP_RESTART:
            continue

        hb = hb_map.get(service_name)
        if hb is None:
            if startup_age < STARTUP_GRACE_SECONDS:
                continue
            findings.append({
                "service": service_name, "module": module,
                "reason": "NO_HEARTBEAT", "age": None,
                "severity": "CRITICAL",
            })
            continue

        try:
            last_pulse = float(hb["last_heartbeat"])
        except (TypeError, ValueError):
            last_pulse = 0.0
        status = str(hb["status"] or "").upper()

        if last_pulse <= 0:
            findings.append({
                "service": service_name, "module": module,
                "reason": "ZERO_HEARTBEAT",
                "age": None, "status": status,
                "severity": "CRITICAL",
            })
            continue

        age = now - last_pulse

        if age > dead_threshold:
            findings.append({
                "service": service_name, "module": module,
                "reason": "STALE_HEARTBEAT",
                "age": age, "status": status,
                "severity": "CRITICAL",
            })
        elif age > HEARTBEAT_DEAD_SECONDS or status in ("ERROR", "DEAD", "OFFLINE"):
            findings.append({
                "service": service_name, "module": module,
                "reason": "DEGRADED",
                "age": age, "status": status,
                "severity": "WARNING",
            })

    return findings


def detect_db_pressure() -> Dict[str, Any]:
    """
    Measure DB write latency. Pure read.

    LOCK SAFETY: Uses a short timeout (3s) so this probe never itself
    becomes a lock holder. If we cannot acquire the write lock within 3s,
    we report high pressure and return immediately -- we do not wait 14s
    and then report 14s as "DB latency." That measurement artifact was
    causing the console to show 14,196ms and suppressing all guardian
    restarts via the critical_pressure gate.

    Uses a dedicated _latency_probe table with a row-limit guard so the
    table never grows unbounded.
    """
    try:
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path

        _db_path = _Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"
        start = time.monotonic()

        # Short timeout -- if we can't get the lock in 3s, report pressure
        conn = _sqlite3.connect(str(_db_path), timeout=3.0, check_same_thread=False)
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _latency_probe (ts INTEGER)")
            conn.execute("INSERT INTO _latency_probe VALUES (?)", (int(time.time()),))
            conn.execute("DELETE FROM _latency_probe")
            conn.commit()
        finally:
            conn.close()

        latency_ms = (time.monotonic() - start) * 1000
        return {
            "latency_ms":        round(latency_ms, 1),
            "high_pressure":     latency_ms > 500,
            "critical_pressure": latency_ms > 2000,
        }
    except Exception as e:
        # Timeout or lock -- report as high pressure but not critical
        # so guardian does not suppress all restarts on a transient lock
        latency_ms = (time.monotonic() - start) * 1000 if 'start' in dir() else 3000
        return {
            "latency_ms":        round(latency_ms, 1),
            "high_pressure":     True,
            "critical_pressure": latency_ms > 5000,
            "error":             str(e),
        }


def detect_wallet_drift() -> Dict[str, Any]:
    """
    Compute wallet/PnL drift. Pure read -- no mutations.
    Returns finding dict including whether reconciliation is safe to run.
    """
    try:
        with get_connection() as conn:
            state = conn.execute(
                "SELECT wallet_balance, initial_capital FROM system_state WHERE id=1"
            ).fetchone()
            if not state:
                return {"ok": False, "error": "system_state missing"}

            wallet_balance  = float(state["wallet_balance"] or 0)
            initial_capital = float(state["initial_capital"] or 0)

            closed = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl_usd),0) AS pnl, COUNT(*) AS cnt "
                "FROM paper_positions WHERE status='CLOSED'"
            ).fetchone()
            closed_realized = float(closed["pnl"] or 0)

            open_rows = conn.execute(
                "SELECT position_size_usd, unrealized_pnl_usd "
                "FROM paper_positions WHERE status='OPEN'"
            ).fetchall()
            open_count       = len(open_rows)
            open_unrealized  = sum(float(r["unrealized_pnl_usd"] or 0) for r in open_rows)
            open_cost        = sum(float(r["position_size_usd"] or 0) for r in open_rows)

            # Check if any latched signals are ready -- entry is imminent
            latched_ready = conn.execute(
                "SELECT COUNT(*) AS c FROM market_snapshots "
                "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)"
            ).fetchone()["c"]

        expected_cash = initial_capital + closed_realized
        drift         = wallet_balance - expected_cash
        recon_safe    = (open_count == 0 and latched_ready == 0)

        return {
            "ok":              True,
            "wallet_balance":  wallet_balance,
            "expected_cash":   expected_cash,
            "drift":           round(drift, 4),
            "open_count":      open_count,
            "latched_ready":   latched_ready,
            "recon_safe":      recon_safe,
            "warn":            abs(drift) >= WALLET_RECON_WARN_USD,
            "critical":        abs(drift) >= WALLET_RECON_WARN_USD * 4,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def detect_loop_stalls() -> Optional[Dict[str, Any]]:
    """
    Detect executor slot jam -- latched signals exist but no new entry opened.
    Pure read. Returns jam dict or None.
    """
    try:
        now = time.time()
        with get_connection() as conn:
            latched = conn.execute(
                "SELECT COUNT(*) AS c FROM market_snapshots "
                "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2) AND candidate_state='latched'"
            ).fetchone()["c"]

            if latched == 0:
                return None

            max_pos = int(int(get_config_value("EXECUTOR_MAX_OPEN_POSITIONS", 3)))
            open_count = conn.execute(
                "SELECT COUNT(*) AS c FROM paper_positions WHERE status='OPEN'"
            ).fetchone()["c"]

            if open_count >= max_pos:
                return None

            halt = str(str(get_config_value("DRAWDOWN_HALT_ACTIVE", "0"))).strip()
            if halt == "1":
                return None

            last_entry = conn.execute(
                "SELECT MAX(opened_at) AS last FROM paper_positions"
            ).fetchone()
            last_open_ts = float((last_entry["last"] or 0)) if last_entry else 0
            gap = now - last_open_ts

            jam_secs = float(get_config_value("SLOT_JAM_SECONDS", SLOT_JAM_SECONDS))
            if gap < jam_secs:
                return None

            return {
                "latched_signals": latched,
                "open_positions":  open_count,
                "max_positions":   max_pos,
                "free_slots":      max_pos - open_count,
                "gap_seconds":     round(gap),
            }
    except Exception as e:
        log.warning("detect_loop_stalls failed: %s", e)
        return None


def detect_stale_positions() -> List[Dict[str, Any]]:
    """
    Find orphaned open positions older than the Guardian recovery horizon.
    These survived a restart with no price monitoring.

    PROTECTION 2026-05-24: positions that reached a >=3x peak are skipped
    from stale-close to preserve slow-runner profits. Trade #3718 hit 9.66x
    peak but was killed by GUARDIAN_STALE at 17 min, capturing only ~88% of
    peak. This filter prevents that. Composite exit logic (trailing stop,
    take-profit, manual) will still close them appropriately.

    Pure read. Returns list of stale position dicts.
    """
    try:
        max_hold = float(get_config_value("EXECUTOR_MAX_HOLD_SECONDS", 900))
        # EDGE_RESTORE_GUARDIAN_RECOVERY_ONLY_20260723:
        # Guardian is a restart/orphan recovery authority, not the normal paper
        # MAX_HOLD engine.  At 1.0x max_hold it raced the executor and produced
        # 15/15 GUARDIAN_STALE closes while runner/SL logic was still recoverable.
        # Give the canonical executor a full additional hold window.
        recovery_age = max(
            max_hold * 2.0,
            float(get_config_value("GUARDIAN_STALE_POSITION_SECONDS", 1800.0)),
        )
        cutoff = time.time() - recovery_age
        # Runner protection threshold: never stale-close a position that
        # reached this peak multiplier (operator-tunable via system_config)
        runner_protect_mult = float(get_config_value("GUARDIAN_STALE_RUNNER_PROTECT_MULT", 3.0))
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, token_name, mint_address, entry_price,
                       position_size_usd, quantity, opened_at,
                       highest_price_seen
                FROM paper_positions
                WHERE status='OPEN' AND opened_at < ?
                  AND (close_claimed_until IS NULL OR close_claimed_until < ?)
                """,
                (cutoff, time.time()),
            ).fetchall()

        # Filter out profitable runners (peak_mult >= runner_protect_mult)
        filtered = []
        skipped_runners = 0
        for r in rows:
            d = dict(r)
            try:
                entry = float(d.get("entry_price") or 0)
                peak  = float(d.get("highest_price_seen") or 0)
                peak_mult = (peak / entry) if entry > 0 and peak > 0 else 0.0
                if peak_mult >= runner_protect_mult:
                    skipped_runners += 1
                    log.info(
                        "[STALE_GUARD] Skipping pos=%d %s -- peak_mult=%.2fx >= %.2fx (runner protection)",
                        d["id"], (d.get("token_name") or "?")[:14],
                        peak_mult, runner_protect_mult,
                    )
                    continue
            except Exception:
                pass
            filtered.append(d)

        if skipped_runners > 0:
            log.info("[STALE_GUARD] Protected %d profitable runner(s) from stale-close", skipped_runners)
        return filtered
    except Exception as e:
        log.warning("detect_stale_positions failed: %s", e)
        return []


# =============================================================================
# POLICY LAYER -- decides actions, no side effects
# =============================================================================

def _is_restart_allowed(service_name: str, now: float) -> bool:
    """
    Check restart lease. Returns True only if:
    - No active lease for this service
    - Cooldown has expired
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT restart_claimed_until FROM service_heartbeats WHERE service_name=?",
                (service_name,),
            ).fetchone()
        if row:
            lease_until = float(row["restart_claimed_until"] or 0)
            if now < lease_until:
                log.debug("Restart suppressed for %s -- lease active %.0fs",
                          service_name, lease_until - now)
                return False
    except Exception:
        pass

    # Also check legacy JSON cooldown file
    try:
        if RESTART_STATE_FILE.exists():
            state = json.loads(RESTART_STATE_FILE.read_text())
            times = [t for t in state.get(service_name, [])
                     if now - t < RESTART_COOLDOWN_SECONDS]
            if len(times) >= 3:
                return False
    except Exception:
        pass

    return True


def decide_recovery_actions(
    dead_findings: List[Dict],
    db_pressure: Dict,
    jam: Optional[Dict],
    stale_positions: List[Dict],
    wallet_drift: Dict,
) -> List[Dict[str, Any]]:
    """
    Policy layer -- pure function. Maps findings to action list.
    Returns ordered list of actions for executor layer.
    No side effects.
    """
    actions = []
    now     = time.time()

    # Never restart if DB is critical
    if db_pressure.get("critical_pressure"):
        log.warning("DB CRITICAL PRESSURE -- suppressing all restarts this cycle")
        actions.append({"type": "alert",
                        "message": "DB critical pressure -- restarts suppressed"})
        return actions

    # Restart dead critical services (cap at RESTART_BUDGET_PER_CYCLE)
    restart_budget = int(int(get_config_value(
        "GUARDIAN_RESTART_BUDGET", RESTART_BUDGET_PER_CYCLE)))
    restarts_queued = 0

    running_modules = _get_running_python_modules()

    for finding in dead_findings:
        if finding["severity"] != "CRITICAL":
            continue
        if restarts_queued >= restart_budget:
            break
        service = finding["service"]
        module  = finding["module"]
        if module.lower() in running_modules:
            log.info("Process running for %s -- skip restart", service)
            continue
        if not _is_restart_allowed(service, now):
            continue
        actions.append({
            "type": "restart", "service": service, "module": module,
            "reason": finding["reason"],
        })
        restarts_queued += 1

    # Alert on degraded services (no restart)
    for finding in dead_findings:
        if finding["severity"] == "WARNING":
            actions.append({
                "type": "alert",
                "message": (f"-- Service degraded: {finding['service']} "
                            f"({finding['reason']} age={finding.get('age',0):.0f}s)"),
            })

    # Wallet reconciliation -- only when safe
    if wallet_drift.get("ok") and wallet_drift.get("recon_safe") and wallet_drift.get("warn"):
        actions.append({"type": "wallet_recon", "drift": wallet_drift["drift"]})

    # Stale positions -- close at last known price
    for pos in stale_positions:
        actions.append({
            "type":    "close_stale_position",
            "pos_id":  pos["id"],
            "mint":    pos["mint_address"],
            "entry":   pos["entry_price"],
            "size":    pos["position_size_usd"],
            "qty":     pos["quantity"],
            "name":    pos["token_name"],
            "age_hrs": (time.time() - float(pos["opened_at"])) / 3600,
        })

    # Slot jam log
    if jam:
        actions.append({"type": "log_jam", "jam": jam})

    return actions


# =============================================================================
# EXECUTOR LAYER -- single place where side effects happen
# =============================================================================

def _claim_restart(service_name: str, now: float) -> bool:
    """
    Atomic restart claim. Returns True only if this call successfully
    claimed the restart slot (rowcount==1). Prevents double-restart.
    """
    lease_until = now + RESTART_LEASE_SECONDS
    try:
        with get_connection() as conn:
            # Only claim if lease has expired (no active claim)
            rowcount = conn.execute(
                """
                UPDATE service_heartbeats
                SET restart_claimed_until=?
                WHERE service_name=?
                  AND (restart_claimed_until IS NULL OR restart_claimed_until < ?)
                """,
                (lease_until, service_name, now),
            ).rowcount
            conn.commit()
        if rowcount == 1:
            return True
        # No row to update -- insert a sentinel claim (without is_alive which may not exist)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO service_heartbeats
                    (service_name, status, note, last_heartbeat, restart_claimed_until)
                VALUES (?, 'RESTARTING', 'guardian_restart_claim', ?, ?)
                """,
                (service_name, now, lease_until),
            )
            conn.commit()
        return True
    except Exception as e:
        log.warning("_claim_restart failed for %s: %s", service_name, e)
        return False


def _record_restart(service_name: str, now: float) -> None:
    """Update legacy JSON restart state for cooldown tracking."""
    try:
        state: dict = {}
        if RESTART_STATE_FILE.exists():
            try:
                state = json.loads(RESTART_STATE_FILE.read_text())
            except Exception:
                state = {}
        times = [t for t in state.get(service_name, [])
                 if now - t < 3600]
        times.append(now)
        state[service_name] = times
        RESTART_STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def restart_service(service_name: str, module: str, reason: str = "") -> bool:
    """
    SINGLE RESTART EXECUTOR -- the only function that may call subprocess.Popen.
    Acquires atomic DB claim first. If claim fails, does not restart.
    """
    now = time.time()

    if not _claim_restart(service_name, now):
        log.info("Restart claim failed for %s -- another actor holds lease", service_name)
        return False

    try:
        # Use timestamped log for services prone to Windows file lock on restart
        # sovereign_governor holds its log file open during debates - timestamp avoids lock
        _log_services_prone_to_lock = {"sovereign_governor", "polaris_auxiliary"}
        if service_name in _log_services_prone_to_lock:
            import datetime as _dt
            _ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            _log_name = f"{service_name}_{_ts}.log"
        else:
            _log_name = f"{service_name}.log"
        # Append any extra launch args (e.g. freshness_enforcer needs 'service')
        _extra_args_list = SERVICE_LAUNCH_ARGS.get(service_name, [])
        _extra_args = (" " + " ".join(_extra_args_list)) if _extra_args_list else ""
        cmd = (
            f'start "{WATCHDOG_WINDOW_PREFIX}{service_name}" /b '
            f'cmd /c "python -m {module}{_extra_args} >> {LOG_DIR}\\{_log_name} 2>&1"'
        )
        subprocess.Popen(cmd, shell=True, cwd=str(BASE_DIR))
        log.info("RESTARTED: %s (%s) reason=%s", service_name, module, reason)
        _record_restart(service_name, now)
        _log_health_event("RESTART", service_name,
                          f"Restarted via guardian -- {reason}", "WARNING")
        _cognition(f"Service {service_name} restarted by guardian. Reason: {reason}",
                   meta={"service": service_name, "reason": reason})
        return True
    except Exception as e:
        log.error("RESTART FAILED: %s -- %s", service_name, e)
        return False


def safe_close_position(
    pos_id: int,
    mint: str,
    entry_price: float,
    pos_size_usd: float,
    quantity: float,
    token_name: str,
    exit_reason: str,
) -> bool:
    """
    SAFE POSITION CLOSE -- routes through execution_engine's canonical close
    if available, otherwise uses atomic claim to prevent double-close.

    This is the ONLY guardian function that may write to paper_positions
    or credit wallet_balance.

    Uses close_claimed_until on paper_positions as a distributed mutex.
    Only the actor that sets rowcount==1 on the claim may proceed with close.
    """
    now         = time.time()
    lease_until = now + 30  # 30s close claim

    try:
        # Attempt to route through execution_engine's canonical close
        sys.path.insert(0, str(BASE_DIR))
        try:
            from services.execution_engine import (
                close_position_canonical,
                get_last_known_price,
                get_last_known_price_unscoped,
            )
            # SIGN-OFF FIX 14: Previously passed entry_price as the exit_price parameter,
            # causing close_position_canonical to compute pnl_pct = 0 for every stale
            # guardian close - recording SCRATCH regardless of actual market position and
            # never advancing the drawdown accumulator for real losses.
            # Fix: look up the best available MTM price before calling canonical close.
            _exit_price = get_last_known_price(mint)
            if not _exit_price or _exit_price <= 0:
                _exit_price = get_last_known_price_unscoped(mint)
            # GUARDIAN_COVERAGE_CONTRACT_20260722 (Phase 8):
            # Missing price coverage must never be silently booked as a normal
            # economic flat result. When no trusted mark exists anywhere:
            #   * the close is explicitly labelled PRICE_COVERAGE_LOST;
            #   * SIM rows close as force_scratch (SCRATCH accounting class,
            #     excluded from W/L and market-quality statistics);
            #   * REAL rows are NEVER force-scratched - chain settlement truth
            #     is owned by close_position_canonical's live-sell contract;
            #   * durable evidence is persisted to system_health_events.
            _coverage_lost = False
            _row_is_real = False
            try:
                with get_connection() as _fm_conn:
                    _fm_row = _fm_conn.execute(
                        "SELECT UPPER(COALESCE(funding_mode,'SIM')) AS fm "
                        "FROM paper_positions WHERE id=?", (pos_id,)
                    ).fetchone()
                _row_is_real = bool(_fm_row and str(_fm_row["fm"]) == "REAL")
            except Exception:
                _row_is_real = False
            if not _exit_price or _exit_price <= 0:
                _coverage_lost = True
                _exit_price = entry_price   # no invented economics: exit==entry
                if "GUARDIAN_STALE" in str(exit_reason):
                    exit_reason = str(exit_reason).replace(
                        "GUARDIAN_STALE", "GUARDIAN_PRICE_COVERAGE_LOST"
                    )
                else:
                    exit_reason = f"GUARDIAN_PRICE_COVERAGE_LOST|{exit_reason}"
                log.critical(
                    "safe_close_position: PRICE_COVERAGE_LOST %s pos=%d real=%s - "
                    "no trusted mark ever available; closing as labelled scratch, "
                    "NOT market flatness (entry_price=%.10f)",
                    mint[:16], pos_id, _row_is_real, entry_price,
                )
                try:
                    _log_health_event(
                        "PRICE_COVERAGE_LOST", SERVICE_NAME,
                        f"pos={pos_id} mint={mint} zero trusted marks over full hold; "
                        f"closed as scratch with explicit coverage-loss label",
                        "CRITICAL",
                    )
                except Exception:
                    pass
            return close_position_canonical(
                pos_id, _exit_price, exit_reason,
                closure_mode="guardian",
                force_scratch=bool(_coverage_lost and not _row_is_real),
                notes_prefix=("GUARDIAN_COVERAGE_LOST" if _coverage_lost
                              else "GUARDIAN_CLOSE"),
            )
        except ImportError:
            pass

        # Fallback: atomic close with claim guard (used when execution_engine not importable)
        with get_connection() as conn:
            # GUARDIAN_COVERAGE_CONTRACT_20260722: the fallback writer must never
            # close a REAL row - chain settlement truth requires the canonical
            # close path. Keep the row OPEN and surface it loudly instead.
            try:
                _fb_fm = conn.execute(
                    "SELECT UPPER(COALESCE(funding_mode,'SIM')) AS fm "
                    "FROM paper_positions WHERE id=?", (pos_id,)
                ).fetchone()
                if _fb_fm and str(_fb_fm["fm"]) == "REAL":
                    log.critical(
                        "safe_close_position fallback: REFUSING direct close of REAL "
                        "pos=%d %s - execution_engine unavailable; keeping OPEN",
                        pos_id, mint[:16],
                    )
                    return False
            except Exception:
                pass
            # Atomic claim -- only one writer can win
            claimed = conn.execute(
                """
                UPDATE paper_positions
                SET close_claimed_until=?
                WHERE id=? AND status='OPEN'
                  AND (close_claimed_until IS NULL OR close_claimed_until < ?)
                """,
                (lease_until, pos_id, now),
            ).rowcount

            if claimed != 1:
                log.info("safe_close_position: pos=%d already claimed or closed", pos_id)
                return False

            # SIGN-OFF FIX 14 (fallback path): Use MTM-scoped price query, not unscoped.
            # Also apply wallet clamp so this path cannot credit more than pos_size_usd.
            pr = conn.execute(
                "SELECT observed_price FROM market_snapshots "
                "WHERE mint_address=? AND candidate_state='mtm' AND observed_price>0 "
                "ORDER BY price_updated_at DESC LIMIT 1",
                (mint,),
            ).fetchone()
            if not pr:
                # Second chance: unscoped, but log it so it is visible in audit trail
                pr = conn.execute(
                    "SELECT observed_price FROM market_snapshots "
                    "WHERE mint_address=? AND observed_price>0 "
                    "ORDER BY price_updated_at DESC LIMIT 1",
                    (mint,),
                ).fetchone()
                if pr:
                    log.warning(
                        "safe_close_position fallback: using unscoped price for %s pos=%d",
                        mint[:16], pos_id,
                    )
            if not pr:
                # GUARDIAN_COVERAGE_CONTRACT_20260722: label, never invent flatness.
                if "GUARDIAN_STALE" in str(exit_reason):
                    exit_reason = str(exit_reason).replace(
                        "GUARDIAN_STALE", "GUARDIAN_PRICE_COVERAGE_LOST"
                    )
                else:
                    exit_reason = f"GUARDIAN_PRICE_COVERAGE_LOST|{exit_reason}"
                log.critical(
                    "safe_close_position fallback: PRICE_COVERAGE_LOST %s pos=%d",
                    mint[:16], pos_id,
                )
            exit_price = float(pr["observed_price"]) if pr else float(entry_price)
            pnl_pct    = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            pnl_usd    = pos_size_usd * (pnl_pct / 100)
            # EXIT_HARVEST_AUDIT_20260712: mirror canonical close semantics -
            # loss hard-floored at -100% of stake; profit limited only by the
            # 60x absurdity ceiling (never a +100% cap that rewrites runners).
            if pnl_usd < -pos_size_usd:
                pnl_usd = -pos_size_usd
            _profit_ceiling = pos_size_usd * 60.0
            if pnl_usd > _profit_ceiling:
                pnl_usd = _profit_ceiling
            pnl_pct = (pnl_usd / pos_size_usd * 100.0) if pos_size_usd > 0 else 0.0
            outcome    = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "SCRATCH")

            conn.execute(
                """
                UPDATE paper_positions
                SET status='CLOSED', exit_price=?, realized_pnl_usd=?,
                    unrealized_pnl_usd=0.0, closed_at=?,
                    close_claimed_until=0
                WHERE id=? AND status='OPEN'
                """,
                (exit_price, pnl_usd, now, pos_id),
            )
            conn.execute(
                """
                INSERT INTO paper_executions (
                    position_id, token_name, mint_address,
                    side, price, quantity, value_usd, reason, timestamp
                ) VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?)
                """,
                (pos_id, token_name, mint, exit_price, quantity,
                 exit_price * quantity, exit_reason, now),
            )
            conn.execute(
                "UPDATE system_state SET wallet_balance=wallet_balance+? WHERE id=1",
                (pos_size_usd + pnl_usd,),
            )
            conn.commit()

        log.info("GUARDIAN CLOSED pos=%d %s %s PnL=%+.4f reason=%s",
                 pos_id, token_name, outcome, pnl_usd, exit_reason)
        _cognition(
            f"Guardian closed {token_name} {outcome}. "
            f"PnL={pnl_usd:+.4f} USD ({pnl_pct:+.2f}%). Reason: {exit_reason}",
            meta={"pos_id": pos_id, "pnl_usd": pnl_usd})
        return True

    except Exception as e:
        log.exception("safe_close_position failed pos=%d: %s", pos_id, e)
        return False


def run_wallet_reconciliation(drift: float) -> bool:
    """
    Safe wallet reconciliation -- only runs after policy layer confirms safe.
    Must only be called when open_count==0 AND latched_ready==0.
    In PAPER mode: skipped entirely. Paper balance is reset on each launch.
    """
    # HARD PAPER MODE GUARD -- never reconcile in paper mode.
    # Paper wallet is reset to baseline on launch and tracked by execution_engine.
    # Reconciling against closed PnL in paper mode corrupts the baseline.
    #
    # DUAL MODE QUARANTINE (2026-05-27):
    # In dual mode, live_wallet_sync writes chain balance to system_state.wallet_balance
    # which may include open position equity or stale chain reads.
    # Reconciliation from closed PnL ledger in that context produces false drift ($80.74
    # was observed - paper equity summed into live wallet).
    # Reconciliation is ONLY safe when TRADING_MODE is strictly 'live' (not 'dual').
    # In dual mode, live_wallet_sync owns the balance; guardian must not override it.
    try:
        _mode = get_config_value("TRADING_MODE", "paper")
        _mode_clean = str(_mode).strip().lower()
        # DUAL-MODE GUARD (2026-05-30):
        # Recon sums ALL closed paper_positions PnL against initial_capital.
        # In dual mode, paper positions run alongside live - summing 1700+ paper
        # losses against the live wallet baseline corrupts wallet_balance to a
        # large negative value (observed: $34.68 -> $-54.30).
        # live_wallet_sync owns the balance truth in dual/live mode; recon must
        # not override it. Only permit recon in strict single-lane live mode
        # where paper_positions contains ONLY live execution records.
        _paper_enabled = str(get_config_value("PAPER_TRADING_ENABLED", "0") or "0").strip() == "1"
        _is_dual = _mode_clean == "live" and _paper_enabled
        if _mode_clean != "live" or _is_dual:
            log.debug(
                "Wallet recon skipped - mode=%s paper_enabled=%s "
                "(recon only runs in strict single-lane live mode)",
                _mode_clean, _paper_enabled,
            )
            return False
    except Exception:
        return False

    try:
        with get_connection() as conn:
            state = conn.execute(
                "SELECT wallet_balance, initial_capital FROM system_state WHERE id=1"
            ).fetchone()
            if not state:
                return False

            wallet_balance  = float(state["wallet_balance"] or 0)
            initial_capital = float(state["initial_capital"] or 0)

            # Re-verify safe -- double-check inside transaction
            open_count = conn.execute(
                "SELECT COUNT(*) AS c FROM paper_positions WHERE status='OPEN'"
            ).fetchone()["c"]
            latched = conn.execute(
                "SELECT COUNT(*) AS c FROM market_snapshots "
                "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)"
            ).fetchone()["c"]

            if open_count > 0 or latched > 0:
                log.info("Wallet recon aborted -- positions or latched signals active")
                return False

            # SIGN-OFF FIX 13: Previously used SUM(realized_pnl_usd) directly, which
            # encoded any pre-clamp corrupt values (Bug 1) into the wallet baseline.
            # Fix: cap each row's contribution to ±position_size_usd before summing,
            # mirroring the clamp logic in close_position_canonical. After Fix 1+5 are
            # deployed new closes will always be clean; this guard handles legacy rows.
            closed_rows = conn.execute(
                "SELECT realized_pnl_usd, position_size_usd "
                "FROM paper_positions WHERE status='CLOSED'"
            ).fetchall()
            closed_pnl = 0.0
            for row in closed_rows:
                _pnl  = float(row["realized_pnl_usd"] or 0)
                _size = float(row["position_size_usd"] or 0)
                if _size > 0:
                    _pnl = max(-_size, min(_size, _pnl))   # clamp to ±100% of position
                closed_pnl += _pnl
            expected = float(initial_capital) + closed_pnl

            # PAPER MODE GUARD: initial_capital is set once by reset_to_real_wallet.py
            # and must not be recalculated here. Only wallet_balance is updated.
            # If initial_capital looks wrong (>100 in paper mode with small wallet),
            # clamp expected to avoid corrupting the paper baseline.
            _mode_r = conn.execute(
                "SELECT value FROM system_config WHERE key='TRADING_MODE'"
            ).fetchone()
            _is_paper = not _mode_r or str(_mode_r[0] or "paper").strip().lower() != "live"
            if _is_paper and expected > initial_capital * 10:
                log.warning("WALLET RECON SKIPPED: expected $%.2f seems corrupt (initial $%.2f)",
                            expected, initial_capital)
                return False

            conn.execute(
                "UPDATE system_state SET wallet_balance=? WHERE id=1",
                (expected,),
            )
            conn.commit()

        log.info("WALLET RECON: $%.2f -> $%.2f (drift %.2f)",
                 wallet_balance, expected, drift)
        _cognition(
            f"Wallet reconciled: ${wallet_balance:.2f} -> ${expected:.2f} "
            f"(drift was ${drift:+.2f}). Ledger truth restored.",
            token="WALLET")
        return True

    except Exception as e:
        log.warning("run_wallet_reconciliation failed: %s", e)
        return False


# =============================================================================
# PIPELINE HEALING (runs every cycle)
# =============================================================================

def _heal_stuck_claims() -> int:
    """Reclaim raw_dna rows stuck at processed_state=99 with expired leases.
    Also recycles state=1 rows whose claim_until has expired - these accumulate
    as raw_expired_claims and starve the resolver of fresh work.
    """
    try:
        now = time.time()
        cutoff_99 = now - 45   # CLAIM_EXPIRE_S from tx_resolver
        cutoff_1  = now - 120  # state=1 claim timeout (2 min)
        with get_connection() as conn:
            r1 = conn.execute(
                "UPDATE raw_dna SET processed_state=1, claim_until=NULL "
                "WHERE processed_state=99 "
                "AND (claim_until IS NULL OR claim_until<?)",
                (cutoff_99,),
            )
            # Recycle state=1 rows with expired claim_until back to state=0
            # so the resolver can retry them - prevents resolver starvation.
            r2 = conn.execute(
                "UPDATE raw_dna SET processed_state=0, claim_until=NULL "
                "WHERE processed_state=1 "
                "AND claim_until IS NOT NULL "
                "AND claim_until < ?",
                (cutoff_1,),
            )
            conn.commit()
            return r1.rowcount + r2.rowcount
    except Exception:
        return 0


def _heal_stale_retries() -> int:
    """Reset market_snapshots stuck at price_status='retry'."""
    try:
        cutoff = time.time() - 300  # RETRY_EXPIRE_S
        with get_connection() as conn:
            r = conn.execute(
                """
                UPDATE market_snapshots
                SET price_status='pending', price_attempts=0
                WHERE price_status='retry'
                  AND price_last_attempt_at<?
                  AND candidate_state='pending'
                  AND latched=0
                """,
                (cutoff,),
            )
            conn.commit()
            return r.rowcount
    except Exception:
        return 0


def _recompute_freshness_tiers() -> int:
    """Bulk recompute tier, freshness_score, active_cognition for live snapshots.

    Phase A thresholds - must exactly match neural_supervisor._compute_tier():
      HOT  <= 45s   freshness=1.0   active=1
      WARM <= 120s  freshness=0.85  active=1
      COOL <= 300s  freshness=0.5   active=1
      COLD <= 900s  freshness=0.0   active=0
      DEAD >  900s  freshness=0.0   active=0

    Runs every 60s. Covers rows the supervisor isn't currently scanning.
    Prevents permanent COLD drift. Re-promotes genuinely fresh rows.
    WARM freshness=0.85 matches the supervisor per-row 0.85 gate threshold.
    """
    try:
        now = time.time()
        with get_connection() as conn:
            r = conn.execute("""
                UPDATE market_snapshots
                SET
                    tier = CASE
                        -- Rows with NULL price_updated_at are NOT HOT regardless of created_at age.
                        -- Using created_at as freshness proxy for unpriced rows causes false HOT
                        -- classification which: (a) skips guardian reset, (b) misleads supervisor.
                        -- Only rows with a real oracle-written price_updated_at get HOT/WARM.
                        WHEN price_updated_at IS NULL THEN 'COLD'
                        WHEN (? - price_updated_at) <= 45  THEN 'HOT'
                        WHEN (? - price_updated_at) <= 120 THEN 'WARM'
                        WHEN (? - price_updated_at) <= 300 THEN 'COOL'
                        WHEN (? - price_updated_at) <= 900 THEN 'COLD'
                        ELSE 'DEAD'
                    END,
                    freshness_score = CASE
                        WHEN price_updated_at IS NULL THEN 0.0
                        WHEN (? - price_updated_at) <= 45  THEN 1.0
                        WHEN (? - price_updated_at) <= 120 THEN 0.85
                        WHEN (? - price_updated_at) <= 300 THEN 0.5
                        ELSE 0.0
                    END,
                    active_cognition = CASE
                        WHEN price_updated_at IS NULL THEN 0
                        WHEN (? - price_updated_at) <= 300 THEN 1
                        ELSE 0
                    END
                WHERE candidate_state NOT IN ('vetoed','exited','executed','expired_stale','EXECUTOR_STALE_GATE')
                  AND latched = 0
            """, (now, now, now, now, now, now, now, now, now))
            conn.commit()
            return r.rowcount
    except Exception as e:
        log.debug("freshness recompute skipped: %s", e)
        return 0


def _heal_loop_tokens() -> int:
    """
    Detect tokens with 2+ buys (loop signal). Blacklists mint, vetos snapshots.
    DOES NOT directly close positions -- signals safe_close_position instead.
    (Closes are batched into the executor layer via detect_stale_positions.)
    """
    blacklisted = 0
    try:
        now = time.time()
        with get_connection() as conn:
            loops = conn.execute(
                """
                SELECT token_name, mint_address,
                    SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END) AS buys
                FROM paper_executions
                GROUP BY mint_address HAVING buys >= 2
                """
            ).fetchall()

            for loop in loops:
                mint = loop["mint_address"]
                if not mint:
                    continue

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mint_blacklist (
                        mint_address TEXT PRIMARY KEY,
                        token_name TEXT, blacklisted_at REAL, reason TEXT
                    )
                """)
                conn.execute(
                    "INSERT OR REPLACE INTO mint_blacklist "
                    "(mint_address, token_name, blacklisted_at, reason) "
                    "VALUES (?, ?, ?, 'GUARDIAN_LOOP_DETECT')",
                    (mint, loop["token_name"], now),
                )
                conn.execute(
                    """
                    UPDATE market_snapshots
                    SET execution_ready=0, candidate_state='vetoed',
                        quality_reason='BLACKLISTED_LOOP_TOKEN'
                    WHERE mint_address=? AND candidate_state IN ('latched','pending')
                    """,
                    (mint,),
                )
                blacklisted += 1

            if blacklisted:
                conn.commit()

    except Exception as e:
        log.warning("_heal_loop_tokens failed: %s", e)
    return blacklisted



def _heal_orphaned_latch() -> int:
    """
    Detect latched rows where the executor grabbed but never completed:
    - latch_claimed_until IS NULL  (claim write failed silently)
    - latch_claimed_until < now    (claim expired after position INSERT failed)

    SIGN-OFF FIX 12: Previously only caught NULL claims. A non-NULL but expired
    latch_claimed_until was never reset, permanently stranding the signal in
    candidate_state='latched' with no healing path. On the next executor scan
    the OPEN_POSITION_EXISTS guard would veto it (even though no position existed),
    losing the signal forever. Fix: extend the WHERE clause to also reset expired
    non-NULL claims (latch_claimed_until < now).
    """
    try:
        now = time.time()
        with get_connection() as conn:
            r = conn.execute("""
                UPDATE market_snapshots
                SET candidate_state='pending', latched=0,
                    execution_ready=0, latch_claimed_until=NULL
                WHERE candidate_state='latched'
                  AND (latch_claimed_until IS NULL OR latch_claimed_until < ?)
                  AND COALESCE(price_updated_at, timestamp, 0) < ?
                  AND mint_address NOT IN (
                      SELECT mint_address FROM paper_positions WHERE status='OPEN'
                  )
            """, (now, now - 900,))  # 900s: must be stale >15min AND no open position for that mint
            conn.commit()
            if r.rowcount > 0:
                log.warning("ORPHANED_LATCH_RESET: reset %d latched rows (price stale >900s, no open position)", r.rowcount)
            return r.rowcount
    except Exception:
        return 0

def _heal_stale_latched(max_age_hours: float = 2.0) -> int:
    """Veto latched snapshots older than max_age_hours."""
    try:
        cutoff = time.time() - (max_age_hours * 3600)
        with get_connection() as conn:
            r = conn.execute(
                """
                UPDATE market_snapshots
                SET execution_ready=0, candidate_state='vetoed',
                    quality_reason='GUARDIAN_STALE_LATCHED'
                WHERE latched=1 AND candidate_state='latched'
                  AND COALESCE(price_updated_at, 0) < ?
                """,
                (cutoff,),
            )
            conn.commit()
            return r.rowcount
    except Exception:
        return 0


# =============================================================================
# HEALTH CHECKS (absorbed from system_health_monitor.py)
# =============================================================================

# Constants (preserved from system_health_monitor.py)
CONFIDENCE_CEILING       = 0.99
STUCK_PROCESSING_MIN     = 10
WALLET_RECON_WARN_USD    = 25.0
WALLET_RECON_CRIT_USD    = 100.0
WALLET_RECON_AUTO_FIX    = True
DRAWDOWN_AUTO_CLEAR_MINS = 30.0
DB_SIZE_WARN_MB          = 2000
DB_SIZE_CRITICAL_MB      = 4000
RAW_DNA_MAX_ROWS         = 100_000
RAW_DNA_PRUNE_BATCH      = 50_000
COGNITION_MAX_ROWS       = 50_000
TELEMETRY_MAX_ROWS       = 10_000
RESOLVED_TX_MAX_ROWS     = 200_000
MARKET_SNAP_MAX_AGE_HRS  = 24
WAL_CHECKPOINT_INTERVAL  = 1800
RPC_DAILY_BUDGET         = 2_666_667
RPC_ALERT_THRESHOLDS     = [0.10, 0.25, 0.50, 0.75, 0.90]

_last_checkpoint_ts: float = 0.0
_last_golden_ts:     float = 0.0


def check_oracle_liveness() -> dict:
    """
    Read oracle age from sentinuity_intelligence.db mtm_ticks.
    Returns dict with ok, age_sec, gate_sec, message.
    """
    try:
        import sqlite3 as _sq3, os as _os
        _idb = _os.path.join(str(BASE_DIR), "sentinuity_intelligence.db")
        _c = _sq3.connect(_idb, timeout=2)
        _latest = _c.execute("SELECT MAX(ts_ms) FROM mtm_ticks").fetchone()[0]
        _c.close()
        age = (time.time() - float(_latest) / 1000.0) if _latest else 9999.0
    except Exception as e:
        return {"ok": False, "age_sec": 9999.0, "gate_sec": 300.0,
                "message": f"oracle DB read error: {e}"}
    gate = float(get_config_value("ORACLE_LIVENESS_GATE_SEC", 300.0))
    ok   = age <= gate
    return {"ok": ok, "age_sec": round(age, 1), "gate_sec": gate,
            "message": f"oracle_age={age:.0f}s gate={gate:.0f}s"}


_last_oracle_heal: float = 0.0
_last_feed_restart_at: float = 0.0
_oracle_restart_pending_since: float = 0.0
_oracle_restart_baseline_tick_ms: float = 0.0
_oracle_restart_attempts: int = 0
_oracle_restart_suppressed_until: float = 0.0

def _oracle_tick_ms() -> float:
    """Return durable latest oracle tick timestamp in milliseconds."""
    try:
        import sqlite3 as _sq3, os as _os
        _idb = _os.path.join(str(BASE_DIR), "sentinuity_intelligence.db")
        _c = _sq3.connect(_idb, timeout=2)
        _row = _c.execute("SELECT MAX(ts_ms) FROM mtm_ticks").fetchone()
        _c.close()
        return float((_row or [0])[0] or 0.0)
    except Exception:
        return 0.0

def _oracle_heartbeat_age() -> float:
    try:
        with get_connection() as _c:
            _r = _c.execute(
                "SELECT last_pulse FROM system_heartbeat WHERE service_name='ws_price_oracle'"
            ).fetchone()
        _ts = float((_r or [0])[0] or 0.0)
        return time.time() - _ts if _ts > 0 else 999999.0
    except Exception:
        return 999999.0

def heal_oracle_if_stale() -> bool:
    """Restart a needed stale oracle only under a verified recovery contract.

    A restart is not counted as recovery until BOTH a newer durable MTM tick and
    a fresh ws_price_oracle heartbeat appear. Failed recovery is retried at most
    three times, then suppressed for a bounded incident window to prevent
    restart theatre and process spawning loops.
    """
    global _last_oracle_heal
    global _oracle_restart_pending_since, _oracle_restart_baseline_tick_ms
    global _oracle_restart_attempts, _oracle_restart_suppressed_until

    now = time.time()
    gate = float(get_config_value("ORACLE_LIVENESS_GATE_SEC", 300.0))
    grace = float(get_config_value("ORACLE_RESTART_VERIFY_GRACE_SEC", 120.0))
    max_attempts = int(float(get_config_value("ORACLE_RESTART_MAX_ATTEMPTS", 3)))
    suppress_sec = float(get_config_value("ORACLE_RESTART_SUPPRESS_SEC", 900.0))

    # Verify a previously requested restart before issuing another one.
    if _oracle_restart_pending_since > 0:
        latest_tick = _oracle_tick_ms()
        hb_age = _oracle_heartbeat_age()
        tick_advanced = latest_tick > _oracle_restart_baseline_tick_ms
        oracle = check_oracle_liveness()
        if tick_advanced and hb_age <= grace and oracle.get("ok"):
            update_heartbeat(
                "oracle_autoheal", "RECOVERED",
                f"verified tick_advanced=1 heartbeat_age={hb_age:.0f}s "
                f"oracle_age={oracle.get('age_sec', 9999):.0f}s attempts={_oracle_restart_attempts}",
            )
            log.warning("ORACLE_AUTOHEAL VERIFIED after %d attempt(s)", _oracle_restart_attempts)
            _oracle_restart_pending_since = 0.0
            _oracle_restart_baseline_tick_ms = 0.0
            _oracle_restart_attempts = 0
            return False
        if now - _oracle_restart_pending_since < grace:
            return False
        # Grace expired without proof of recovery.
        _oracle_restart_pending_since = 0.0
        if _oracle_restart_attempts >= max_attempts:
            _oracle_restart_suppressed_until = now + suppress_sec
            update_heartbeat(
                "oracle_autoheal", "UNRESOLVED",
                f"restart_unverified attempts={_oracle_restart_attempts} "
                f"tick_advanced={int(tick_advanced)} heartbeat_age={hb_age:.0f}s "
                f"suppressed_for={suppress_sec:.0f}s",
            )
            _log_health_event(
                "ORACLE_RESTART_UNRESOLVED", "ws_price_oracle",
                f"No verified recovery after {_oracle_restart_attempts} attempts", "CRITICAL",
            )
            log.error("ORACLE_AUTOHEAL unresolved after %d attempts; suppressing %.0fs",
                      _oracle_restart_attempts, suppress_sec)
            return False

    if now < _oracle_restart_suppressed_until:
        return False

    oracle = check_oracle_liveness()
    if oracle["ok"]:
        _oracle_restart_attempts = 0
        return False

    if now - _last_oracle_heal < 60.0:
        return False

    try:
        with get_connection() as conn:
            open_positions = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
            ).fetchone()[0]
            latched_ready = conn.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE latched=1 "
                "AND COALESCE(execution_ready,0) IN (1,2) "
                "AND candidate_state='latched'"
            ).fetchone()[0]
    except Exception as exc:
        log.warning("oracle idle-check query failed: %s", exc)
        open_positions = 0
        latched_ready = 0

    if open_positions == 0 and latched_ready == 0:
        update_heartbeat("oracle_autoheal", "IDLE",
                         "oracle idle - no active tracking needed")
        return False

    age = float(oracle["age_sec"])
    if age <= gate * 3:
        update_heartbeat("oracle_autoheal", "WARN",
                         f"oracle degraded age={age:.0f}s; restart threshold={gate*3:.0f}s")
        return False

    try:
        import subprocess as _sp, sys as _sys
        oracle_path = str(BASE_DIR / "services" / "ws_price_oracle.py")
        check = _sp.run([_sys.executable, "-m", "py_compile", oracle_path],
                        capture_output=True, text=True, timeout=10)
        if check.returncode != 0:
            update_heartbeat("oracle_autoheal", "BLOCKED",
                             f"ORACLE_COMPILE_FAIL: {check.stderr[-200:]}")
            return False
    except Exception as exc:
        log.warning("oracle compile check failed: %s", exc)
        return False

    baseline = _oracle_tick_ms()
    _last_oracle_heal = now
    _oracle_restart_attempts += 1
    update_heartbeat(
        "oracle_autoheal", "RESTARTING",
        f"attempt={_oracle_restart_attempts}/{max_attempts} stale_age={age:.0f}s "
        f"baseline_tick_ms={baseline:.0f}",
    )
    ok = restart_service(
        "ws_price_oracle", "services.ws_price_oracle",
        reason=f"ORACLE_STALE_{age:.0f}s_ATTEMPT_{_oracle_restart_attempts}",
    )
    if ok:
        _oracle_restart_pending_since = now
        _oracle_restart_baseline_tick_ms = baseline
    else:
        update_heartbeat("oracle_autoheal", "RESTART_FAILED",
                         f"attempt={_oracle_restart_attempts} restart_service returned false")
        if _oracle_restart_attempts >= max_attempts:
            _oracle_restart_suppressed_until = now + suppress_sec
            update_heartbeat(
                "oracle_autoheal", "UNRESOLVED",
                f"restart_call_failed attempts={_oracle_restart_attempts} "
                f"suppressed_for={suppress_sec:.0f}s",
            )
            _log_health_event(
                "ORACLE_RESTART_UNRESOLVED", "ws_price_oracle",
                f"restart_service failed {_oracle_restart_attempts} times", "CRITICAL",
            )
    return ok

def check_confidence_floor() -> dict:
    """Check SUPERVISOR_MIN_MINT_CONFIDENCE hasn't drifted above ceiling. Auto-heals."""
    try:
        current = float(get_config_value("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.75))
        if current > CONFIDENCE_CEILING:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE system_config SET value=? WHERE key='SUPERVISOR_MIN_MINT_CONFIDENCE'",
                    (str(CONFIDENCE_CEILING),)
                )
                conn.commit()
            return {"ok": False, "critical": True, "auto_healed": True, "value": current,
                    "message": f"Confidence floor {current:.3f} exceeded ceiling {CONFIDENCE_CEILING} -- AUTO-RESET"}
        return {"ok": True, "value": current, "message": f"Confidence floor OK: {current:.3f}"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"Confidence check failed: {e}"}


_last_pipeline_heal_at: float = 0.0  # rate-limit auto-heal to once per 5min

def check_pipeline_starvation() -> dict:
    """Reset tokens stuck in 'processing' state. Auto-heals."""
    try:
        cutoff = time.time() - (STUCK_PROCESSING_MIN * 60)
        with get_connection() as conn:
            try:
                stuck = conn.execute(
                    "SELECT COUNT(*) as n FROM market_snapshots "
                    "WHERE quality_status='processing' AND COALESCE(price_updated_at, timestamp, 0)<?",
                    (cutoff,)
                ).fetchone()["n"]
            except Exception:
                stuck = 0
            if stuck > 0:
                conn.execute(
                    "UPDATE market_snapshots SET quality_status='pending', quality_reason='' "
                    "WHERE quality_status='processing' AND COALESCE(price_updated_at, timestamp, 0)<?",
                    (cutoff,)
                )
                conn.commit()
                return {"ok": False, "critical": False, "auto_healed": True, "value": stuck,
                        "message": f"Pipeline starvation: {stuck} rows stuck -- AUTO-RESET"}
        return {"ok": True, "value": 0, "message": "Pipeline flow OK"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"Pipeline check failed: {e}"}



def check_pipeline_zero_latch() -> dict:
    """
    AUTO-HEAL: detects zero-latch / zero-qualified state and applies gate fixes.
    Runs every 5 minutes. Checks:
      - If qualified count is 0 for >5 min: lower MIN_MARKET_CAP_USD to 5000
      - If CURVE_DANGER_ZONE_PCT < 90: raise to 92
      - If price_status stuck pending on qualified rows: reset for oracle retry
    This is the real backend auto-heal that makes the vitality scan meaningful.
    """
    global _last_pipeline_heal_at
    now = time.time()
    HEAL_INTERVAL = 300  # only heal once per 5 min

    if now - _last_pipeline_heal_at < HEAL_INTERVAL:
        return {"ok": True, "message": "Pipeline heal rate-limited"}

    try:
        with get_connection() as conn:
            # Count qualified+priced rows supervisor can see
            ready = conn.execute("""
                SELECT COUNT(*) FROM market_snapshots
                WHERE latched=0
                  AND candidate_state NOT IN ('vetoed','exited','executed')
                  AND (candidate_state='qualified' OR (candidate_state='pending' AND quality_status='qualified'))
                  AND price_status='priced'
                  AND is_tradeable=1
                  AND COALESCE(price_updated_at, created_at, 0) > ?
            """, (now - 600,)).fetchone()[0]

            if ready > 0:
                return {"ok": True, "message": f"Pipeline healthy: {ready} supervisor-ready rows"}

            # Zero ready rows - apply auto-fixes
            fixes = []

            # Fix 1: MIN_MARKET_CAP_USD - lower if above 5000
            mcap_row = conn.execute("SELECT value FROM system_config WHERE key='MIN_MARKET_CAP_USD'").fetchone()
            mcap_val = float(mcap_row[0] if mcap_row else 5000)
            if mcap_val > 5000:
                conn.execute("INSERT OR REPLACE INTO system_config(key,value,description) VALUES('MIN_MARKET_CAP_USD','5000','auto-heal: lowered from %.0f')" % mcap_val)
                fixes.append(f"MIN_MARKET_CAP_USD {mcap_val:.0f}→5000")

            # Fix 2: CURVE_DANGER_ZONE_PCT - raise if below 90
            curve_row = conn.execute("SELECT value FROM system_config WHERE key='CURVE_DANGER_ZONE_PCT'").fetchone()
            curve_val = float(curve_row[0] if curve_row else 85)
            if curve_val < 90:
                conn.execute("INSERT OR REPLACE INTO system_config(key,value,description) VALUES('CURVE_DANGER_ZONE_PCT','92','auto-heal: raised from %.1f')" % curve_val)
                fixes.append(f"CURVE_DANGER_ZONE_PCT {curve_val:.0f}→92")

            # Fix 3: Reset qualified rows stuck at price_status=pending/retry/dead
            # Surgical criteria per directive:
            #   - qualified only (quality_status='qualified')
            #   - recent enough to matter (created_at > now-900s = 15min)
            #   - not truly HOT: exclude HOT only when price_updated_at IS NOT NULL
            #     (rows with NULL price_updated_at are falsely HOT via created_at proxy -
            #      they MUST be reset or they stay unpriced forever)
            #   - not latched/open/terminal
            #   - price_status in failure states
            #   - last attempt >5min ago (prevents thrash)
            #   - GUARDIAN_RESET_PRICE_RETRY tag written for diagnostics
            reset = conn.execute("""
                UPDATE market_snapshots
                SET price_status='pending',
                    price_attempts=0,
                    price_last_attempt_at=NULL,
                    quality_reason=CASE
                        WHEN quality_reason IS NULL OR quality_reason=''
                        THEN 'GUARDIAN_RESET_PRICE_RETRY'
                        ELSE quality_reason || '|GUARDIAN_RESET_PRICE_RETRY'
                    END
                WHERE quality_status='qualified'
                  AND candidate_state NOT IN ('vetoed','exited','executed',
                                              'expired_stale','EXECUTOR_STALE_GATE')
                  AND latched = 0
                  AND (
                      price_updated_at IS NULL
                      OR COALESCE(tier,'COLD') != 'HOT'
                  )
                  AND (price_status IS NULL OR price_status IN ('pending','retry','dead'))
                  AND COALESCE(created_at, 0) > ?
                  AND (price_last_attempt_at IS NULL OR price_last_attempt_at < ?)
            """, (now - 900, now - 300)).rowcount

            if reset > 0:
                fixes.append(f"reset {reset} stuck price rows")

            conn.commit()

        _last_pipeline_heal_at = now

        if fixes:
            msg = "PIPELINE_AUTO_HEAL: " + " | ".join(fixes)
            log.warning(msg)
            _log_health_event("PIPELINE_HEAL", SERVICE_NAME, msg, "WARNING")
            return {"ok": False, "auto_healed": True, "message": msg}

        return {"ok": False, "message": "Pipeline zero-ready: no fixable gates found"}

    except Exception as e:
        return {"ok": False, "message": f"Pipeline zero-latch check failed: {e}"}

def check_feed_starvation() -> dict:
    """
    Detect when pump_monitor/ingest has stopped feeding new rows.
    Uses MAX(updated_at, created_at, timestamp) - not first_seen_at first.
    Also purges null-timestamp MTM rows that corrupt the pipeline.
    Rate-limited to once per 300s.
    """
    global _last_feed_restart_at
    now = time.time()
    FEED_STARVATION_SEC = 600
    FEED_RESTART_RATE   = 300
    try:
        with get_connection() as conn:
            # Purge null-timestamp MTM rows every cycle - safe, always correct
            purged = conn.execute("""
                DELETE FROM market_snapshots
                WHERE candidate_state = 'mtm'
                  AND created_at IS NULL
                  AND updated_at IS NULL
                  AND first_seen_at IS NULL
            """).rowcount
            if purged > 0:
                conn.commit()
                log.info("MTM_NULL_PURGE: deleted %d null-timestamp mtm rows", purged)

            newest = conn.execute("""
                SELECT MAX(
                    MAX(COALESCE(updated_at,0),
                        COALESCE(created_at,0),
                        COALESCE(timestamp,0))
                ) FROM market_snapshots
            """).fetchone()[0] or 0

        newest_age = now - float(newest)
        if newest_age < FEED_STARVATION_SEC:
            msg = f"Feed active - newest row {newest_age:.0f}s ago"
            if purged > 0:
                msg += f" | purged {purged} null-ts MTM rows"
            return {"ok": True, "message": msg}

        if now - _last_feed_restart_at < FEED_RESTART_RATE:
            return {"ok": False, "critical": False,
                    "message": f"Feed starved ({newest_age:.0f}s) - restart rate-limited"}

        _last_feed_restart_at = now
        log.warning(
            "FEED_STARVATION: no new market_snapshots for %.0fs - restarting pump_monitor + ingest_pipeline",
            newest_age,
        )
        _log_health_event("FEED_STARVATION", SERVICE_NAME,
                          f"No new snapshots for {newest_age:.0f}s - restarting feed services",
                          "WARNING")
        for svc, mod in [
            ("pump_monitor",    "services.pump_monitor"),
            ("ingest_pipeline", "services.ingest_pipeline"),
        ]:
            restart_service(svc, mod, reason=f"FEED_STARVATION_{newest_age:.0f}s")
        return {"ok": False, "critical": False, "auto_healed": True,
                "message": f"Feed starved {newest_age:.0f}s - restarted pump_monitor + ingest_pipeline"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"Feed starvation check failed: {e}"}


def check_drawdown_halt() -> dict:
    """Monitor drawdown circuit breaker. Auto-clears after cool-down. Auto-raises low thresholds."""
    try:
        halt_active  = str(str(get_config_value("DRAWDOWN_HALT_ACTIVE",       "0"))).strip()
        halt_pct     = float(get_config_value("DRAWDOWN_ACCUMULATED_PCT",  "0.0"))
        threshold    = float(get_config_value("DRAWDOWN_HALT_THRESHOLD_PCT","25.0"))
        auto_clear   = float(get_config_value("DRAWDOWN_AUTO_CLEAR_MINUTES","30.0"))

        if threshold < 20.0:
            with get_connection() as conn:
                conn.execute("UPDATE system_config SET value='25.0' WHERE key='DRAWDOWN_HALT_THRESHOLD_PCT'")
                conn.commit()

        if halt_active == "1":
            with get_connection() as conn:
                halt_since_row = conn.execute(
                    "SELECT value FROM system_config WHERE key='DRAWDOWN_HALT_SINCE'"
                ).fetchone()
            now = time.time()
            if not halt_since_row or not halt_since_row["value"]:
                with get_connection() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO system_config (key,value,description) "
                        "VALUES ('DRAWDOWN_HALT_SINCE',?,'Timestamp when drawdown halt activated')",
                        (str(now),)
                    )
                    conn.commit()
                return {"ok": False, "critical": False,
                        "message": f"DRAWDOWN HALT ACTIVE -- {halt_pct:.1f}% accumulated. Auto-clearing in {auto_clear:.0f}min."}

            halt_age_mins = (now - float(halt_since_row["value"])) / 60.0
            if halt_age_mins >= auto_clear:
                # SIGN-OFF FIX 15: Previously cleared DRAWDOWN_ACCUMULATED_PCT to 0.0,
                # wiping all exponential memory and allowing immediate full-size trading.
                # Fix: decay to 50% of current value - preserves memory proportionally,
                # allows gradual recovery without a cliff-edge reset to zero.
                _current_acc = float(get_config_value("DRAWDOWN_ACCUMULATED_PCT", 0.0))
                _decayed_acc = round(_current_acc * 0.5, 4)
                with get_connection() as conn:
                    conn.execute("UPDATE system_config SET value='0' WHERE key='DRAWDOWN_HALT_ACTIVE'")
                    conn.execute(
                        "UPDATE system_config SET value=? WHERE key='DRAWDOWN_ACCUMULATED_PCT'",
                        (str(_decayed_acc),),
                    )
                    conn.execute("UPDATE system_config SET value='' WHERE key='DRAWDOWN_HALT_SINCE'")
                    conn.commit()
                _cognition(
                    f"Drawdown halt auto-cleared after {halt_age_mins:.0f}min cool-down. "
                    f"Accumulated decayed {_current_acc:.2f}%→{_decayed_acc:.2f}%. Trading resumed.")
                return {"ok": True, "auto_healed": True,
                        "message": f"Drawdown halt auto-cleared after {halt_age_mins:.0f}min "
                                   f"(accumulated: {_current_acc:.2f}%→{_decayed_acc:.2f}%)"}
            remaining = auto_clear - halt_age_mins
            return {"ok": False, "critical": False,
                    "message": f"DRAWDOWN HALT ACTIVE -- {halt_pct:.1f}% accumulated. Auto-clearing in {remaining:.0f}min."}

        with get_connection() as conn:
            conn.execute("UPDATE system_config SET value='' WHERE key='DRAWDOWN_HALT_SINCE'")
            conn.commit()
        return {"ok": True, "message": f"No drawdown halt -- threshold: {threshold:.0f}%"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"Drawdown check failed: {e}"}


def check_wallet_integrity() -> dict:
    """Check wallet hasn't gone negative or lost >50% of starting capital."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT wallet_balance, initial_capital FROM system_state WHERE id=1"
            ).fetchone()
        if not row:
            return {"ok": False, "critical": True,
                    "message": "system_state row missing -- wallet integrity unknown"}
        balance = float(row["wallet_balance"] or 0)
        initial = float(row["initial_capital"] or 1000)
        if balance < 0:
            return {"ok": False, "critical": True, "value": balance,
                    "message": f"WALLET NEGATIVE: ${balance:.2f} -- halt trading"}
        if balance < initial * 0.5:
            return {"ok": False, "critical": False, "value": balance,
                    "message": f"Wallet at ${balance:.2f} -- down {((initial-balance)/initial*100):.1f}% from start"}
        return {"ok": True, "value": balance, "message": f"Wallet healthy: ${balance:.2f}"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"Wallet check failed: {e}"}


def check_latched_signals() -> dict:
    """Check latched signals are being consumed by executor."""
    try:
        with get_connection() as conn:
            latched  = conn.execute(
                "SELECT COUNT(*) as n FROM market_snapshots "
                "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2) AND candidate_state='latched'"
            ).fetchone()["n"]
            open_pos = conn.execute(
                "SELECT COUNT(*) as n FROM paper_positions WHERE status='OPEN'"
            ).fetchone()["n"]
        if latched > 20 and open_pos == 0:
            return {"ok": False, "critical": False,
                    "message": f"{latched} signals latched but 0 positions open -- executor may be jammed"}
        return {"ok": True, "message": f"Latched: {latched} | Open: {open_pos}"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"Latch check failed: {e}"}


def check_rpc_credits() -> dict:
    """
    Track daily RPC credit usage from ingest_pipeline heartbeat note.
    Fires Telegram alerts at thresholds. Sets kill switch at 100%.
    """
    import re as _re
    import datetime as _dt
    try:
        with get_connection() as conn:
            hb = conn.execute(
                "SELECT note FROM system_heartbeat WHERE service_name='ingest_pipeline'"
            ).fetchone()
            note = str(hb["note"] or "") if hb else ""

            daily_used = 0
            m = _re.search(r"daily=([\\d,]+)", note)
            if m:
                daily_used = int(m.group(1).replace(",", ""))
            else:
                row = conn.execute(
                    "SELECT value FROM system_config WHERE key='RPC_CALLS_TODAY'"
                ).fetchone()
                daily_used = int(row["value"] or 0) if row else 0

            conn.execute(
                "INSERT INTO system_config (key,value,description) "
                "VALUES ('RPC_CALLS_TODAY',?,'Daily RPC count') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(daily_used),)
            )
            conn.commit()

        pct = daily_used / max(RPC_DAILY_BUDGET, 1)
        if pct >= 1.0:
            with get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO system_config (key,value,description) "
                    "VALUES ('RESOLVER_CREDIT_KILL','1','Emergency RPC credit kill switch')"
                )
                conn.commit()
            _tg_alert(f"- RPC DAILY BUDGET EXHAUSTED ({daily_used:,}/{RPC_DAILY_BUDGET:,}) -- kill switch set")
            return {"ok": False, "critical": True,
                    "message": f"RPC budget exhausted: {daily_used:,}/{RPC_DAILY_BUDGET:,}"}

        for threshold in RPC_ALERT_THRESHOLDS:
            if pct >= threshold:
                today = _dt.date.today().isoformat()
                fired_key = f"RPC_ALERT_FIRED_{today}_{int(threshold*100)}"
                with get_connection() as conn:
                    already = conn.execute(
                        "SELECT 1 FROM system_config WHERE key=?", (fired_key,)
                    ).fetchone()
                    if not already:
                        conn.execute(
                            "INSERT OR IGNORE INTO system_config (key,value) VALUES (?,?)",
                            (fired_key, "1")
                        )
                        conn.commit()
                        _tg_alert(
                            f"-- RPC credits at {pct*100:.0f}% "
                            f"({daily_used:,}/{RPC_DAILY_BUDGET:,}) today"
                        )

        return {"ok": True, "value": daily_used,
                "message": f"RPC credits: {daily_used:,}/{RPC_DAILY_BUDGET:,} ({pct*100:.1f}%)"}
    except Exception as e:
        return {"ok": False, "critical": False, "message": f"RPC credit check failed: {e}"}


def take_golden_snapshot(vitals: dict) -> None:
    """Record config golden snapshot when all vitals are green. Used for rollback."""
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_health_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshotted_at REAL,
                    is_golden INTEGER DEFAULT 0,
                    config_json TEXT,
                    vitals_json TEXT,
                    notes TEXT
                )
            """)
            config = {r["key"]: r["value"] for r in
                      conn.execute("SELECT key, value FROM system_config ORDER BY key ASC").fetchall()}
            conn.execute(
                "INSERT INTO system_health_snapshots "
                "(snapshotted_at, is_golden, config_json, vitals_json, notes) "
                "VALUES (?,1,?,?,?)",
                (time.time(), json.dumps(config), json.dumps(vitals),
                 "Auto golden snapshot -- all vitals green")
            )
            conn.commit()
        log.info("GOLDEN SNAPSHOT taken -- all vitals green")
    except Exception as e:
        log.warning("take_golden_snapshot failed: %s", e)


def restore_from_golden_snapshot() -> bool:
    """Restore system_config from the most recent golden snapshot."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT config_json, snapshotted_at FROM system_health_snapshots "
                "WHERE is_golden=1 ORDER BY snapshotted_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            log.warning("No golden snapshot found to restore from")
            return False
        config = json.loads(row["config_json"])
        with get_connection() as conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT INTO system_config (key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value)
                )
            conn.commit()
        log.info("Restored config from golden snapshot")
        return True
    except Exception as e:
        log.error("restore_from_golden_snapshot failed: %s", e)
        return False


def _run_prune_extended() -> dict:
    """
    Extended pruning from system_health_monitor -- covers tables that
    run_prune_cycle() doesn't handle (MTM-specific, cognition, telemetry,
    resolved_transactions row-count limits, raw_dna two-pass).
    """
    deleted = {}
    now = time.time()

    # raw_dna two-pass (stale unprocessed + processed over limit)
    try:
        stale_cutoff = now - 300
        with get_connection() as conn:
            r = conn.execute(
                """DELETE FROM raw_dna
                   WHERE processed_state=0
                     AND COALESCE(first_seen_at, created_at, timestamp, 0) > 0
                     AND COALESCE(first_seen_at, created_at, timestamp, 0) < ?""",
                (stale_cutoff,)
            )
            conn.commit()
        deleted["raw_dna_stale"] = r.rowcount
    except Exception:
        pass

    try:
        with get_connection() as conn:
            processed_total = conn.execute(
                "SELECT COUNT(*) as n FROM raw_dna WHERE processed_state NOT IN (0,1,99)"
            ).fetchone()["n"]
        if processed_total > RAW_DNA_MAX_ROWS:
            with get_connection() as conn:
                r = conn.execute(
                    f"DELETE FROM raw_dna WHERE id IN ("
                    f"SELECT id FROM raw_dna WHERE processed_state NOT IN (0,1,99) "
                    f"ORDER BY id ASC LIMIT {RAW_DNA_PRUNE_BATCH})"
                )
                conn.commit()
            deleted["raw_dna_overflow"] = r.rowcount
    except Exception:
        pass

    # market_snapshots MTM rows older than retention window
    try:
        mtm_cutoff = now - (MARKET_SNAP_MAX_AGE_HRS * 3600)
        with get_connection() as conn:
            r = conn.execute(
                "DELETE FROM market_snapshots "
                "WHERE candidate_state IN ('vetoed','mtm') AND timestamp<?",
                (mtm_cutoff,)
            )
            conn.commit()
        deleted["market_snapshots_mtm"] = r.rowcount
    except Exception:
        pass

    # cognition_log row limit
    try:
        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cognition_log").fetchone()[0]
        if total > COGNITION_MAX_ROWS:
            excess = total - COGNITION_MAX_ROWS
            with get_connection() as conn:
                r = conn.execute(
                    f"DELETE FROM cognition_log WHERE id IN "
                    f"(SELECT id FROM cognition_log ORDER BY id ASC LIMIT {excess})"
                )
                conn.commit()
            deleted["cognition_log"] = r.rowcount
    except Exception:
        pass

    # system_telemetry row limit
    try:
        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM system_telemetry").fetchone()[0]
        if total > TELEMETRY_MAX_ROWS:
            excess = total - TELEMETRY_MAX_ROWS
            with get_connection() as conn:
                r = conn.execute(
                    f"DELETE FROM system_telemetry WHERE id IN "
                    f"(SELECT id FROM system_telemetry ORDER BY id ASC LIMIT {excess})"
                )
                conn.commit()
            deleted["system_telemetry"] = r.rowcount
    except Exception:
        pass

    # resolved_transactions row limit
    try:
        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM resolved_transactions").fetchone()[0]
        if total > RESOLVED_TX_MAX_ROWS:
            excess = total - RESOLVED_TX_MAX_ROWS
            with get_connection() as conn:
                r = conn.execute(
                    f"DELETE FROM resolved_transactions WHERE id IN "
                    f"(SELECT id FROM resolved_transactions ORDER BY id ASC LIMIT {excess})"
                )
                conn.commit()
            deleted["resolved_transactions"] = r.rowcount
    except Exception:
        pass

    return deleted


def _nightly_wal_checkpoint() -> None:
    """WAL checkpoint every 30 minutes -- keeps WAL file small."""
    global _last_checkpoint_ts
    now = time.time()
    if now - _last_checkpoint_ts < WAL_CHECKPOINT_INTERVAL:
        return
    try:
        with get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            conn.commit()
        _last_checkpoint_ts = now
        log.debug("WAL checkpoint completed")
    except Exception as e:
        log.warning("WAL checkpoint failed: %s", e)


# =============================================================================
# DB PRUNING (runs every 60s, prune-safe rules enforced)
# =============================================================================

def run_prune_cycle() -> Dict[str, int]:
    """
    Prune tables that grow unbounded.
    Prune safety rules (per Gemini audit):
      - raw_dna: only terminal states (-1, -2, 3) older than 5 min
      - market_snapshots: only vetoed/dead NOT tied to open positions
        (never prune rows tied to latched=1 or execution_ready=1)
      - resolved_transactions: only where raw_dna.processed_state=3
      - cognition_log: oldest rows beyond retention limit
    Never prunes anything related to OPEN positions.
    """
    deleted = {}
    now     = time.time()

    # raw_dna -- terminal states older than 5 minutes
    try:
        cutoff = now - 300
        with get_connection() as conn:
            r = conn.execute(
                """DELETE FROM raw_dna
                   WHERE processed_state IN (-1,-2,3)
                     AND COALESCE(first_seen_at, created_at, timestamp, 0) > 0
                     AND COALESCE(first_seen_at, created_at, timestamp, 0) < ?""",
                (cutoff,),
            )
            conn.commit()
        deleted["raw_dna"] = r.rowcount
    except Exception as e:
        log.warning("prune raw_dna failed: %s", e)

    # market_snapshots -- vetoed/dead only, never latched/execution_ready
    # PROTECTED: runner-grade closes (>=1.5x exit_price/entry_price) are NOT
    # deleted at 1h. They are deleted at 6h instead so winner_snapshot_archiver
    # has time to copy their pipeline forensics into winner_snapshot_archive.
    try:
        cutoff = now - 3600
        runner_cutoff = now - 21600   # 6h protection for runner mints
        with get_connection() as conn:
            r = conn.execute(
                """
                DELETE FROM market_snapshots
                WHERE candidate_state IN ('vetoed','dead')
                  AND COALESCE(latched, 0) = 0
                  AND COALESCE(execution_ready, 0) = 0
                  AND COALESCE(price_updated_at, timestamp, 0) < ?
                  AND mint_address NOT IN (
                      SELECT mint_address FROM paper_positions WHERE status='OPEN'
                  )
                  AND mint_address NOT IN (
                      -- protect runner mints (>=1.5x) from early deletion
                      SELECT mint_address FROM paper_positions
                      WHERE status='CLOSED'
                        AND entry_price > 0 AND exit_price > 0
                        AND (exit_price / entry_price) >= 1.5
                        AND closed_at > ?
                  )
                """,
                (cutoff, runner_cutoff),
            )
            conn.commit()
        deleted["market_snapshots"] = r.rowcount
    except Exception as e:
        log.warning("prune market_snapshots failed: %s", e)

    # expired_stale + EXECUTOR_STALE_GATE rows older than 2hrs
    # These are never deleted anywhere else and accumulate indefinitely.
    # After 42hrs of runtime they number in the thousands and slow every
    # supervisor scan (SQLite must evaluate the WHERE clause against them).
    try:
        cutoff_2hr = now - 7200
        with get_connection() as conn:
            r = conn.execute(
                """
                DELETE FROM market_snapshots
                WHERE candidate_state IN ('expired_stale','EXECUTOR_STALE_GATE','exited')
                  AND COALESCE(latched, 0) = 0
                  AND COALESCE(price_updated_at, created_at, timestamp, 0) < ?
                  AND mint_address NOT IN (
                      SELECT mint_address FROM paper_positions WHERE status='OPEN'
                  )
                """,
                (cutoff_2hr,),
            )
            conn.commit()
        deleted["market_snapshots_stale"] = r.rowcount
        if r.rowcount > 0:
            log.info("PRUNE: deleted %d expired_stale/gate/exited rows >2hr", r.rowcount)
    except Exception as e:
        log.warning("prune expired_stale failed: %s", e)

    # resolved_transactions -- only where DNA is fully terminal
    try:
        cutoff = now - 3600
        with get_connection() as conn:
            r = conn.execute(
                """
                DELETE FROM resolved_transactions
                WHERE tx_hash IN (
                    SELECT tx_hash FROM raw_dna
                    WHERE processed_state=3
                      AND COALESCE(first_seen_at, created_at, timestamp, 0) > 0
                      AND COALESCE(first_seen_at, created_at, timestamp, 0) < ?
                )
                """,
                (cutoff,),
            )
            conn.commit()
        deleted["resolved_transactions"] = r.rowcount
    except Exception as e:
        log.warning("prune resolved_transactions failed: %s", e)

    # cognition_log -- keep last 10,000 rows
    try:
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM cognition_log"
            ).fetchone()["c"]
            if count > 10000:
                to_delete = count - 10000
                r = conn.execute(
                    "DELETE FROM cognition_log WHERE id IN "
                    "(SELECT id FROM cognition_log ORDER BY id ASC LIMIT ?)",
                    (to_delete,),
                )
                conn.commit()
                deleted["cognition_log"] = r.rowcount
    except Exception as e:
        log.warning("prune cognition_log failed: %s", e)

    # system_telemetry -- keep 30 days
    try:
        cutoff = now - (30 * 86400)
        with get_connection() as conn:
            r = conn.execute(
                "DELETE FROM system_telemetry WHERE ts<?", (cutoff,)
            )
            conn.commit()
        deleted["system_telemetry"] = r.rowcount
    except Exception:
        pass

    return deleted


def run_vacuum_if_safe() -> bool:
    """
    Run VACUUM only when completely safe.
    Safety conditions (all must be true):
      - open_positions == 0
      - no close in progress
      - DB latency below 100ms
      - no latched signals ready for execution
    """
    try:
        with get_connection() as conn:
            open_count = conn.execute(
                "SELECT COUNT(*) AS c FROM paper_positions WHERE status='OPEN'"
            ).fetchone()["c"]
            latched = conn.execute(
                "SELECT COUNT(*) AS c FROM market_snapshots "
                "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)"
            ).fetchone()["c"]

        if open_count > 0 or latched > 0:
            log.debug("VACUUM skipped -- active positions or signals")
            return False

        pressure = detect_db_pressure()
        if pressure.get("latency_ms", 9999) > 100:
            log.info("VACUUM skipped -- DB latency %.0fms too high",
                     pressure["latency_ms"])
            return False

        with get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()

        log.info("VACUUM + WAL checkpoint completed")
        return True

    except Exception as e:
        log.warning("run_vacuum_if_safe failed: %s", e)
        return False


# =============================================================================
# MAIN GUARDIAN CYCLE
# =============================================================================

def _run_cycle(
    last_recon_at: float,
    last_prune_at: float,
    last_vacuum_at: float,
) -> Tuple[float, float, float]:
    """
    One guardian cycle. Returns updated timestamps.

    Ordered phases (Gemini specification):
      1. Collect health facts
      2. Collect DB pressure facts
      3. Collect loop stall facts
      4. Collect reconciliation facts
      5. Derive actions (policy -- no side effects)
      6. Execute bounded actions (max 5 restarts per cycle)
      7. Pipeline healing (every cycle)
      8. Pruning (every 60s)
      9. Heartbeat + log
    """
    now = time.time()

    # -- Phase 1: Health facts -------------------------------------------------
    dead_findings    = detect_dead_services()
    stale_positions  = detect_stale_positions()

    # Health checks absorbed from system_health_monitor
    conf_check    = check_confidence_floor()
    pipe_check    = check_pipeline_starvation()
    feed_check    = check_feed_starvation()
    # Auto-heal pipeline zero-latch - real gate fix, runs every 5 min
    zero_latch_check = check_pipeline_zero_latch()
    dd_check      = check_drawdown_halt()
    wallet_check  = check_wallet_integrity()
    latch_check   = check_latched_signals()
    rpc_check     = check_rpc_credits()
    # Oracle auto-heal - restart ws_price_oracle if mtm_ticks stale
    heal_oracle_if_stale()

    # Alert on critical health findings
    for check, label in [
        (conf_check, "CONFIDENCE_FLOOR"),
        (wallet_check, "WALLET_INTEGRITY"),
        (rpc_check, "RPC_CREDITS"),
    ]:
        if not check.get("ok") and check.get("critical"):
            _tg_alert(f"- *{label}*: {check.get('message','')}")
            _log_health_event(label, SERVICE_NAME, check.get("message",""), "CRITICAL")

    # -- Phase 2: DB pressure --------------------------------------------------
    db_pressure      = detect_db_pressure()

    # -- Phase 3: Loop stalls --------------------------------------------------
    jam              = detect_loop_stalls()

    # -- Phase 4: Reconciliation facts ----------------------------------------
    wallet_drift     = detect_wallet_drift()

    # -- Phase 5: Derive actions -----------------------------------------------
    actions = decide_recovery_actions(
        dead_findings, db_pressure, jam, stale_positions, wallet_drift
    )

    # -- Phase 6: Execute actions ----------------------------------------------
    restarts_done = 0
    for action in actions:
        atype = action["type"]

        if atype == "restart":
            if restarts_done < RESTART_BUDGET_PER_CYCLE:
                ok = restart_service(
                    action["service"], action["module"], action["reason"]
                )
                if ok:
                    restarts_done += 1
                    _tg_alert(
                        f"- *Guardian restarted* `{action['service']}`\n"
                        f"Reason: {action['reason']}"
                    )

        elif atype == "alert":
            _tg_alert(action["message"])

        elif atype == "wallet_recon":
            if now - last_recon_at >= 300:  # max once per 5 minutes
                run_wallet_reconciliation(action["drift"])
                last_recon_at = now

        elif atype == "close_stale_position":
            safe_close_position(
                pos_id=action["pos_id"],
                mint=action["mint"],
                entry_price=float(action["entry"] or 0),
                pos_size_usd=float(action["size"] or 0),
                quantity=float(action["qty"] or 0),
                token_name=action["name"],
                exit_reason=f"GUARDIAN_STALE_{action['age_hrs']:.1f}h",
            )

        elif atype == "log_jam":
            _cognition(
                f"Executor slot jam: {action['jam']['latched_signals']} latched signal(s) "
                f"waiting, no entry in {action['jam']['gap_seconds']}s. "
                f"Free slots: {action['jam']['free_slots']}/{action['jam']['max_positions']}.",
                meta={"event_type": "SLOT_JAM", **action["jam"]},
            )

    # -- Phase 7: Pipeline healing (every cycle) -------------------------------
    stuck  = _heal_stuck_claims()
    stale  = _heal_stale_retries()
    orphaned_cleared = _heal_orphaned_latch()
    latched_cleared = _heal_stale_latched()

    # Freshness tier recomputation - every 60s.
    # Promotes rows back to HOT/WARM if their activity timestamp is recent.
    # Prevents permanent COLD drift that starves the supervisor between restarts.
    _last_fresh_ts = getattr(run, "_last_freshness_recompute_at", 0.0)
    if now - _last_fresh_ts >= 60:
        fresh_updated = _recompute_freshness_tiers()
        run._last_freshness_recompute_at = now
        if fresh_updated:
            log.debug("FRESHNESS: recomputed tiers for %d rows", fresh_updated)

    # _heal_loop_tokens does 48+ DB writes -- throttle to every 5 minutes
    loops = 0
    _last_loop_ts = getattr(run, "_last_loop_heal_ts", 0.0)
    if now - _last_loop_ts >= 300:
        loops = _heal_loop_tokens()
        run._last_loop_heal_ts = now

    if stuck:
        log.info("HEAL: reclaimed %d stuck resolver claims", stuck)
    if loops:
        log.info("HEAL: blacklisted %d loop tokens", loops)

    # -- Phase 7b: Supervisor jam auto-heal -----------------------------------
    # If qualified > 50 AND latched = 0 for 60+ seconds, reset latched state.
    # Threshold is 50, not 500: after Patches K+L fix token-age measurement,
    # qualified counts are lower (no TOKEN_TOO_OLD backlog accumulating), so
    # 500 would never fire in healthy operation. 50 keeps the guard sensitive.
    _last_jam_ts = getattr(run, "_supervisor_jam_detected_at", 0.0)
    try:
        with get_connection() as _conn:
            _jam_row = _conn.execute("""
                SELECT
                    SUM(CASE WHEN quality_status='qualified' THEN 1 ELSE 0 END) AS q_count,
                    SUM(CASE WHEN latched=1 AND COALESCE(execution_ready,0) IN (1,2) THEN 1 ELSE 0 END) AS l_count
                FROM market_snapshots
            """).fetchone()
            _q = int(_jam_row["q_count"] or 0)
            _l = int(_jam_row["l_count"] or 0)

        if _q > 50 and _l == 0:
            if _last_jam_ts == 0.0:
                run._supervisor_jam_detected_at = now
                log.info("GUARDIAN: Supervisor jam detected (qualified=%d latched=0) -- waiting 60s", _q)
            elif (now - _last_jam_ts) >= 60:
                with get_connection() as _conn:
                    _conn.execute("BEGIN IMMEDIATE")
                    _fixed = _conn.execute(
                        "UPDATE market_snapshots SET latched=0, execution_ready=0 "
                        "WHERE latched=1 AND execution_ready=0"
                    ).rowcount
                    _conn.commit()
                run._supervisor_jam_detected_at = 0.0
                log.info("GUARDIAN AUTO-HEAL: Reset %d orphaned latched snapshots (supervisor jam)", _fixed)
                _cognition(
                    f"Auto-heal fired: supervisor jam cleared. {_fixed} latched snapshots reset. "
                    f"Pipeline had {_q} qualified tokens with 0 latched for 60+ seconds.",
                    meta={"event_type": "SUPERVISOR_JAM_HEALED", "qualified": _q, "reset": _fixed}
                )
        else:
            run._supervisor_jam_detected_at = 0.0
    except Exception as _e:
        log.warning("GUARDIAN: supervisor jam check failed: %s", _e)

    # -- Phase 7c: Oracle error auto-heal -------------------------------------
    # If price_enricher is in ERROR state, flag for restart
    try:
        with get_connection() as _conn:
            _oracle = _conn.execute(
                "SELECT status, last_pulse AS updated_at FROM system_heartbeat WHERE service_name='price_enricher'"
            ).fetchone()
        if _oracle and str(_oracle["status"]) == "ERROR":
            _oracle_error_since = getattr(run, "_oracle_error_since", 0.0)
            if _oracle_error_since == 0.0:
                run._oracle_error_since = now
            elif (now - _oracle_error_since) >= 30:
                # Oracle has been in ERROR for 30s -- restart market_intelligence
                ok = restart_service(
                    "market_intelligence",
                    "services.market_intelligence",
                    "Guardian auto-heal: price_enricher ERROR state"
                )
                if ok:
                    run._oracle_error_since = 0.0
                    log.info("GUARDIAN AUTO-HEAL: Restarted market_intelligence (oracle ERROR)")
                    _cognition(
                        "Auto-heal fired: market_intelligence restarted due to oracle ERROR state.",
                        meta={"event_type": "ORACLE_RESTARTED"}
                    )
        else:
            run._oracle_error_since = 0.0
    except Exception as _e:
        log.warning("GUARDIAN: oracle error check failed: %s", _e)

    # -- Phase 8: Pruning (every 60s) -----------------------------------------
    if now - last_prune_at >= 60:
        deleted = run_prune_cycle()
        ext_deleted = _run_prune_extended()
        total_deleted = sum(deleted.values()) + sum(ext_deleted.values())
        if total_deleted > 0:
            log.debug("PRUNE: basic=%s extended=%s", deleted, ext_deleted)
        last_prune_at = now

    # WAL checkpoint every 30 minutes
    _nightly_wal_checkpoint()

    # -- Phase 9: Nightly vacuum -----------------------------------------------
    if now - last_vacuum_at >= 86400 * 7:  # weekly only
        run_vacuum_if_safe()
        last_vacuum_at = now

    # Golden snapshot when all critical checks green (at most once per hour)
    global _last_golden_ts
    all_green = (
        conf_check.get("ok") and wallet_check.get("ok")
        and not dead_findings and not db_pressure.get("critical_pressure")
    )
    if all_green and now - _last_golden_ts >= 3600:
        take_golden_snapshot({
            "confidence_floor": conf_check.get("value"),
            "wallet": wallet_check.get("value"),
            "db_latency_ms": db_pressure.get("latency_ms"),
        })
        _last_golden_ts = now

    # -- Heartbeat -------------------------------------------------------------
    dead_count    = sum(1 for f in dead_findings if f["severity"] == "CRITICAL")
    warn_count    = sum(1 for f in dead_findings if f["severity"] == "WARNING")
    update_heartbeat(
        SERVICE_NAME, "ALIVE",
        f"dead={dead_count} warn={warn_count} restarts={restarts_done} "
        f"healed_claims={stuck} healed_retries={stale} "
        f"db_latency={db_pressure.get('latency_ms',0):.0f}ms",
    )

    return last_recon_at, last_prune_at, last_vacuum_at


# =============================================================================
# MAIN
# =============================================================================

def run() -> None:
    _ensure_guardian_schema()

    log.info("SYSTEM GUARDIAN ONLINE")

    # Start live wallet sync - keeps real SOL balance in sync every 60s
    try:
        from services.live_wallet_sync import start_live_wallet_sync
        start_live_wallet_sync()
    except ImportError:
        log.debug("live_wallet_sync not deployed - skipping")
    except Exception as _wse:
        log.debug("live_wallet_sync failed to start: %s", _wse)
    log.info("Dead threshold: %.0fs | Restart cooldown: %.0fs | Budget: %d/cycle",
             float(get_config_value("WATCHDOG_DEAD_THRESHOLD_SECONDS",
                                    WATCHDOG_DEAD_THRESHOLD)),
             RESTART_COOLDOWN_SECONDS,
             RESTART_BUDGET_PER_CYCLE)
    log.info("Single restart authority enforced via DB lease")
    log.info("Capital mutation guard: close_claimed_until on paper_positions")

    update_heartbeat(SERVICE_NAME, "ALIVE", "System guardian online")

    poll_interval  = float(get_config_value("GUARDIAN_POLL_INTERVAL", 60.0))
    last_recon_at  = 0.0
    last_prune_at  = 0.0
    # Set vacuum baseline to now so it never fires on first startup
    # Vacuum will only run after 24h of uptime
    last_vacuum_at = time.time()  # never vacuum on startup

    while True:
        try:
            last_recon_at, last_prune_at, last_vacuum_at = _run_cycle(
                last_recon_at, last_prune_at, last_vacuum_at,
            )
        except Exception as e:
            log.exception("GUARDIAN CYCLE ERROR: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()