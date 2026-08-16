"""
ui/sovereign_glassbox.py — Sentinuity Sovereign Glassbox v2
SIGNOFF_FINAL_GATE_20260611 / VISUAL_UPGRADE_20260611

The living 2.5D organism: half psilocybin rainforest / half Solana execution vault.
Uses the hub's own fonts (Orbitron, Share Tech Mono, Rajdhani), colour tokens, and
CSS idiom so the Glassbox feels native, not bolted-on.

ALL data comes from ui/data_sources.py (read-only, traceable).
Nothing is faked. Missing sources render as 'not wired'.
"""
from __future__ import annotations
import html as _h
import time

import streamlit as st
from ui import data_sources as D

# ── Hub colour tokens (mirrors sovereign_hub.py constants) ──────────────────
C_PURPLE = "#9945FF"
C_GREEN  = "#14F195"
C_GOLD   = "#FFD700"
C_CYAN   = "#8EF9FF"
C_RED    = "#FF073A"
C_EMBER  = "#FF6B35"
C_MIST   = "#9DB5A8"
C_STEEL  = "#AAB4C8"

FOREST_DEEP = "#080E0B"
VAULT_DARK  = "#0A0D14"

# ── CSS injected once ────────────────────────────────────────────────────────
_CSS = f"""
<style>
/* ════════ CRYSTALLINE GLASS SKIN — VISUAL_UPGRADE_20260611_v2 ════════
   Liquid glass / sentient-web crystal: translucent gold-tinted facets,
   holographic borders cycling the Solana palette (purple→cyan→green→gold),
   iridescent sheen sweeps. Pure CSS — zero impact on data wiring. */

@property --gbx-angle {{
  syntax: '<angle>'; initial-value: 0deg; inherits: false;
}}
@keyframes gbx-holo-rotate {{ to {{ --gbx-angle: 360deg; }} }}
@keyframes gbx-sheen {{
  0%   {{ transform: translateX(-130%) skewX(-18deg); }}
  100% {{ transform: translateX(230%)  skewX(-18deg); }}
}}
@keyframes gbx-pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.45}} }}

/* ── CRYSTAL FACET (replaces flat .gbx-world) ── */
.gbx-world {{
    position: relative;
    border-radius: 20px;
    padding: 18px 20px 14px;
    margin-bottom: 14px;
    overflow: hidden;
    /* gold-tinted liquid glass body over the forest/vault gradient */
    background:
      linear-gradient(115deg,
        rgba(255,215,0,0.045) 0%, rgba(255,215,0,0.015) 40%,
        rgba(153,69,255,0.03) 100%),
      linear-gradient(108deg,
        rgba(8,14,11,0.78) 0%, rgba(15,26,20,0.72) 28%,
        rgba(10,13,20,0.74) 60%, rgba(14,16,32,0.80) 100%);
    backdrop-filter: blur(14px) saturate(1.5);
    -webkit-backdrop-filter: blur(14px) saturate(1.5);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.10),          /* top crystal edge */
      inset 0 -1px 0 rgba(255,215,0,0.06),            /* gold under-glow */
      inset 0 0 38px rgba(255,215,0,0.035),           /* gold body tint  */
      0 8px 32px rgba(0,0,0,0.55);
    border: 1px solid transparent;
}}
/* holographic rotating border — Solana palette with gold dominance */
.gbx-world::before {{
    content: '';
    position: absolute; inset: 0;
    border-radius: 20px;
    padding: 1.5px;
    background: conic-gradient(from var(--gbx-angle),
        {C_GOLD}cc, {C_PURPLE}99, {C_CYAN}77, {C_GREEN}99,
        {C_GOLD}cc, {C_PURPLE}66, {C_GOLD}cc);
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    mask-composite: exclude;
    animation: gbx-holo-rotate 14s linear infinite;
    opacity: .85;
    pointer-events: none;
}}
/* iridescent sheen sweep across the facet */
.gbx-world::after {{
    content: '';
    position: absolute; top: 0; left: 0;
    width: 38%; height: 100%;
    background: linear-gradient(90deg,
        transparent 0%,
        rgba(255,215,0,0.07) 35%,
        rgba(153,69,255,0.10) 50%,
        rgba(142,249,255,0.07) 65%,
        transparent 100%);
    animation: gbx-sheen 7s ease-in-out infinite;
    pointer-events: none;
}}
.gbx-world:hover {{
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.14),
      inset 0 0 48px rgba(255,215,0,0.06),
      0 8px 44px rgba(153,69,255,0.30),
      0 0 24px rgba(255,215,0,0.15);
}}

.gbx-section-hdr {{
    font-family:'Orbitron',sans-serif;
    font-size:.72rem; font-weight:700;
    letter-spacing:.22em; text-transform:uppercase;
    margin-bottom:10px; padding-bottom:5px;
    border-bottom:1px solid rgba(255,215,0,.16);
    text-shadow: 0 0 14px currentColor;
}}
.gbx-mono {{ font-family:'Share Tech Mono',monospace; }}

/* ── CRYSTAL CHIPS — frosted glass capsules ── */
.gbx-chip {{
    display:inline-block; padding:3px 11px; margin:2px 3px 2px 0;
    border-radius:999px; font-family:'Share Tech Mono',monospace;
    font-size:.71rem;
    border:1px solid currentColor;
    background:
      linear-gradient(120deg, rgba(255,215,0,0.05), rgba(255,255,255,0.02)),
      rgba(4,8,6,0.55);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10),
                0 0 10px -4px currentColor;
    text-shadow: 0 0 8px currentColor;
}}
.gbx-alive {{ animation:gbx-pulse 2s ease-in-out infinite; }}
.gbx-src {{
    font-family:'Share Tech Mono',monospace; font-size:.59rem;
    color:#4A5C50; margin-top:5px; opacity:.85;
}}

/* ── LANE CARDS — crystal rails ── */
.gbx-lane {{
    position: relative;
    border-radius:12px; padding:11px 15px; margin:5px 0;
    border:1px solid; overflow: hidden;
    background:
      linear-gradient(120deg, rgba(255,215,0,0.04), transparent 55%),
      rgba(3,6,5,0.50);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.08),
                inset 0 0 24px -14px currentColor;
    font-family:'Share Tech Mono',monospace; font-size:.78rem;
    text-shadow: 0 0 7px currentColor;
}}
.gbx-lane.paper   {{ border-color:{C_GREEN};  color:{C_GREEN}; }}
.gbx-lane.live    {{ border-color:{C_PURPLE}; color:{C_PURPLE}; }}
.gbx-lane.closed  {{ opacity:.5; border-style:dashed; }}

/* ── SERVICE PILLS — crystal beads ── */
.svc-pill {{
    display:inline-flex; align-items:center; gap:6px;
    padding:4px 12px; margin:3px; border-radius:9px;
    border:1px solid rgba(255,215,0,.14);
    background:
      linear-gradient(120deg, rgba(255,215,0,0.05), rgba(153,69,255,0.03)),
      rgba(2,5,4,0.55);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.09);
    font-family:'Share Tech Mono',monospace; font-size:.72rem;
    text-shadow: 0 0 7px currentColor;
}}
.svc-dot {{
    width:7px; height:7px; border-radius:50%; flex-shrink:0;
    box-shadow: 0 0 8px currentColor, 0 0 3px currentColor;
    background: currentColor;
}}
.gbx-gate-wrap {{ overflow-x:auto; }}

/* ── COUNCIL CARDS — crystal tablets ── */
.council-card {{
    border-radius:12px; padding:11px 15px; margin:5px 0;
    border-left:3px solid;
    background:
      linear-gradient(120deg, rgba(255,215,0,0.04), transparent 60%),
      rgba(3,6,5,0.48);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.07);
    font-family:'Rajdhani',sans-serif; font-size:.88rem; line-height:1.5;
}}
.gbx-big {{ font-size:1.5rem; font-weight:700; font-family:'Orbitron',sans-serif; }}
.gbx-dim {{ color:#5A7060; font-size:.68rem; font-family:'Share Tech Mono',monospace; }}
.gbx-pulse-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin:7px 0 4px;max-width:100%;overflow:hidden}}
.gbx-pulse-stack{{border:1px solid rgba(142,249,255,.12);border-radius:10px;padding:7px;background:rgba(3,6,12,.58);min-width:0}}
.gbx-pulse-stack h4{{font-family:'Orbitron',sans-serif;font-size:.58rem;letter-spacing:.18em;margin:0 0 5px;color:#6f8090}}
.gbx-pulse-stack .svc-pill{{display:flex!important;width:100%;margin:3px 0!important;justify-content:flex-start;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-sizing:border-box}}
@media(max-width:700px){{.gbx-pulse-grid{{grid-template-columns:1fr;width:100%;max-width:100%}}.gbx-pulse-stack{{width:100%;max-width:100%}}}}

/* SIGNOFF_GLASSBOX_TRACE_20260808 */
.gbx-trace-shell {{ border:1px solid rgba(142,249,255,.16); border-radius:14px; padding:10px 11px; background:linear-gradient(140deg,rgba(153,69,255,.045),rgba(3,7,12,.72)); overflow:hidden; }}
.gbx-trace-head {{ display:flex; justify-content:space-between; gap:8px; align-items:center; font-family:'Share Tech Mono',monospace; font-size:.66rem; color:#8EF9FF; margin-bottom:7px; }}
.gbx-stage-rail {{ display:grid; grid-template-columns:repeat(7,minmax(44px,1fr)); gap:4px; margin:6px 0 9px; }}
.gbx-stage {{ font:600 .58rem 'Share Tech Mono',monospace; text-align:center; padding:4px 2px; border-top:2px solid #39424C; color:#73808A; white-space:nowrap; }}
.gbx-stage.on {{ border-color:#14F195; color:#C9FFE8; }}
.gbx-stage.gold {{ border-color:#FFD700; color:#FFD700; }}
.gbx-stage.block {{ border-color:#FF5A69; color:#FF7A86; }}
.gbx-stage.abs {{ border-color:#5E6470; color:#7E8490; border-top-style:dashed; }}
.gbx-trace-grid {{ display:grid; grid-template-columns:78px 86px minmax(90px,1fr) 70px minmax(120px,2fr); gap:5px; align-items:baseline; font:.62rem 'Share Tech Mono',monospace; padding:3px 0; border-bottom:1px solid rgba(255,255,255,.035); min-width:620px; }}
.gbx-trace-scroll {{ overflow-x:auto; }}
.gbx-raw {{ font:.59rem 'Share Tech Mono',monospace; color:#8D92A0; white-space:pre; overflow-x:auto; padding:2px 0; border-bottom:1px solid rgba(255,255,255,.025); }}
.gbx-code-op {{ color:#8EF9FF; }} .gbx-code-trust {{ color:#14F195; }} .gbx-code-gold {{ color:#FFD700; }} .gbx-code-block {{ color:#FF6B75; }} .gbx-code-abs {{ color:#747985; }}
@media (max-width:430px) {{ .gbx-trace-shell {{ padding:8px 8px; }} .gbx-stage-rail {{ grid-template-columns:repeat(4,1fr); }} .gbx-trace-head {{ font-size:.61rem; }} }}
</style>
"""

