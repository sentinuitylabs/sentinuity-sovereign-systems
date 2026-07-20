import sqlite3
import sys
import time
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [SUPERVISOR] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("SUPERVISOR")
SERVICE_NAME = "neural_supervisor"


def safe_float(value, default=0.0):
    try:
        if value in (None, "", "null"):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value in (None, "", "null"):
            return default
        return int(float(value))
    except Exception:
        return default


def cfg_float(key, default):
    try:
        return float(get_config_value(key, str(default)))
    except Exception:
        return float(default)


def _table_cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_supervisor_schema():
    """Add only columns the May22 latch/open contract needs. Safe/idempotent."""
    wanted = {
        "candidate_state": "TEXT DEFAULT 'pending'",
        "quality_status": "TEXT DEFAULT 'pending'",
        "quality_reason": "TEXT DEFAULT ''",
        "price_status": "TEXT DEFAULT 'pending'",
        "is_tradeable": "INTEGER DEFAULT 0",
        "observed_price": "REAL DEFAULT 0",
        "price_updated_at": "REAL",
        "latched": "INTEGER DEFAULT 0",
        "latched_at": "REAL",
        "execution_ready": "INTEGER DEFAULT 0",
        "execution_ready_at": "REAL",
        "executed": "INTEGER DEFAULT 0",
        "signal_generated_at": "REAL",
        "mint_confidence": "REAL DEFAULT 0",
        "confidence": "REAL DEFAULT 0",
        "updated_at": "REAL",
        "meta": "TEXT DEFAULT '{}'",
    }
    with get_connection() as conn:
        cols = _table_cols(conn, "market_snapshots")
        for col, typ in wanted.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} {typ}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ms_supervisor_visible ON market_snapshots(candidate_state, quality_status, price_status, is_tradeable, latched, execution_ready, price_updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ms_latched_exec ON market_snapshots(latched, execution_ready, latched_at)")
        conn.commit()


def latch_write(snapshot_id: int, conf: float, observed_price: float):
    now = time.time()
    for attempt in range(4):
        try:
            with get_connection() as conn:
                cols = _table_cols(conn, "market_snapshots")
                sets = []
                vals = []
                def set_if(col, expr="?"):
                    if col in cols:
                        sets.append(f"{col}={expr}")
                        return True
                    return False

                if set_if("latched"):
                    vals.append(1)
                if set_if("execution_ready"):
                    vals.append(1)
                if set_if("candidate_state"):
                    vals.append("latched")
                if set_if("quality_reason"):
                    vals.append("OK")
                if set_if("latched_at"):
                    vals.append(now)
                if set_if("execution_ready_at"):
                    vals.append(now)
                if set_if("signal_generated_at"):
                    vals.append(now)
                if set_if("updated_at"):
                    vals.append(now)
                # Stamp price fresh only if the candidate has a real nonzero price.
                if observed_price > 0 and set_if("price_updated_at"):
                    vals.append(now)
                if set_if("confidence"):
                    vals.append(conf)
                if set_if("mint_confidence"):
                    vals.append(conf)
                if set_if("meta", "json_set(COALESCE(meta,'{}'),'$.may22_direct_latch_ts',?)"):
                    vals.append(now)

                vals.append(snapshot_id)
                conn.execute(f"UPDATE market_snapshots SET {', '.join(sets)} WHERE id=?", tuple(vals))
                conn.commit()
            return True
        except Exception as exc:
            if attempt < 3:
                time.sleep(0.05 * (2 ** attempt))
            else:
                log.warning("latch_write failed id=%s: %s", snapshot_id, exc)
    return False


def supervise_once():
    now = time.time()
    min_conf = cfg_float("SUPERVISOR_MIN_MINT_CONF", cfg_float("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.65))
    # Prefer the explicit Phase-A/current key, then legacy key.
    max_price_age = cfg_float("SUPERVISOR_MAX_PRICE_AGE_SEC", cfg_float("SUPERVISOR_PRICE_MAX_AGE_SECONDS", 180))
    max_signal_age = cfg_float("SUPERVISOR_PHASE_A_SIGNAL_AGE_SEC", cfg_float("SUPERVISOR_MAX_SIGNAL_AGE_SEC", 1800))
    max_discovery_age = cfg_float("SUPERVISOR_MAX_DISCOVERY_AGE_SEC", 1800)
    # PAPER-FALLBACK CONTRACT 2026-07-07:
    # The May22 direct-latch supervisor is the PAPER admission spine.
    # It must run in paper mode AND dual mode. Live is an overlay, not a reason
    # to kill paper learning/latching. Invalid live state should block live only.
    trading_mode = str(get_config_value("TRADING_MODE", "paper")).strip().lower()
    paper_enabled = str(get_config_value("PAPER_TRADING_ENABLED", "1")).strip() == "1"
    live_enabled = str(get_config_value("LIVE_TRADING_ENABLED", "0")).strip() == "1"
    live_armed = str(get_config_value("LIVE_ARMED", "0")).strip() == "1"

    paper_lane_allowed = paper_enabled and trading_mode in ("paper", "dual", "hybrid", "live")

    if not paper_lane_allowed:
        update_heartbeat(
            SERVICE_NAME,
            "ERROR",
            f"May22 direct supervisor refused: paper lane disabled mode={trading_mode} paper_enabled={paper_enabled}",
        )
        time.sleep(5)
        return

    if trading_mode in ("dual", "live") and (not live_enabled or not live_armed):
        # Non-fatal: paper keeps running. Live overlay can be blocked elsewhere.
        log.warning(
            "Live/dual overlay not fully armed; continuing PAPER supervisor mode=%s live_enabled=%s live_armed=%s",
            trading_mode,
            live_enabled,
            live_armed,
        )

    # COPYTRADE PAPER INFLUENCE RESTORE 2026-07-12:
    # Include candidates within the bounded +0.03 copytrade window. The actual
    # bonus is evidence-gated below; live mode can never consume it.
    try:
        from services.copytrade_influence import HARD_BONUS_CAP as _ct_cap
    except Exception:
        _ct_cap = 0.03
    query_floor = max(0.0, min_conf - float(_ct_cap))

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT *
            FROM market_snapshots
            WHERE COALESCE(latched,0)=0
              AND COALESCE(execution_ready,0) != 2
              AND COALESCE(mint_address,'') != ''
              AND (candidate_state='qualified' OR (candidate_state='pending' AND quality_status='qualified'))
              AND COALESCE(price_status,'')='priced'
              AND COALESCE(is_tradeable,0)=1
              AND COALESCE(observed_price,0)>0
              AND COALESCE(price_updated_at,0) > (? - ?)
              AND MAX(
                  COALESCE(mint_confidence,0),
                  COALESCE(calibrated_confidence,0),
                  COALESCE(confidence,0),
                  COALESCE(confidence_score,0)
              ) >= ?
              AND COALESCE(tx_hash,'') NOT LIKE 'mtm:%'
            ORDER BY COALESCE(price_updated_at,0) DESC
            LIMIT 25
        """, (now, max_price_age, query_floor)).fetchall()

    approved = 0
    vetoed = 0
    deferred = 0

    for row in rows:
        keys = set(row.keys())
        sid = int(row["id"])
        conf = max(
            safe_float(row["mint_confidence"] if "mint_confidence" in keys else 0.0, 0.0),
            safe_float(row["calibrated_confidence"] if "calibrated_confidence" in keys else 0.0, 0.0),
            safe_float(row["confidence"] if "confidence" in keys else 0.0, 0.0),
            safe_float(row["confidence_score"] if "confidence_score" in keys else 0.0, 0.0),
        )
        price = safe_float(row["observed_price"] if "observed_price" in keys else 0.0, 0.0)
        price_ts = safe_float(row["price_updated_at"] if "price_updated_at" in keys else 0.0, 0.0)
        signal_ts = max(
            safe_float(row["signal_generated_at"] if "signal_generated_at" in keys else 0.0, 0.0),
            safe_float(row["qualified_at"] if "qualified_at" in keys else 0.0, 0.0),
            price_ts,
        )
        discovery_ts = max(
            safe_float(row["first_seen_at"] if "first_seen_at" in keys else 0.0, 0.0),
            safe_float(row["created_at"] if "created_at" in keys else 0.0, 0.0),
            safe_float(row["timestamp"] if "timestamp" in keys else 0.0, 0.0),
        )
        price_age = now - price_ts if price_ts > 0 else 999999
        signal_age = now - signal_ts if signal_ts > 0 else price_age
        discovery_age = now - discovery_ts if discovery_ts > 0 else signal_age

        if price_age > max_price_age:
            deferred += 1
            continue
        if signal_age > max_signal_age:
            vetoed += 1
            continue
        if discovery_age > max_discovery_age:
            vetoed += 1
            continue
        if price <= 0:
            vetoed += 1
            continue

        # Bounded paper-only smart-wallet influence. This cannot bypass any
        # freshness/age/tradeability gate above and returns zero in live mode.
        ct_bonus = 0.0
        ct_reason = "CT_NOT_EVALUATED"
        try:
            from services.copytrade_influence import evaluate_paper_bonus
            ct_bonus, ct_reason, _ct_evidence = evaluate_paper_bonus(
                str(row["mint_address"]), conf, min_conf,
                symbol=str(row["token_symbol"] if "token_symbol" in keys else ""),
            )
        except Exception as _ct_exc:
            ct_reason = f"CT_ERROR:{type(_ct_exc).__name__}"
        final_conf = min(1.0, conf + max(0.0, float(ct_bonus or 0.0)))

        if final_conf < min_conf:
            vetoed += 1
            continue

        if latch_write(sid, final_conf, price):
            approved += 1
            log.info("[MAY22_DIRECT_LATCH] id=%s mint=%s base=%.3f ct=+%.3f final=%.3f ct_reason=%s price_age=%.1fs signal_age=%.1fs",
                     sid, str(row["mint_address"])[:18], conf, ct_bonus, final_conf, ct_reason, price_age, signal_age)

    if approved or vetoed or deferred:
        update_heartbeat(SERVICE_NAME, "ALIVE", f"Approved: {approved} | Vetoed: {vetoed} | Deferred: {deferred} | Floor: {min_conf:.2f}", work_processed=approved, last_success_at=time.time() if approved else None)
    else:
        update_heartbeat(SERVICE_NAME, "ALIVE", f"Idle — awaiting qualified priced snapshots | Floor: {min_conf:.2f}")


def supervise_loop():
    ensure_supervisor_schema()
    update_heartbeat(SERVICE_NAME, "ALIVE", "May22 paper direct-latch supervisor online")
    while True:
        try:
            supervise_once()
        except Exception as exc:
            log.exception("SUPERVISOR ERROR: %s", exc)
            try:
                update_heartbeat(SERVICE_NAME, "ERROR", str(exc))
            except Exception:
                pass
        time.sleep(2)


if __name__ == "__main__":
    supervise_loop()
