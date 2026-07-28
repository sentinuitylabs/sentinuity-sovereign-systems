"""
services/shadow_runner_tracker.py — v1.2

Finds runners we missed and records why.
Pure observation, zero live risk.

Fixes over v1:
- actual peak timestamp comes from the max-price row, not last seen timestamp.
- schema-safe price column detection: price / observed_price / price_usd / current_price.
- wallet signal lookup detects token_mint vs mint_address dynamically.
- adds smart_wallet_count, elite_wallet_count, top_wallet_lead_time_sec.
- populates tide_at_peak where possible.
- checks paper and live positions if live position tables exist.
- emits cognition events for monster classes.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))
DB_PATH = BASE_DIR / "sentinuity_matrix.db"

try:
    from core.schema import get_connection, update_heartbeat  # type: ignore
except Exception:
    get_connection = None
    update_heartbeat = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [shadow_tracker] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("shadow_runner_tracker")

SERVICE_NAME = "shadow_runner_tracker"
CYCLE_SECONDS = 90
LOOKBACK_SECONDS = 3600
RUNNER_THRESHOLD = 2.0
MONSTER_THRESHOLD = 5.0
DEEP_LOG_INTERVAL = 600
_WARNED = set()


def connect() -> sqlite3.Connection:
    if get_connection:
        return get_connection()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def hb(status: str, note: str, work: int = 0) -> None:
    if update_heartbeat:
        try:
            update_heartbeat(SERVICE_NAME, status, note, work_processed=work)
            return
        except Exception:
            pass
    try:
        with connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_heartbeat(
                    service_name TEXT PRIMARY KEY, status TEXT, note TEXT,
                    last_pulse REAL, work_processed INTEGER DEFAULT 0, last_success_at REAL
                )
            """)
            conn.execute("""
                INSERT INTO system_heartbeat(service_name,status,note,last_pulse,work_processed,last_success_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(service_name) DO UPDATE SET
                    status=excluded.status, note=excluded.note, last_pulse=excluded.last_pulse,
                    work_processed=excluded.work_processed, last_success_at=excluded.last_success_at
            """, (SERVICE_NAME, status, note, time.time(), work, time.time() if status == "alive" else None))
    except Exception:
        pass


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