def _inject_css() -> None:
    # CSS_RERUN_FIX_20260612: Streamlit reruns rebuild the page but module
    # globals survive — a once-flag meant styles vanished after first rerun
    # and every chip/glyph rendered as raw text. Inject every render.
    st.markdown(_CSS, unsafe_allow_html=True)

# ── Helpers ──────────────────────────────────────────────────────────────────
def _chip(label:str, color:str, pulse:bool=False, title:str="") -> str:
    cls = "gbx-chip gbx-alive" if pulse else "gbx-chip"
    t = f' title="{_h.escape(title)}"' if title else ""
    return f'<span class="{cls}" style="color:{color}"{t}>{_h.escape(str(label))}</span>'

def _src(text:str) -> str:
    return f'<div class="gbx-src">⬡ {_h.escape(text)}</div>'

def _not_wired(what:str) -> str:
    return _chip(f"{what}: not wired", C_MIST,
                 title="No backend source — nothing is faked here.")

def _hdr(icon:str, title:str, color:str=C_CYAN, help_key:str="") -> str:
    try:
        from ui.holo_help import glyph as _hg
        q = _hg(help_key) if help_key else ""
    except Exception:
        q = ""
    return (f'<div class="gbx-section-hdr" style="color:{color}">'
            f'{icon} {_h.escape(title)}{q}</div>')

