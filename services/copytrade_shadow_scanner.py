"""
services/copytrade_shadow_scanner.py — SIGNOFF_COPYTRADE_PAPER_BRIDGE_20260610
===============================================================================
THE MISSING LINK identified by the 2026-06-10 audit (finding D1/D2).

Launch_Sentinuity.bat line 351 already starts `services.copytrade_shadow_scanner`;
this file revives that launch line. Drop into the services folder — no BAT change needed.

What it does (OBSERVE-ONLY — zero trading authority):
  1. Scans recent candidate mints from market_snapshots (fresh launches AND
     the T2 mid-cap band, so tiered mcaps are covered, not just birth zone).
  2. Calls the REAL conviction engine: smart_wallet_conviction.generate_signal_for_token
     (mode="OBSERVE"), which persists wallet_entry_likelihood_signals itself.
  3. Stamps market_snapshots.copytrade_scanned_at / copytrade_signal_state /
     copytrade_matched_wallets — the exact columns sovereign_world_component.py
     reads (lines 270-272) and which the audit confirmed had NO writer.
  4. Writes structured gate_trace rows for every decision.
  5. Heartbeats as 'copytrade_shadow_scanner'.

What it does NOT do:
  - Never touches latched / execution_ready / candidate_state / positions / wallet.
  - Never boosts confidence. Mode is OBSERVE. Live influence stays impossible.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
import math
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - [COPYTRADE] %(levelname)-7s %(message)s",
                    handlers=[logging.StreamHandler()])
log = logging.getLogger("copytrade_shadow_scanner")

SERVICE_NAME = "copytrade_shadow_scanner"
os.environ.setdefault("SENTINUITY_SERVICE", SERVICE_NAME)

# ── Core wiring with graceful fallbacks (services/ vs flat layout) ───────────
try:
    from core.schema import get_connection, update_heartbeat, get_config_value
except Exception:
    from schema import get_connection, update_heartbeat, get_config_value  # type: ignore

try:
    from core.gate_trace import trace, flush as trace_flush, mcap_tier
except Exception:
    try:
        from gate_trace import trace, flush as trace_flush, mcap_tier  # type: ignore
    except Exception:
        def trace(**kw):  # type: ignore
            pass
        def trace_flush():  # type: ignore
            return 0
        def mcap_tier(m):  # type: ignore
            return "UNKNOWN"

try:
    from services.smart_wallet_conviction import (
        ensure_smart_wallet_schema, generate_signal_for_token)
    _CONVICTION_OK = True
except Exception:
    try:
        from smart_wallet_conviction import (  # type: ignore
            ensure_smart_wallet_schema, generate_signal_for_token)
        _CONVICTION_OK = True
    except Exception as _e:
        log.error("smart_wallet_conviction unavailable: %s — scanner will idle", _e)
        _CONVICTION_OK = False

DEFAULT_POLL_SEC          = float(os.getenv("COPYTRADE_SCAN_INTERVAL_SEC", "60"))
DEFAULT_RESCAN_AFTER_SEC  = 300.0   # re-score a mint at most every 5 minutes by default
DEFAULT_MAX_PER_CYCLE     = 5       # V6: observe lane must not starve entry path
DEFAULT_SIGNAL_FRESH_SEC  = 900.0   # candidate window: anything seen in last 15 min

_LOCK_BACKOFF_UNTIL = 0.0
_LOCK_BACKOFF_SEC = 0.0

def _cfg_value(key: str, default):
    try:
        v = get_config_value(key, default)
        if v is None or str(v).strip() == "":
            return default
        return v
    except Exception:
        return default

def _cfg_float(key: str, default: float) -> float:
    try:
        return float(_cfg_value(key, default))
    except Exception:
        return float(default)

def _cfg_int(key: str, default: int) -> int:
    try:
        return int(float(_cfg_value(key, default)))
    except Exception:
        return int(default)

def _cfg_bool(key: str, default: bool = True) -> bool:
    v = str(_cfg_value(key, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "on", "enabled")

def _is_db_locked(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower() or "database table is locked" in str(exc).lower()

def _note_lock_backoff(exc: BaseException, where: str = "unknown") -> None:
    global _LOCK_BACKOFF_UNTIL, _LOCK_BACKOFF_SEC
    base = _cfg_float("COPYTRADE_LOCK_BACKOFF_BASE_SEC", 60.0)
    max_sec = _cfg_float("COPYTRADE_LOCK_BACKOFF_MAX_SEC", 600.0)
    _LOCK_BACKOFF_SEC = min(max_sec, max(base, (_LOCK_BACKOFF_SEC * 2.0) if _LOCK_BACKOFF_SEC else base))
    _LOCK_BACKOFF_UNTIL = time.time() + _LOCK_BACKOFF_SEC
    log.warning("DB_LOCK_BACKOFF where=%s sleep=%.0fs err=%s", where, _LOCK_BACKOFF_SEC, str(exc)[:160])
    try:
        update_heartbeat(SERVICE_NAME, "THROTTLED_DB_LOCK", f"{where}: backoff={_LOCK_BACKOFF_SEC:.0f}s")
    except Exception:
        pass

def _in_lock_backoff() -> float:
    return max(0.0, _LOCK_BACKOFF_UNTIL - time.time())


def _ensure_copytrade_columns() -> None:
    """Additive only — the three columns the world layer already reads."""
    cols = [
        ("copytrade_scanned_at",      "REAL"),
        ("copytrade_signal_state",    "TEXT"),
        ("copytrade_matched_wallets", "INTEGER DEFAULT 0"),
    ]
    try:
        with get_connection() as conn:
            existing = {r[1] for r in conn.execute(
                "PRAGMA table_info(market_snapshots)").fetchall()}
            for col, decl in cols:
                if col not in existing:
                    conn.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} {decl}")
            conn.commit()
    except Exception as e:
        log.warning("column ensure failed (non-fatal): %s", e)


def _ensure_calibration_table() -> None:
    """signal → outcome calibration spine (Phase 5.4)."""
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS copytrade_calibration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    mint TEXT, snap_id INTEGER, position_id INTEGER,
                    signal_time REAL, conviction_score REAL,
                    matched_wallets INTEGER, sm_tier TEXT,
                    mcap_at_signal REAL, mcap_tier TEXT,
                    advisory_only INTEGER DEFAULT 1,
                    pnl_usd REAL, pnl_pct REAL,
                    max_favorable_pct REAL, max_adverse_pct REAL,
                    outcome TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ct_calib_mint"
                         " ON copytrade_calibration(mint, created_at)")
            conn.commit()
    except Exception as e:
        log.warning("calibration table ensure failed: %s", e)


def _candidates(conn) -> list[dict]:
    """Fresh + mid-cap candidates not scanned recently. Read-only on pipeline state."""
    now = time.time()
    max_per_cycle = max(0, _cfg_int("COPYTRADE_MAX_PER_CYCLE", DEFAULT_MAX_PER_CYCLE))
    signal_fresh_sec = max(60.0, _cfg_float("COPYTRADE_SIGNAL_FRESH_SEC", DEFAULT_SIGNAL_FRESH_SEC))
    rescan_after_sec = max(60.0, _cfg_float("COPYTRADE_RESCAN_AFTER_SEC", DEFAULT_RESCAN_AFTER_SEC))
    if max_per_cycle <= 0:
        return []
    rows = conn.execute("""
        SELECT id, mint_address, token_name, market_cap_usd,
               holder_count, vol_5m_usd, vol_acceleration,
               curve_progress_pct, copytrade_scanned_at,
               MAX(COALESCE(qualified_at,0), COALESCE(updated_at,0),
                   COALESCE(price_updated_at,0), COALESCE(first_seen_at,0),
                   COALESCE(created_at,0)) AS seen_ts
        FROM market_snapshots
        WHERE mint_address IS NOT NULL AND TRIM(mint_address) != ''
          AND COALESCE(tx_hash,'') NOT LIKE 'mtm:%'
          AND COALESCE(candidate_state,'') NOT IN ('vetoed','exited','expired_stale','mtm')
          AND (? - MAX(COALESCE(qualified_at,0), COALESCE(updated_at,0),
                       COALESCE(price_updated_at,0), COALESCE(first_seen_at,0),
                       COALESCE(created_at,0))) <= ?
          AND (copytrade_scanned_at IS NULL OR copytrade_scanned_at < ?)
        ORDER BY seen_ts DESC
        LIMIT ?
    """, (now, signal_fresh_sec, now - rescan_after_sec, max_per_cycle)).fetchall()
    return [dict(r) for r in rows]


def _entry_path_under_pressure() -> bool:
    """Throttle observe-only copytrade if the entry executor is currently stale-gating."""
    try:
        now = time.time()
        with get_connection() as conn:
            row = conn.execute("""
                SELECT
                  SUM(CASE WHEN reason_code IN ('EXEC_SKIP_SIGNAL_AGE','EXEC_SKIP_PRICE_AGE') THEN 1 ELSE 0 END) AS stale_skips,
                  SUM(CASE WHEN reason_code IN ('EXEC_OPEN_ATTEMPT','EXEC_OPEN_SUCCESS') THEN 1 ELSE 0 END) AS opens
                FROM gate_trace
                WHERE gate='ENTRY_EXECUTOR' AND CAST(ts AS REAL) >= ?
            """, (now - 600,)).fetchone()
        stale = int((row[0] if row else 0) or 0)
        opens = int((row[1] if row else 0) or 0)
        return stale >= _cfg_int("COPYTRADE_ENTRY_PRESSURE_STALE_SKIP_LIMIT", 8) and stale > max(1, opens * 3)
    except Exception as e:
        if _is_db_locked(e):
            _note_lock_backoff(e, "entry_pressure_probe")
        return False

def _scan_once() -> dict:
    stats = {"scanned": 0, "scored": 0, "no_match": 0, "errors": 0, "throttled": 0}
    if not _CONVICTION_OK:
        return stats
    if not _cfg_bool("COPYTRADE_SHADOW_SCANNER_ENABLED", True):
        stats["throttled"] = 1
        return stats
    backoff_left = _in_lock_backoff()
    if backoff_left > 0:
        stats["throttled"] = 1
        log.info("copytrade scanner in DB-lock backoff for %.0fs", backoff_left)
        return stats
    if _entry_path_under_pressure():
        stats["throttled"] = 1
        log.warning("copytrade scanner throttled: entry executor is stale-gating; preserving open path")
        try:
            update_heartbeat(SERVICE_NAME, "THROTTLED_ENTRY_PRESSURE", "entry executor stale-gating; observe lane paused")
        except Exception:
            pass
        return stats
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cands = _candidates(conn)
    except Exception as e:
        stats["errors"] += 1
        if _is_db_locked(e):
            _note_lock_backoff(e, "candidate_read")
            return stats
        raise

    # SENTINUITY_LATCH_OPEN_FIX_V4:
    # Never open a write transaction per candidate. Conviction scoring may call
    # other DB writers internally, and a per-row UPDATE/commit here was producing
    # lock thrash in live audits. Compute all decisions first, then issue one
    # short executemany transaction for market_snapshots stamps.
    pending_updates: list[tuple[float, str, int, int]] = []

    for c in cands:
        mint = str(c["mint_address"])
        snap_id = int(c["id"])
        mcap = c.get("market_cap_usd")
        try:
            def _num(value, default=0.0):
                try:
                    return float(default if value is None or value == "" else value)
                except (TypeError, ValueError):
                    return float(default)

            metrics = {
                "market_cap_usd":      _num(mcap),
                "holder_count":        _num(c.get("holder_count")),
                "vol_5m_usd":          _num(c.get("vol_5m_usd")),
                "volume_acceleration": _num(c.get("vol_acceleration")),
                "curve_progress_pct":  _num(c.get("curve_progress_pct")),
            }
            for key, value in list(metrics.items()):
                if not math.isfinite(value):
                    metrics[key] = 0.0
            sig = generate_signal_for_token(
                token_mint=mint,
                token_symbol=str(c.get("token_name") or "")[:20],
                current_metrics=metrics,
                mode="OBSERVE",
            )
            matched = int(getattr(sig, "matched_wallet_count", 0) or 0)
            conviction = float(getattr(sig, "copy_conviction_score", 0.0) or 0.0)
            veto = str(getattr(sig, "veto_reason", "") or "")

            if matched > 0 and conviction > 0:
                state, decision, reason = "scored", "PASS", "CT_SIGNAL_SCORED"
                stats["scored"] += 1
            else:
                state, decision = "no_match", "SKIP"
                reason = ("CT_" + veto) if veto else "CT_NO_MATCH"
                stats["no_match"] += 1

            pending_updates.append((time.time(), state, matched, snap_id))

            trace(stage="COPYTRADE", gate="CONVICTION_SCAN", decision=decision,
                  reason_code=reason[:64], snap_id=snap_id, mint=mint,
                  value=conviction, threshold=None, lane="OBSERVE",
                  mcap=float(mcap) if mcap else None, sm_tier=None,
                  reason_detail=f"matched={matched}")
            stats["scanned"] += 1
        except Exception as e:
            stats["errors"] += 1
            if _is_db_locked(e):
                _note_lock_backoff(e, "conviction_score")
            log.warning("scan error mint=%s: %s", mint[:16], e)
            trace(stage="COPYTRADE", gate="CONVICTION_SCAN", decision="ERROR",
                  reason_code="CT_SCAN_ERROR", snap_id=snap_id, mint=mint,
                  reason_detail=str(e)[:200])
            if _is_db_locked(e):
                break

    if pending_updates:
        try:
            with get_connection() as conn:
                conn.executemany(
                    "UPDATE market_snapshots SET copytrade_scanned_at=?,"
                    " copytrade_signal_state=?, copytrade_matched_wallets=?"
                    " WHERE id=?",
                    pending_updates,
                )
                conn.commit()
        except Exception as e:
            stats["errors"] += len(pending_updates)
            if _is_db_locked(e):
                _note_lock_backoff(e, "batch_stamp")
            log.warning("batched scan stamp failed rows=%d: %s", len(pending_updates), e)
            try:
                for _, _, _, _snap_id in pending_updates[:10]:
                    trace(stage="COPYTRADE", gate="CONVICTION_SCAN", decision="ERROR",
                          reason_code="CT_BATCH_WRITE_ERROR", snap_id=_snap_id,
                          reason_detail=str(e)[:200])
            except Exception:
                pass

    trace_flush()
    return stats


def run() -> None:
    log.info("Copytrade shadow scanner online — OBSERVE mode, zero trade authority")
    if _CONVICTION_OK:
        try:
            ensure_smart_wallet_schema()
        except Exception as e:
            log.warning("conviction schema ensure failed: %s", e)
    _ensure_copytrade_columns()
    _ensure_calibration_table()
    update_heartbeat(SERVICE_NAME, "ALIVE", "Copytrade scanner online (OBSERVE)")

    cycle = 0
    while True:
        sleep_for = _cfg_float("COPYTRADE_SCAN_INTERVAL_SEC", DEFAULT_POLL_SEC)
        try:
            cycle += 1
            if not _cfg_bool("COPYTRADE_SHADOW_SCANNER_ENABLED", True):
                if cycle % 5 == 0:
                    update_heartbeat(SERVICE_NAME, "IDLE_DISABLED", "COPYTRADE_SHADOW_SCANNER_ENABLED=0")
                time.sleep(max(30.0, sleep_for))
                continue
            s = _scan_once()
            if s.get("throttled"):
                sleep_for = max(sleep_for, _cfg_float("COPYTRADE_THROTTLED_SLEEP_SEC", 120.0), _in_lock_backoff())
            if cycle % 3 == 0 or s["scored"] > 0 or s.get("throttled"):
                update_heartbeat(
                    SERVICE_NAME, "ALIVE" if not s.get("throttled") else "THROTTLED",
                    f"scanned={s['scanned']} scored={s['scored']} "
                    f"no_match={s['no_match']} errors={s['errors']} throttled={s.get('throttled',0)} mode=OBSERVE",
                    work_processed=s["scanned"])
        except Exception as e:
            log.exception("scanner loop error: %s", e)
            if _is_db_locked(e):
                _note_lock_backoff(e, "loop")
                sleep_for = max(sleep_for, _in_lock_backoff())
            try:
                update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])
            except Exception:
                pass
        time.sleep(max(5.0, sleep_for))


if __name__ == "__main__":
    run()
