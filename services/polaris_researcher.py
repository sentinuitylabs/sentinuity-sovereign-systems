"""POLARIS research synthesiser — local evidence only.

Consumes already-fetched forge_research_cache / inspiration ledger records and
creates transparent research events. It performs no network calls and never
modifies trading policy or execution state.
"""
from __future__ import annotations
import asyncio, json, sqlite3, time
from pathlib import Path
from core.schema import get_connection, update_heartbeat

SERVICE_NAME = "polaris_researcher"
CYCLE_SECONDS = 300

def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS research_activity_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
      event_type TEXT NOT NULL, actor TEXT NOT NULL, task_id TEXT, query TEXT,
      source_ref TEXT, source_type TEXT, commit_sha TEXT, licence TEXT,
      safety_status TEXT, summary TEXT, confidence REAL, parent_event_id INTEGER,
      disposition TEXT, metadata_json TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_activity_created ON research_activity_ledger(created_at DESC)")
    conn.commit()

def _cycle() -> int:
    now=time.time(); written=0
    with get_connection() as conn:
        conn.row_factory=sqlite3.Row; _ensure(conn)
        rows=conn.execute("""SELECT id,project_key,topic,summary,source,confidence,created_at
          FROM forge_research_cache WHERE created_at>? ORDER BY created_at ASC LIMIT 100""",(now-86400,)).fetchall()
        for r in rows:
            marker=f"forge_cache:{r['id']}"
            if conn.execute("SELECT 1 FROM research_activity_ledger WHERE source_ref=? AND event_type='INSIGHT_EXTRACTED'",(marker,)).fetchone(): continue
            conn.execute("""INSERT INTO research_activity_ledger(created_at,event_type,actor,task_id,source_ref,source_type,summary,confidence,disposition,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",(now,'INSIGHT_EXTRACTED','POLARIS',str(r['project_key'] or ''),marker,str(r['source'] or 'cache'),str(r['summary'] or '')[:1200],float(r['confidence'] or 0.5),'PENDING_COUNCIL',json.dumps({'topic':r['topic']})))
            written+=1
        conn.commit()
    return written

async def researcher_loop() -> None:
    update_heartbeat(SERVICE_NAME,'starting','local research synthesiser online')
    while True:
        try:
            n=_cycle(); update_heartbeat(SERVICE_NAME,'alive',f'insights_written={n}',work_processed=n)
        except Exception as exc:
            update_heartbeat(SERVICE_NAME,'warn',f'error: {str(exc)[:160]}')
        await asyncio.sleep(CYCLE_SECONDS)