def _svc_pill(name:str, age_s:float|None, note:str="") -> str:
    try:
        from ui.theme import service_heartbeat_thresholds
        fresh_sec, aging_sec = service_heartbeat_thresholds(name)
    except Exception:
        fresh_sec, aging_sec = 40.0, 90.0
    if age_s is None:
        color, dot = C_MIST, C_MIST
    elif age_s <= fresh_sec:
        color, dot = C_GREEN, C_GREEN
    elif age_s <= aging_sec:
        color, dot = C_GOLD, C_GOLD
    elif age_s <= max(aging_sec * 3.0, 300.0):
        color, dot = C_EMBER, C_EMBER
    else:
        color, dot = C_RED, C_RED
    pulse_cls = ' gbx-alive' if (age_s is not None and age_s <= fresh_sec) else ''
    age_txt = f"{age_s:.0f}s" if age_s is not None else "?"
    tip = _h.escape(note or "")
    return (f'<span class="svc-pill{pulse_cls}" style="color:{color}" title="{tip}">'
            f'<span class="svc-dot" style="background:{dot}"></span>'
            f'{_h.escape(name)} {age_txt}</span>')

def _gate_rail_svg(phase_a:int, shadow:int, demoted:int,
                   insuff:int, live_veto:int, terminal:int,
                   paper_open:bool, live_open:bool,
                   live_reason:str="") -> str:
    gates = [
        ("PHASE A",           phase_a,   C_GREEN  if phase_a   else "#2C3A2C"),
        ("SHADOW\nVETO",      shadow,    C_MIST   if shadow    else "#2C3A2C"),
        ("HARD\nDEMOTED",     demoted,   C_CYAN   if demoted   else "#2C3A2C"),
        ("INSUFF\nDATA",      insuff,    C_CYAN   if insuff    else "#2C3A2C"),
        ("LIVE\nVETO",        live_veto, C_EMBER  if live_veto else "#2C3A2C"),
        ("TERMINAL\nVETO",    terminal,  C_RED    if terminal  else "#2C3A2C"),
    ]
    W, gw, gh, gx0, gy = 820, 100, 72, 12, 52
    parts = [
        f'<svg viewBox="0 0 {W} 180" width="100%" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block;margin-bottom:4px">',
        # crystal glow filter + gold-tinted facet gradient
        '<defs>'
        '<filter id="gbxglow" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="2.2" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        '</filter>'
        f'<linearGradient id="gbxfacet" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="#FFD700" stop-opacity="0.10"/>'
        f'<stop offset="0.5" stop-color="#080E0B" stop-opacity="0.55"/>'
        f'<stop offset="1" stop-color="#9945FF" stop-opacity="0.08"/>'
        f'</linearGradient>'
        '</defs>',
        # forest inflow
        f'<path d="M0 95 C30 60,30 130,60 95" stroke="{C_GREEN}" '
        f'stroke-width="2.5" fill="none" opacity=".55"/>',
        f'<text x="2" y="40" font-family="Share Tech Mono,monospace" '
        f'font-size="9" fill="{C_GREEN}" opacity=".7">FOREST\nINTAKE</text>',
        # vault wall
        f'<rect x="{W-10}" y="8" width="6" height="164" rx="3" fill="#1B2230" opacity=".8"/>',
    ]
    cur_x = 65
    for i,(name, count, color) in enumerate(gates):
        # connector line
        if i>0:
            parts.append(
                f'<line x1="{cur_x}" y1="88" x2="{cur_x+6}" y2="88" '
                f'stroke="{C_MIST}" stroke-width="1.5" opacity=".4"/>')
            cur_x += 6
        lines = name.split("\n")
        parts.append(
            f'<rect x="{cur_x}" y="{gy}" width="{gw}" height="{gh}" rx="8" '
            f'fill="url(#gbxfacet)" stroke="{color}" stroke-width="1.8" filter="url(#gbxglow)"/>'
        )
        for li, ln in enumerate(lines):
            parts.append(
                f'<text x="{cur_x+gw//2}" y="{gy+20+li*13}" text-anchor="middle" '
                f'font-family="Share Tech Mono,monospace" font-size="9" fill="{color}">'
                f'{_h.escape(ln)}</text>')
        parts.append(
            f'<text x="{cur_x+gw//2}" y="{gy+gh-8}" text-anchor="middle" '
            f'font-family="Orbitron,sans-serif" font-size="16" font-weight="700" fill="{color}">'
            f'{count}</text>')
        cur_x += gw + 2
    # lane split
    split_x = cur_x + 8
    pc = C_GREEN if paper_open else "#2C4A3C"
    lc = C_PURPLE if live_open else "#3A2C52"
    pr = "" if paper_open else " ✕"
    lr = "" if live_open else f" ✕ {live_reason[:24]}"
    parts += [
        f'<line x1="{cur_x}" y1="88" x2="{split_x}" y2="88" stroke="{C_MIST}" '
        f'stroke-width="1.5" opacity=".4"/>',
        f'<path d="M{split_x} 88 C{split_x+20} 88,{split_x+20} 58,{split_x+46} 58" '
        f'stroke="{pc}" stroke-width="3.5" fill="none"/>',
        f'<text x="{split_x+50}" y="62" font-family="Share Tech Mono,monospace" '
        f'font-size="11" font-weight="600" fill="{pc}">PAPER{pr}</text>',
        f'<path d="M{split_x} 88 C{split_x+20} 88,{split_x+20} 118,{split_x+46} 118" '
        f'stroke="{lc}" stroke-width="3.5" fill="none"/>',
        f'<text x="{split_x+50}" y="122" font-family="Share Tech Mono,monospace" '
        f'font-size="11" font-weight="600" fill="{lc}">LIVE{lr}</text>',
    ]
    parts.append("</svg>")
    return "".join(parts)


