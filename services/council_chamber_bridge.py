#!/usr/bin/env python3
'''Sentinuity Council Chamber continuity bridge.

Canonical responsibilities:
- keep the matrix DB standing-task schema boot-safe and resumable;
- seed the operator-approved pre-live standing tasks idempotently;
- recover expired task claims without duplicating work;
- project factual task/boot status into the same debate_log consumed by Sovereign Hub;
- keep a heartbeat so blank-chamber failures are diagnosable.

This service stores concise inspectable summaries only. It never fabricates model
reasoning, enables live trading, or applies trading-core code.
'''
from __future__ import annotations
import json, os, sqlite3, time, socket, traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DB=ROOT/'sentinuity_matrix.db'
SERVICE='council_chamber_bridge'
CYCLE=max(10,int(float(os.getenv('COUNCIL_CHAMBER_CYCLE_SEC','15'))))
CLAIM_TTL=max(120,int(float(os.getenv('COUNCIL_TASK_CLAIM_TTL_SEC','600'))))

TASKS=[
 ('PRICE_INTEGRITY_RESTORE','Price integrity restoration','SOLANA',1,'ORACLE',
  'Audit Helius, Dexscreener, Jupiter and Birdeye unit consistency; admit only canonical executable marks to trusted peak state.',
  'Verify provider units, family identity, timestamp and executable quote path.'),
 ('MODEST_BANKING_RESTORE','Modest banking restoration','SOLANA',1,'AXON',
  'Validate 5–50% banking while preserving the July 10 runner-profit-lock ladder and July 11 trusted-peak trail latch.',
  'Run paper evidence audit and classify every exit by peak and capture.'),
 ('MONSTER_RUNNER_SPINE','Monster runner spine audit','ARCHIVE',2,'ARCHIVIST',
  'Compare the current engine with the June 24 monster-runner CLAUDIT when supplied; extract only independently verified runner-preservation mechanisms.',
  'Await or locate June 24 source, then produce a function-level comparison.'),
 ('FINAL_OVERNIGHT_PRELIVE','Final overnight pre-live validation','COUNCIL',1,'POLARIS',
  'Monitor the paper-only overnight run against price integrity, mark cadence, modest banks, runner capture, queue latency and service health acceptance gates.',
  'Collect continuous paper evidence and prepare morning sign-off.'),
 ('DEBATE_CHAMBER_CONTINUITY','Debate Chamber continuity','INFRA',1,'FORGE',
  'Prove chamber population, structured standing tasks, claim recovery and restart continuity using one authoritative database.',
  'Verify boot, restart and continuous task/debate visibility.'),
]

def con():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=30000'); return c

def addcol(c,t,n,spec):
 cols={r[1] for r in c.execute(f'PRAGMA table_info("{t}")')}
 if n not in cols:
  try:c.execute(f'ALTER TABLE "{t}" ADD COLUMN "{n}" {spec}')
  except sqlite3.Error:pass

