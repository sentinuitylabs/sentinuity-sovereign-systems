"""
Sovereign Voice Gate UI panel for Streamlit.

Safe first production build:
- voice transcript is confirmed before task creation,
- A/B/C/D clarification is mandatory,
- requests become tasks, not direct mutations,
- dangerous voice requests are refused.
"""
from __future__ import annotations

import html

try:
    import streamlit as st
    import streamlit.components.v1 as components
except Exception:
    st = None
    components = None

try:
    from services.sovereign_voice_gate import (
        db_connect, init_schema, generate_local_pairing_code,
        create_voice_task, recent_tasks, summary
    )
except Exception:
    try:
        from sovereign_voice_gate import (
            db_connect, init_schema, generate_local_pairing_code,
            create_voice_task, recent_tasks, summary
        )
    except Exception:
        db_connect = init_schema = generate_local_pairing_code = create_voice_task = recent_tasks = summary = None

ACCENT_GOLD = "#FFD700"
ACCENT_GREEN = "#14F195"
ACCENT_CYAN = "#8EF9FF"
ACCENT_RED = "#FF4D6D"

def _mic_html() -> str:
    return """
<style>
*{box-sizing:border-box}
body{margin:0;background:transparent;color:#f6e8b1;font-family:'Share Tech Mono',monospace}
.wrap{border:1px solid rgba(255,215,0,.32);border-radius:15px;padding:12px;background:linear-gradient(135deg,rgba(153,69,255,.14),rgba(5,8,13,.92));}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{border:1px solid rgba(255,215,0,.6);background:rgba(255,215,0,.10);color:#FFD700;border-radius:10px;padding:10px 14px;font-family:inherit;font-weight:700;cursor:pointer}
#heard{margin-top:10px;min-height:72px;padding:10px;border-radius:10px;border:1px solid rgba(142,249,255,.3);color:#8EF9FF;background:rgba(0,0,0,.35);font-size:15px;line-height:1.45;white-space:pre-wrap}
.bad{color:#FF4D6D;font-size:12px;margin-top:8px}.good{color:#14F195;font-size:12px;margin-top:8px}
</style>
<div class="wrap">
  <div class="row"><button onclick="startVoice()">🎙️ Start Voice</button><button onclick="stopVoice()">Stop</button><button onclick="copyText()">Copy Transcript</button></div>
  <div id="heard">Transcript appears here. Copy it into the confirmed transcript box below before creating any task.</div>
  <div id="msg" class="good">VIEW-ONLY / PAPER-ONLY. Voice cannot mutate code/trading.</div>
</div>
<script>
let rec=null, finalText="";
function setMsg(t, cls){ const m=document.getElementById('msg'); m.textContent=t; m.className=cls||'good'; }
function startVoice(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){ setMsg('Speech API not supported in this browser. Type the command instead.', 'bad'); return; }
  rec = new SR(); rec.lang='en-AU'; rec.continuous=true; rec.interimResults=true;
  rec.onresult = (e) => {
    let interim='';
    for(let i=e.resultIndex;i<e.results.length;i++){
      const t=e.results[i][0].transcript;
      if(e.results[i].isFinal) finalText += t + ' ';
      else interim += t;
    }
    document.getElementById('heard').textContent = (finalText + interim).trim() || 'Listening...';
  };
  rec.onerror = (e)=>setMsg('Speech error: '+e.error, 'bad');
  rec.onend = ()=>setMsg('Voice capture stopped. Copy transcript into the box below.', 'good');
  rec.start(); setMsg('Listening...', 'good');
}
function stopVoice(){ if(rec) rec.stop(); }
async function copyText(){
  const t=document.getElementById('heard').textContent || '';
  try{ await navigator.clipboard.writeText(t); setMsg('Transcript copied. Paste it into the confirmed box below.', 'good'); }
  catch(e){ setMsg('Copy failed. Select the transcript and copy manually.', 'bad'); }
}
</script>
"""

def _safe_card(title: str, body: str, accent: str = ACCENT_GOLD) -> None:
    st.markdown(
        f"""
<div style="border:1px solid {accent}55;border-radius:16px;padding:14px 16px;margin:12px 0;
background:linear-gradient(135deg,{accent}12,rgba(5,8,13,.88));font-family:Share Tech Mono,monospace;">
  <div style="font-size:.72rem;letter-spacing:3px;color:{accent};font-weight:900;">{html.escape(title)}</div>
  <div style="font-size:.78rem;color:#CBD5D1;line-height:1.55;margin-top:8px;">{body}</div>
</div>
""", unsafe_allow_html=True)