# ══════════════════════════════ PANELS ═══════════════════════════════════════

def _panel_pulse() -> dict:
    hb  = D.heartbeats()
    grd = D.guardian_events()
    lks = D.db_lock_warnings()
    st.markdown(_hdr("🜁", "Sovereign Pulse", C_CYAN, help_key="pulse"), unsafe_allow_html=True)

    if not hb.get("wired"):
        st.markdown(_not_wired("heartbeats") + _src(hb["src"]), unsafe_allow_html=True)
        return hb

    grouped = {"HEALTHY": [], "WATCH": [], "STALE": [], "BROKEN": []}
    for r in hb["rows"]:
        age = r.get("age_s")
        blob = (str(r.get("status") or "") + " " + str(r.get("note") or "")).lower()
        if any(x in blob for x in ("error", "failed", "broken", "traceback", "dead", "unauthorized")):
            bucket = "BROKEN"
        elif age is None or age >= 420:
            bucket = "STALE"
        elif age >= 120 or any(x in blob for x in ("degraded", "warn", "stall", "idle")):
            bucket = "WATCH"
        else:
            bucket = "HEALTHY"
        grouped[bucket].append(_svc_pill(r["service_name"], age, str(r.get("note") or "")))
    stack_cols = []
    for bucket in ("BROKEN", "STALE", "WATCH", "HEALTHY"):
        body = "".join(grouped[bucket]) or '<span class="gbx-dim">none</span>'
        stack_cols.append(f'<div class="gbx-pulse-stack"><h4>{bucket} · {len(grouped[bucket])}</h4>{body}</div>')
    pills = '<div class="gbx-pulse-grid">' + ''.join(stack_cols) + '</div>'
    restarts = grd.get("restarts", {})
    exec_restarts = restarts.get("execution_engine", 0)
    st.markdown(
        pills
        + "<br>"
        + _chip(f"guardian restarts 1h: {sum(restarts.values())}",
                C_RED if sum(restarts.values()) else C_GREEN)
        + (_chip(f"executor restarts: {exec_restarts}", C_RED) if exec_restarts else "")
        + _chip(f"db locks 1h: {lks.get('count','?')}",
                C_EMBER if lks.get("count") else C_GREEN)
        + _src(hb["src"]),
        unsafe_allow_html=True)
    return hb


def _panel_arena(gates:dict) -> dict:
    lane = D.lanes()
    st.markdown(_hdr("⬡", "Dual-Lane Execution Arena", C_PURPLE, help_key="arena"),
                unsafe_allow_html=True)
    if not lane.get("wired"):
        st.markdown(_not_wired("lanes") + _src(lane["src"]), unsafe_allow_html=True)
        return lane
    c = gates.get("counts", {})
    reasons = gates.get("live_block_reasons", {})
    live_reason = next(iter(reasons), "")
    paper_lines = [
        f"open {lane['paper_open']} / {lane.get('paper_max','?')} slots",
        f"PAPER_OPENED 10m: {c.get('paper_opened',0)}   shadow: {c.get('paper_shadow_opened',0)}",
    ]
    if lane.get("paper_equity") is not None:
        paper_lines.append(f"equity ${lane['paper_equity']:.2f}")
    live_lines = [
        f"open {lane['live_open']} / {lane.get('live_max','?')} slots",
        f"LIVE_OPENED 10m: {c.get('live_opened',0)}",
        f"live blocks 10m: {c.get('live_blocked',0)}   hour-gate: {c.get('hour_gate_live',0)}",
    ]
    live_open_now = c.get("live_blocked",0)==0 or c.get("live_opened",0)>0
    pcls = "gbx-lane paper" + ("" if lane["paper_enabled"] else " closed")
    lcls = "gbx-lane live" + ("" if live_open_now else " closed")
    p_body = "<br>".join(_h.escape(l) for l in paper_lines)
    l_body = "<br>".join(_h.escape(l) for l in live_lines)
    lr_html = (f'<div class="gbx-dim">blocked: {_h.escape(live_reason)}</div>'
               if live_reason and not live_open_now else "")
    st.markdown(
        f'<div class="{pcls}"><b>PAPER · proving rail</b><br>{p_body}</div>'
        f'<div class="{lcls}"><b>LIVE · vault rail</b><br>{l_body}{lr_html}</div>'
        + ("".join(_chip(f"live→paper: {r} ×{n}", C_CYAN) for r,n in reasons.items())
           if reasons else "")
        + _chip("shadow_on_block=ON", C_GREEN if lane.get("shadow_on_block") else C_EMBER)
        + _src(lane["src"]),
        unsafe_allow_html=True)
    return lane


