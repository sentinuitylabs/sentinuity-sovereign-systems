from __future__ import annotations
import html, sqlite3, time
WINDOWS=("2h","6h","24h","72h","7d","30d","All")
SECS={"2h":7200,"6h":21600,"24h":86400,"72h":259200,"7d":604800,"30d":2592000,"All":None}
def _metrics(db_path, window):
    c=sqlite3.connect(str(db_path),timeout=8); c.row_factory=sqlite3.Row
    where="UPPER(COALESCE(status,''))='CLOSED'"; args=[]
    if SECS.get(window) is not None: where += " AND CAST(closed_at AS REAL)>=?"; args=[time.time()-SECS[window]]
    r=c.execute(f"""SELECT COUNT(*) closed,SUM(CASE WHEN COALESCE(realized_pnl_usd,0)>0 THEN 1 ELSE 0 END) wins,SUM(CASE WHEN COALESCE(realized_pnl_usd,0)<0 THEN 1 ELSE 0 END) losses,SUM(CASE WHEN COALESCE(realized_pnl_usd,0)=0 THEN 1 ELSE 0 END) scratches,SUM(COALESCE(realized_pnl_usd,0)) net,AVG(COALESCE(realized_pnl_usd,0)) avg,SUM(CASE WHEN realized_pnl_usd>0 THEN realized_pnl_usd ELSE 0 END) gp,ABS(SUM(CASE WHEN realized_pnl_usd<0 THEN realized_pnl_usd ELSE 0 END)) gl FROM paper_positions WHERE {where}""",args).fetchone(); c.close()
    d={k:(r[k] or 0) for k in r.keys()}; d['wr']=100*d['wins']/d['closed'] if d['closed'] else 0; d['pf']=d['gp']/d['gl'] if d['gl'] else (999 if d['gp'] else 0); return d
def _afterlife_rows(db_path,limit=5):
    c=sqlite3.connect(str(db_path),timeout=8); c.row_factory=sqlite3.Row
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'trade_afterlife_metrics' not in tables: c.close(); return []
    rows=c.execute("""SELECT a.source_trade_id,a.mint,a.closed_at,a.max_pct_after_close,a.time_to_post_exit_peak_sec,a.complete,a.updated_at,p.token_name,p.realized_pnl_usd,p.position_size_usd FROM trade_afterlife_metrics a LEFT JOIN paper_positions p ON p.id=a.source_trade_id WHERE a.updated_at>=? ORDER BY a.updated_at DESC LIMIT ?""",(time.time()-43200,limit)).fetchall(); c.close(); return [dict(r) for r in rows]
def render_performance_truth(db_path,st,key_prefix='sol'):
    key=f'{key_prefix}_performance_window'; st.session_state.setdefault(key,'72h')
    st.markdown("<div style='font:700 12px Share Tech Mono;letter-spacing:2px;color:#8EF9FF;margin:8px 2px 4px'>PERFORMANCE TRAJECTORY</div>",unsafe_allow_html=True)
    st.radio('Performance window',WINDOWS,horizontal=True,key=key,label_visibility='collapsed'); w=st.session_state[key]; m=_metrics(db_path,w)
    def card(label,value,sub,col): return f"<div style='border:1px solid {col}55;border-radius:14px;padding:11px;background:linear-gradient(145deg,rgba(3,20,16,.88),rgba(22,5,42,.72));box-shadow:inset 0 0 18px {col}12'><div style='font:9px Share Tech Mono;color:#8EF9FF;letter-spacing:1.4px'>{label}</div><div style='font:700 20px Share Tech Mono;color:{col};margin-top:4px'>{value}</div><div style='font:9px Share Tech Mono;color:#8b7aa8'>{sub}</div></div>"
    pnlcol='#14F195' if m['net']>=0 else '#FF073A'
    grid=''.join([card('NET REALISED PNL',f"${m['net']:,.2f}",w,pnlcol),card('CLOSED',f"{int(m['closed']):,}",f"{int(m['scratches'])} scratch",'#8EF9FF'),card('WIN RATE',f"{m['wr']:.1f}%",f"{int(m['wins'])}W · {int(m['losses'])}L",'#14F195' if m['wr']>=45 else '#9945FF'),card('PROFIT FACTOR',f"{m['pf']:.2f}",f"avg ${m['avg']:.2f}",'#9945FF')])
    st.markdown(f"<div style='display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:8px 0 10px'>{grid}</div>",unsafe_allow_html=True)
    rows=_afterlife_rows(db_path)
    if rows:
        cards=[]
        for r in rows:
            size=float(r.get('position_size_usd') or 0); pnl=float(r.get('realized_pnl_usd') or 0); exitpct=100*pnl/size if size else 0; extra=float(r.get('max_pct_after_close') or 0); age=max(0,time.time()-float(r.get('updated_at') or 0)); state='LIVE TRACKING' if not int(r.get('complete') or 0) and age<90 else ('STALE PRICE' if not int(r.get('complete') or 0) else 'EXPIRED'); col='#14F195' if extra>=0 else '#FF073A'; width=max(2,min(100,50+extra)); name=html.escape(str(r.get('token_name') or r.get('mint') or '?')[:18])
            cards.append(f"<div style='padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.05)'><div style='display:flex;justify-content:space-between;font:10px Share Tech Mono'><b style='color:#d9fff0'>{name}</b><span style='color:{col}'>{state}</span></div><div style='height:8px;border-radius:8px;background:rgba(153,69,255,.18);margin:7px 0;overflow:hidden'><div style='width:{width}%;height:100%;background:linear-gradient(90deg,#14F195,#8EF9FF,#9945FF);box-shadow:0 0 10px {col}'></div></div><div style='font:9px Share Tech Mono;color:#8b7aa8'>exit {exitpct:+.1f}% · ran {extra:+.1f}% after exit · peak in {float(r.get('time_to_post_exit_peak_sec') or 0)/60:.0f}m</div></div>")
        st.markdown("<div style='margin-top:10px;border:1px solid rgba(20,241,149,.22);border-radius:14px;background:linear-gradient(145deg,rgba(2,18,14,.88),rgba(20,4,38,.78));overflow:hidden'><div style='padding:10px 12px;color:#8EF9FF;font:700 11px Share Tech Mono;letter-spacing:1.8px'>AFTER WE EXITED</div>"+''.join(cards)+"</div>",unsafe_allow_html=True)
