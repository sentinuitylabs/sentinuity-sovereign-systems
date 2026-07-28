from __future__ import annotations
import argparse, sqlite3
from pathlib import Path

CACHE_COLUMNS = [
    'dedup_key','source_db','source_label','era_label','position_id','mint_address',
    'token_name','opened_at','closed_at','entry_price','exit_price','position_size_usd',
    'realized_pnl_usd','realized_pnl_pct','peak_pnl_pct','mfe_pct','mae_pct',
    'exit_reason','classification','strategy'
]

def exists(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def cols(c,t):
    return {r[1] for r in c.execute(f'PRAGMA table_info("{t}")')}

def pick(avail,*names):
    return next((x for x in names if x in avail),None)

def expr(col, default='NULL'):
    return f'"{col}"' if col else default

def ensure_cache(d):
    d.execute('''CREATE TABLE IF NOT EXISTS historical_trade_pnl_cache(
      dedup_key TEXT PRIMARY KEY, source_db TEXT, source_label TEXT, era_label TEXT,
      position_id INTEGER, mint_address TEXT, token_name TEXT, opened_at REAL,
      closed_at REAL, entry_price REAL, exit_price REAL, position_size_usd REAL,
      realized_pnl_usd REAL, realized_pnl_pct REAL, peak_pnl_pct REAL, mfe_pct REAL,
      mae_pct REAL, exit_reason TEXT, classification TEXT, strategy TEXT
    )''')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='sentinuity_matrix.db')
    p.add_argument('--intel',default='sentinuity_intelligence.db')
    a=p.parse_args()
    src=Path(a.db).resolve(); dst=Path(a.intel).resolve()
    if not src.exists(): raise SystemExit(f'missing {src}')
    s=sqlite3.connect(f'file:{src.as_posix()}?mode=ro',uri=True,timeout=30); s.row_factory=sqlite3.Row
    if not exists(s,'paper_positions'): raise SystemExit('paper_positions missing')
    cs=cols(s,'paper_positions')
    m={
      'position_id':pick(cs,'id','position_id','trade_id'),
      'mint_address':pick(cs,'mint_address','mint','token_mint'),
      'token_name':pick(cs,'token_name','symbol','name'),
      'opened_at':pick(cs,'opened_at','entry_time','created_at'),
      'closed_at':pick(cs,'closed_at','exit_time','updated_at'),
      'entry_price':pick(cs,'entry_price'), 'exit_price':pick(cs,'exit_price','close_price'),
      'position_size_usd':pick(cs,'position_size_usd','size_usd'),
      'realized_pnl_usd':pick(cs,'realized_pnl_usd','pnl_usd','realized_pnl','pnl'),
      'realized_pnl_pct':pick(cs,'realized_pnl_pct','pnl_pct','exit_pct','final_exec_pct'),
      'peak_pnl_pct':pick(cs,'peak_pnl_pct','peak_pct'), 'mfe_pct':pick(cs,'mfe_pct','max_favorable_excursion_pct'),
      'mae_pct':pick(cs,'mae_pct','max_adverse_excursion_pct'), 'exit_reason':pick(cs,'exit_reason'),
      'classification':pick(cs,'classification','exit_category','win_loss'), 'strategy':pick(cs,'strategy','strategy_version'),
      'status':pick(cs,'status','state','position_state')
    }
    if not m['closed_at'] or not m['realized_pnl_usd']:
        raise SystemExit('closed timestamp or pnl column missing')
    pid=expr(m['position_id'],'rowid'); mint=expr(m['mint_address'],"''"); closed=expr(m['closed_at'],'0')
    fields={k:expr(v,"NULL") for k,v in m.items() if k!='status'}
    where=f'COALESCE(CAST({closed} AS REAL),0)>0'
    if m['status']:
        status_expr = expr(m['status'], "''")
        where += f" AND UPPER(COALESCE({status_expr},''))='CLOSED'"
    q=f'''SELECT {pid} AS position_id, {mint} AS mint_address,
      {fields['token_name']} AS token_name, {fields['opened_at']} AS opened_at,
      {closed} AS closed_at, {fields['entry_price']} AS entry_price,
      {fields['exit_price']} AS exit_price, {fields['position_size_usd']} AS position_size_usd,
      {fields['realized_pnl_usd']} AS realized_pnl_usd,
      {fields['realized_pnl_pct']} AS realized_pnl_pct, {fields['peak_pnl_pct']} AS peak_pnl_pct,
      {fields['mfe_pct']} AS mfe_pct, {fields['mae_pct']} AS mae_pct,
      {fields['exit_reason']} AS exit_reason, {fields['classification']} AS classification,
      {fields['strategy']} AS strategy FROM paper_positions WHERE {where}'''
    rows=s.execute(q).fetchall(); s.close()
    d=sqlite3.connect(dst,timeout=30); ensure_cache(d)
    dc=cols(d,'historical_trade_pnl_cache')
    missing=[x for x in CACHE_COLUMNS if x not in dc]
    if missing: raise SystemExit(f'cache schema missing columns: {missing}')
    sql='INSERT OR IGNORE INTO historical_trade_pnl_cache ('+','.join(CACHE_COLUMNS)+') VALUES ('+','.join(['?']*20)+')'
    payload=[]
    for r in rows:
        pidv=r['position_id']; mintv=str(r['mint_address'] or ''); op=float(r['opened_at'] or 0); cl=float(r['closed_at'] or 0)
        key=f'{pidv}|{mintv}|{op}|{cl}'
        payload.append((key,src.name,'HOT_DB_CURRENT','CURRENT_SYNC',pidv,mintv,str(r['token_name'] or ''),op,cl,
          r['entry_price'],r['exit_price'],r['position_size_usd'],float(r['realized_pnl_usd'] or 0),r['realized_pnl_pct'],
          r['peak_pnl_pct'],r['mfe_pct'],r['mae_pct'],r['exit_reason'],r['classification'],r['strategy']))
    before=d.execute('SELECT COUNT(*) FROM historical_trade_pnl_cache').fetchone()[0]
    d.executemany(sql,payload); d.commit()
    after=d.execute('SELECT COUNT(*) FROM historical_trade_pnl_cache').fetchone()[0]
    print(f'historical_cache_before={before}')
    print(f'historical_cache_after={after}')
    print(f'inserted={after-before}')
    d.close()
if __name__=='__main__': main()