def _panel_glassbox(gates:dict) -> dict:
    latch = D.latch_state()
    mcfg  = D.momentum_config()
    st.markdown(_hdr("🔥", "Final Gate Glassbox", C_GOLD, help_key="final_gate"), unsafe_allow_html=True)

    if not gates.get("wired"):
        st.markdown(_not_wired("exec log") + _src(gates["src"]),
                    unsafe_allow_html=True)
        return latch

    c = gates["counts"]
    reasons = gates.get("live_block_reasons", {})
    paper_open = c.get("paper_opened",0)>0 or c.get("mg_hard_terminal",0)==0
    live_open  = c.get("live_opened",0)>0

    svg = _gate_rail_svg(
        phase_a   = c.get("phase_a_pass",0),
        shadow    = c.get("mg_shadow_veto",0),
        demoted   = c.get("mg_hard_demoted",0),
        insuff    = c.get("mg_insufficient",0),
        live_veto = c.get("mg_hard_live_only",0),
        terminal  = c.get("mg_hard_terminal",0),
        paper_open= paper_open,
        live_open = live_open,
        live_reason= next(iter(reasons),""),
    )
    st.markdown(f'<div class="gbx-gate-wrap">{svg}</div>', unsafe_allow_html=True)

    repeat = gates.get("max_same_snap_hard_veto",0)
    jam    = latch.get("vetoed_still_visible","?")
    st.markdown(
        _chip(f"terminal vetoes: {c.get('mg_hard_terminal',0)}",
              C_RED if c.get("mg_hard_terminal") else C_GREEN)
        + _chip(f"repeat-pickup max: {repeat}×"
                + (f" snap={gates.get('worst_snap')}" if repeat>2 else ""),
                C_RED if repeat>2 else C_GREEN,
                title="Same snap looped = slot jam regression")
        + _chip(f"executor-visible: {latch.get('executor_visible','?')}",
                C_GREEN)
        + _chip(f"vetoed+latched: {jam}",
                C_RED if (isinstance(jam,int) and jam>0) else C_GREEN),
        unsafe_allow_html=True)

    if mcfg.get("wired"):
        st.markdown(
            "".join(_chip(f"{k.replace('MOMENTUM_GATE_','MG_')}={v}", C_MIST)
                    for k,v in mcfg["vals"].items())
            + _src(mcfg["src"]),
            unsafe_allow_html=True)

    st.markdown(_src(f"{gates['src']} | {latch.get('src','')}"),
                unsafe_allow_html=True)
    return latch


def _panel_price_truth() -> None:
    pt = D.price_truth()
    ma = D.momentum_audit_recent()
    st.markdown(_hdr("◈", "Price Truth", C_CYAN, help_key="price_truth"), unsafe_allow_html=True)

    if not pt.get("wired") or not pt.get("rows"):
        st.markdown(
            (_not_wired("ENTRY_AUDIT") if not pt.get("wired")
             else _chip("no entries in tail — awaiting first open", C_MIST))
            + _src(pt["src"]), unsafe_allow_html=True)
    else:
        for r in pt["rows"][:5]:
            drift = (((r["final"]/r["qualify"])-1)*100
                     if r["qualify"] and r["qualify"]>0 else None)
            drift_txt = f"{drift:+.2f}%" if drift is not None else "unmeasurable"
            src_color = C_CYAN if "router" in r["source"] else (
                C_GOLD if r["source"]=="upgraded" else C_MIST)
            pa_color = C_GREEN if r["price_age_s"]<60 else C_EMBER
            st.markdown(
                _chip(r["mint"][:10], C_MIST)
                + _chip(f"src={r['source']}", src_color)
                + _chip(f"qual→final {drift_txt}", src_color)
                + _chip(f"p-age {r['price_age_s']:.0f}s", pa_color)
                + _chip(f"s-age {r['signal_age_s']:.0f}s",
                        C_GREEN if r["signal_age_s"]<120 else C_EMBER),
                unsafe_allow_html=True)
        st.markdown(_src(pt["src"]), unsafe_allow_html=True)



