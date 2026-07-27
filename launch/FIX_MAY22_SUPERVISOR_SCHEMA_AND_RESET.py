import sqlite3, time, os
DB = "sentinuity_matrix.db"
if not os.path.exists(DB):
    raise SystemExit(f"DB not found: {DB}. Run from trading-bot root.")
conn = sqlite3.connect(DB)
c = conn.cursor()

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
    "qualified_at": "REAL",
    "mint_confidence": "REAL DEFAULT 0",
    "confidence": "REAL DEFAULT 0",
    "calibrated_confidence": "REAL DEFAULT 0",
    "updated_at": "REAL",
    "meta": "TEXT DEFAULT '{}'",
}
cols = {r[1] for r in c.execute("PRAGMA table_info(market_snapshots)").fetchall()}
added=[]
for col, typ in wanted.items():
    if col not in cols:
        c.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} {typ}")
        added.append(col)

now=time.time()
# Reset recent qualified OK rows that are visible but never latched.
c.execute("""
UPDATE market_snapshots
SET latched=0,
    execution_ready=0,
    executed=0,
    candidate_state='qualified',
    quality_status='qualified',
    quality_reason='OK',
    updated_at=COALESCE(updated_at, ?)
WHERE COALESCE(updated_at,timestamp,created_at,first_seen_at,0) >= ?
  AND COALESCE(mint_address,'') != ''
  AND COALESCE(price_status,'')='priced'
  AND COALESCE(is_tradeable,0)=1
  AND COALESCE(observed_price,0)>0
  AND COALESCE(mint_confidence,calibrated_confidence,confidence,0) >= 0.65
  AND COALESCE(latched,0)=0
  AND COALESCE(execution_ready,0)=0
  AND candidate_state IN ('qualified','pending')
""", (now, now-3600))
reset=c.rowcount

# Clear stale supervisor ERROR status so relaunch state is clean.
c.execute("""
INSERT INTO system_heartbeat(service_name,last_pulse,status,note)
VALUES('neural_supervisor',?,'ALIVE','supervisor schema reset ready')
ON CONFLICT(service_name) DO UPDATE SET last_pulse=excluded.last_pulse,status=excluded.status,note=excluded.note
""", (now,))

c.execute("CREATE INDEX IF NOT EXISTS idx_ms_supervisor_visible ON market_snapshots(candidate_state, quality_status, price_status, is_tradeable, latched, execution_ready, price_updated_at)")
c.execute("CREATE INDEX IF NOT EXISTS idx_ms_latched_exec ON market_snapshots(latched, execution_ready, latched_at)")
conn.commit(); conn.close()
print("MAY22 supervisor schema/reset complete")
print("added columns:", added if added else "none")
print("reset visible qualified rows:", reset)
print("Next: cmd /c launch\\Shutdown_Sentinuity.bat && cmd /c launch\\Launch_Sentinuity.bat")
