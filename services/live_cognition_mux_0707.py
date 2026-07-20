from __future__ import annotations
import sqlite3, time, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
SERVICE = "live_cognition_mux_0707"

CYCLE_SEC = float(os.getenv("COGNITION_MUX_CYCLE_SEC", "6"))
MAX_DUP_SEC = float(os.getenv("COGNITION_MUX_MAX_DUP_SEC", "20"))

STAGE_BY_SERVICE = {
    "neural_supervisor": "SUPERVISOR",
    "execution_engine": "EXECUTOR",
    "market_intelligence": "QUALIFIER",
    "ingest_pipeline": "INGEST",
    "ingest": "INGEST",
    "resolver": "RESOLVER",
    "ws_price_oracle": "ORACLE",
    "oracle_autoheal": "ORACLE",
    "freshness_enforcer": "FRESHNESS",
    "system_guardian": "GUARDIAN",
    "polaris": "POLARIS",
    "polaris_auxiliary": "POLARIS",
    "promotion_signoff_bridge_0707": "PROMOTION",
    "paper_lilypad_capture_bridge_0707": "LILYPAD",
}

def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def cols(cur, table):
    try:
        return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()

def table_exists(cur, table):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None

def ensure_cognition_log(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cognition_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            stage TEXT,
            token TEXT,
            message TEXT,
            confidence REAL
        )
    """)

def ensure_heartbeat(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_heartbeat (
            service_name TEXT PRIMARY KEY,
            status TEXT,
            last_pulse REAL,
            note TEXT
        )
    """)

def insert_cognition(cur, stage, message, token="", confidence=0.0):
    ensure_cognition_log(cur)
    cc = cols(cur, "cognition_log")
    now = time.time()

    # Dedup same stage/message inside MAX_DUP_SEC.
    if {"timestamp", "stage", "message"}.issubset(cc):
        r = cur.execute("""
            SELECT 1 FROM cognition_log
            WHERE stage=? AND message=? AND COALESCE(timestamp,0) >= ?
            LIMIT 1
        """, (stage, message, now - MAX_DUP_SEC)).fetchone()
        if r:
            return False

    fields, vals = [], []
    for col, val in [
        ("timestamp", now),
        ("stage", stage),
        ("token", token),
        ("message", message[:500]),
        ("confidence", confidence),
    ]:
        if col in cc:
            fields.append(col)
            vals.append(val)

    if not fields:
        return False

    q = f"INSERT INTO cognition_log ({','.join(fields)}) VALUES ({','.join(['?']*len(fields))})"
    cur.execute(q, vals)
    return True

def heartbeat(cur, note, status="ALIVE"):
    ensure_heartbeat(cur)
    now = time.time()
    cur.execute("""
        INSERT INTO system_heartbeat(service_name,status,last_pulse,note)
        VALUES(?,?,?,?)
        ON CONFLICT(service_name) DO UPDATE SET
            status=excluded.status,
            last_pulse=excluded.last_pulse,
            note=excluded.note
    """, (SERVICE, status, now, note[:500]))

def get_heartbeat_rows(cur):
    if not table_exists(cur, "system_heartbeat"):
        return []
    hc = cols(cur, "system_heartbeat")
    service_col = "service_name" if "service_name" in hc else ("name" if "name" in hc else None)
    status_col = "status" if "status" in hc else None
    pulse_col = "last_pulse" if "last_pulse" in hc else ("updated_at" if "updated_at" in hc else None)
    note_col = "note" if "note" in hc else ("details" if "details" in hc else None)
    if not service_col:
        return []

    return cur.execute(f"""
        SELECT
          {service_col} AS service,
          {status_col or "''"} AS status,
          {pulse_col or "0"} AS pulse,
          {note_col or "''"} AS note
        FROM system_heartbeat
        ORDER BY {service_col}
    """).fetchall()

def synth_from_heartbeats(cur):
    now = time.time()
    made = 0
    rows = get_heartbeat_rows(cur)

    for r in rows:
        service = str(r["service"] or "")
        stage = STAGE_BY_SERVICE.get(service)
        if not stage:
            continue

        status = str(r["status"] or "UNKNOWN")
        note = str(r["note"] or "")
        try:
            age = int(now - float(r["pulse"] or 0))
        except Exception:
            age = -1

        if age > 900:
            continue

        msg = f"{service} {status} age={age}s"
        if note:
            msg += f" | {note[:180]}"

        if insert_cognition(cur, stage, msg):
            made += 1

    return made

