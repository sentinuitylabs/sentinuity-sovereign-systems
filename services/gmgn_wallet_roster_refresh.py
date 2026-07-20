"""Canonical GMGN roster refresh.
Delegates to wallet_scout's Cloudflare-capable, schema-tolerant client so the
system has one GMGN truth path. Also records append-only performance snapshots.
"""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from services.wallet_scout import ingest_gmgn_top_wallets, GMGN_LIMIT
import requests
DB=ROOT/'sentinuity_matrix.db'

def _snapshot():
    con=sqlite3.connect(DB)
    now=time.time()
    con.executescript('''CREATE TABLE IF NOT EXISTS smart_wallet_performance_snapshots(
      id INTEGER PRIMARY KEY AUTOINCREMENT,wallet_address TEXT NOT NULL,captured_at REAL NOT NULL,
      period TEXT NOT NULL DEFAULT '7d',source_rank INTEGER,realized_pnl REAL,win_rate REAL,total_trades INTEGER,
      source_name TEXT NOT NULL,raw_json TEXT);
      CREATE INDEX IF NOT EXISTS swps_wallet_time ON smart_wallet_performance_snapshots(wallet_address,captured_at DESC);''')
    cols={r[1] for r in con.execute('PRAGMA table_info(smart_wallet_profiles)')}
    wanted=['wallet_address','source_rank','realized_pnl','win_rate','total_trades','source_name','raw_json']
    if set(wanted).issubset(cols):
        rows=con.execute("SELECT wallet_address,source_rank,realized_pnl,win_rate,total_trades,source_name,raw_json FROM smart_wallet_profiles WHERE lower(source_name) LIKE 'gmgn%' ORDER BY source_rank LIMIT 100").fetchall()
        con.executemany("INSERT INTO smart_wallet_performance_snapshots(wallet_address,captured_at,period,source_rank,realized_pnl,win_rate,total_trades,source_name,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",[(r[0],now,'7d',r[1],r[2],r[3],r[4],r[5],r[6]) for r in rows])
    con.commit(); con.close(); return len(rows) if 'rows' in locals() else 0

def refresh_once():
    stats=ingest_gmgn_top_wallets(requests.Session(),GMGN_LIMIT)
    stats['snapshots']=_snapshot() if stats.get('status')=='OK' else 0
    print(json.dumps(stats,indent=2,default=str)); return 0 if stats.get('status')=='OK' else 1
if __name__=='__main__': raise SystemExit(refresh_once())
