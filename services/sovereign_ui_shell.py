"""Sentinuity Grand Vision UI shell.

Read-only presentation layer for the canonical services/sovereign_hub.py.
No trading, wallet, execution, schema, or configuration writes occur here.
Every database read is schema-tolerant and fail-silent so the shell cannot block
or regress the live backend.
"""
from __future__ import annotations

import hashlib
import html
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import streamlit as st


DOCTRINE_CSS = r"""
<style id="sentinuity-grand-vision-v1">
:root {
  --snty-void:#050210;
  --snty-surface:rgba(10,13,20,.88);
  --snty-surface-2:rgba(28,20,50,.34);
  --snty-cyan:#38E1FF;
  --snty-green:#14F195;
  --snty-purple:#9945FF;
  --snty-red:#FF073A;
  --snty-gold:#FFD700;
  --snty-amber:#FFB020;
  --snty-muted:#8190A8;
  --snty-line:rgba(255,255,255,.075);
}
html,body,[data-testid="stAppViewContainer"],.stApp {
  background:
    radial-gradient(circle at 15% -8%,rgba(56,225,255,.09),transparent 34rem),
    radial-gradient(circle at 86% 0%,rgba(153,69,255,.10),transparent 38rem),
    linear-gradient(180deg,#050210 0%,#070512 54%,#04020b 100%) !important;
}
[data-testid="stHeader"] {background:rgba(5,2,16,.72)!important;backdrop-filter:blur(16px)}
[data-testid="stMainBlockContainer"] {max-width:1500px;padding-top:.65rem;padding-bottom:4rem}

/* One coherent surface language instead of equal-weight telemetry boxes. */
[data-testid="stExpander"] {
  border:1px solid var(--snty-line)!important;border-radius:12px!important;
  background:linear-gradient(145deg,rgba(12,9,25,.74),rgba(5,2,16,.76))!important;
  box-shadow:0 12px 32px rgba(0,0,0,.16)!important;overflow:hidden
}
[data-testid="stExpander"] summary {min-height:44px!important}
[data-testid="stMetric"] {
  background:linear-gradient(145deg,rgba(14,11,29,.82),rgba(5,2,16,.86));
  border:1px solid rgba(56,225,255,.12);border-radius:10px;padding:.55rem .7rem;
}
[data-testid="stMetricLabel"] {font-size:.72rem!important;letter-spacing:.09em!important;text-transform:uppercase}
[data-testid="stMetricValue"] {font-family:'Share Tech Mono',monospace!important}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border:1px solid var(--snty-line);border-radius:10px;overflow:auto;
  background:rgba(5,2,16,.58)
}
button,[role="button"],a {transition:transform .16s ease,border-color .16s ease,background .16s ease}
button:hover,[role="button"]:hover {transform:translateY(-1px)}

.snty-gv-shell {position:relative;margin:.15rem 0 .85rem;border:1px solid rgba(56,225,255,.18);
 border-radius:16px;background:linear-gradient(135deg,rgba(8,8,20,.97),rgba(20,10,38,.91));
 box-shadow:0 20px 70px rgba(0,0,0,.34),inset 0 1px 0 rgba(255,255,255,.035);overflow:hidden}
.snty-gv-shell:before {content:"";position:absolute;inset:0;background:
 linear-gradient(90deg,transparent 0 49.8%,rgba(56,225,255,.025) 50%,transparent 50.2%),
 linear-gradient(0deg,transparent 0 49.8%,rgba(153,69,255,.025) 50%,transparent 50.2%);
 background-size:48px 48px;pointer-events:none}
.snty-gv-top {position:relative;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 18px 11px}
.snty-gv-brand {display:flex;align-items:center;gap:12px;min-width:0}
.snty-gv-sigil {width:38px;height:38px;border-radius:11px;display:grid;place-items:center;color:var(--snty-cyan);
 border:1px solid rgba(56,225,255,.35);background:radial-gradient(circle,rgba(56,225,255,.16),rgba(153,69,255,.06));
 box-shadow:0 0 24px rgba(56,225,255,.12);font-size:20px}
.snty-gv-title {font-family:Orbitron,sans-serif;font-size:clamp(.86rem,2vw,1.16rem);font-weight:800;letter-spacing:.19em;color:#eefaff;white-space:nowrap}
.snty-gv-sub {font:600 .68rem Rajdhani,sans-serif;letter-spacing:.13em;color:#7f94ab;text-transform:uppercase;margin-top:2px}
.snty-gv-mode {font:800 .66rem 'Share Tech Mono',monospace;letter-spacing:.12em;border-radius:999px;padding:7px 11px;border:1px solid currentColor;white-space:nowrap}
.snty-gv-metrics {position:relative;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;padding:0 12px 12px}
.snty-gv-metric {min-height:64px;padding:9px 10px;border-radius:10px;border:1px solid var(--snty-line);
 background:linear-gradient(155deg,rgba(255,255,255,.028),rgba(255,255,255,.009));min-width:0}
.snty-gv-label {font:700 .55rem 'Share Tech Mono',monospace;letter-spacing:.13em;color:#718198;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-gv-value {font:800 clamp(.78rem,1.5vw,1.02rem) 'Share Tech Mono',monospace;color:#eaf8ff;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-gv-detail {font:600 .59rem Rajdhani,sans-serif;color:#73839a;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-gv-rail {display:flex;align-items:center;gap:5px;overflow-x:auto;padding:7px 10px;margin:0 0 9px;
 border:1px solid rgba(153,69,255,.14);border-radius:12px;background:rgba(5,2,16,.66);scrollbar-width:none}
.snty-gv-rail::-webkit-scrollbar{display:none}
.snty-gv-rail a {flex:0 0 auto;text-decoration:none!important;color:#9aa7b9!important;font:700 .57rem 'Share Tech Mono',monospace;
 letter-spacing:.09em;text-transform:uppercase;padding:7px 10px;border-radius:8px;border:1px solid transparent}
.snty-gv-rail a:hover {color:#eefaff!important;border-color:rgba(56,225,255,.22);background:rgba(56,225,255,.055)}
.snty-section-head {display:flex;align-items:center;gap:11px;margin:19px 0 7px;padding:0 2px}
.snty-section-index {font:800 .57rem 'Share Tech Mono',monospace;color:var(--snty-cyan);letter-spacing:.12em;border:1px solid rgba(56,225,255,.27);border-radius:6px;padding:4px 6px}
.snty-section-copy {min-width:0}
.snty-section-title {font:800 .75rem Orbitron,sans-serif;letter-spacing:.15em;color:#e6f8ff;text-transform:uppercase}
.snty-section-sub {font:600 .68rem Rajdhani,sans-serif;color:#77869d;letter-spacing:.04em;margin-top:1px}
.snty-section-line {height:1px;flex:1;background:linear-gradient(90deg,rgba(56,225,255,.28),rgba(153,69,255,.12),transparent)}
.snty-runtime-stamp {font:600 .53rem 'Share Tech Mono',monospace;color:#68768b;letter-spacing:.06em;padding:0 14px 10px;position:relative}

@media (max-width:900px){
 [data-testid="stMainBlockContainer"]{padding-left:.72rem!important;padding-right:.72rem!important}
 .snty-gv-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media (max-width:600px){
 html,body,[data-testid="stAppViewContainer"]{overflow-x:hidden!important}
 [data-testid="stMainBlockContainer"]{padding:.45rem .48rem 3rem!important;max-width:100vw!important}
 .snty-gv-shell{border-radius:13px;margin-top:0}
 .snty-gv-top{padding:12px 11px 9px;align-items:flex-start}
 .snty-gv-title{font-size:.82rem;letter-spacing:.13em}
 .snty-gv-sub{font-size:.61rem;letter-spacing:.09em}
 .snty-gv-sigil{width:34px;height:34px;flex:0 0 34px}
 .snty-gv-mode{font-size:.57rem;padding:6px 8px}
 .snty-gv-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;padding:0 8px 9px}
 .snty-gv-metric{min-height:60px;padding:8px}
 .snty-gv-label{font-size:.52rem}.snty-gv-value{font-size:.81rem}.snty-gv-detail{font-size:.61rem}
 .snty-gv-rail{position:sticky;top:2.9rem;z-index:90;border-radius:10px;backdrop-filter:blur(14px);padding:6px}
 .snty-gv-rail a{min-height:40px;display:flex;align-items:center;font-size:.55rem;padding:5px 9px}
 .snty-section-head{margin-top:15px}.snty-section-title{font-size:.67rem}.snty-section-sub{font-size:.65rem}
 [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important;gap:.5rem!important}
 [data-testid="column"]{min-width:100%!important;width:100%!important;flex:1 1 100%!important}
 [data-testid="stDataFrame"],[data-testid="stTable"]{max-width:calc(100vw - 1rem)!important;overflow-x:auto!important}
 [data-testid="stMarkdownContainer"] p,[data-testid="stMarkdownContainer"] li{font-size:.88rem!important;line-height:1.48!important}
 [data-testid="stCaptionContainer"],.stCaption{font-size:.7rem!important}
 button,[role="button"],summary{min-height:44px!important}
}
</style>
"""