def schema(c):
 c.execute('CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT,description TEXT)')
 c.execute('''CREATE TABLE IF NOT EXISTS polaris_standing_tasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_key TEXT, source TEXT, domain TEXT,
 title TEXT, description TEXT, priority INTEGER DEFAULT 5, status TEXT DEFAULT 'OPEN',
 stage TEXT DEFAULT 'seeded', current_owner TEXT, assigned_model TEXT,
 created_at REAL, updated_at REAL, started_at REAL, completed_at REAL,
 blocked_reason TEXT, last_error TEXT, next_action TEXT, progress_pct REAL DEFAULT 0,
 vote_state TEXT, golden_gate_state TEXT, proposal_id INTEGER, patch_id INTEGER,
 artifact_path TEXT, file_targets TEXT, risk_level TEXT DEFAULT 'LOW', launch_run_id TEXT,
 heartbeat_at REAL, retry_count INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 3,
 blocker_code TEXT, needs_you INTEGER DEFAULT 0, last_model_error TEXT,
 last_recovered_at REAL, claimed_by TEXT, claim_until REAL)''')
 for n,s in [('task_key','TEXT'),('claimed_by','TEXT'),('claim_until','REAL'),('heartbeat_at','REAL'),('next_action','TEXT'),('progress_pct','REAL DEFAULT 0'),('domain','TEXT'),('current_owner','TEXT')]: addcol(c,'polaris_standing_tasks',n,s)
 c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_standing_task_key ON polaris_standing_tasks(task_key) WHERE task_key IS NOT NULL')
 c.execute('''CREATE TABLE IF NOT EXISTS debate_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL DEFAULT 0,
 logged_at REAL NOT NULL, round_num INTEGER DEFAULT 0, speaker TEXT NOT NULL,
 action TEXT NOT NULL, content_json TEXT, consensus INTEGER DEFAULT 0,
 confidence REAL DEFAULT 0.0, is_final INTEGER DEFAULT 0)''')
 # Existing Sentinuity databases may carry an older debate_log shape.
 # Migrate it additively so historical rows remain intact.
 for n,spec in [
  ('proposal_id','INTEGER NOT NULL DEFAULT 0'),
  ('logged_at','REAL'),
  ('round_num','INTEGER DEFAULT 0'),
  ('speaker','TEXT'),
  ('action','TEXT'),
  ('content_json','TEXT'),
  ('consensus','INTEGER DEFAULT 0'),
  ('confidence','REAL DEFAULT 0.0'),
  ('is_final','INTEGER DEFAULT 0'),
 ]: addcol(c,'debate_log',n,spec)
 c.execute('CREATE INDEX IF NOT EXISTS idx_debate_log_time ON debate_log(logged_at DESC)')
 c.execute('''CREATE TABLE IF NOT EXISTS system_heartbeat(
 service_name TEXT PRIMARY KEY,status TEXT,note TEXT,last_pulse REAL,
 work_processed INTEGER DEFAULT 0,last_success_at REAL)''')
 c.commit()

def event(c,speaker,action,summary,details=None,confidence=.8,final=0):
 key=f'{action}:{summary}'
 cutoff=time.time()-900
 row=c.execute("SELECT 1 FROM debate_log WHERE action=? AND logged_at>? AND content_json LIKE ? LIMIT 1",(action,cutoff,'%'+summary[:80]+'%')).fetchone()
 if row:return False
 payload={'summary':summary,'details':details or {},'event_key':key,'structured':True}
 c.execute('INSERT INTO debate_log(proposal_id,logged_at,round_num,speaker,action,content_json,consensus,confidence,is_final) VALUES(0,?,?,?,?,?,?,?,?)',
           (time.time(),0,speaker,action,json.dumps(payload,default=str),0,confidence,final))
 return True

def seed(c):
 now=time.time(); inserted=0
 for key,title,domain,priority,owner,desc,next_action in TASKS:
  row=c.execute('SELECT id FROM polaris_standing_tasks WHERE task_key=? OR title=? LIMIT 1',(key,title)).fetchone()
  if row:
   c.execute('UPDATE polaris_standing_tasks SET task_key=COALESCE(task_key,?),domain=COALESCE(domain,?),current_owner=COALESCE(current_owner,?),next_action=COALESCE(next_action,?),updated_at=COALESCE(updated_at,?) WHERE id=?',(key,domain,owner,next_action,now,row['id']))
  else:
   c.execute('''INSERT INTO polaris_standing_tasks(task_key,source,domain,title,description,priority,status,stage,current_owner,created_at,updated_at,next_action,progress_pct,risk_level)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,'MEDIUM')''',(key,'OPERATOR_SIGNOFF_20260712',domain,title,desc,priority,'OPEN','seeded',owner,now,now,next_action)); inserted+=1
 return inserted

def recover(c):
 now=time.time()
 rows=c.execute("SELECT id,title,claimed_by FROM polaris_standing_tasks WHERE status IN ('ACTIVE','CLAIMED','RESEARCHING','DEBATING','BUILDING','VALIDATING') AND claim_until IS NOT NULL AND claim_until<?",(now,)).fetchall()
 for r in rows:
  c.execute("UPDATE polaris_standing_tasks SET status='OPEN',stage='resumed_after_restart',claimed_by=NULL,claim_until=NULL,last_recovered_at=?,updated_at=?,next_action=COALESCE(next_action,'Resume from persisted checkpoint') WHERE id=?",(now,now,r['id']))
  event(c,'GUARDIAN','claim_recovered',f"Recovered interrupted task: {r['title']}",{'previous_claim':r['claimed_by']},.95)
 return len(rows)

