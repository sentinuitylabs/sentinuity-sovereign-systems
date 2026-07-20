from __future__ import annotations
import sqlite3, time, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
SERVICE = "paper_lilypad_capture_bridge_0707"

CYCLE_SEC = float(os.getenv("LILYPAD_BRIDGE_CYCLE_SEC", "2.0"))
MAX_PRICE_AGE_SEC = float(os.getenv("LILYPAD_MAX_PRICE_AGE_SEC", "900"))

def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def cols(cur, table):
    try:
        return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()

def heartbeat(cur, note, status="ALIVE"):
    now = time.time()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_heartbeat (
            service_name TEXT PRIMARY KEY,
            status TEXT,
            last_pulse REAL,
            note TEXT
        )
    """)
    cur.execute("""
        INSERT INTO system_heartbeat(service_name,status,last_pulse,note)
        VALUES(?,?,?,?)
        ON CONFLICT(service_name) DO UPDATE SET
            status=excluded.status,
            last_pulse=excluded.last_pulse,
            note=excluded.note
    """, (SERVICE, status, now, note[:500]))

def ensure_lifecycle(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            mint_address TEXT,
            event_type TEXT,
            price REAL,
            pct_from_entry REAL,
            age_sec REAL,
            source TEXT,
            can_execute INTEGER,
            tick_count INTEGER,
            coverage_score REAL,
            first_tick_delay_sec REAL,
            created_at REAL
        )
    """)

def latest_price(cur, mint):
    return cur.execute("""
        SELECT observed_price, price_updated_at
        FROM market_snapshots
        WHERE mint_address=?
          AND COALESCE(observed_price,0)>0
        ORDER BY COALESCE(price_updated_at,updated_at,created_at,0) DESC
        LIMIT 1
    """, (mint,)).fetchone()

def mark_open_positions(cur):
    now = time.time()
    pc = cols(cur, "paper_positions")
    if not {"id","mint_address","status","entry_price"}.issubset(pc):
        return 0, 0

    rows = cur.execute("""
        SELECT *
        FROM paper_positions
        WHERE UPPER(COALESCE(status,''))='OPEN'
        ORDER BY id DESC
    """).fetchall()

    marked = 0
    events = 0

    for p in rows:
        mint = p["mint_address"]
        entry = float(p["entry_price"] or 0)
        if not mint or entry <= 0:
            continue

        s = latest_price(cur, mint)
        if not s:
            continue

        px = float(s["observed_price"] or 0)
        pts = float(s["price_updated_at"] or 0)
        if px <= 0 or (now - pts) > MAX_PRICE_AGE_SEC:
            continue

        pct = ((px / entry) - 1.0) * 100.0
        qty = float(p["quantity"] or 0) if "quantity" in pc else 0.0
        pos_size = float(p["position_size_usd"] or 0) if "position_size_usd" in pc else 0.0
        unreal = (qty * px - pos_size) if qty and pos_size else (pos_size * pct / 100.0 if pos_size else 0.0)

        old_peak = float(p["peak_pnl_pct"] or 0) if "peak_pnl_pct" in pc else 0.0
        new_peak = max(old_peak, pct)

        old_high = float(p["highest_price_seen"] or 0) if "highest_price_seen" in pc and p["highest_price_seen"] is not None else 0.0
        new_high = max(old_high, px)

        sets, vals = [], []
        for col, val in [
            ("last_price", px),
            ("current_price", px),
            ("last_marked_at", now),
            ("updated_at", now),
            ("unrealized_pnl_pct", pct),
            ("unrealized_pnl_usd", unreal),
            ("peak_pnl_pct", new_peak),
            ("highest_price_seen", new_high),
            ("market_value_usd", qty * px if qty else None),
            ("mark_source", SERVICE),
        ]:
            if col in pc and val is not None:
                sets.append(f"{col}=?")
                vals.append(val)

        if sets:
            vals.append(p["id"])
            cur.execute(f"UPDATE paper_positions SET {', '.join(sets)} WHERE id=?", vals)
            marked += 1

        tick_count = cur.execute(
            "SELECT COUNT(*) FROM trade_lifecycle_events WHERE position_id=?",
            (p["id"],)
        ).fetchone()[0] + 1

        cur.execute("""
            INSERT INTO trade_lifecycle_events(
                position_id,mint_address,event_type,price,pct_from_entry,age_sec,
                source,can_execute,tick_count,coverage_score,first_tick_delay_sec,created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p["id"], mint, "MTM_TICK", px, pct,
            now - float(p["opened_at"] or now) if "opened_at" in pc else None,
            SERVICE, 1, tick_count, 1.0, None, now
        ))
        events += 1

    return marked, events

def normalize_fresh_latched(cur):
    now = time.time()
    mc = cols(cur, "market_snapshots")
    needed = {"quality_status","is_tradeable","latched","execution_ready","candidate_state","price_updated_at","observed_price"}
    if not needed.issubset(mc):
        return 0

    cur.execute("""
        UPDATE market_snapshots
        SET candidate_state='latched',
            execution_ready=2,
            execution_ready_at=?,
            updated_at=?
        WHERE COALESCE(quality_status,'')='qualified'
          AND COALESCE(is_tradeable,0)=1
          AND COALESCE(latched,0)=1
          AND COALESCE(execution_ready,0)=0
          AND COALESCE(observed_price,0)>0
          AND COALESCE(price_updated_at,0) >= ?
          AND COALESCE(candidate_state,'') IN ('vetoed','qualified','pending','')
    """, (now, now, now - MAX_PRICE_AGE_SEC))
    return cur.rowcount or 0

def main():
    print("="*90)
    print("PAPER LILYPAD CAPTURE / LIFECYCLE BRIDGE 0707")
    print("DB:", DB)
    print("Ctrl+C to stop")
    print("="*90)

    while True:
        try:
            con = connect()
            cur = con.cursor()
            ensure_lifecycle(cur)
            norm = normalize_fresh_latched(cur)
            marked, events = mark_open_positions(cur)
            note = f"marked_open_positions={marked} lifecycle_events={events} normalized_latched={norm} cycle={CYCLE_SEC}s"
            heartbeat(cur, note)
            con.commit()
            con.close()
            print(time.strftime("%H:%M:%S"), note)
        except KeyboardInterrupt:
            print("stopped")
            break
        except Exception as e:
            print("ERROR:", type(e).__name__, e)
        time.sleep(CYCLE_SEC)

if __name__ == "__main__":
    main()
