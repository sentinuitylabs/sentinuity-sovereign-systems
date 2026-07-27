import os, sqlite3, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MATRIX = os.path.join(ROOT, 'sentinuity_matrix.db')
INTEL = os.path.join(ROOT, 'sentinuity_intelligence.db')
now = time.time()

def connect(path):
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=5000')
    return con

def cols(con, table):
    try:
        return {r[1] for r in con.execute(f'PRAGMA table_info({table})').fetchall()}
    except Exception:
        return set()

def add_col(con, table, col, typ, added):
    c = cols(con, table)
    if col not in c:
        con.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
        added.append(f'{table}.{col}')

added=[]
con=connect(MATRIX)
cur=con.cursor()

cur.execute('''
CREATE TABLE IF NOT EXISTS raw_dna (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL,
    tx_hash TEXT,
    program TEXT,
    instruction TEXT,
    token TEXT,
    amount REAL,
    entropy REAL,
    logs TEXT,
    processed INTEGER DEFAULT 0,
    processed_at REAL,
    processed_state INTEGER DEFAULT 0,
    claim_until REAL,
    created_at REAL,
    updated_at REAL,
    resolved_at REAL,
    resolution_status TEXT,
    resolution_note TEXT,
    mint_address TEXT,
    mint_confidence REAL,
    confidence REAL,
    resolution_method TEXT,
    forensic_bundle TEXT,
    first_seen_at REAL,
    resolver_claimed_at REAL,
    resolver_claim_id TEXT
)
''')
for col, typ in {
    'timestamp':'REAL', 'tx_hash':'TEXT', 'program':'TEXT', 'instruction':'TEXT', 'token':'TEXT',
    'amount':'REAL', 'entropy':'REAL', 'logs':'TEXT', 'processed':'INTEGER DEFAULT 0',
    'processed_at':'REAL', 'processed_state':'INTEGER DEFAULT 0', 'claim_until':'REAL',
    'created_at':'REAL', 'updated_at':'REAL', 'resolved_at':'REAL', 'resolution_status':'TEXT',
    'resolution_note':'TEXT', 'mint_address':'TEXT', 'mint_confidence':'REAL', 'confidence':'REAL',
    'resolution_method':'TEXT', 'forensic_bundle':'TEXT', 'first_seen_at':'REAL',
    'resolver_claimed_at':'REAL', 'resolver_claim_id':'TEXT'
}.items():
    add_col(con, 'raw_dna', col, typ, added)
cur.execute('UPDATE raw_dna SET first_seen_at=COALESCE(first_seen_at,timestamp,created_at,?) WHERE first_seen_at IS NULL', (now,))
cur.execute('UPDATE raw_dna SET timestamp=COALESCE(timestamp,first_seen_at,created_at,?) WHERE timestamp IS NULL', (now,))
cur.execute('UPDATE raw_dna SET created_at=COALESCE(created_at,first_seen_at,timestamp,?) WHERE created_at IS NULL', (now,))
cur.execute('UPDATE raw_dna SET updated_at=COALESCE(updated_at,created_at,first_seen_at,timestamp,?) WHERE updated_at IS NULL', (now,))
cur.execute('UPDATE raw_dna SET processed_state=COALESCE(processed_state,processed,0) WHERE processed_state IS NULL')
cur.execute("UPDATE raw_dna SET logs=COALESCE(logs,'[]') WHERE logs IS NULL")
cur.execute("DELETE FROM raw_dna WHERE tx_hash IS NULL OR TRIM(tx_hash)='' ")
cur.execute('''
DELETE FROM raw_dna
WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM raw_dna GROUP BY tx_hash
)
''')
cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_dna_tx_hash ON raw_dna(tx_hash)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_raw_dna_processed_state ON raw_dna(processed_state)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_raw_dna_first_seen_at ON raw_dna(first_seen_at)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_raw_dna_timestamp ON raw_dna(timestamp)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_raw_dna_claim_until ON raw_dna(claim_until)')

cur.execute('''
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL,
    created_at REAL,
    updated_at REAL,
    first_seen_at REAL,
    mint_address TEXT,
    token_name TEXT,
    token_symbol TEXT,
    confidence_score REAL,
    calibrated_confidence REAL,
    candidate_state TEXT DEFAULT 'pending',
    quality_reason TEXT DEFAULT '',
    execution_ready INTEGER DEFAULT 0,
    latched INTEGER DEFAULT 0,
    latched_at REAL,
    execution_ready_at REAL,
    price_updated_at REAL,
    observed_price REAL,
    market_cap_usd REAL
)
''')
for col, typ in {
    'timestamp':'REAL','created_at':'REAL','updated_at':'REAL','first_seen_at':'REAL',
    'candidate_state':'TEXT DEFAULT \'pending\'','quality_reason':'TEXT DEFAULT \'\'',
    'execution_ready':'INTEGER DEFAULT 0','latched':'INTEGER DEFAULT 0','latched_at':'REAL',
    'execution_ready_at':'REAL','price_updated_at':'REAL','observed_price':'REAL',
    'market_cap_usd':'REAL','calibrated_confidence':'REAL','confidence_score':'REAL',
    'mint_address':'TEXT','token_name':'TEXT','token_symbol':'TEXT'
}.items():
    add_col(con, 'market_snapshots', col, typ, added)
cur.execute('CREATE INDEX IF NOT EXISTS idx_market_snapshots_state ON market_snapshots(candidate_state)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_market_snapshots_updated ON market_snapshots(updated_at)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_market_snapshots_mint ON market_snapshots(mint_address)')

cur.execute('''CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    description TEXT,
    updated_at REAL
)''')
cur.execute('''CREATE TABLE IF NOT EXISTS system_heartbeat (
    service_name TEXT PRIMARY KEY,
    last_pulse REAL,
    status TEXT,
    restart_claimed_until REAL,
    note TEXT,
    work_processed INTEGER DEFAULT 0,
    last_success_at REAL
)''')
cur.execute('''CREATE TABLE IF NOT EXISTS service_heartbeats (
    service_name TEXT PRIMARY KEY,
    status TEXT,
    note TEXT,
    last_seen REAL,
    work_processed INTEGER DEFAULT 0,
    last_success_at REAL,
    restart_claimed_until REAL,
    message TEXT,
    last_heartbeat REAL,
    details TEXT
)''')
con.commit(); con.close()

# Intelligence DB / mtm_ticks. Recreate only if malformed.
recreate=False
try:
    icon=sqlite3.connect(INTEL)
    ok=icon.execute('PRAGMA integrity_check').fetchone()[0]
    if ok.lower()!='ok': recreate=True
    icon.close()
except Exception:
    recreate=True
if recreate:
    try: os.remove(INTEL)
    except FileNotFoundError: pass
    print('sentinuity_intelligence.db: recreated')
else:
    print('sentinuity_intelligence.db: ok')
icon=connect(INTEL)
icon.execute('''CREATE TABLE IF NOT EXISTS mtm_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint_address TEXT,
    price REAL,
    ts REAL,
    source TEXT,
    created_at REAL
)''')
icon.execute('CREATE INDEX IF NOT EXISTS idx_mtm_ticks_mint_ts ON mtm_ticks(mint_address, ts)')
icon.execute('CREATE INDEX IF NOT EXISTS idx_mtm_ticks_ts ON mtm_ticks(ts)')
icon.commit(); icon.close()

print('MAY22 runtime schema ok')
print('added:', ', '.join(added) if added else 'none')
