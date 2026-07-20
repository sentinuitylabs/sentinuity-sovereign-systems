#!/usr/bin/env python3
"""
0707 PROMOTION SIGNOFF BRIDGE — paper-safe DB contract enforcer

Purpose:
  Live audits proved priced, high-mint-confidence rows were not being promoted:
    price_status='priced' + mint_confidence>=0.65 + confidence=0 + is_tradeable=0

This bridge runs beside the stack and enforces only the missing promotion contract.
It does NOT latch, execute, or open trades. It feeds the normal supervisor path by
turning valid priced candidates into qualified/tradeable candidates.

Lifecycle enforced:
  pending/priced + priced + confident + no rejection reason
    -> quality_status='qualified', quality_reason='OK', candidate_state='qualified', is_tradeable=1
    -> confidence/calibrated_confidence/confidence_score inherit mint_confidence

It deliberately leaves execution_ready/latched alone so neural_supervisor/executor
keep ownership of latch/open.
"""
from __future__ import annotations

import os
import sqlite3
import time
import traceback
from pathlib import Path

SERVICE = "promotion_signoff_bridge_0707"
DB_PATH = Path(os.environ.get("SENTINUITY_DB", "sentinuity_matrix.db"))
SLEEP_SEC = float(os.environ.get("PROMOTION_SIGNOFF_SLEEP_SEC", "2.0"))
DEFAULT_MIN_CONF = float(os.environ.get("PROMOTION_SIGNOFF_MIN_CONF", "0.65"))
DEFAULT_MAX_PRICE_AGE = float(os.environ.get("PROMOTION_SIGNOFF_MAX_PRICE_AGE_SEC", "900"))
DEFAULT_MAX_SIGNAL_AGE = float(os.environ.get("PROMOTION_SIGNOFF_MAX_SIGNAL_AGE_SEC", "900"))
DRY_RUN = os.environ.get("PROMOTION_SIGNOFF_DRY_RUN", "0").strip() == "1"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def cols(con: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(con, table):
        return set()
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def get_config(con: sqlite3.Connection, key: str, default: float) -> float:
    if not table_exists(con, "system_config"):
        return default
    try:
        r = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        if r is None:
            return default
        return float(str(r[0]).strip())
    except Exception:
        return default


def ensure_schema(con: sqlite3.Connection) -> None:
    if not table_exists(con, "market_snapshots"):
        raise RuntimeError("market_snapshots table not found")
    c = cols(con, "market_snapshots")
    # Only add low-risk compatibility columns if missing. Existing schema is preserved.
    additions = {
        "claimed_by": "TEXT",
        "qualified_at": "REAL",
        "execution_ready_at": "REAL",
        "source_note": "TEXT",
    }
    for name, typ in additions.items():
        if name not in c:
            try:
                con.execute(f"ALTER TABLE market_snapshots ADD COLUMN {name} {typ}")
            except Exception:
                pass
    # Heartbeat table: use existing if present; create simple fallback if missing.
    if not table_exists(con, "system_heartbeat"):
        con.execute("""
            CREATE TABLE IF NOT EXISTS system_heartbeat (
                service_name TEXT PRIMARY KEY,
                status TEXT,
                last_pulse REAL,
                note TEXT
            )
        """)
    con.commit()


def heartbeat(con: sqlite3.Connection, status: str, note: str) -> None:
    try:
        hc = cols(con, "system_heartbeat")
        service_col = "service_name" if "service_name" in hc else ("name" if "name" in hc else None)
        if service_col is None:
            return
        pulse_col = "last_pulse" if "last_pulse" in hc else ("updated_at" if "updated_at" in hc else None)
        status_col = "status" if "status" in hc else None
        note_col = "note" if "note" in hc else ("details" if "details" in hc else None)
        existing = con.execute(f"SELECT 1 FROM system_heartbeat WHERE {service_col}=?", (SERVICE,)).fetchone()
        if existing:
            sets, vals = [], []
            if status_col:
                sets.append(f"{status_col}=?"); vals.append(status)
            if pulse_col:
                sets.append(f"{pulse_col}=?"); vals.append(time.time())
            if note_col:
                sets.append(f"{note_col}=?"); vals.append(note[:500])
            if sets:
                vals.append(SERVICE)
                con.execute(f"UPDATE system_heartbeat SET {', '.join(sets)} WHERE {service_col}=?", vals)
        else:
            fields = [service_col]
            vals = [SERVICE]
            qs = ["?"]
            if status_col:
                fields.append(status_col); vals.append(status); qs.append("?")
            if pulse_col:
                fields.append(pulse_col); vals.append(time.time()); qs.append("?")
            if note_col:
                fields.append(note_col); vals.append(note[:500]); qs.append("?")
            con.execute(f"INSERT INTO system_heartbeat({','.join(fields)}) VALUES({','.join(qs)})", vals)
        con.commit()
    except Exception:
        pass


def promote_once(con: sqlite3.Connection) -> dict[str, int]:
    c = cols(con, "market_snapshots")
    now = time.time()
    min_conf = get_config(con, "PROMOTION_SIGNOFF_MIN_CONF", DEFAULT_MIN_CONF)
    max_price_age = get_config(con, "PROMOTION_SIGNOFF_MAX_PRICE_AGE_SEC", DEFAULT_MAX_PRICE_AGE)
    max_signal_age = get_config(con, "PROMOTION_SIGNOFF_MAX_SIGNAL_AGE_SEC", DEFAULT_MAX_SIGNAL_AGE)

    required = {"price_status", "mint_confidence", "observed_price", "is_tradeable"}
    missing = required - c
    if missing:
        raise RuntimeError(f"market_snapshots missing required columns: {sorted(missing)}")

    reason_ok = "(quality_reason IS NULL OR TRIM(CAST(quality_reason AS TEXT))='' OR UPPER(TRIM(CAST(quality_reason AS TEXT)))='OK')" if "quality_reason" in c else "1=1"
    qstatus_ok = "(quality_status IS NULL OR TRIM(CAST(quality_status AS TEXT))='' OR LOWER(TRIM(CAST(quality_status AS TEXT))) IN ('pending','priced','qualified','error'))" if "quality_status" in c else "1=1"
    cstate_ok = "(candidate_state IS NULL OR TRIM(CAST(candidate_state AS TEXT))='' OR LOWER(TRIM(CAST(candidate_state AS TEXT))) IN ('pending','priced','candidate','scored','qualified','execution_ready'))" if "candidate_state" in c else "1=1"
    price_age_ok = "(price_updated_at IS NOT NULL AND (? - COALESCE(price_updated_at,0)) BETWEEN 0 AND ?)" if "price_updated_at" in c else "1=1"
    signal_time_expr = "COALESCE(created_at, updated_at, price_updated_at, 0)" if any(x in c for x in ("created_at", "updated_at", "price_updated_at")) else "0"
    signal_age_ok = f"(({signal_time_expr}) > 0 AND (? - ({signal_time_expr})) BETWEEN 0 AND ?)"

    params = []
    if "price_updated_at" in c:
        params += [now, max_price_age]
    params += [now, max_signal_age, min_conf]

    where = f"""
        COALESCE(price_status,'')='priced'
        AND COALESCE(observed_price,0) > 0
        AND COALESCE(mint_confidence,0) >= ?
        AND COALESCE(is_tradeable,0)=0
        AND {reason_ok}
        AND {qstatus_ok}
        AND {cstate_ok}
        AND {price_age_ok}
        AND {signal_age_ok}
    """
    # Params order is where order: min_conf appears before age clauses in text? Actually fix by rebuilding.
    params = [min_conf]
    if "price_updated_at" in c:
        params += [now, max_price_age]
    params += [now, max_signal_age]

    count_sql = f"SELECT COUNT(*) FROM market_snapshots WHERE {where}"
    eligible = int(con.execute(count_sql, params).fetchone()[0])

    # Backfill confidence fields on eligible rows and promote to qualified/tradeable.
    set_parts = []
    if "quality_status" in c:
        set_parts.append("quality_status='qualified'")
    if "quality_reason" in c:
        set_parts.append("quality_reason='OK'")
    if "candidate_state" in c:
        set_parts.append("candidate_state='qualified'")
    if "is_tradeable" in c:
        set_parts.append("is_tradeable=1")
    if "confidence" in c:
        set_parts.append("confidence=COALESCE(NULLIF(confidence,0), mint_confidence)")
    if "calibrated_confidence" in c:
        set_parts.append("calibrated_confidence=COALESCE(NULLIF(calibrated_confidence,0), mint_confidence)")
    if "confidence_score" in c:
        set_parts.append("confidence_score=COALESCE(NULLIF(confidence_score,0), mint_confidence)")
    if "score" in c:
        set_parts.append("score=COALESCE(NULLIF(score,0), mint_confidence)")
    if "qualified_at" in c:
        set_parts.append(f"qualified_at={now}")
    if "updated_at" in c:
        set_parts.append(f"updated_at={now}")
    if "source_note" in c:
        set_parts.append("source_note=COALESCE(source_note,'') || '|0707_PROMOTION_SIGNOFF_BRIDGE'")
    # Explicitly do not set execution_ready or latched.

    promoted = 0
    if eligible and set_parts and not DRY_RUN:
        cur = con.execute(f"UPDATE market_snapshots SET {', '.join(set_parts)} WHERE {where}", params)
        promoted = cur.rowcount if cur.rowcount is not None else eligible

    # Confidence inheritance for same-mint priced MTM rows: prevents zero-confidence shadows from dominating displays.
    inherited = 0
    if "mint_address" in c and any(x in c for x in ("confidence", "calibrated_confidence", "confidence_score")):
        # Only fill fields; do not qualify mtm/vetoed/rejected rows.
        inherit_sets = []
        if "confidence" in c:
            inherit_sets.append("confidence=(SELECT MAX(m2.mint_confidence) FROM market_snapshots m2 WHERE m2.mint_address=market_snapshots.mint_address)")
        if "calibrated_confidence" in c:
            inherit_sets.append("calibrated_confidence=(SELECT MAX(m2.mint_confidence) FROM market_snapshots m2 WHERE m2.mint_address=market_snapshots.mint_address)")
        if "confidence_score" in c:
            inherit_sets.append("confidence_score=(SELECT MAX(m2.mint_confidence) FROM market_snapshots m2 WHERE m2.mint_address=market_snapshots.mint_address)")
        if inherit_sets and not DRY_RUN:
            cur = con.execute(f"""
                UPDATE market_snapshots
                SET {', '.join(inherit_sets)}
                WHERE COALESCE(price_status,'')='priced'
                  AND COALESCE(mint_confidence,0)=0
                  AND COALESCE(confidence,0)=0
                  AND EXISTS (
                      SELECT 1 FROM market_snapshots m2
                      WHERE m2.mint_address=market_snapshots.mint_address
                        AND COALESCE(m2.mint_confidence,0) >= ?
                  )
            """, (min_conf,))
            inherited = cur.rowcount if cur.rowcount is not None else 0

    con.commit()
    return {"eligible": eligible, "promoted": promoted, "inherited": inherited}


def main() -> int:
    print("=" * 90)
    print("0707 PROMOTION SIGNOFF BRIDGE")
    print("DB:", DB_PATH.resolve())
    print("DRY_RUN:", DRY_RUN)
    print("Ctrl+C to stop")
    print("=" * 90)
    while True:
        try:
            with connect() as con:
                ensure_schema(con)
                stats = promote_once(con)
                note = f"eligible={stats['eligible']} promoted={stats['promoted']} inherited={stats['inherited']} sleep={SLEEP_SEC}s"
                heartbeat(con, "ALIVE", note)
                print(time.strftime("%H:%M:%S"), note, flush=True)
        except KeyboardInterrupt:
            print("stopped")
            return 0
        except Exception as e:
            try:
                with connect() as con:
                    heartbeat(con, "ERROR", f"{type(e).__name__}: {e}")
            except Exception:
                pass
            print("ERROR:", repr(e), flush=True)
            traceback.print_exc()
            time.sleep(max(SLEEP_SEC, 5.0))
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
