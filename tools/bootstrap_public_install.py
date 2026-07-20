#!/usr/bin/env python3
from pathlib import Path
import os, sqlite3, time, json, sys
ROOT=Path(__file__).resolve().parent.parent
os.environ.setdefault('SENTINUITY_ROOT',str(ROOT))
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from core.schema import init_db, ensure_hub_compat_schema, startup_cleanup, get_connection

def precreate_index_contracts():
 p=ROOT/'sentinuity_matrix.db'; c=sqlite3.connect(p)
 c.executescript("""
 CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT);
 CREATE TABLE IF NOT EXISTS market_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL,raw_dna_id INTEGER,tx_hash TEXT,token_name TEXT,confidence_score REAL,entropy REAL,buy_velocity REAL,cluster_id INTEGER,logic_breakdown TEXT,latched INTEGER DEFAULT 0,executed INTEGER DEFAULT 0,candidate_state TEXT DEFAULT 'pending',execution_ready INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS paper_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,mint_address TEXT,token_name TEXT,status TEXT DEFAULT 'OPEN',opened_at REAL,closed_at REAL,entry_price REAL,position_size_usd REAL DEFAULT 0,realized_pnl_usd REAL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cognition_log (id INTEGER PRIMARY KEY AUTOINCREMENT,stage TEXT,timestamp REAL);
 CREATE TABLE IF NOT EXISTS polaris_trade_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,reviewed_at REAL);
 """)
 c.commit(); c.close()

def ensure_intelligence():
 p=ROOT/'sentinuity_intelligence.db'; c=sqlite3.connect(p)
 c.execute('CREATE TABLE IF NOT EXISTS intelligence_meta(key TEXT PRIMARY KEY,value TEXT,updated_at REAL)')
 c.execute('CREATE TABLE IF NOT EXISTS research_sources(id INTEGER PRIMARY KEY AUTOINCREMENT,source_type TEXT,source_ref TEXT,title TEXT,retrieved_at REAL,provenance_json TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS intelligence_findings(id INTEGER PRIMARY KEY AUTOINCREMENT,topic TEXT,summary TEXT,evidence_json TEXT,created_at REAL)')
 c.execute('INSERT OR REPLACE INTO intelligence_meta VALUES (?,?,?)',('schema_version','SENTINUITY_V2_1',time.time())); c.commit(); c.close()
def main():
 precreate_index_contracts(); init_db(); ensure_hub_compat_schema(); startup_cleanup(); ensure_intelligence()
 from services.council_build_orchestrator import ensure_tables,seed_roles,seed_tasks,seed_strategies,sync_global_standing_tasks
 with get_connection() as c:
  ensure_tables(c); seed_roles(c); seed_tasks(c); seed_strategies(c); sync_global_standing_tasks(c)
  c.execute('CREATE TABLE IF NOT EXISTS installation_meta(key TEXT PRIMARY KEY,value TEXT,updated_at REAL)')
  for k,v in {'schema_version':'SENTINUITY_V2_1','paper_safe':'1','installed_root':str(ROOT)}.items(): c.execute('INSERT OR REPLACE INTO installation_meta VALUES (?,?,?)',(k,v,time.time()))
  c.commit()
 (ROOT/'runtime').mkdir(exist_ok=True); (ROOT/'runtime'/'first_run_report.json').write_text(json.dumps({'matrix':'ready','intelligence':'ready','council':'ready','fabricated_trade_history':False},indent=2))
 print('Matrix database: ready\nIntelligence database: ready\nCouncil roster: ready\nNo fabricated trade history was installed.')
if __name__=='__main__': raise SystemExit(main())