def synth_from_positions(cur):
    if not table_exists(cur, "paper_positions"):
        return 0
    pc = cols(cur, "paper_positions")
    needed = {"id", "mint_address", "status"}
    if not needed.issubset(pc):
        return 0

    fields = [c for c in [
        "id", "mint_address", "status", "entry_price", "last_price", "current_price",
        "unrealized_pnl_pct", "peak_pnl_pct", "realized_pnl_usd", "exit_reason",
        "opened_at", "closed_at"
    ] if c in pc]

    made = 0
    rows = cur.execute(f"""
        SELECT {','.join(fields)}
        FROM paper_positions
        ORDER BY id DESC
        LIMIT 8
    """).fetchall()

    for p in rows:
        status = str(p["status"] or "")
        mint = str(p["mint_address"] or "")[:10]
        pid = p["id"]

        if status.upper() == "OPEN":
            upct = p["unrealized_pnl_pct"] if "unrealized_pnl_pct" in p.keys() else None
            peak = p["peak_pnl_pct"] if "peak_pnl_pct" in p.keys() else None
            msg = f"paper position open id={pid} {mint} unreal={upct} peak={peak}"
            if insert_cognition(cur, "EXECUTOR", msg, token=mint, confidence=0.0):
                made += 1

        elif status.upper() == "CLOSED":
            pnl = p["realized_pnl_usd"] if "realized_pnl_usd" in p.keys() else None
            reason = p["exit_reason"] if "exit_reason" in p.keys() else ""
            msg = f"paper position closed id={pid} {mint} pnl=${pnl} reason={reason}"
            if insert_cognition(cur, "EXECUTOR", msg, token=mint, confidence=0.0):
                made += 1

    return made

def synth_from_snapshots(cur):
    if not table_exists(cur, "market_snapshots"):
        return 0
    mc = cols(cur, "market_snapshots")
    if not {"id", "mint_address"}.issubset(mc):
        return 0

    made = 0

    if {"quality_status", "candidate_state", "price_status", "is_tradeable"}.issubset(mc):
        r = cur.execute("""
            SELECT
              SUM(CASE WHEN quality_status='qualified' OR candidate_state='qualified' THEN 1 ELSE 0 END) qualified,
              SUM(CASE WHEN COALESCE(is_tradeable,0)=1 THEN 1 ELSE 0 END) tradeable,
              SUM(CASE WHEN COALESCE(latched,0)=1 THEN 1 ELSE 0 END) latched,
              SUM(CASE WHEN COALESCE(execution_ready,0)>0 THEN 1 ELSE 0 END) exec_ready
            FROM market_snapshots
        """).fetchone()
        msg = f"flow q={r['qualified'] or 0} tradeable={r['tradeable'] or 0} latched={r['latched'] or 0} exec_ready={r['exec_ready'] or 0}"
        if insert_cognition(cur, "SUPERVISOR", msg):
            made += 1

    if {"price_status", "observed_price", "price_updated_at"}.issubset(mc):
        r = cur.execute("""
            SELECT COUNT(*) priced
            FROM market_snapshots
            WHERE price_status='priced' AND COALESCE(observed_price,0)>0
        """).fetchone()
        msg = f"priced snapshots active total={r['priced'] or 0}"
        if insert_cognition(cur, "ORACLE", msg):
            made += 1

    return made

def main():
    print("="*90)
    print("LIVE COGNITION MUX 0707")
    print("Writes UI-only cognition_log rows from heartbeat/positions/snapshots.")
    print("Does not trade/latch/open/close.")
    print("DB:", DB)
    print("Ctrl+C to stop")
    print("="*90)

    while True:
        try:
            con = connect()
            cur = con.cursor()

            made_hb = synth_from_heartbeats(cur)
            made_pos = synth_from_positions(cur)
            made_snap = synth_from_snapshots(cur)

            note = f"cognition_rows hb={made_hb} positions={made_pos} snapshots={made_snap} cycle={CYCLE_SEC}s"
            heartbeat(cur, note)
            con.commit()
            con.close()

            print(time.strftime("%H:%M:%S"), note)

        except KeyboardInterrupt:
            print("stopped")
            break
        except Exception as e:
            print("ERROR:", type(e).__name__, e)
            try:
                con = connect()
                cur = con.cursor()
                heartbeat(cur, f"ERROR {type(e).__name__}: {e}", "ERROR")
                con.commit()
                con.close()
            except Exception:
                pass

        time.sleep(CYCLE_SEC)

if __name__ == "__main__":
    main()
