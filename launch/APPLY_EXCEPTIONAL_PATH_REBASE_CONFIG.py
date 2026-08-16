#!/usr/bin/env python3
"""Apply the signed exceptional-path compatibility configuration.
Run only while Sentinuity is stopped. Does not touch keys, wallets or sizing.
"""
from __future__ import annotations
import sqlite3, time
from pathlib import Path
DB=Path('sentinuity_matrix.db')
VALUES={
    'TRADING_MODE':'live',
    'DUAL_MODE_ENABLED':'1',
    'PAPER_TRADING_ENABLED':'1',
    'LIVE_TRADING_ENABLED':'1',
    'CALIBRATION_ADMISSION_MODE':'shadow',
    'PAPER_HONOURS_MODE_B':'0',
}
if not DB.exists():
    raise SystemExit(f'[ABORT] missing {DB.resolve()}')
con=sqlite3.connect(DB,timeout=15)
try:
    con.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)')
    for k,v in VALUES.items():
        con.execute('INSERT OR REPLACE INTO system_config(key,value) VALUES (?,?)',(k,v))
    con.commit()
    for k in VALUES:
        r=con.execute('SELECT value FROM system_config WHERE key=?',(k,)).fetchone()
        print(f'{k}={r[0] if r else "MISSING"}')
finally:
    con.close()
print('[OK] exceptional-path dual configuration applied')