def warn_once(key: str, msg: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning(msg)


def _ensure_shadow_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_runners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint_address TEXT NOT NULL,
            token_name TEXT,
            detected_at REAL NOT NULL,
            entry_price_seen REAL,
            peak_price_seen REAL,
            peak_mult REAL,
            time_to_peak_sec REAL,
            tide_at_peak TEXT,
            we_qualified INTEGER DEFAULT 0,
            we_latched INTEGER DEFAULT 0,
            we_opened INTEGER DEFAULT 0,
            position_id INTEGER,
            rejection_reason TEXT,
            quality_reason TEXT,
            smart_wallet_signal INTEGER DEFAULT 0,
            classification TEXT,
            updated_at REAL,
            smart_wallet_count INTEGER DEFAULT 0,
            elite_wallet_count INTEGER DEFAULT 0,
            top_wallet_lead_time_sec REAL
        )
    """)
    for col, spec in [
        ("smart_wallet_count", "INTEGER DEFAULT 0"),
        ("elite_wallet_count", "INTEGER DEFAULT 0"),
        ("top_wallet_lead_time_sec", "REAL"),
        ("tide_at_peak", "TEXT"),
    ]:
        ensure_column(conn, "shadow_runners", col, spec)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_mint ON shadow_runners(mint_address)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_detected ON shadow_runners(detected_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_class ON shadow_runners(classification, detected_at DESC)")


def _get_config(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def _price_column(cols: set[str]) -> str | None:
    """Return the best available market_snapshots price column for this workspace."""
    for col in ("price", "observed_price", "price_usd", "current_price", "last_price", "live_exec_price"):
        if col in cols:
            return col
    return None


def _find_runner_candidates(conn: sqlite3.Connection, lookback_cutoff: float) -> list[dict[str, Any]]:
    if not table_exists(conn, "market_snapshots"):
        return []

    cols = columns(conn, "market_snapshots")
    price_col = _price_column(cols)
    if "mint_address" not in cols or not price_col:
        warn_once(
            "ms_schema",
            "market_snapshots missing mint_address or recognised price column; shadow tracking skipped. "
            f"Known columns include: {', '.join(sorted(list(cols))[:20])}"
        )
        return []

    ts_col = "price_updated_at" if "price_updated_at" in cols else (
        "updated_at" if "updated_at" in cols else (
            "created_at" if "created_at" in cols else (
                "timestamp" if "timestamp" in cols else "id"
            )
        )
    )
    token_expr = "MAX(token_name)" if "token_name" in cols else "NULL"

    rows = conn.execute(f"""
        SELECT mint_address,
               {token_expr} AS token_name,
               (SELECT {price_col} FROM market_snapshots m2
                 WHERE m2.mint_address = m.mint_address
                   AND m2.{price_col} IS NOT NULL AND m2.{price_col} > 0
                   AND COALESCE(m2.{ts_col}, 0) >= ?
                 ORDER BY COALESCE(m2.{ts_col}, 0) ASC LIMIT 1) AS first_price,
               (SELECT COALESCE({ts_col}, 0) FROM market_snapshots m2
                 WHERE m2.mint_address = m.mint_address
                   AND m2.{price_col} IS NOT NULL AND m2.{price_col} > 0
                   AND COALESCE(m2.{ts_col}, 0) >= ?
                 ORDER BY COALESCE(m2.{ts_col}, 0) ASC LIMIT 1) AS first_price_ts,
               (SELECT {price_col} FROM market_snapshots m3
                 WHERE m3.mint_address = m.mint_address
                   AND m3.{price_col} IS NOT NULL AND m3.{price_col} > 0
                   AND COALESCE(m3.{ts_col}, 0) >= ?
                 ORDER BY m3.{price_col} DESC, COALESCE(m3.{ts_col}, 0) ASC LIMIT 1) AS peak_price,
               (SELECT COALESCE({ts_col}, 0) FROM market_snapshots m3
                 WHERE m3.mint_address = m.mint_address
                   AND m3.{price_col} IS NOT NULL AND m3.{price_col} > 0
                   AND COALESCE(m3.{ts_col}, 0) >= ?
                 ORDER BY m3.{price_col} DESC, COALESCE(m3.{ts_col}, 0) ASC LIMIT 1) AS peak_price_ts
        FROM market_snapshots m
        WHERE COALESCE(m.{ts_col}, 0) >= ?
          AND m.{price_col} IS NOT NULL
          AND m.{price_col} > 0
          AND m.mint_address IS NOT NULL
        GROUP BY mint_address
        HAVING peak_price IS NOT NULL
           AND first_price IS NOT NULL
           AND first_price > 0
           AND (peak_price / first_price) >= ?
    """, (lookback_cutoff, lookback_cutoff, lookback_cutoff, lookback_cutoff, lookback_cutoff, RUNNER_THRESHOLD)).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            first = float(r["first_price"])
            peak = float(r["peak_price"])
            first_ts = float(r["first_price_ts"] or 0)
            peak_ts = float(r["peak_price_ts"] or first_ts)
            if first <= 0 or peak <= first:
                continue
            out.append({
                "mint_address": r["mint_address"],
                "token_name": r["token_name"],
                "first_price": first,
                "peak_price": peak,
                "peak_mult": peak / first,
                "time_to_peak": max(0.0, peak_ts - first_ts),
                "first_seen_ts": first_ts,
                "peak_ts": peak_ts,
                "tide_at_peak": _tide_at_time(conn, r["mint_address"], peak_ts),
            })
        except Exception:
            continue
    return out

def _tide_at_time(conn: sqlite3.Connection, mint: str, ts: float) -> str | None:
    cols = columns(conn, "market_snapshots")
    tide_cols = [c for c in ("tide_state", "market_tide_state", "tide_at_peak") if c in cols]
    if tide_cols and ts:
        ts_col = "price_updated_at" if "price_updated_at" in cols else ("timestamp" if "timestamp" in cols else "id")
        expr = tide_cols[0]
        try:
            row = conn.execute(f"""
                SELECT {expr} AS tide FROM market_snapshots
                WHERE mint_address=? AND {expr} IS NOT NULL
                ORDER BY ABS({ts_col} - ?) ASC LIMIT 1
            """, (mint, ts)).fetchone()
            if row and row["tide"]:
                return str(row["tide"])
        except Exception:
            pass
    return str(_get_config(conn, "MARKET_TIDE_STATE", "UNKNOWN") or "UNKNOWN")


def _check_our_activity(conn: sqlite3.Connection, mint: str) -> dict[str, Any]:
    activity = {
        "we_qualified": 0,
        "we_latched": 0,
        "we_opened": 0,
        "position_id": None,
        "rejection_reason": None,
        "quality_reason": None,
    }
    if table_exists(conn, "market_snapshots"):
        cols = columns(conn, "market_snapshots")
        if "quality_status" in cols:
            fields = ["quality_status"]
            for c in ["quality_reason", "latched", "candidate_state"]:
                if c in cols:
                    fields.append(c)
            try:
                qrow = conn.execute(
                    f"SELECT {','.join(fields)} FROM market_snapshots WHERE mint_address=? AND quality_status IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (mint,),
                ).fetchone()
                if qrow:
                    qs = str(qrow["quality_status"] or "").lower()
                    qr = qrow["quality_reason"] if "quality_reason" in fields else None
                    activity["quality_reason"] = qr
                    if qs == "qualified":
                        activity["we_qualified"] = 1
                    elif qs == "rejected":
                        activity["rejection_reason"] = qr
                    if "latched" in fields and qrow["latched"]:
                        activity["we_latched"] = 1
            except Exception:
                pass
    for table in ["paper_positions", "live_positions", "positions"]:
        if not table_exists(conn, table):
            continue
        cols = columns(conn, table)
        if "mint_address" not in cols or "id" not in cols:
            continue
        try:
            pos = conn.execute(f"SELECT id FROM {table} WHERE mint_address=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
            if pos:
                activity["we_opened"] = 1
                activity["position_id"] = pos["id"]
                break
        except Exception:
            pass
    return activity


def _wallet_signal_column(conn: sqlite3.Connection) -> str | None:
    if not table_exists(conn, "wallet_entry_likelihood_signals"):
        return None
    cols = columns(conn, "wallet_entry_likelihood_signals")
    if "token_mint" in cols:
        return "token_mint"
    if "mint_address" in cols:
        return "mint_address"
    return None


def _check_smart_wallet_signal(conn: sqlite3.Connection, mint: str, first_seen_ts: float | None = None) -> dict[str, Any]:
    out = {"signal": 0, "smart_wallet_count": 0, "elite_wallet_count": 0, "top_wallet_lead_time_sec": None}
    col = _wallet_signal_column(conn)
    if not col:
        return out
    cols = columns(conn, "wallet_entry_likelihood_signals")
    count_exprs = []
    if "matched_wallet_count" in cols:
        count_exprs.append("MAX(COALESCE(matched_wallet_count,0)) AS smart_wallet_count")
    elif "smart_wallet_count" in cols:
        count_exprs.append("MAX(COALESCE(smart_wallet_count,0)) AS smart_wallet_count")
    else:
        count_exprs.append("COUNT(*) AS smart_wallet_count")
    if "elite_wallet_count" in cols:
        count_exprs.append("MAX(COALESCE(elite_wallet_count,0)) AS elite_wallet_count")
    else:
        count_exprs.append("0 AS elite_wallet_count")
    ts_cols = [c for c in ["signal_time", "created_at", "timestamp", "detected_at"] if c in cols]
    ts_expr = f"MIN({ts_cols[0]}) AS first_signal_ts" if ts_cols else "NULL AS first_signal_ts"
    try:
        row = conn.execute(f"""
            SELECT {', '.join(count_exprs)}, {ts_expr}
            FROM wallet_entry_likelihood_signals
            WHERE {col}=?
        """, (mint,)).fetchone()
        if row and int(row["smart_wallet_count"] or 0) > 0:
            out["signal"] = 1
            out["smart_wallet_count"] = int(row["smart_wallet_count"] or 0)
            out["elite_wallet_count"] = int(row["elite_wallet_count"] or 0)
            if first_seen_ts and row["first_signal_ts"]:
                out["top_wallet_lead_time_sec"] = float(first_seen_ts) - float(row["first_signal_ts"])
    except Exception as exc:
        warn_once("smart_wallet_lookup", f"smart wallet lookup failed: {exc}")
    return out


def _classify(cand: dict[str, Any], activity: dict[str, Any], sm: dict[str, Any]) -> str:
    is_monster = float(cand["peak_mult"]) >= MONSTER_THRESHOLD
    if activity["we_opened"]:
        return "CAUGHT_MONSTER" if is_monster else "CAUGHT"
    if sm.get("signal") and is_monster:
        return "WALLET_CONFIRMED_MISSED"
    if activity["we_qualified"] and not activity["we_latched"]:
        return "QUALIFIED_NO_LATCH_MONSTER" if is_monster else "QUALIFIED_NO_LATCH"
    if activity["we_qualified"] and activity["we_latched"]:
        return "LATCHED_NO_OPEN_MONSTER" if is_monster else "LATCHED_NO_OPEN"
    if activity["rejection_reason"]:
        return "REJECTED_MONSTER" if is_monster else "REJECTED"
    return "UNSEEN_MONSTER" if is_monster else "UNSEEN"


def _log_cognition(conn: sqlite3.Connection, classification: str, cand: dict[str, Any], activity: dict[str, Any]) -> None:
    if classification not in {"UNSEEN_MONSTER", "REJECTED_MONSTER", "CAUGHT_MONSTER", "WALLET_CONFIRMED_MISSED"}:
        return
    if not table_exists(conn, "cognition_log"):
        return
    cols = columns(conn, "cognition_log")
    msg = f"{classification}: {(cand.get('token_name') or cand['mint_address'])} peak={cand['peak_mult']:.2f}x ttp={cand['time_to_peak']:.0f}s reject={activity.get('rejection_reason')}"
    data: dict[str, Any] = {}
    now = time.time()
    for c in ["timestamp", "logged_at", "created_at"]:
        if c in cols:
            data[c] = now
            break
    if "source" in cols:
        data["source"] = SERVICE_NAME
    if "stage" in cols:
        data["stage"] = classification
    if "message" in cols:
        data["message"] = msg
    elif "content" in cols:
        data["content"] = msg
    if data:
        keys = list(data)
        conn.execute(f"INSERT INTO cognition_log({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [data[k] for k in keys])


def _upsert_shadow(conn: sqlite3.Connection, cand: dict[str, Any], activity: dict[str, Any], sm: dict[str, Any]) -> str:
    now = time.time()
    classification = _classify(cand, activity, sm)
    existing = conn.execute("SELECT id, peak_mult, classification FROM shadow_runners WHERE mint_address=? LIMIT 1", (cand["mint_address"],)).fetchone()
    values = (
        cand["peak_price"], cand["peak_mult"], cand["time_to_peak"], cand.get("tide_at_peak"),
        activity["we_qualified"], activity["we_latched"], activity["we_opened"], activity["position_id"],
        activity["rejection_reason"], activity["quality_reason"], int(sm.get("signal") or 0), classification,
        int(sm.get("smart_wallet_count") or 0), int(sm.get("elite_wallet_count") or 0), sm.get("top_wallet_lead_time_sec"), now,
    )
    if existing:
        if cand["peak_mult"] > float(existing["peak_mult"] or 0) * 1.03 or classification != existing["classification"]:
            conn.execute("""
                UPDATE shadow_runners
                SET peak_price_seen=?, peak_mult=?, time_to_peak_sec=?, tide_at_peak=?,
                    we_qualified=?, we_latched=?, we_opened=?, position_id=?,
                    rejection_reason=?, quality_reason=?, smart_wallet_signal=?, classification=?,
                    smart_wallet_count=?, elite_wallet_count=?, top_wallet_lead_time_sec=?, updated_at=?
                WHERE id=?
            """, values + (existing["id"],))
            _log_cognition(conn, classification, cand, activity)
            return "UPDATED"
        return "SKIPPED"
    conn.execute("""
        INSERT INTO shadow_runners(
            mint_address, token_name, detected_at, entry_price_seen, peak_price_seen,
            peak_mult, time_to_peak_sec, tide_at_peak, we_qualified, we_latched,
            we_opened, position_id, rejection_reason, quality_reason,
            smart_wallet_signal, classification, smart_wallet_count, elite_wallet_count,
            top_wallet_lead_time_sec, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        cand["mint_address"], cand.get("token_name"), now, cand["first_price"], cand["peak_price"],
        cand["peak_mult"], cand["time_to_peak"], cand.get("tide_at_peak"), activity["we_qualified"], activity["we_latched"],
        activity["we_opened"], activity["position_id"], activity["rejection_reason"], activity["quality_reason"],
        int(sm.get("signal") or 0), classification, int(sm.get("smart_wallet_count") or 0), int(sm.get("elite_wallet_count") or 0),
        sm.get("top_wallet_lead_time_sec"), now,
    ))
    _log_cognition(conn, classification, cand, activity)
    return "INSERTED"


