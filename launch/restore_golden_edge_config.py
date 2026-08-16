from pathlib import Path
import sqlite3, time, shutil
ROOT=Path(__file__).resolve().parents[1]
db=ROOT/'data'/'sentinuity_matrix.db'
if not db.exists(): db=ROOT/'sentinuity_matrix.db'
if not db.exists(): raise SystemExit('sentinuity_matrix.db not found')
backup=db.with_name(f'{db.stem}_before_edge_restore_{time.strftime("%Y%m%d_%H%M%S")}.db')
shutil.copy2(db, backup)
settings={
 'SUPERVISOR_MIN_MINT_CONFIDENCE':'0.75',
 'HARD_STOP_LOSS_PCT':'4.0',
 'STOP_LOSS_PCT':'4.0',
 'TAKE_PROFIT_PCT':'25.0',
 'EXECUTOR_MAX_HOLD_SECONDS':'900',
 'EXECUTOR_MAX_OPEN_POSITIONS':'3',
 'PAPER_MAX_OPEN_POSITIONS':'3',
 'POSITION_SIZE_USD':'25.0',
 'PAPER_POSITION_SIZE_USD':'25.0',
}
with sqlite3.connect(db) as c:
 c.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)')
 for k,v in settings.items():
  c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
 c.commit()
print('PASS golden edge configuration restored')
print('DB backup:', backup)
for k,v in settings.items(): print(f'{k}={v}')
