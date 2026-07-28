"""
services/pump_activity_monitor.py
===================================
Market Tide Classification Service.

Watches pump.fun discovery rate in rolling windows.
Classifies market as FLOOD / NORMAL / DROUGHT.
Writes state to system_config so Polaris, supervisor,
and intelligence_tab can all read it.

Cycle: every 60 seconds.
Heartbeat: service_name='pump_activity_monitor'

system_config keys written:
    MARKET_TIDE_STATE       = FLOOD | NORMAL | DROUGHT
    MARKET_TIDE_DENSITY     = discoveries per minute (float)
    MARKET_TIDE_UPDATED_AT  = epoch timestamp
"""
from __future__ import annotations

import sys, time, logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tide_monitor] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pump_activity_monitor")

SERVICE_NAME   = "pump_activity_monitor"
CYCLE_SECONDS  = 60
WINDOW_SEC     = 600   # 10-minute rolling window for density calc

# Tide thresholds (discoveries/minute)
FLOOD_THRESHOLD   = 50.0    # >50/min  = market flooded with launches
DROUGHT_THRESHOLD = 10.0    # <10/min  = market quiet
EXTREME_THRESHOLD = 200.0   # >200/min = market overwhelmed — oracle backlog risk


def _safe_ts(val) -> float:
    if not val: return 0.0
    try:
        f = float(val)
        return f if f > 1_000_000_000 else 0.0
    except: pass
    try:
        import datetime as dt
        s = str(val).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try: return dt.datetime.strptime(s, fmt).timestamp()
            except: continue
    except: pass
    return 0.0


def _measure_tide(conn, now: float) -> dict:
    """Measure current market discovery density."""
    import datetime as dt
    window_start = now - WINDOW_SEC
    window_start_str = dt.datetime.fromtimestamp(window_start).strftime('%Y-%m-%d %H:%M:%S')

    # Count raw_dna discoveries in the last 10 minutes
    # Handles both float epoch and ISO string timestamps
    try:
        n_float = conn.execute("""
            SELECT COUNT(*) FROM raw_dna
            WHERE CAST(COALESCE(first_seen_at, created_at, timestamp, 0) AS REAL) > ?
              AND CAST(COALESCE(first_seen_at, created_at, timestamp, 0) AS REAL) > 0
        """, (window_start,)).fetchone()[0]

        n_str = conn.execute("""
            SELECT COUNT(*) FROM raw_dna
            WHERE CAST(COALESCE(first_seen_at, created_at, timestamp, 0) AS REAL) = 0
              AND COALESCE(first_seen_at, created_at, timestamp, '') > ?
              AND COALESCE(first_seen_at, created_at, timestamp, '') != ''
        """, (window_start_str,)).fetchone()[0]

        total_discovered = n_float + n_str
    except Exception as e:
        log.warning("Discovery count failed: %s", e)
        total_discovered = 0

    density = total_discovered / (WINDOW_SEC / 60.0)  # per minute

    # Also count market_snapshots insertions (confirmed through ingest)
    try:
        n_ms = conn.execute("""
            SELECT COUNT(*) FROM market_snapshots
            WHERE CAST(COALESCE(created_at, first_seen_at, timestamp, 0) AS REAL) > ?
              OR COALESCE(created_at, first_seen_at, timestamp, '') > ?
        """, (window_start, window_start_str)).fetchone()[0]
        ingestion_density = n_ms / (WINDOW_SEC / 60.0)
    except:
        ingestion_density = 0

    # Classify
    if density >= EXTREME_THRESHOLD:
        state = "EXTREME"
        description = f"Extreme launch activity ({density:.1f}/min) — oracle backlog risk, Mode B exceptional fire blocked"
    elif density >= FLOOD_THRESHOLD:
        state = "FLOOD"
        description = f"High launch activity ({density:.1f}/min) — increased opportunity density"
    elif density <= DROUGHT_THRESHOLD:
        state = "DROUGHT"
        description = f"Low launch activity ({density:.1f}/min) — reduce position confidence floor"
    else:
        state = "NORMAL"
        description = f"Normal launch activity ({density:.1f}/min)"

    return {
        "state": state,
        "density": density,
        "ingestion_density": ingestion_density,
        "discovered": total_discovered,
        "description": description,
    }


def _write_tide_state(conn, tide: dict, now: float) -> None:
    """Write tide state to system_config."""
    updates = [
        ("MARKET_TIDE_STATE",       tide["state"]),
        ("MARKET_TIDE_DENSITY",     f"{tide['density']:.2f}"),
        ("MARKET_TIDE_UPDATED_AT",  str(now)),
        ("MARKET_TIDE_DESCRIPTION", tide["description"]),
    ]
    for key, value in updates:
        conn.execute("""
            INSERT OR REPLACE INTO system_config(key, value, description)
            VALUES(?, ?, ?)
        """, (key, value, f"Written by pump_activity_monitor"))
    conn.commit()


def run() -> None:
    log.info("Market tide monitor started — cycle=%ds window=%ds", CYCLE_SECONDS, WINDOW_SEC)
    log.info("Thresholds: FLOOD>%.0f/min DROUGHT<%.0f/min", FLOOD_THRESHOLD, DROUGHT_THRESHOLD)
    update_heartbeat(SERVICE_NAME, "starting", "pump_activity_monitor online")

    while True:
        try:
            now = time.time()
            with get_connection() as conn:
                import sqlite3 as _sq
                conn.row_factory = _sq.Row
                tide = _measure_tide(conn, now)
                _write_tide_state(conn, tide, now)

            state_icon = {"EXTREME": "🔴", "FLOOD": "🌊", "NORMAL": "〰", "DROUGHT": "🏜"}.get(tide["state"], "?")
            note = (f"{state_icon} {tide['state']} | "
                    f"{tide['density']:.1f} disc/min | "
                    f"{tide['discovered']} in {WINDOW_SEC//60}min window")
            log.info("[TIDE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note)

        except Exception as exc:
            log.warning("[TIDE_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
