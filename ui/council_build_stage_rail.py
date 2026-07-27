from __future__ import annotations
import html, sqlite3, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BUILD_DB=ROOT/"sentinuity_build.db"
CANARY_REVISION=2
ORDER=["OPEN","CLAIMED","RESEARCHING","EVIDENCE_READY","PROPOSING","DEBATING","GATED","PATCH_READY","APPLYING","VERIFYING","COMPLETED"]
LABEL={"OPEN":"INTAKE","CLAIMED":"CLAIMED","RESEARCHING":"RESEARCHING","EVIDENCE_READY":"EVIDENCE READY","PROPOSING":"POLARIS SYNTHESIS","DEBATING":"IVARIS CRITIQUE / DEBATE","GATED":"CONSENSUS","PATCH_READY":"PATCH / COMPILE / SMOKE","APPLYING":"APPLYING","VERIFYING":"VERIFYING","COMPLETED":"COMPLETE","BLOCKED_EXTERNAL":"BLOCKED","BLOCKED_TRANSIENT":"BLOCKED","FAILED_RETRYABLE":"BLOCKED","FAILED_FINAL":"BLOCKED","ROLLED_BACK":"ROLLED BACK","NEEDS_OPERATOR":"WAITING FOR OPERATOR"}
COLOR={"OPEN":"#8EF9FF","CLAIMED":"#8EF9FF","RESEARCHING":"#9945FF","EVIDENCE_READY":"#B8A7FF","PROPOSING":"#9945FF","DEBATING":"#FFD700","GATED":"#FFD700","PATCH_READY":"#20E0D0","APPLYING":"#20E0D0","VERIFYING":"#14F195","COMPLETED":"#14F195","BLOCKED_EXTERNAL":"#FF073A","BLOCKED_TRANSIENT":"#FFB347","FAILED_RETRYABLE":"#FFB347","FAILED_FINAL":"#FF073A","ROLLED_BACK":"#FF073A","NEEDS_OPERATOR":"#FFB347"}
def _load():
    if not BUILD_DB.exists(): return None,"sentinuity_build.db missing"
    try:
        c=sqlite3.connect(f"file:{BUILD_DB}?mode=ro",uri=True,timeout=.15); c.row_factory=sqlite3.Row; c.execute("PRAGMA query_only=ON"); c.execute("PRAGMA busy_timeout=100")
        t=c.execute("SELECT * FROM council_task_ledger ORDER BY CASE WHEN phase IN ('COMPLETED','FAILED_FINAL','ROLLED_BACK','SUPERSEDED') THEN 1 ELSE 0 END,updated_at DESC LIMIT 1").fetchone()
        if not t: c.close(); return None,"No canonical build task"
        d=dict(t); cid=d['canonical_id']; tr=[dict(r) for r in c.execute("SELECT * FROM council_task_transitions WHERE canonical_id=? ORDER BY ts DESC LIMIT 12",(cid,))]; ev=[dict(r) for r in c.execute("SELECT * FROM council_task_evidence WHERE canonical_id=? ORDER BY ts DESC LIMIT 3",(cid,))]; pa=c.execute("SELECT * FROM code_patches WHERE canonical_task_id=? ORDER BY id DESC LIMIT 1",(cid,)).fetchone(); c.close(); return (d,tr,ev,dict(pa) if pa else None),""
    except Exception as e: return None,f"{type(e).__name__}: {e}"
def render_council_build_stage_rail(st):
    data,note=_load()
    if not data: st.markdown(f"<div style='border:1px solid #FFB34755;padding:9px;color:#FFB347;font-family:monospace'>BUILD PLANE — {html.escape(note)}</div>",unsafe_allow_html=True); return
    t,tr,ev,pa=data; phase=str(t.get('phase') or 'OPEN').upper(); reached={str(x.get('to_phase') or '').upper() for x in tr}; nodes=[]
    for x in ORDER:
        col=COLOR.get(x,'#888'); op='1' if x in reached or x==phase else '.25'; glow=f"box-shadow:0 0 12px {col}77" if x==phase else ''
        nodes.append(f"<div style='flex:1;min-width:75px;border:1px solid {col}88;color:{col};opacity:{op};padding:6px 4px;text-align:center;font-size:.55rem;{glow}'>{LABEL.get(x,x)}</div>")
    latest=tr[0] if tr else {}; evidence=ev[0].get('summary') if ev else 'No evidence persisted'; patch=(f"#{pa.get('id')} {pa.get('status')} compile={pa.get('compile_ok')} smoke={pa.get('smoke_ok')} verify={pa.get('verify_ok')} {pa.get('target_file') or ''}" if pa else 'No patch generated'); age=int(max(0,time.time()-float(t.get('updated_at') or time.time())))
    body="<section style='border:1px solid #9945ff55;background:#070411dd;padding:10px;margin-bottom:10px'><div style='display:flex;flex-wrap:wrap;gap:4px'>"+''.join(nodes)+"</div>"+f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;margin-top:9px;color:#d7d1e5;font-family:monospace;font-size:.64rem'><div><b style='color:{COLOR.get(phase, '#ffffff')}'>{html.escape(str(t.get('title') or 'Untitled'))}</b><br>{html.escape(LABEL.get(phase,phase))} · owner={html.escape(str(t.get('claimed_by') or 'unclaimed'))} · {age}s</div><div><b style='color:#8EF9FF'>CURRENT</b><br>{html.escape(str(latest.get('reason') or 'Awaiting transition')[:220])}</div><div><b style='color:#B8A7FF'>EVIDENCE</b><br>{html.escape(str(evidence)[:220])}</div><div><b style='color:#20E0D0'>IMPLEMENTATION</b><br>{html.escape(patch[:240])}</div><div><b style='color:#FFD700'>NEXT</b><br>{html.escape(str(t.get('next_action') or 'No next action')[:220])}</div></div></section>"
    st.markdown(body,unsafe_allow_html=True)
