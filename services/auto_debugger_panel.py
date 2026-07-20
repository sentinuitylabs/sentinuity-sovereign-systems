"""
Sentinuity Auto Debugger Panel
==============================
Streamlit read-only renderer for the same gate map used by core/sovereign_doctor.py.
Import and call near the top of services/sovereign_hub.py:

    from ui.auto_debugger_panel import render_auto_debugger_panel
    render_auto_debugger_panel(str(DB_PATH))
"""
from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None  # type: ignore

from core.sovereign_gate_map import collect_gate_map


def _age(v: Any) -> str:
    if v is None:
        return "unknown"
    try:
        s = float(v)
    except Exception:
        return "unknown"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    return f"{s/3600:.1f}h"


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else "—"))


def _color_state(state: str) -> str:
    s = (state or "unknown").lower()
    if s in {"open", "fresh", "active"}:
        return "#14F195"
    if s in {"hour_gated", "starved", "idle", "shadow_only", "unknown"}:
        return "#FFD700"
    if s in {"blocked", "stale", "dead", "error", "wallet_limited"}:
        return "#FF5577"
    return "#8EF9FF"


def _card(title: str, state: str, body: str) -> str:
    col = _color_state(state)
    return f"""
    <div class="sgm-card" style="border-color:{col}88;box-shadow:0 0 18px {col}22;">
      <div class="sgm-title" style="color:{col};">{_esc(title)}</div>
      <div class="sgm-state" style="color:{col};">{_esc(state.upper() if state else 'UNKNOWN')}</div>
      <div class="sgm-body">{body}</div>
    </div>
    """


def render_auto_debugger_panel(db_path: str | None = None, intel_db_path: str | None = None) -> None:
    if st is None:
        return
    try:
        g = collect_gate_map(db_path, intel_db_path)
        p = g.get("paper", {})
        l = g.get("live", {})
        o = g.get("oracle", {})
        c = g.get("copytrade", {})
        f = g.get("candidates", {})

        live_reason = l.get("last_block_reason") or "no live blocker"
        if l.get("state") == "hour_gated":
            nxt = l.get("next_open_melbourne") or f"UTC hour {l.get('next_open_utc_hour')}"
            live_reason = f"normal live suppressed until {nxt}; Mode B still allowed"

        paper_body = (
            f"open {p.get('open_positions',0)}/{p.get('cap','?')}<br>"
            f"last fill age: {_age(p.get('last_fill_age_sec'))}<br>"
            f"reason: {_esc(p.get('last_block_reason') or 'paper lane open / no confirmed paper blocker')}"
        )
        live_body = (
            f"UTC hour {l.get('current_utc_hour')} · blocked {l.get('blocked_hours',[])}<br>"
            f"wallet: ${float(l.get('wallet_balance') or 0):.2f} · flat: ${float(l.get('flat_size') or 0):.2f}<br>"
            f"reason: {_esc(live_reason)}"
        )
        oracle_body = (
            f"age: {_age(o.get('age_sec'))} · gate: {float(o.get('gate_sec') or 0):.0f}s<br>"
            f"source: {_esc(o.get('source'))}<br>"
            f"reason: {_esc(o.get('block_reason') or 'fresh / no oracle block')}"
        )
        copy_body = (
            f"wallets watched: {c.get('wallets_watched',0)} · scan age: {_age(c.get('last_scan_age_sec'))}<br>"
            f"signals 10m: {c.get('signals_found_10m',0)} · promoted: {c.get('signals_promoted_10m',0)}<br>"
            f"reason: {_esc(c.get('last_reason') or 'no copytrade reason')}"
        )
        flow_body = (
            f"discovered {f.get('discovered_10m',0)} · priced {f.get('priced_10m',0)} · qualified {f.get('qualified_10m',0)}<br>"
            f"latched {f.get('latched_10m',0)} · ready {f.get('execution_ready_10m',0)} · expired {f.get('expired_10m',0)} · vetoed {f.get('vetoed_10m',0)}"
        )
        top_reasons = f.get("top_veto_reasons") or []
        reasons_html = "<br>".join([f"{_esc(r.get('count'))} × {_esc(r.get('reason'))}" for r in top_reasons[:4]]) or "no recent veto reasons"

        st.markdown(f"""
<style>
.sgm-wrap {{
  margin: 12px 0 18px; padding: 14px; border: 1px solid rgba(153,69,255,.35);
  border-radius: 16px; background: radial-gradient(circle at top left, rgba(153,69,255,.16), rgba(5,2,16,.92) 55%);
  font-family: 'Share Tech Mono', monospace;
}}
.sgm-head {{ color:#8EF9FF; letter-spacing:3px; font-size:.72rem; margin-bottom:8px; }}
.sgm-grid {{ display:grid; grid-template-columns: repeat(5, minmax(130px,1fr)); gap:10px; }}
.sgm-card {{ border:1px solid; border-radius:12px; padding:10px; background:rgba(0,0,0,.28); min-height:120px; }}
.sgm-title {{ font-size:.58rem; letter-spacing:2px; opacity:.9; }}
.sgm-state {{ font-size:.92rem; font-weight:800; margin:5px 0; }}
.sgm-body {{ font-size:.56rem; color:rgba(230,240,255,.72); line-height:1.45; }}
.sgm-verdict {{ margin-top:10px; padding:10px; border-left:3px solid #FFD700; background:rgba(255,215,0,.06); color:rgba(255,255,255,.82); font-size:.62rem; line-height:1.45; }}
@media (max-width: 900px) {{ .sgm-grid {{ grid-template-columns:1fr; }} }}
</style>
<div class="sgm-wrap">
  <div class="sgm-head">◈ AUTO DEBUGGER / GLASSBOX GATE MAP — READ ONLY</div>
  <div class="sgm-grid">
    {_card('PAPER EXECUTION', str(p.get('state','unknown')), paper_body)}
    {_card('LIVE EXECUTION', str(l.get('state','unknown')), live_body)}
    {_card('ORACLE', str(o.get('state','unknown')), oracle_body)}
    {_card('CANDIDATE FLOW', 'open' if f.get('qualified_10m',0) else 'idle', flow_body)}
    {_card('COPYTRADE', str(c.get('state','unknown')), copy_body)}
  </div>
  <div class="sgm-verdict">
    <b>PRIMARY CURRENT BLOCKER:</b> {_esc(g.get('primary_blocker'))}<br>
    <b>SECONDARY PRESSURE:</b> {_esc(g.get('secondary_pressure'))}<br>
    <b>TOP REASONS:</b> {reasons_html}<br>
    <b>SAFE NEXT ACTION:</b> {_esc(g.get('safe_next_action'))}
  </div>
</div>
""", unsafe_allow_html=True)
    except Exception as exc:
        st.markdown(
            f"""
            <div style='margin:12px 0;padding:14px;border:1px solid #FF5577;border-radius:12px;background:rgba(255,85,119,.08);font-family:Share Tech Mono,monospace;color:#FF99AA;'>
              AUTO DEBUGGER ERROR — {html.escape(str(exc))[:260]}<br>
              The panel failed safely instead of blanking the dashboard.
            </div>
            """,
            unsafe_allow_html=True,
        )