def inject_grand_vision_css() -> None:
    st.markdown(DOCTRINE_CSS, unsafe_allow_html=True)


def _scalar(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = (), default: Any = None) -> Any:
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def _age(value: Any, now: float) -> str:
    try:
        sec = max(0, int(now - float(value)))
        return f"{sec}s" if sec < 120 else f"{sec//60}m"
    except Exception:
        return "no pulse"


def _safe(s: Any) -> str:
    return html.escape(str(s if s is not None else "—"))


def render_grand_vision_header(db_path: str | Path, canonical_file: str | Path) -> None:
    """Render read-only command truth. Never raises into the canonical hub."""
    now = time.time()
    mode = "PAPER"
    mode_color = "#14F195"
    paper_equity = paper_cash = realized = None
    live_wallet = None
    paper_open = live_open = 0
    oracle_pulse = executor_pulse = council_pulse = None
    oracle_status = executor_status = council_status = "UNKNOWN"
    live_keys: dict[str, str] = {}
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT key,value FROM system_config WHERE key IN ("
                "'TRADING_MODE','PAPER_TRADING_ENABLED','DUAL_MODE_ENABLED','DUAL_MODE_ARMED',"
                "'LIVE_TRADING_ENABLED','LIVE_MODE_B_ENABLED','LIVE_ARMED')").fetchall()
            cfg = {str(r[0]): str(r[1] or '') for r in rows}
            live_keys = cfg
            armed = all(cfg.get(k, "0").strip().lower() in ("1","true","yes","on") for k in
                        ("DUAL_MODE_ENABLED","DUAL_MODE_ARMED","LIVE_TRADING_ENABLED","LIVE_MODE_B_ENABLED","LIVE_ARMED"))
            dual_requested = cfg.get("DUAL_MODE_ENABLED", "0") in ("1","true","TRUE") or cfg.get("LIVE_MODE_B_ENABLED", "0") in ("1","true","TRUE")
            raw = cfg.get("TRADING_MODE", "paper").upper()
            if armed:
                mode, mode_color = "DUAL · ARMED", "#9945FF"
            elif dual_requested:
                mode, mode_color = "DUAL · UNARMED", "#FFB020"
            elif raw == "LIVE":
                mode, mode_color = "LIVE · GATED", "#FF073A"
        except Exception:
            pass
        try:
            r = conn.execute("SELECT cash_balance,equity,realized_pnl FROM paper_wallet ORDER BY updated_at DESC LIMIT 1").fetchone()
            if r:
                paper_cash, paper_equity, realized = r[0], r[1], r[2]
        except Exception:
            pass
        for sql in (
            "SELECT balance_usd FROM live_wallet_state ORDER BY updated_at DESC LIMIT 1",
            "SELECT wallet_balance FROM live_wallet_state ORDER BY updated_at DESC LIMIT 1",
            "SELECT balance FROM live_wallet ORDER BY updated_at DESC LIMIT 1",
        ):
            live_wallet = _scalar(conn, sql, default=None)
            if live_wallet is not None:
                break
        paper_open = int(_scalar(conn, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND COALESCE(funding_mode,'SIM')!='REAL'", default=0) or 0)
        live_open = int(_scalar(conn, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND (COALESCE(funding_mode,'SIM')='REAL' OR COALESCE(entry_price_source,'') LIKE 'LIVE%')", default=0) or 0)
        try:
            for svc, pulse, status in conn.execute("SELECT service_name,last_pulse,status FROM system_heartbeat"):
                key = str(svc or '').lower()
                if key in ("ws_price_oracle","price_oracle","oracle") and (oracle_pulse is None or float(pulse or 0)>oracle_pulse):
                    oracle_pulse, oracle_status = float(pulse or 0), str(status or 'UNKNOWN')
                if key in ("execution_engine","trade_executor") and (executor_pulse is None or float(pulse or 0)>executor_pulse):
                    executor_pulse, executor_status = float(pulse or 0), str(status or 'UNKNOWN')
                if key in ("debate_engine","council_chamber_bridge","council_execution_spine") and (council_pulse is None or float(pulse or 0)>council_pulse):
                    council_pulse, council_status = float(pulse or 0), str(status or 'UNKNOWN')
        except Exception:
            pass
        conn.close()
    except Exception:
        pass

    def status_detail(status: str, pulse: Any) -> tuple[str, str]:
        age = (now - pulse) if pulse else 1e9
        blob = status.upper()
        if any(x in blob for x in ("ERROR","DEAD","FAILED","BLOCKED")):
            return "#FF073A", f"{blob} · {_age(pulse,now)}"
        if age > 180 or blob in ("WARN","DEGRADED","STALE"):
            return "#FFB020", f"{blob} · {_age(pulse,now)}"
        return "#14F195", f"{blob} · {_age(pulse,now)}"

    oracle_color, oracle_text = status_detail(oracle_status, oracle_pulse)
    exec_color, exec_text = status_detail(executor_status, executor_pulse)
    council_color, council_text = status_detail(council_status, council_pulse)
    metrics = [
        ("PAPER EQUITY", _money(paper_equity), "cumulative wallet truth", "#38E1FF"),
        ("REALIZED PNL", _money(realized), "cumulative · not one trade", "#38E1FF"),
        ("LIVE WALLET", _money(live_wallet), f"{live_open} open", "#9945FF"),
        ("POSITIONS", f"{paper_open} paper · {live_open} live", "current exposure", "#14F195"),
        ("PRICE TRUTH", oracle_text, "oracle heartbeat", oracle_color),
        ("COUNCIL", council_text, f"executor {exec_text}", council_color if council_color != '#14F195' else exec_color),
    ]
    cards = "".join(
        f'<div class="snty-gv-metric" style="border-top:2px solid {c}"><div class="snty-gv-label">{_safe(l)}</div>'
        f'<div class="snty-gv-value" style="color:{c}">{_safe(v)}</div><div class="snty-gv-detail">{_safe(d)}</div></div>'
        for l,v,d,c in metrics
    )
    try:
        p = Path(canonical_file)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        stamp = f"CANONICAL {p.as_posix()} · SHA {digest} · MODIFIED {mtime} · PID {os.getpid()}"
    except Exception:
        stamp = f"CANONICAL RUNTIME · PID {os.getpid()}"

    st.markdown(
        f'<div class="snty-gv-shell"><div class="snty-gv-top"><div class="snty-gv-brand">'
        f'<div class="snty-gv-sigil">◇</div><div><div class="snty-gv-title">SENTINUITY SOVEREIGN OS</div>'
        f'<div class="snty-gv-sub">TRADING TRUTH · INTELLIGENCE · AUTONOMOUS LEARNING</div></div></div>'
        f'<div class="snty-gv-mode" style="color:{mode_color};background:{mode_color}0D">{_safe(mode)}</div></div>'
        f'<div class="snty-gv-metrics">{cards}</div><div class="snty-runtime-stamp">{_safe(stamp)}</div></div>',
        unsafe_allow_html=True,
    )


def render_mission_rail() -> None:
    links = [
        ("truth","Trade Truth"),("gate","Final Gate"),("flow","Flow Engine"),
        ("learning","Post-Exit"),("council","Council"),("intelligence","Intelligence"),
        ("diagnostics","Diagnostics"),
    ]
    st.markdown('<div class="snty-gv-rail">' + ''.join(
        f'<a href="#{a}">{html.escape(t)}</a>' for a,t in links) + '</div>', unsafe_allow_html=True)


def render_section_header(anchor: str, index: str, title: str, subtitle: str) -> None:
    st.markdown(
        f'<div id="{html.escape(anchor)}" class="snty-section-head"><div class="snty-section-index">{html.escape(index)}</div>'
        f'<div class="snty-section-copy"><div class="snty-section-title">{html.escape(title)}</div>'
        f'<div class="snty-section-sub">{html.escape(subtitle)}</div></div><div class="snty-section-line"></div></div>',
        unsafe_allow_html=True,
    )
