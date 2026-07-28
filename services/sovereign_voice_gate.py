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

# ═════════════════════════════════════════════════════════════════════════════
# VOICE_GATE_BACKEND_BIND_20260715 (Claude final directive item 10)
# ROOT CAUSE OF "backend is disconnected": this module previously tried to
# import its own backend functions from services.sovereign_voice_gate — i.e.
# FROM ITSELF — and no file anywhere defined db_connect / init_schema /
# generate_local_pairing_code / create_voice_task / recent_tasks / summary.
# The self-import always failed, every name resolved to None, and the panel
# showed the disconnected banner forever.
#
# The backend now lives HERE, in the same module the panel binds to, so the
# import path the rest of the codebase already uses (services.sovereign_voice_gate)
# is the backend. The panel below is unchanged.
#
# SAFE COMMAND CONTRACT (enforced server-side, not just in copy):
#   * Voice creates REVIEWABLE tasks only, written to sovereign_voice_gate_tasks.
#   * Any transcript that asks to enable live trading, move funds, expose keys,
#    delete files, apply code, or change launcher/safety state is REFUSED and
#    recorded with a refusal reason. Nothing here writes system_config,
#    paper_positions, or any launch/arming key.
# ═════════════════════════════════════════════════════════════════════════════
import random
import sqlite3
import time as _time
from pathlib import Path as _Path

_VG_ROOT = _Path(__file__).resolve().parent.parent
_VG_DB = next((_VG_ROOT / c for c in ("sentinuity_matrix.db", "data/sentinuity_matrix.db")
               if (_VG_ROOT / c).exists()), _VG_ROOT / "sentinuity_matrix.db")

_VG_FORBIDDEN = (
    ("live", "enable"), ("live", "arm"), ("live", "trading"), ("go", "live"),
    ("send", "fund"), ("transfer", "sol"), ("withdraw",), ("private", "key"),
    ("export", "key"), ("seed", "phrase"), ("delete", "file"), ("rm ",),
    ("apply", "code"), ("launch", "config"), ("disable", "safety"),
    ("disable", "gate"), ("hard", "stop", "off"),
)