def _run_tracker_cycle() -> dict[str, int]:
    now = time.time()
    cutoff = now - LOOKBACK_SECONDS
    stats = {"candidates": 0, "inserted": 0, "updated": 0, "monsters": 0, "caught": 0, "rejected": 0, "unseen": 0, "smart_signals": 0}
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        _ensure_shadow_table(conn)
        candidates = _find_runner_candidates(conn, cutoff)
        stats["candidates"] = len(candidates)
        for cand in candidates:
            activity = _check_our_activity(conn, cand["mint_address"])
            sm = _check_smart_wallet_signal(conn, cand["mint_address"], cand.get("first_seen_ts"))
            result = _upsert_shadow(conn, cand, activity, sm)
            if result == "INSERTED": stats["inserted"] += 1
            elif result == "UPDATED": stats["updated"] += 1
            if cand["peak_mult"] >= MONSTER_THRESHOLD: stats["monsters"] += 1
            if activity["we_opened"]: stats["caught"] += 1
            elif activity["rejection_reason"]: stats["rejected"] += 1
            else: stats["unseen"] += 1
            if sm.get("signal"): stats["smart_signals"] += 1
        try: conn.commit()
        except Exception: pass
    return stats


def _log_summary(conn: sqlite3.Connection) -> None:
    try:
        rows = conn.execute("""
            SELECT classification, COUNT(*) AS cnt, AVG(peak_mult) AS avg_mult, MAX(peak_mult) AS max_mult
            FROM shadow_runners WHERE detected_at > ? GROUP BY classification ORDER BY cnt DESC
        """, (time.time() - 86400,)).fetchall()
        if rows:
            log.info("──── 24h SHADOW SUMMARY ────")
            for r in rows:
                log.info("  %-32s : %3d runners avg=%.2fx max=%.2fx", r["classification"], r["cnt"], float(r["avg_mult"] or 0), float(r["max_mult"] or 0))
    except Exception:
        pass


def run() -> None:
    log.info("Shadow runner tracker started — cycle=%ds threshold=%.1fx lookback=%ds", CYCLE_SECONDS, RUNNER_THRESHOLD, LOOKBACK_SECONDS)
    hb("starting", "shadow_runner_tracker online")
    time.sleep(15)
    last_summary = 0.0
    while True:
        try:
            stats = _run_tracker_cycle()
            note = f"runners={stats['candidates']} new={stats['inserted']} updated={stats['updated']} monsters={stats['monsters']} caught={stats['caught']} rejected={stats['rejected']} unseen={stats['unseen']} smart_sig={stats['smart_signals']}"
            if stats["candidates"] > 0:
                log.info("[CYCLE] %s", note)
            hb("alive", note, stats["inserted"] + stats["updated"])
            if time.time() - last_summary > DEEP_LOG_INTERVAL:
                last_summary = time.time()
                with connect() as conn:
                    conn.row_factory = sqlite3.Row
                    _log_summary(conn)
        except Exception as exc:
            log.warning("[CYCLE_ERROR] %s", exc)
            hb("warn", f"error: {exc}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