def render_sovereign_voice_gate(query_db=None) -> None:
    if st is None:
        return
    st.markdown("""
<div id="sovereign-voice-gate" style="margin:22px 0 12px;padding:10px 0;border-top:1px solid rgba(255,215,0,.28);
font-family:Share Tech Mono,monospace;font-size:.72rem;letter-spacing:4px;color:#FFD700;">
◇ SOVEREIGN VOICE GATE — LIVING COMMAND CHAMBER
</div>
""", unsafe_allow_html=True)
    if not all([db_connect, init_schema, generate_local_pairing_code, create_voice_task, recent_tasks, summary]):
        st.warning("Sovereign Voice Gate backend not available. Copy services/sovereign_voice_gate.py.")
        return
    try:
        conn = db_connect(); init_schema(conn)
    except Exception as e:
        st.error(f"Sovereign Voice Gate DB init failed: {e}"); return
    _safe_card("DOCTRINE", "View-only / paper-only by default. Voice creates reviewed tasks only. Voice cannot enable live trading, send funds, export keys, delete files, apply code, change launch config, or disable safety gates.", ACCENT_GREEN)
    with st.expander("🎙️ Voice capture", expanded=True):
        if components is not None:
            components.html(_mic_html(), height=190)
        transcript = st.text_area("Confirmed transcript", key="svg_transcript", placeholder="Paste or type what was heard.", height=110)
        clarification = st.radio(
            "Clarify intent",
            options=["A","B","C","D"],
            format_func=lambda x: {"A":"A) View current trade","B":"B) Ask council to research","C":"C) Draft UI/build request","D":"D) Something else"}[x],
            key="svg_clarification"
        )
        admin_code = st.text_input("Local admin/build pairing code (optional; never for live trading)", key="svg_admin_code", type="password", max_chars=6)
        if st.button("Create reviewed OpenClaw/Council task", key="svg_create_task", use_container_width=True):
            try:
                task = create_voice_task(conn, transcript, clarification, admin_code=admin_code)
                if task.get("status") == "REFUSED":
                    st.error(f"Refused safely: {task.get('refusal_reason')}")
                else:
                    st.success(f"Queued task #{task.get('id')} — {task.get('status')}")
                    if task.get("mirrored_to"):
                        st.info(f"Mirrored to {task.get('mirrored_to')} id={task.get('mirrored_id')}")
                    else:
                        st.caption("Canonical task is in sovereign_voice_gate_tasks. OpenClaw can poll this table.")
            except Exception as e:
                st.error(f"Could not create task: {e}")
    with st.expander("🔐 Generate local admin/build pairing code", expanded=False):
        st.caption("This authorizes drafting/build-review requests, not live trading.")
        ttl = st.slider("Code expiry minutes", 5, 30, 15, key="svg_code_ttl")
        if st.button("Generate 6-digit local code", key="svg_generate_code"):
            try:
                code = generate_local_pairing_code(conn, ttl_minutes=int(ttl))
                st.markdown(f"<div style='font-size:2.3rem;color:{ACCENT_GOLD};font-family:Share Tech Mono,monospace;letter-spacing:8px;font-weight:900;'>{code['code']}</div>", unsafe_allow_html=True)
                st.caption(f"Expires in {code['ttl_minutes']} minutes. This code is shown once.")
            except Exception as e:
                st.error(f"Could not generate code: {e}")
    with st.expander("📋 Recent Sovereign Voice Gate tasks", expanded=True):
        try:
            s = summary(conn)
            st.caption(f"Total: {s.get('total', 0)} | By status: {s.get('by_status', {})}")
            tasks = recent_tasks(conn, limit=10)
            if not tasks:
                st.info("No voice-gate tasks yet.")
            for t in tasks:
                color = ACCENT_RED if t.get("status") == "REFUSED" else ACCENT_GOLD
                _safe_card(f"#{t.get('id')} · {t.get('status')} · {t.get('safety_class')}",
                           f"<b>{html.escape(str(t.get('title','')))}</b><br>Action: {html.escape(str(t.get('requested_action','')))}<br>Mirror: {html.escape(str(t.get('mirrored_to') or 'canonical table only'))}<br>Refusal: {html.escape(str(t.get('refusal_reason') or '—'))}", color)
        except Exception as e:
            st.warning(f"Could not load voice tasks: {e}")
