#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, sqlite3, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'sentinuity_matrix.db'
MARKER=ROOT/'runtime'/'family_live.enable'
ACK='I_ACCEPT_REAL_LOSS'

def _upsert(c,k,v,desc):
    cols={r[1] for r in c.execute('PRAGMA table_info(system_config)')}
    if 'updated_at' in cols and 'description' in cols:
        c.execute("INSERT INTO system_config(key,value,description,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description,updated_at=excluded.updated_at",(k,v,desc,time.time()))
    elif 'description' in cols:
        c.execute("INSERT INTO system_config(key,value,description) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description",(k,v,desc))
    else:
        c.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))

def private_enabled():
    return MARKER.is_file() and os.getenv('SENTINUITY_PRIVATE_LIVE_ENABLE','').strip()==ACK

def desired(requested,size):
    common={
      'PAPER_TRADING_ENABLED':'1','LIVE_PAPER_SHADOW_ON_BLOCK':'1',
      'SMART_WALLET_LIVE_ENABLED':'0','WALLET_COPY_TRADE_ENABLED':'0',
      'RUNNER_LIVE_ESCALATION_ENABLED':'0','LATCHED_OVERRIDE_ENABLED':'0',
      'LIVE_MAX_OPEN_POSITIONS':'1','LIVE_MAX_TOTAL_EXPOSURE_USD':size,
      'LIVE_POSITION_SIZE_USD':size,'LIVE_TRADE_AMOUNT_USD':size,
      'OPERATOR_LIVE_POSITION_SIZE_USD':size,
    }
    if requested=='dual' and private_enabled():
        return 'FAMILY_LIVE_CANARY', common|{
          'TRADING_MODE':'live','DUAL_MODE_ENABLED':'1','DUAL_MODE_ARMED':'1',
          'LIVE_TRADING_ENABLED':'1','LIVE_MODE_B_ENABLED':'1','LIVE_ARMED':'1',
          'LIVE_MONEY_MODE':'1','EXECUTION_ARMED':'1','LIVE_SUBMISSION_BACKEND':'real',
          'DECLARED_POSTURE':'FAMILY_LIVE_CANARY'}
    if requested=='dual':
        return 'PUBLIC_DUAL_STUB', common|{
          'TRADING_MODE':'paper','DUAL_MODE_ENABLED':'1','DUAL_MODE_ARMED':'0',
          'LIVE_TRADING_ENABLED':'0','LIVE_MODE_B_ENABLED':'1','LIVE_ARMED':'0',
          'LIVE_MONEY_MODE':'0','EXECUTION_ARMED':'0','LIVE_SUBMISSION_BACKEND':'stub',
          'DECLARED_POSTURE':'PUBLIC_DUAL_STUB'}
    return 'PAPER_ONLY', common|{
      'TRADING_MODE':'paper','DUAL_MODE_ENABLED':'0','DUAL_MODE_ARMED':'0',
      'LIVE_TRADING_ENABLED':'0','LIVE_MODE_B_ENABLED':'0','LIVE_ARMED':'0',
      'LIVE_MONEY_MODE':'0','EXECUTION_ARMED':'0','LIVE_SUBMISSION_BACKEND':'stub',
      'DECLARED_POSTURE':'PAPER_ONLY'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--requested',choices=['paper','dual'],default='paper'); ap.add_argument('--size',type=float,default=3.0)
    a=ap.parse_args(); size=format(max(0.01,a.size),'.8g')
    if not DB.exists(): print('[POSTURE] FAIL missing DB'); return 2
    profile,pairs=desired(a.requested,size)
    con=sqlite3.connect(DB,timeout=20); c=con.cursor(); c.execute('CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT,description TEXT,updated_at REAL)')
    c.execute('BEGIN IMMEDIATE')
    for k,v in pairs.items(): _upsert(c,k,v,'canonical final launch posture')
    con.commit()
    bad=[]
    for k,v in pairs.items():
        r=c.execute('SELECT value FROM system_config WHERE key=?',(k,)).fetchone(); got=str(r[0]) if r else None
        if got!=v: bad.append(f'{k}={got!r} expected={v!r}')
    con.close()
    if bad:
        print('[POSTURE] FAIL '+'; '.join(bad)); return 3
    print(f'[POSTURE] PASS profile={profile} requested={a.requested} size=${size}')
    if a.requested=='dual' and profile=='PUBLIC_DUAL_STUB':
        print('[POSTURE] Dual analytics active; on-chain submission STUBBED. Private marker/ack absent.')
    return 0
if __name__=='__main__': raise SystemExit(main())