def _panel_council(pulse:dict, gates:dict, lane:dict, latch:dict) -> None:
    st.markdown(_hdr("❄", "Council", C_GOLD, help_key="council"), unsafe_allow_html=True)
    lines = D.council_lines(pulse, gates, lane, latch)
    if not lines:
        st.markdown(_chip("council silent — awaiting grounded telemetry", C_MIST),
                    unsafe_allow_html=True)
        return
    AGENT_COLORS = {"Polaris": C_CYAN, "Ivaris": C_GOLD,
                    "Nugget": C_GREEN, "AXON": C_PURPLE}
    MODE_COLORS  = {"health": C_GREEN, "oracle": C_CYAN, "substrate": C_MIST,
                    "execution": C_PURPLE, "governance": C_GOLD}
    for ln in lines:
        ac = AGENT_COLORS.get(ln["agent"], C_MIST)
        mc = MODE_COLORS.get(ln["mode"], C_MIST)
        st.markdown(
            f'<div class="council-card" style="border-color:{ac}">'
            f'<span style="color:{ac};font-family:Orbitron,sans-serif;font-size:.78rem;'
            f'font-weight:700;">{_h.escape(ln["agent"])}</span> '
            f'<span class="gbx-dim" style="color:{mc};">[{_h.escape(ln["mode"])}]</span><br>'
            f'<span style="color:#CCC;">{_h.escape(ln["text"])}</span></div>',
            unsafe_allow_html=True)
    st.markdown(
        _src("derived from same telemetry as panels above — "
             "ui/data_sources.council_lines(); no invented numbers"),
        unsafe_allow_html=True)


def _panel_copytrade() -> None:
    ct = D.copytrade_state()
    st.markdown(_hdr("◐", "Copytrade · Smart Wallet Outpost", C_PURPLE, help_key="copytrade"),
                unsafe_allow_html=True)
    if not ct.get("wired"):
        st.markdown(
            _chip("copytrade lane: DEAD (not hidden, honestly absent)", C_MIST)
            + _src(ct["src"]), unsafe_allow_html=True)
        return
    st.markdown(
        _chip("scout ALIVE" if ct["scout_alive"] else "scout dead",
              C_GREEN if ct["scout_alive"] else C_EMBER,
              pulse=ct["scout_alive"])
        + "".join(_chip(f"{t}: {n} rows", C_MIST) for t,n in ct.get("tables",{}).items())
        + _chip("conviction→entries: NO (read-only)" if not ct["influences_entries"]
                else "conviction→entries: YES", C_MIST if not ct["influences_entries"] else C_GOLD,
                title="Flips only when smart_wallet_conviction is wired into entry path")
        + _src(ct["src"]),
        unsafe_allow_html=True)


# ══════════════════════════════ WORLD HEADER ═════════════════════════════════
_WORLD_HEADER = f"""
<div style="
    position:relative;
    background:
      linear-gradient(115deg, rgba(255,215,0,0.05), rgba(153,69,255,0.04) 60%, rgba(20,241,149,0.03)),
      linear-gradient(108deg, rgba(8,14,11,0.82) 0%, rgba(15,26,20,0.76) 28%,
        rgba(10,13,20,0.78) 60%, rgba(14,16,32,0.84) 100%);
    backdrop-filter: blur(16px) saturate(1.6);
    -webkit-backdrop-filter: blur(16px) saturate(1.6);
    border:1.5px solid rgba(255,215,0,0.28);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.12),
      inset 0 0 44px rgba(255,215,0,0.05),
      0 0 34px rgba(153,69,255,0.22),
      0 10px 36px rgba(0,0,0,0.6);
    border-radius:22px;
    padding:20px 26px 15px;
    margin-bottom:16px;
    overflow:hidden;">
  <div style="position:absolute;top:0;left:0;width:4px;height:100%;
      background:linear-gradient(to bottom,transparent,{C_GREEN},transparent);
      box-shadow:0 0 12px {C_GREEN};border-radius:4px 0 0 4px;"></div>
  <div style="position:absolute;top:0;right:0;width:4px;height:100%;
      background:linear-gradient(to bottom,transparent,{C_PURPLE},transparent);
      box-shadow:0 0 12px {C_PURPLE};border-radius:0 4px 4px 0;"></div>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
    <div>
      <div style="font-family:'Orbitron',sans-serif;font-size:.78rem;font-weight:700;
          letter-spacing:.3em;text-transform:uppercase;
          background:linear-gradient(90deg,{C_GOLD},{C_PURPLE} 45%,{C_CYAN} 70%,{C_GREEN});
          -webkit-background-clip:text;-webkit-text-fill-color:transparent;
          filter:drop-shadow(0 0 12px rgba(255,215,0,.55));
          margin-bottom:3px;">
        🜂 SOVEREIGN GLASSBOX</div>
      <div style="font-family:'Share Tech Mono',monospace;font-size:.62rem;
          color:rgba(255,215,0,.45);letter-spacing:.15em;
          text-shadow:0 0 8px rgba(255,215,0,.3);">
        forest intake · oracle bridge · cortex · forge · twin rails</div>
    </div>
    <div style="flex:1;min-width:200px;">
      <div style="height:2px;border-radius:2px;margin:8px 0 4px;
          background:linear-gradient(90deg,{C_GREEN}55,{C_GOLD}88,{C_PURPLE}55);
          box-shadow:0 0 8px rgba(255,215,0,.4);"></div>
      <div style="font-family:'Share Tech Mono',monospace;font-size:.6rem;color:#4A5C50;">
        every meter traces to a real backend source · missing = not wired · never faked</div>
    </div>
  </div>
</div>
"""



def _gbx_float(v):
    try:
        if v is None or v == "": return None
        x=float(v); return x if x==x else None
    except Exception: return None

def _gbx_pct_from_prices(entry,px):
    e=_gbx_float(entry); p=_gbx_float(px)
    if not e or e<=0 or p is None: return None
    return (p/e-1.0)*100.0

def _gbx_fmt_pct(v):
    x=_gbx_float(v)
    return "n/a" if x is None else f"{x:+.1f}%"

def _gbx_fmt_age(ts):
    x=_gbx_float(ts)
    return "n/a" if not x else f"{max(0.0,time.time()-x):.2f}s"