_CLARIFY_TITLES = {
    "A": "View current trade",
    "B": "Council research request",
    "C": "UI/build draft request",
    "D": "General reviewed request",
}


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_VG_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sovereign_voice_gate_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            title TEXT,
            transcript TEXT,
            clarification TEXT,
            requested_action TEXT,
            safety_class TEXT,
            status TEXT,
            refusal_reason TEXT,
            mirrored_to TEXT,
            mirrored_id INTEGER,
            paired_code_used INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sovereign_voice_gate_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            created_at REAL,
            expires_at REAL,
            used_at REAL
        )
    """)
    conn.commit()


def generate_local_pairing_code(conn: sqlite3.Connection, ttl_minutes: int = 15) -> dict:
    """6-digit local pairing code for drafting/build-review requests only.
    Never authorizes live trading — nothing in this module can arm live."""
    ttl_minutes = max(5, min(30, int(ttl_minutes)))
    code = "".join(random.SystemRandom().choice("0123456789") for _ in range(6))
    now = _time.time()
    conn.execute(
        "INSERT INTO sovereign_voice_gate_codes (code, created_at, expires_at) VALUES (?,?,?)",
        (code, now, now + ttl_minutes * 60),
    )
    conn.commit()
    return {"code": code, "ttl_minutes": ttl_minutes}


def _code_is_valid(conn: sqlite3.Connection, code: str) -> bool:
    if not code:
        return False
    row = conn.execute(
        "SELECT id FROM sovereign_voice_gate_codes "
        "WHERE code=? AND used_at IS NULL AND expires_at >= ? "
        "ORDER BY id DESC LIMIT 1",
        (str(code).strip(), _time.time()),
    ).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE sovereign_voice_gate_codes SET used_at=? WHERE id=?",
                 (_time.time(), row["id"]))
    return True


def _classify(transcript: str) -> tuple[str, str | None]:
    t = " ".join(str(transcript or "").lower().split())
    for combo in _VG_FORBIDDEN:
        if all(w in t for w in combo):
            return "FORBIDDEN", (
                "Voice cannot " + " ".join(combo).strip() +
                " — live enablement, funds, keys, files, code application and "
                "safety state are operator-console actions only."
            )
    return "REVIEW_ONLY", None


def create_voice_task(conn: sqlite3.Connection, transcript: str,
                      clarification: str, admin_code: str = "") -> dict:
    transcript = str(transcript or "").strip()
    clarification = str(clarification or "D").strip().upper()[:1] or "D"
    if not transcript:
        return {"status": "REFUSED", "refusal_reason": "Empty transcript — nothing to queue."}
    safety_class, refusal = _classify(transcript)
    now = _time.time()
    title = _CLARIFY_TITLES.get(clarification, _CLARIFY_TITLES["D"])
    paired = 1 if _code_is_valid(conn, admin_code) else 0
    if safety_class == "FORBIDDEN":
        cur = conn.execute(
            "INSERT INTO sovereign_voice_gate_tasks "
            "(created_at,title,transcript,clarification,requested_action,"
            " safety_class,status,refusal_reason,paired_code_used) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (now, title, transcript[:2000], clarification, transcript[:300],
             safety_class, "REFUSED", refusal, paired))
        conn.commit()
        return {"id": cur.lastrowid, "status": "REFUSED", "refusal_reason": refusal}

    status = "QUEUED_FOR_REVIEW" if not paired else "QUEUED_BUILD_REVIEW"
    cur = conn.execute(
        "INSERT INTO sovereign_voice_gate_tasks "
        "(created_at,title,transcript,clarification,requested_action,"
        " safety_class,status,paired_code_used) VALUES (?,?,?,?,?,?,?,?)",
        (now, title, transcript[:2000], clarification, transcript[:300],
         safety_class, status, paired))
    task_id = cur.lastrowid
    conn.commit()

    # Optional mirror into the council standing-task board when present —
    # INSERT-only, never touches engine/config tables.
    mirrored_to = mirrored_id = None
    try:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='standing_tasks'"
        ).fetchone()
        if has:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(standing_tasks)")}
            if {"title", "status"}.issubset(cols):
                fields, vals = ["title", "status"], [f"VOICE: {transcript[:120]}", "ACTIVE"]
                if "priority" in cols: fields.append("priority"); vals.append(1)
                if "domain" in cols: fields.append("domain"); vals.append("voice_gate")
                if "task_key" in cols:
                    fields.append("task_key"); vals.append(f"voice_{task_id}")
                mcur = conn.execute(
                    f"INSERT INTO standing_tasks ({','.join(fields)}) "
                    f"VALUES ({','.join('?' for _ in fields)})", vals)
                mirrored_to, mirrored_id = "standing_tasks", mcur.lastrowid
                conn.execute(
                    "UPDATE sovereign_voice_gate_tasks SET mirrored_to=?, mirrored_id=? WHERE id=?",
                    (mirrored_to, mirrored_id, task_id))
                conn.commit()
    except Exception:
        pass  # mirroring is best-effort; the canonical row already exists

    return {"id": task_id, "status": status,
            "mirrored_to": mirrored_to, "mirrored_id": mirrored_id}


def recent_tasks(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sovereign_voice_gate_tasks ORDER BY id DESC LIMIT ?",
        (max(1, min(50, int(limit))),)).fetchall()
    return [dict(r) for r in rows]


def summary(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM sovereign_voice_gate_tasks").fetchone()[0]
    by = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM sovereign_voice_gate_tasks GROUP BY status")}
    return {"total": int(total or 0), "by_status": by}
# ═══════════════════════ END VOICE_GATE_BACKEND_BIND_20260715 ════════════════

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
◇ SOVEREIGN VOICE GATE · REVIEWED COMMAND BRIDGE
</div>
""", unsafe_allow_html=True)
    # SIGNOFF_VOICE_GATE_TRUTH_20260715 ─ concrete backend state, never vague.
    # The panel's actual backend dependency is the task-contract function set
    # (db_connect / init_schema / generate_local_pairing_code / create_voice_task /
    # recent_tasks / summary) expected in this same module, persisting to the
    # sovereign_voice_gate_tasks table. There is no websocket service - the
    # state below reflects exactly that dependency chain and nothing fictional.
    def _status_chip(state: str, color: str, reason: str) -> None:
        st.markdown(
            f"<div style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0 10px;'>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:.6rem;letter-spacing:2px;"
            f"font-weight:900;color:{color};border:1px solid {color}66;border-radius:999px;"
            f"padding:4px 12px;background:{color}14;'>{html.escape(state)}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:.56rem;color:#8b86a0;"
            f"overflow-wrap:anywhere;'>{html.escape(reason)}</span></div>",
            unsafe_allow_html=True,
        )

    if not all([db_connect, init_schema, generate_local_pairing_code, create_voice_task, recent_tasks, summary]):
        _status_chip(
            "DISCONNECTED", ACCENT_RED,
            "Backend contract functions (db_connect/init_schema/create_voice_task/"
            "recent_tasks/summary) are not defined in services/sovereign_voice_gate.py - "
            "the module's self-import resolves to this UI panel. Task creation is offline "
            "until the signed backend build supplies those functions.",
        )
        # Read-only truth: if the canonical task table exists, show it (via the
        # hub's query_db) so the operator still sees real queued/refused tasks.
        if callable(query_db):
            try:
                _df = query_db(
                    "SELECT id, status, safety_class, title, requested_action, created_at "
                    "FROM sovereign_voice_gate_tasks ORDER BY created_at DESC LIMIT 10"
                )
                if _df is not None and not getattr(_df, "empty", True):
                    st.caption("Read-only view of sovereign_voice_gate_tasks (backend offline):")
                    for _, t in _df.iterrows():
                        color = ACCENT_RED if str(t.get("status")) == "REFUSED" else ACCENT_GOLD
                        _safe_card(
                            f"#{t.get('id')} · {t.get('status')} · {t.get('safety_class')}",
                            f"<b>{html.escape(str(t.get('title','')))}</b><br>"
                            f"Action: {html.escape(str(t.get('requested_action','')))}",
                            color,
                        )
                else:
                    st.caption("sovereign_voice_gate_tasks readable but empty - no voice tasks recorded.")
            except Exception as _ro_err:
                st.caption(
                    f"sovereign_voice_gate_tasks not readable in the active DB: "
                    f"{type(_ro_err).__name__}. Nothing to display."
                )
        return
    try:
        conn = db_connect(); init_schema(conn)
    except Exception as e:
        _status_chip("ERROR", ACCENT_RED,
                     f"Backend functions present, but DB init failed: {type(e).__name__}: {e}")
        return
    # Backend functions present and DB reachable → CONNECTED / IDLE / STALE by
    # real task recency from the canonical table.
    _vg_state, _vg_col, _vg_reason = "CONNECTED", ACCENT_GREEN, \
        "Task contract functions loaded; sovereign_voice_gate_tasks reachable."
    try:
        import time as _t
        _tasks_probe = recent_tasks(conn, limit=1) or []
        if not _tasks_probe:
            _vg_state, _vg_col = "IDLE", ACCENT_CYAN
            _vg_reason = "Backend connected; no voice tasks recorded yet."
        else:
            _last_ts = _tasks_probe[0].get("created_at") or 0
            try:
                _age = _t.time() - float(_last_ts)
                if _age > 86400:
                    _vg_state, _vg_col = "STALE", "#FFB347"
                    _vg_reason = f"Backend connected; last voice task {int(_age//3600)}h ago."
            except Exception:
                pass
    except Exception:
        pass
    _status_chip(_vg_state, _vg_col, _vg_reason)
    _safe_card("SAFE COMMAND CONTRACT", "Voice creates reviewable Council tasks only. It cannot enable live trading, move funds, expose keys, delete files, apply code, change launcher state, or bypass safety gates.", ACCENT_GREEN)
    with st.expander("VOICE CAPTURE · CONFIRM BEFORE QUEUE", expanded=True):
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
    with st.expander("LOCAL BUILD PAIRING CODE", expanded=False):
        st.caption("This authorizes drafting/build-review requests, not live trading.")
        ttl = st.slider("Code expiry minutes", 5, 30, 15, key="svg_code_ttl")
        if st.button("Generate 6-digit local code", key="svg_generate_code"):
            try:
                code = generate_local_pairing_code(conn, ttl_minutes=int(ttl))
                st.markdown(f"<div style='font-size:2.3rem;color:{ACCENT_GOLD};font-family:Share Tech Mono,monospace;letter-spacing:8px;font-weight:900;'>{code['code']}</div>", unsafe_allow_html=True)
                st.caption(f"Expires in {code['ttl_minutes']} minutes. This code is shown once.")
            except Exception as e:
                st.error(f"Could not generate code: {e}")
    with st.expander("RECENT REVIEWED VOICE TASKS", expanded=True):
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
