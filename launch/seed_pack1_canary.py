from pathlib import Path
import sqlite3,time
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'sentinuity_build.db'
c=sqlite3.connect(str(DB),timeout=.75); now=time.time(); c.execute('PRAGMA busy_timeout=500')
c.execute("""INSERT INTO council_task_ledger(source_table,source_id,title,description,domain,risk_tier,priority,phase,created_at,updated_at,next_action) VALUES('pack1_canary',1,'Intelligence tab canary — Council stage rail','Harmless Tier-A end-to-end build canary.','UI','A',1,'OPEN',?,?, 'Claim and complete canary') ON CONFLICT(source_table,source_id) DO UPDATE SET phase=CASE WHEN council_task_ledger.phase='COMPLETED' THEN council_task_ledger.phase ELSE 'OPEN' END,updated_at=excluded.updated_at,next_action=excluded.next_action""",(now,now)); c.commit(); print(c.execute("SELECT canonical_id,phase,title FROM council_task_ledger WHERE source_table='pack1_canary' AND source_id=1").fetchone()); c.close()
