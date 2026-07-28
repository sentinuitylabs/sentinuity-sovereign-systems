from __future__ import annotations
import html, json, re, sqlite3, time
from pathlib import Path

_SECRET = re.compile(r"(?i)(private|secret|token|password|seed|authorization|api[_-]?key|bearer)")
_LONG = re.compile(r"[1-9A-HJ-NP-Za-km-z]{28,}")

def _clean(v, n=86):
    s=" ".join(str(v or "").replace("\n"," ").split())
    s=re.sub(r"https?://\S+","[endpoint]",s); s=_SECRET.sub("[redacted]",s)
    s=_LONG.sub(lambda m:m.group(0)[:5]+"…"+m.group(0)[-4:],s)
    return s[:n]

def _realm(name, note=""):
    b=f"{name} {note}".lower()
    if any(k in b for k in ("execution","guardian","decision_contract","gate","settlement","wallet","live_")): return "AUTH"
    if any(k in b for k in ("council","polaris","forge","debate","research","build","proposal","patch")): return "BUILD"
    if any(k in b for k in ("oracle","price","ingest","resolver","qualifier","scout","intelligence")): return "INTEL"
    return "FLOW"

def _truth(path):
    streams=[]; crown={"mode":"UNKNOWN","gate":"UNPUBLISHED","oracle":"NO PULSE","paper":"0","live":"0","council":"NO PULSE"}; now=time.time()
    try:
        db=sqlite3.connect(f"file:{Path(path)}?mode=ro",uri=True,timeout=1.5); db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA busy_timeout=1200")
        try:
            cfg=dict(db.execute("SELECT key,value FROM config").fetchall()); crown["mode"]=_clean(cfg.get("TRADING_MODE","PAPER"),18).upper()
        except Exception: pass
        try:
            rows=db.execute("SELECT service_name,COALESCE(status,''),COALESCE(last_pulse,0),COALESCE(note,'') FROM system_heartbeat ORDER BY last_pulse DESC LIMIT 42").fetchall()
            for name,status,pulse,note in rows:
                age=int(max(0,now-float(pulse or 0))) if pulse else 9999; realm=_realm(name,note); state=_clean(status,16).upper() or "UNKNOWN"
                text=f"{realm.lower()}.{_clean(name,36)}({state}, age={age}s)"; safe=_clean(note,48)
                if safe: text+=f" // {safe}"
                streams.append({"realm":realm,"text":text}); low=str(name).lower()
                if "oracle" in low and crown["oracle"]=="NO PULSE": crown["oracle"]=f"{state} · {age}s"
                if any(k in low for k in ("council_chamber","polaris")) and crown["council"]=="NO PULSE": crown["council"]=f"{state} · {age}s"
        except Exception: pass
        for table,key in (("paper_positions","paper"),("live_positions","live")):
            try: crown[key]=str(db.execute(f"SELECT COUNT(*) FROM {table} WHERE status='OPEN'").fetchone()[0])
            except Exception: pass
        try:
            row=db.execute("SELECT verdict,COALESCE(blocker,''),COALESCE(pattern_state,'') FROM live_decision_contract ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                crown["gate"]=_clean(row[0],28).replace("_"," ").upper(); streams.insert(0,{"realm":"AUTH","text":f"authority.contract(verdict={_clean(row[0],24)}, pattern={_clean(row[2],20)}, blocker={_clean(row[1],44)})"})
        except Exception: pass
        for table,realm in (("candidate_snapshots","FLOW"),("paper_positions","FLOW"),("live_positions","AUTH"),("council_proposals","BUILD"),("wallet_entry_likelihood_signals","INTEL")):
            try: streams.append({"realm":realm,"text":f"db.{table}.count() -> {int(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])}"})
            except Exception: pass
        db.close()
    except Exception: pass
    return streams[:48] or [{"realm":"FLOW","text":"flow.pipeline.await_runtime_truth()"},{"realm":"INTEL","text":"intel.oracle.query_only()"},{"realm":"BUILD","text":"build.council.await_cycle()"},{"realm":"AUTH","text":"authority.gate.fail_closed()"}],crown

def inject_grand_vision_v2(st, db_path):
    streams,crown=_truth(db_path); payload=html.escape(json.dumps(streams,separators=(",",":"))); bad=any(x in crown["gate"] for x in ("BLOCK","MANUAL","ERROR")); gate="#FF365D" if bad else ("#FFD76A" if any(x in crown["gate"] for x in ("WAIT","ALIGN")) else "#14F195")
    st.markdown(r'''<style id="sentinuity-v2-release-doctrine">
:root{--green:#14F195;--violet:#9945FF;--cyan:#8EF9FF;--gold:#FFD76A;--red:#FF365D;--void:#03030a}
html,body,[data-testid="stAppViewContainer"]{background:#03030a!important;color:#e8eeec!important}[data-testid="stAppViewContainer"]{background-image:radial-gradient(950px 520px at 10% -8%,rgba(153,69,255,.17),transparent 62%),radial-gradient(900px 500px at 92% 2%,rgba(20,241,149,.075),transparent 62%),linear-gradient(180deg,#05040d,#020208 70%,#05030c)!important}.block-container{max-width:1500px!important;padding:1rem clamp(14px,3vw,48px) 7rem!important;position:relative;z-index:2}[data-testid="stHeader"]{background:rgba(3,3,10,.55)!important;backdrop-filter:blur(24px)}
.snty-release-crown{margin:.2rem 0 1rem;padding:.25rem 0 1rem;border-bottom:1px solid rgba(142,249,255,.09)}.snty-release-crown__top{display:flex;justify-content:space-between;align-items:flex-end;gap:18px}.snty-release-crown__mark{font:700 clamp(1rem,2vw,1.38rem)/1 Orbitron,sans-serif;letter-spacing:.28em;color:#f1f4f3}.snty-release-crown__sub{margin-top:.45rem;font:600 .61rem/1.4 'Share Tech Mono',monospace;letter-spacing:.14em;color:#64727c}.snty-release-crown__mode{font:700 .62rem/1 'Share Tech Mono',monospace;letter-spacing:.13em;color:var(--cyan)}.snty-release-crown__truth{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;margin-top:1rem;background:linear-gradient(90deg,transparent,rgba(142,249,255,.12),rgba(153,69,255,.13),transparent)}.snty-release-crown__cell{background:rgba(3,3,10,.88);padding:.7rem .8rem}.snty-release-crown__label{font:600 .52rem/1 'Share Tech Mono',monospace;letter-spacing:.14em;color:#53616b}.snty-release-crown__value{margin-top:.36rem;font:650 .73rem/1.25 Orbitron,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-section-head{display:grid!important;grid-template-columns:54px minmax(0,1fr) minmax(180px,34%);align-items:end;gap:18px;margin:4.6rem 0 1.25rem!important;padding:0 0 1rem!important;border:0!important;border-bottom:1px solid rgba(142,249,255,.09)!important}.snty-section-index{font:500 .63rem/1 'Share Tech Mono',monospace!important;color:var(--cyan)!important}.snty-section-title{font:650 clamp(.9rem,1.8vw,1.22rem)/1.1 Orbitron,sans-serif!important;color:#edf2f0!important;letter-spacing:.15em!important}.snty-section-sub{font:500 .68rem/1.5 'Share Tech Mono',monospace!important;color:#687680!important;text-align:right!important}.snty-section-line{display:none!important}
.snty-gv-shell,.snty-crystal-panel,.snty-cyan-panel,.snty-gold-panel,.substrate-card,.cncl-card,.mycelial-artery,[data-testid="stExpander"]{position:relative!important;border:0!important;border-radius:36px 5px 36px 5px!important;background:linear-gradient(136deg,rgba(14,10,33,.66),rgba(5,4,15,.34) 58%,rgba(5,4,15,.16))!important;box-shadow:0 30px 90px rgba(0,0,0,.20),inset 0 1px rgba(255,255,255,.025)!important;overflow:hidden!important}.snty-gv-shell:before,.snty-crystal-panel:before,.snty-cyan-panel:before,.snty-gold-panel:before,.substrate-card:before,.cncl-card:before,.mycelial-artery:before,[data-testid="stExpander"]:before{content:"";position:absolute;left:0;top:0;width:40%;height:1px;background:linear-gradient(90deg,var(--cyan),var(--violet),transparent);opacity:.62}
[data-testid="stMainBlockContainer"] div[style*="border:1px"],[data-testid="stMainBlockContainer"] div[style*="border: 1px"]{border-color:rgba(142,249,255,.08)!important;box-shadow:none!important}[data-testid="stMainBlockContainer"] div[style*="border-radius:12px"],[data-testid="stMainBlockContainer"] div[style*="border-radius: 12px"],[data-testid="stMainBlockContainer"] div[style*="border-radius:16px"],[data-testid="stMainBlockContainer"] div[style*="border-radius: 16px"]{border-radius:26px 5px 26px 5px!important}
.snty-gv-metric,.snty-metric-card,[data-testid="stMetric"]{border:0!important;border-radius:0!important;background:rgba(4,4,12,.82)!important;box-shadow:none!important;padding:.7rem .75rem!important}.stButton>button{border:0!important;border-radius:999px!important;background:rgba(10,8,23,.58)!important;box-shadow:inset 0 0 0 1px rgba(142,249,255,.11)!important}.stTabs [data-baseweb="tab-list"]{gap:0!important;background:transparent!important;border-bottom:1px solid rgba(142,249,255,.08)!important}.stTabs [data-baseweb="tab"]{border:0!important;background:transparent!important;color:#69757e!important}.stTabs [aria-selected="true"]{color:var(--cyan)!important;border-bottom:1px solid var(--cyan)!important}
[data-testid="stDataFrame"],.stDataFrame,[data-testid="stTable"]{border:0!important;border-radius:28px 5px 28px 5px!important;background:rgba(3,3,10,.58)!important;box-shadow:inset 0 1px rgba(142,249,255,.065)!important;overflow:hidden!important}[data-testid="stDataFrame"] *{font-family:'Share Tech Mono',monospace!important;font-size:.70rem!important}.snty-hero,.snty-cortex-hero,.snty-identity{border:0!important;background:transparent!important;box-shadow:none!important;text-align:left!important;padding:3rem 0 2rem!important}.snty-hero-word{font-size:clamp(3.2rem,8.5vw,7.5rem)!important;line-height:.87!important;letter-spacing:.10em!important;background:linear-gradient(94deg,#f5f1ff,#c8c2ff 25%,var(--cyan) 50%,var(--green) 76%,#f6e8a3);-webkit-background-clip:text;background-clip:text;color:transparent!important}
.mycelial-artery{padding:1.1rem!important;background:radial-gradient(circle at 10% 0%,rgba(153,69,255,.16),transparent 40%),linear-gradient(136deg,rgba(11,7,29,.76),rgba(4,4,12,.38))!important}.mycelial-artery [style*="background:rgba(5,2,16"],.mycelial-artery [style*="background: rgba(5,2,16"]{background:transparent!important}.snty-live-funded{border:0!important;background:linear-gradient(135deg,rgba(255,215,106,.11),rgba(20,241,149,.045),rgba(5,4,15,.72))!important;box-shadow:0 0 0 1px rgba(255,215,106,.43),0 0 44px rgba(255,215,106,.11)!important}
.snty-help-orb{display:inline-grid;place-items:center;width:17px;height:17px;margin-left:8px;border-radius:50%;font:700 .58rem/1 'Share Tech Mono',monospace;color:#71808a;box-shadow:inset 0 0 0 1px rgba(142,249,255,.16);cursor:help;vertical-align:middle}#snty-truth-fabric{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden;opacity:.19;mask-image:linear-gradient(180deg,rgba(0,0,0,.86),rgba(0,0,0,.30) 62%,transparent)}.snty-code-rune{position:absolute;top:-30vh;writing-mode:vertical-rl;font:500 .55rem/1.55 'Share Tech Mono',monospace;letter-spacing:.07em;white-space:nowrap;animation:snty-code-fall linear infinite;filter:drop-shadow(0 0 7px currentColor)}.snty-code-rune.FLOW{color:var(--green)}.snty-code-rune.INTEL{color:var(--cyan)}.snty-code-rune.BUILD{color:var(--violet)}.snty-code-rune.AUTH{color:var(--gold)}@keyframes snty-code-fall{to{transform:translateY(145vh)}}
@media(max-width:760px){.block-container{padding:9px 11px 76px!important}.snty-release-crown__top{align-items:flex-start;flex-direction:column}.snty-release-crown__truth{grid-template-columns:repeat(2,minmax(0,1fr))}.snty-release-crown__cell:last-child{grid-column:1/-1}.snty-section-head{grid-template-columns:36px minmax(0,1fr)!important;gap:10px!important;margin-top:3rem!important}.snty-section-sub{grid-column:2;text-align:left!important}.snty-gv-shell,.snty-crystal-panel,.snty-cyan-panel,.snty-gold-panel,.substrate-card,.cncl-card,.mycelial-artery,[data-testid="stExpander"]{border-radius:25px 4px 25px 4px!important}[data-testid="stDataFrame"] *{font-size:.75rem!important}.snty-hero-word{font-size:clamp(2.7rem,15vw,4.7rem)!important}}
@media(prefers-reduced-motion:reduce){.snty-code-rune{animation:none!important}}
</style>''',unsafe_allow_html=True)
    cells=[("AUTHORITY",crown["gate"],gate),("PRICE TRUTH",crown["oracle"],"#8EF9FF"),("PAPER OPEN",crown["paper"],"#14F195"),("LIVE OPEN",crown["live"],"#FFD76A"),("COUNCIL",crown["council"],"#9945FF")]
    ch="".join(f"<div class='snty-release-crown__cell'><div class='snty-release-crown__label'>{html.escape(a)}</div><div class='snty-release-crown__value' style='color:{c}'>{html.escape(b)}</div></div>" for a,b,c in cells)
    st.markdown(f"<section class='snty-release-crown'><div class='snty-release-crown__top'><div><div class='snty-release-crown__mark'>SENTINUITY</div><div class='snty-release-crown__sub'>SOVEREIGN GLASSBOX ORGANISM · V2 CHECKPOINT</div></div><div class='snty-release-crown__mode'>{html.escape(crown['mode'])} MODE</div></div><div class='snty-release-crown__truth'>{ch}</div></section>",unsafe_allow_html=True)
    st.markdown(f"<div id='snty-truth-fabric' data-streams='{payload}'></div>",unsafe_allow_html=True)
    st.markdown(r'''<script>(function(){const root=document.getElementById('snty-truth-fabric');if(root&&root.dataset.ready!=='1'){root.dataset.ready='1';let s=[];try{s=JSON.parse(root.dataset.streams||'[]')}catch(e){}const n=Math.min(32,Math.max(16,s.length));for(let i=0;i<n;i++){const x=s[i%s.length],e=document.createElement('div');e.className='snty-code-rune '+(x.realm||'INTEL');e.textContent=x.text;e.style.left=((i+.45)*100/n)+'%';e.style.animationDuration=(20+(i%10)*2.8)+'s';e.style.animationDelay=(-1*(i%15)*1.7)+'s';root.appendChild(e)}}const h={truth:'Economic ground truth.',gate:'Every reason capital may fire or be refused.',flow:'Candidate movement from discovery to settlement.',learning:'Outcome evidence returned to future decisions.',copytrade:'Advisory smart-money evidence only.',intelligence:'Council, research, Substrate and world systems.',diagnostics:'Exact faults and provenance.'};Object.keys(h).forEach(id=>{const x=document.getElementById(id);if(!x||x.dataset.h)return;const t=x.querySelector('.snty-section-title');if(!t)return;const q=document.createElement('span');q.className='snty-help-orb';q.textContent='?';q.title=h[id];t.appendChild(q);x.dataset.h='1'})})();</script>''',unsafe_allow_html=True)