def _gbx_short(s,n=48):
    s=str(s or ""); return _h.escape(s if len(s)<=n else s[:n-1]+"…")

def _panel_execution_trace() -> None:
    payload=D.active_execution_glassbox()
    st.markdown(_hdr("⌬","Execution Glassbox · Harvest & Exit Route",C_CYAN),unsafe_allow_html=True)
    if not payload.get("wired"):
        st.markdown(_not_wired("execution trace")+_src(payload.get("src","")),unsafe_allow_html=True); return
    rows=payload.get("rows") or []
    if not rows:
        st.markdown(_chip("NO OPEN POSITION · waiting for next admission",C_MIST)+_src(payload.get("src","")),unsafe_allow_html=True); return
    try:
        from services.position_truth_payload import build_position_truth_payload
    except Exception:
        build_position_truth_payload=None
    for item in rows:
        p=item.get("position") or {}; parity=item.get("parity") or {}; fire=item.get("live_fire") or {}
        token=str(item.get("token") or "?"); pid=p.get("id")
        canon=build_position_truth_payload(int(pid)) if (build_position_truth_payload and pid) else {}
        A=canon.get("A_pool") or {}; B=canon.get("B_tape") or {}; Cx=canon.get("C_exec") or {}; Dobs=canon.get("D_observation") or {}
        obs_pct=_gbx_float(canon.get("observed_pnl_pct")); a_pct=_gbx_float(A.get("pnl_pct")); c_pct=_gbx_float(canon.get("executable_pnl_pct"))
        trust_pct=_gbx_float(canon.get("trusted_peak_pct")); floor_pct=_gbx_float(canon.get("protected_floor_pct"))
        floor_armed=str(canon.get("protected_floor_status") or "").upper()=="FRESH" and floor_pct is not None
        a_ok=str(A.get("status") or "").upper()=="FRESH"; b_ok=str(B.get("status") or "").upper()=="FRESH"
        c_ok=str(canon.get("executable_status") or "").upper()=="FRESH"; trust_ok=str(canon.get("trusted_peak_status") or "").upper()=="FRESH"
        route_ok=bool(Cx.get("route")) and c_ok
        live_verdict=str(parity.get("live_verdict") or "")
        live_reason=str(parity.get("terminal_reason") or parity.get("live_refusal_reason") or fire.get("reason") or "")
        fired=bool(fire) and str(fire.get("outcome") or "").upper() in ("SUBMITTED","FILLED","RECONCILED")
        refused=bool(live_reason) and not fired
        stages=[("OBS",obs_pct is not None,"on"),("A",a_ok,"on"),("B",b_ok,"on"),("EXEC",c_ok,"on" if c_ok else "abs"),
                ("TRUST",trust_ok,"gold" if trust_ok else "abs"),("ARM",floor_armed,"gold" if floor_armed else "abs"),
                ("FIRE",fired,"on" if fired else ("block" if refused else "abs"))]
        rail="".join(f"<div class='gbx-stage {cls if ok or cls in ('block','abs') else 'abs'}'>{label}</div>" for label,ok,cls in stages)
        acd=_gbx_float(canon.get("a_c_divergence_pct")); oed=_gbx_float(canon.get("obs_exec_divergence_pct"))
        ac_txt="n/a" if acd is None else f"{acd:.1f}%"; oe_txt="n/a" if oed is None else f"{oed:+.1f} pts"
        exec_socket=str((canon.get("stage_executable") or {}).get("socket_state") or "")
        oe_cls="gbx-code-block" if exec_socket=="REJECTED" else ("gbx-code-trust" if oed is not None else "gbx-code-abs")
        head=f"<div class='gbx-trace-head'><span>POS {pid} · {_gbx_short(token,22)}</span><span>OBS {_gbx_fmt_pct(obs_pct)} · EXEC {_gbx_fmt_pct(c_pct)} · TRUST {_gbx_fmt_pct(trust_pct)} · FLOOR {(_gbx_fmt_pct(floor_pct) if floor_armed else 'WITHHELD')}</span></div>"
        latency=Cx.get("latency_ms")
        latency_txt="unknown" if latency is None else f"{_gbx_float(latency):.0f}ms"
        traces=[
          ("A:POOL",str(A.get("source") or "NO WITNESS"),f"slot {A.get('slot') if A.get('slot') is not None else '—'}",_gbx_fmt_pct(a_pct),f"age {'unknown' if A.get('age_sec') is None else format(A['age_sec'],'.2f')+'s'} · {A.get('status') or 'ABSENT'}","gbx-code-trust" if a_ok else "gbx-code-abs"),
          ("B:TAPE",str(B.get("source") or "NO WITNESS"),f"slot {B.get('slot') if B.get('slot') is not None else '—'}","signature-backed" if b_ok else "—",_gbx_short(B.get("tx_signature") or "NO WITNESS",38),"gbx-code-trust" if b_ok else "gbx-code-abs"),
          ("C:EXEC",str(Cx.get("source") or "NO QUOTE"),f"slot {Cx.get('slot') if Cx.get('slot') is not None else '—'}",_gbx_fmt_pct(c_pct),f"age {'unknown' if Cx.get('age_sec') is None else format(Cx['age_sec'],'.2f')+'s'} · lat={latency_txt} · {Cx.get('status') or 'ABSENT'} · {Cx.get('reason_code') or ''}","gbx-code-trust" if c_ok else "gbx-code-abs"),
          ("D:OBS",str(Dobs.get("source") or "NO OBS"),"market",_gbx_fmt_pct(obs_pct),f"market age {'unknown' if Dobs.get('age_sec') is None else format(Dobs['age_sec'],'.2f')+'s'} · {Dobs.get('status') or 'ABSENT'}","gbx-code-op" if obs_pct is not None else "gbx-code-abs"),
          ("TRUTH","A↔C","divergence",ac_txt,f"independence diagnostic · authority={canon.get('authority_stage') or 'NONE'}","gbx-code-trust" if acd is not None else "gbx-code-abs"),
          ("TRUTH","OBS↔EXEC","divergence",oe_txt,f"reason={canon.get('reason_code') or ''}",oe_cls),
          ("HARVEST",str(canon.get("authority_stage") or "NONE"),"peak→floor",_gbx_fmt_pct(trust_pct),f"floor={_gbx_fmt_pct(floor_pct) if floor_armed else 'WITHHELD'}","gbx-code-gold" if floor_armed else "gbx-code-abs"),
          ("EXIT_BUILD",str(Cx.get("source") or "WAITING"),"route","READY" if route_ok else "WAITING_FOR_TRIGGER/QUOTE",_gbx_short(Cx.get("route") or Cx.get("reason_code") or "NO ROUTE EVIDENCE",80),"gbx-code-trust" if route_ok else "gbx-code-abs"),
          ("CAPITAL",str(live_verdict or fire.get("outcome") or "NO DECISION"),"Mode 3","FIRE" if fired else ("HELD" if refused else "WAITING"),_gbx_short(live_reason or "no current blocker published",100),"gbx-code-trust" if fired else ("gbx-code-block" if refused else "gbx-code-abs"))
        ]
        body=["<div class='gbx-trace-grid'>"+f"<span class='{cls}'>{_gbx_short(stage,16)}</span><span>{_gbx_short(source,18)}</span><span>{_gbx_short(slot,28)}</span><span class='{cls}'>{_gbx_short(value,18)}</span><span>{_gbx_short(detail,120)}</span></div>" for stage,source,slot,value,detail,cls in traces]
        raw_lines=[f"<div class='gbx-raw'><span class='gbx-code-op'>{_gbx_short(ev.get('source'),22)}</span>  {_gbx_short(ev.get('line'),420)}</div>" for ev in (item.get("raw_trace") or [])]
        raw_html="".join(raw_lines) if raw_lines else "<div class='gbx-raw gbx-code-abs'>NO MATCHING INTERNAL EXIT/HARVEST STRING YET</div>"
        st.markdown("<div class='gbx-trace-shell'>"+head+f"<div class='gbx-stage-rail'>{rail}</div><div class='gbx-trace-scroll'>"+"".join(body)+"</div><details style='margin-top:8px'><summary style='font:600 .62rem Share Tech Mono;color:#8EF9FF;cursor:pointer'>RAW INTERNAL STRINGS · LIVE ENGINE TAIL</summary>"+raw_html+"</details></div>",unsafe_allow_html=True)
    st.markdown(_src(payload.get("src","")),unsafe_allow_html=True)