def boot_messages(c,inserted,recovered):
 recent=c.execute('SELECT COUNT(*) FROM debate_log WHERE logged_at>?',(time.time()-21600,)).fetchone()[0]
 if recent:return 0
 active=c.execute("SELECT COUNT(*) FROM polaris_standing_tasks WHERE status NOT IN ('DONE','COMPLETED','ARCHIVED')").fetchone()[0]
 n=0
 n+=event(c,'SYSTEM','session_restored',f'Council restored: {active} standing tasks available',{'seeded_now':inserted,'recovered_claims':recovered,'host':socket.gethostname()},1.0)
 n+=event(c,'POLARIS','focus_assignment','Primary focus: final paper-only pre-live validation',{'mode':'distributed with council convergence on blockers','live_enabled':False},.95)
 n+=event(c,'GUARDIAN','safety_gate','Live remains operator-gated until overnight acceptance checks pass',{'required':'oracle freshness, trusted prices, no runner surrender, healthy services'},1.0)
 n+=event(c,'ORACLE','work_assignment','Working task: price integrity restoration',{'next':'normalise provider units and verify executable quote path'},.9)
 n+=event(c,'AXON','work_assignment','Working task: modest banking and runner capture validation',{'next':'audit protected floors before MAX_HOLD'},.9)
 n+=event(c,'FORGE','work_assignment','Working task: Debate Chamber continuity and standing-task visibility',{'next':'validate boot/restart persistence'},.9)
 n+=event(c,'ARCHIVIST','work_assignment','Queued task: June 24 monster-runner spine comparison',{'dependency':'June 24 CLAUDIT source'},.85)
 return n

def project_changes(c):
 n=0
 for r in c.execute("SELECT id,title,status,stage,current_owner,progress_pct,next_action,blocked_reason,updated_at FROM polaris_standing_tasks WHERE updated_at>? ORDER BY updated_at",(time.time()-CYCLE-5,)).fetchall():
  summary=f"{r['title']} — {r['status']} / {r['stage']}"
  n+=event(c,(r['current_owner'] or 'COUNCIL').upper(),'task_status',summary,{'task_id':r['id'],'progress_pct':r['progress_pct'],'next_action':r['next_action'],'blocked_reason':r['blocked_reason']},.8)
 return n

def heartbeat(c,status,note,work=0):
 now=time.time(); c.execute('''INSERT INTO system_heartbeat(service_name,status,note,last_pulse,work_processed,last_success_at) VALUES(?,?,?,?,?,?)
 ON CONFLICT(service_name) DO UPDATE SET status=excluded.status,note=excluded.note,last_pulse=excluded.last_pulse,work_processed=excluded.work_processed,last_success_at=excluded.last_success_at''',(SERVICE,status,note,now,work,now if status=='ALIVE' else None))
 c.execute("INSERT OR REPLACE INTO system_config(key,value,description) VALUES('COUNCIL_CHAMBER_STATE',?,'Council Chamber continuity bridge')",(status,))

def once():
 c=con()
 try:
  schema(c); ins=seed(c); rec=recover(c); msgs=boot_messages(c,ins,rec); changes=project_changes(c)
  heartbeat(c,'ALIVE',f'standing seeded={ins} recovered={rec} chamber_events={msgs+changes}',ins+rec+msgs+changes); c.commit()
  return {'seeded':ins,'recovered':rec,'events':msgs+changes}
 finally:c.close()

def main():
 print('[COUNCIL_CHAMBER] boot',once(),flush=True)
 while True:
  time.sleep(CYCLE)
  try: print('[COUNCIL_CHAMBER]',once(),flush=True)
  except Exception as e:
   print('[COUNCIL_CHAMBER][ERROR]',e,traceback.format_exc(),flush=True)
   try:
    c=con(); schema(c); heartbeat(c,'WARN',str(e)); c.commit(); c.close()
   except Exception:pass
if __name__=='__main__': main()
