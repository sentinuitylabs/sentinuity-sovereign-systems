#!/usr/bin/env python3
from pathlib import Path
import sqlite3,subprocess,sys
ROOT=Path(__file__).resolve().parent.parent; DB=ROOT/'sentinuity_matrix.db'; ARC=ROOT/'sentinuity_archive.db'; REPORT=ROOT/'runtime'/'shutdown_retention.json'
def opens(c):
 n=0
 for t in ('paper_positions','live_positions','substrate_positions','substrate_paper_positions'):
  try:
   cs={r[1] for r in c.execute(f'pragma table_info("{t}")')}; sc=next((x for x in ('status','state','position_state') if x in cs),None)
   if sc: n+=c.execute(f'''SELECT COUNT(*) FROM "{t}" WHERE UPPER(COALESCE("{sc}",'')) IN ('OPEN','ACTIVE','PENDING','LIVE','EXECUTING','SUBMITTED')''').fetchone()[0]
  except Exception: pass
 return n
if not DB.exists(): print('No matrix DB; maintenance not required.'); raise SystemExit(0)
c=sqlite3.connect(DB); q=c.execute('pragma quick_check').fetchone()[0]; n=opens(c); c.close()
if q!='ok': print('SAFETY ABORT quick_check='+str(q)); raise SystemExit(10)
if n: print(f'SAFETY HOLD: {n} open/active position(s); prune skipped.'); raise SystemExit(11)
REPORT.parent.mkdir(exist_ok=True)
cmd=[sys.executable,str(ROOT/'launch/db_retention_trim.py'),'--db',str(DB),'--archive',str(ARC),'--apply','--vacuum','--target-mb','10','--max-safe-mb','20','--heartbeat-grace-seconds','15','--json',str(REPORT)]
raise SystemExit(subprocess.run(cmd,cwd=ROOT).returncode)