# ══════════════════════════════ MOUNT POINT ══════════════════════════════════
def render_glassbox() -> None:
    _inject_css()
    st.markdown(_WORLD_HEADER, unsafe_allow_html=True)

    # CONSOLIDATION_PASS_20260611 — one truth per concept:
    #   the live maintenance trace proves the hero claim with real events,
    #   the diagnostic console is the SINGLE home for raw evidence
    #   (per-panel raw expanders removed).
    try:
        from ui.maintenance_trace import render_maintenance_trace
        render_maintenance_trace(st)
    except Exception as _mt_err:
        st.caption(f"maintenance trace unavailable: {_mt_err}")
    try:
        from ui.diagnostic_report import render_diagnostic_console
        render_diagnostic_console(st)
    except ModuleNotFoundError:
        pass
    except Exception as _dr_err:
        st.caption(f"diagnostic console unavailable: {type(_dr_err).__name__}")

    gates = D.gate_counters()

    # Row 1: Pulse full-width
    with st.container():
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        pulse = _panel_pulse()
        st.markdown('</div>', unsafe_allow_html=True)

    # Row 2: Final Gate (wide) + Price Truth (narrower)
    col_gate, col_price = st.columns([3, 2])
    with col_gate:
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        latch = _panel_glassbox(gates)
        st.markdown('</div>', unsafe_allow_html=True)
    with col_price:
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        _panel_price_truth()
        st.markdown('</div>', unsafe_allow_html=True)

    # Full-width causal Glassbox: real evidence, route build, harvest state and
    # capital decision. Read-only; it cannot promote authority or fire capital.
    with st.container():
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        _panel_execution_trace()
        st.markdown('</div>', unsafe_allow_html=True)

    # Row 3: Dual-Lane Arena + Copytrade
    col_arena, col_ct = st.columns([3, 2])
    with col_arena:
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        lane = _panel_arena(gates)
        st.markdown('</div>', unsafe_allow_html=True)
    with col_ct:
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        _panel_copytrade()
        st.markdown('</div>', unsafe_allow_html=True)

    # Row 4: Council full-width
    with st.container():
        st.markdown('<div class="gbx-world">', unsafe_allow_html=True)
        _panel_council(
            pulse if isinstance(pulse, dict) else {},
            gates,
            lane  if isinstance(lane,  dict) else {},
            latch if isinstance(latch, dict) else {},
        )
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="gbx-src" style="text-align:right;margin-top:6px;">'
        f'rendered {time.strftime("%H:%M:%S")} · read-only · '
        f'cache TTL 5–60s per source</div>',
        unsafe_allow_html=True)
