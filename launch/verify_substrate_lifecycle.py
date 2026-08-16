from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp=tempfile.mkdtemp(prefix='substrate_signoff_')
os.environ['SENTINUITY_DB']=str(Path(_tmp)/'matrix.db')
from wallets.substrate_wallet_schema import connect, ensure_schema, cfg_set
from services.substrate_opportunity_scanner import scan_once, STRATEGY_ID
from services.substrate_portfolio_supervisor import promote_copytrade_to_opportunity
from wallets.substrate_paper_ledger import open_paper_position_from_opportunity

fails=[]
def ck(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))
    if not ok: fails.append(name)

class Provider:
    def __init__(self): self.i=0
    def __call__(self,url,timeout):
        self.i+=1; ts=time.time()-20
        # persistent uptrend with enough separation to clear conservative costs
        sol=100.0*(1.012**self.i); eth=3000.0*(0.999**self.i); btc=90000.0*(1.0002**self.i)
        return {'solana':{'usd':sol,'last_updated_at':ts},'weth':{'usd':eth,'last_updated_at':ts},'coinbase-wrapped-btc':{'usd':btc,'last_updated_at':ts}}

def main():
    ensure_schema(); con=connect()
    for k,v in [('SUBSTRATE_SIGNAL_MIN_SAMPLES','6'),('SUBSTRATE_SIGNAL_MIN_CONFIDENCE','0.55'),('SUBSTRATE_SIGNAL_MIN_NET_EDGE_PCT','0.20'),('SUBSTRATE_EST_ROUND_TRIP_COST_PCT','0.10'),('SUBSTRATE_PAPER_CASH_USD','100'),('SUBSTRATE_POSITION_SIZE_USD','25')]: cfg_set(con,k,v)
    con.commit(); con.close()
    p=Provider(); inserted=0
    for _ in range(8): inserted += scan_once(fetch_json=p)
    con=connect(); rows=con.execute("SELECT * FROM substrate_opportunities WHERE source='PRICE_EVIDENCE'").fetchall()
    ck('evidence scanner emits at least one candidate', len(rows)>=1, f'rows={len(rows)}')
    if rows:
        r=dict(rows[0]); ck('strategy is evidence based',r['strategy_id']==STRATEGY_ID); ck('score components persisted',bool(r.get('score_json')) and 'volatility_pct' in r['score_json']); ck('positive cost-adjusted edge',float(r['expected_edge'])>0)
        res=open_paper_position_from_opportunity(int(r['id'])); ck('canonical ledger opens candidate',bool(res.get('ok')),str(res))
    # copytrade alone must not create an opportunity
    con.execute("INSERT INTO substrate_copytrade_signals(wallet_address,chain,asset_symbol,asset_address,action,confidence,observed_size_usd,pnl_hint,state,raw_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('w','base','WETH','wrapped','BUY',0.95,100,0,'NEW','{}',time.time(),time.time()))
    before=con.execute('SELECT COUNT(*) FROM substrate_opportunities').fetchone()[0]; con.commit(); con.close()
    influenced=promote_copytrade_to_opportunity(fetch_json=p)
    con=connect(); after=con.execute('SELECT COUNT(*) FROM substrate_opportunities').fetchone()[0]; state=con.execute("SELECT state FROM substrate_copytrade_signals ORDER BY id DESC LIMIT 1").fetchone()[0]; con.close()
    ck('copytrade cannot manufacture candidate',after==before and influenced==0 and state=='OBSERVED_NO_BASE',f'before={before} after={after} state={state}')
    import services.substrate_paper_trader as compat
    ck('legacy trader is canonical delegate','supervise_once' in Path(compat.__file__).read_text())
    print('\nSUBSTRATE SIGNOFF:', 'FAIL '+str(fails) if fails else 'PASS')
    return 1 if fails else 0
if __name__=='__main__': raise SystemExit(main())
