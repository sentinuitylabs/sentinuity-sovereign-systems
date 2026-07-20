#!/usr/bin/env python3
from pathlib import Path
import sqlite3,json
ROOT=Path(__file__).resolve().parent.parent
def audit(p):
 if not p.exists(): return {'database':str(p),'exists':False}
 c=sqlite3.connect(p); page=c.execute('pragma page_size').fetchone()[0]; count=c.execute('pragma page_count').fetchone()[0]; free=c.execute('pragma freelist_count').fetchone()[0]
 try: tops=[{'object':r[0],'mb':round((r[1] or 0)/1048576,3)} for r in c.execute('select name,sum(pgsize) from dbstat group by name order by sum(pgsize) desc limit 30')]
 except Exception: tops=[]
 rows=[]
 for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
  try: rows.append({'table':t,'rows':c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]})
  except Exception: pass
 c.close(); return {'database':str(p),'exists':True,'file_mb':round(p.stat().st_size/1048576,3),'reclaimable_mb':round(page*free/1048576,3),'top_objects':tops,'top_row_counts':sorted(rows,key=lambda x:x['rows'],reverse=True)[:30]}
out=[audit(ROOT/'sentinuity_matrix.db'),audit(ROOT/'sentinuity_intelligence.db')]; print(json.dumps(out,indent=2)); (ROOT/'runtime').mkdir(exist_ok=True); (ROOT/'runtime'/'db_growth_audit.json').write_text(json.dumps(out,indent=2))
