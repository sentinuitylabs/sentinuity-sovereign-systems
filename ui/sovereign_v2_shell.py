"""Sentinuity V2 Sovereign Glassbox shell.

Presentation-only layer for the canonical Streamlit hub. It reads a very small
set of runtime truth values and renders the Calm / Glassbox / Full Matrix
atmosphere. It never writes to the database and never changes trading state.
"""
from __future__ import annotations

import html
import sqlite3
import time
from pathlib import Path
from typing import Any


def _safe(value: Any) -> str:
    return html.escape(str(value if value is not None else "NOT WIRED"))


def _truth(db_path: str | Path) -> dict[str, str]:
    out = {
        "oracle":"NOT WIRED",
        "executor":"NOT WIRED",
        "council":"NOT WIRED",
        "mode":"UNKNOWN",
        "paper":"0 OPEN",
        "live":"0 OPEN",
        "gate":"AWAITING TRUTH",
    }
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.5)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=750")
        now = time.time()
        try:
            rows = conn.execute(
                "SELECT service_name, COALESCE(status,''), COALESCE(last_pulse,0) "
                "FROM system_heartbeat WHERE service_name IN "
                "('ws_price_oracle','execution_engine','council_chamber_bridge','polaris')"
            ).fetchall()
            for service, status, pulse in rows:
                age = int(max(0, now - float(pulse or 0))) if pulse else -1
                label = (
                    f"{str(status or 'UNKNOWN').upper()} · {age}s"
                    if age >= 0 else str(status or "NO PULSE").upper()
                )
                s = str(service).lower()
                if "oracle" in s:
                    out["oracle"] = label
                elif "execution" in s:
                    out["executor"] = label
                elif "council" in s or "polaris" in s:
                    out["council"] = label
        except Exception:
            pass
        try:
            cfg = dict(conn.execute("SELECT key, value FROM config").fetchall())
            mode = str(cfg.get("TRADING_MODE", "paper")).upper()
            live_b = str(cfg.get("LIVE_MODE_B_ENABLED", "0")).lower() in {
                "1","true","yes","on"
            }
            out["mode"] = "DUAL" if live_b and mode != "LIVE" else mode
        except Exception:
            pass
        try:
            paper_open = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
            ).fetchone()[0]
            out["paper"] = f"{int(paper_open)} OPEN"
        except Exception:
            pass
        try:
            live_open = conn.execute(
                "SELECT COUNT(*) FROM live_positions WHERE status='OPEN'"
            ).fetchone()[0]
            out["live"] = f"{int(live_open)} OPEN"
        except Exception:
            pass
        conn.close()
    except Exception:
        return out

    # PARITY_20260718: this strip previously derived its own gate verdict from
    # heartbeats — a second, independent truth that could disagree with the
    # executor. The executor-authored live_decision_contract is now the ONLY
    # source of the verdict shown here (same doctrine as the Live Gate
    # Constellation). The heartbeat derivation survives only as an explicitly
    # labelled SERVICE-HEALTH fallback when no contract is readable, and never
    # claims a fire path.
    contract_verdict = None
    try:
        from services.live_decision_contract import read_contract as _read_ldc
        _c = _read_ldc()
        if _c.get("available") and not _c.get("stale"):
            _v = str(_c.get("verdict") or "").upper()
            _map = {
                "FIRE_PATH_OPEN": "FIRE PATH OPEN",
                "ARMED_WAITING": "ARMED · WAITING",
                "ALIGNING": "ALIGNING",
                "BLOCKED": "BLOCKED",
                "BUY_SUBMITTED": "BUY SUBMITTED",
                "OPEN_REAL": "OPEN REAL",
                "SELL_SUBMITTED": "SELL SUBMITTED",
                "SETTLED": "SETTLED",
                "MANUAL_INTERVENTION": "BLOCKED · MANUAL INTERVENTION",
            }
            contract_verdict = _map.get(_v, _v or None)
            if contract_verdict == "BLOCKED" and _c.get("blocker"):
                contract_verdict = f"BLOCKED · {str(_c['blocker'])[:48]}"
    except Exception:
        contract_verdict = None

    if contract_verdict:
        out["gate"] = contract_verdict
    else:
        oracle_bad = any(
            x in out["oracle"] for x in ("STALE","DEAD","ERROR","STALLED","NO PULSE")
        )
        executor_bad = any(
            x in out["executor"] for x in ("STALE","DEAD","ERROR","FAILED","NO PULSE")
        )
        out["gate"] = (
            "BLOCKED · SERVICE HEALTH"
            if oracle_bad or executor_bad
            else "SERVICES HEALTHY · EXECUTOR VERDICT UNPUBLISHED"
        )
    return out


def inject_sovereign_v2(st, db_path: str | Path) -> None:
    """Inject the V2 shell with no horizontal or diagonal Matrix renderer."""
    truth = _truth(db_path)

    st.markdown(f"""
<style>
:root{{--snty-green:#14F195;--snty-violet:#9945FF;--snty-cyan:#8EF9FF;
--snty-gold:#FFD700;--snty-red:#FF073A;--snty-void:#04030b;}}
html,body,[data-testid="stAppViewContainer"]{{background:#04030b!important;}}
[data-testid="stAppViewContainer"]{{
background-image:
radial-gradient(circle at 15% -10%,rgba(153,69,255,.12),transparent 34%),
radial-gradient(circle at 88% 8%,rgba(142,249,255,.07),transparent 28%),
linear-gradient(180deg,#050410 0%,#030208 72%,#05030d 100%)!important;}}
[data-testid="stHeader"]{{background:rgba(4,3,11,.74)!important;
backdrop-filter:blur(18px);}}
.block-container{{max-width:1540px;padding-top:1.15rem!important;
padding-bottom:5rem!important;position:relative;z-index:2;}}
#MainMenu,footer{{visibility:hidden;}}

/* Signed-off kill switch: all known legacy sideways/diagonal layers. */
.snty-v2-matrix,
.snty-matrix-watermark,
.matrix-watermark,
.matrix-drift,
.matrix-overlay,
.cinematic-watermark,
.truth-watermark,
[class*="matrix-drift"],
[id*="matrix-drift"],
[class*="matrix-watermark"],
[id*="matrix-watermark"],
[class*="horizontal-watermark"],
[id*="horizontal-watermark"]{{
display:none!important;
visibility:hidden!important;
opacity:0!important;
animation:none!important;
transform:none!important;
pointer-events:none!important;}}

.snty-v2-control{{position:relative;z-index:3;display:flex;align-items:center;
justify-content:space-between;gap:18px;padding:11px 14px;margin:2px 0 12px;
border-top:1px solid rgba(142,249,255,.17);
border-bottom:1px solid rgba(153,69,255,.16);
background:linear-gradient(90deg,rgba(8,6,20,.88),rgba(5,4,13,.58),
rgba(8,6,20,.88));backdrop-filter:blur(18px);}}
.snty-v2-control__truth{{font:600 .62rem/1.4 'Share Tech Mono',monospace;
letter-spacing:.13em;color:#d7d4e2;}}
.snty-v2-control__truth b{{color:{
'#FF073A' if 'BLOCKED' in truth['gate'] else '#14F195'
};font-weight:700;}}
.snty-v2-control__mode{{font:600 .53rem/1 'Orbitron',sans-serif;
letter-spacing:.14em;color:#8EF9FF;border:1px solid rgba(142,249,255,.22);
border-radius:999px;padding:7px 10px;background:rgba(142,249,255,.04);}}
[data-testid="stVerticalBlock"]>div:has(>.element-container){{position:relative;}}
[data-testid="stExpander"]{{border:1px solid rgba(153,69,255,.13)!important;
border-radius:10px!important;background:rgba(7,5,17,.38)!important;
box-shadow:none!important;}}
[data-testid="stExpander"] summary{{font-family:'Orbitron',sans-serif!important;
letter-spacing:.11em;color:#9b96aa!important;}}
[data-testid="stMetric"]{{background:transparent!important;border:0!important;
padding:.25rem .1rem!important;}}
[data-testid="stMetricLabel"]{{font-family:'Share Tech Mono',monospace!important;
letter-spacing:.12em;color:#77738a!important;}}
[data-testid="stMetricValue"]{{font-family:'Orbitron',sans-serif!important;
color:#e8e5ef!important;}}
.stButton>button{{border-radius:999px!important;
border:1px solid rgba(142,249,255,.18)!important;
background:rgba(8,6,20,.68)!important;box-shadow:none!important;}}
.stTabs [data-baseweb="tab-list"]{{gap:4px;background:transparent!important;
border-bottom:1px solid rgba(153,69,255,.13);}}
.stTabs [data-baseweb="tab"]{{border:0!important;background:transparent!important;
color:#77738a!important;}}
.stTabs [aria-selected="true"]{{color:#8EF9FF!important;
border-bottom:1px solid #8EF9FF!important;}}
.snty-section-head{{margin-top:34px!important;margin-bottom:13px!important;
padding:0 0 10px!important;border-bottom:1px solid rgba(142,249,255,.10)!important;}}
.snty-section-line{{opacity:.25!important;}}
.snty-gv-shell{{border:0!important;border-radius:0!important;
background:transparent!important;box-shadow:none!important;padding:10px 0 4px!important;}}
.snty-gv-metric{{background:rgba(8,6,20,.34)!important;border-left:0!important;
border-right:0!important;border-bottom:0!important;border-radius:3px!important;
box-shadow:none!important;}}
.snty-gv-rail{{position:sticky;top:2.9rem;z-index:20;
background:rgba(4,3,11,.85)!important;backdrop-filter:blur(16px);border:0!important;
border-bottom:1px solid rgba(142,249,255,.12)!important;border-radius:0!important;}}
[id="council"]~div [style*="border-radius:12px"],
[id="council"]~div [style*="border-radius: 12px"]{{box-shadow:none!important;}}
/* Grand-Vision hierarchy normalization: one doctrine from crown to diagnostics. */
[data-testid="stAppViewContainer"] p,[data-testid="stAppViewContainer"] li{{font-family:'Share Tech Mono',monospace;line-height:1.55;}}
[data-testid="stDataFrame"],.stDataFrame{{border:1px solid rgba(142,249,255,.12)!important;border-radius:10px!important;overflow:hidden;background:rgba(5,3,15,.72)!important;}}
[data-testid="stDataFrame"] *{{font-family:'Share Tech Mono',monospace!important;font-size:.69rem!important;}}
.snty-authority,.snty-command-truth{{border-color:rgba(255,215,0,.38)!important;box-shadow:0 0 24px rgba(255,215,0,.055)!important;}}
.snty-flow,.snty-cognition{{border-color:rgba(142,249,255,.24)!important;}}
.snty-structure,.snty-market{{border-color:rgba(153,69,255,.28)!important;}}
.snty-blocker,.snty-risk{{border-color:rgba(255,7,58,.42)!important;}}
.snty-live-funded{{position:relative!important;border-color:rgba(255,215,0,.72)!important;background:linear-gradient(145deg,rgba(255,215,0,.08),rgba(20,241,149,.045),rgba(5,3,15,.92))!important;box-shadow:0 0 28px rgba(255,215,0,.12),inset 0 0 18px rgba(255,215,0,.035)!important;animation:snty-funded-breathe 3.2s ease-in-out infinite;}}
.snty-live-funded.loss{{background:linear-gradient(145deg,rgba(255,215,0,.075),rgba(255,7,58,.05),rgba(5,3,15,.92))!important;}}
@keyframes snty-funded-breathe{{0%,100%{{box-shadow:0 0 18px rgba(255,215,0,.08)}}50%{{box-shadow:0 0 34px rgba(255,215,0,.18)}}}}
@media(prefers-reduced-motion:reduce){{.snty-live-funded{{animation:none!important}}}}
@media(max-width:720px){{
[data-testid="stAppViewContainer"] p,[data-testid="stAppViewContainer"] li{{font-size:.86rem!important;}}
[data-testid="stMetricValue"]{{font-size:1rem!important;}}
.stTabs [data-baseweb="tab"]{{font-size:.68rem!important;padding:.55rem .45rem!important;}}
.block-container{{padding-left:.65rem!important;padding-right:.65rem!important;}}
.snty-v2-control{{align-items:flex-start;flex-direction:column;gap:8px;}}
.snty-gv-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))!important;}}
.snty-section-title{{font-size:.76rem!important;letter-spacing:.12em!important;}}}}

/* V2 EVOLVED CHECKPOINT — clarity-first, non-boxy organism surfaces. */
:root{{--snty-plane:rgba(8,6,22,.66);--snty-line:rgba(142,249,255,.12);--snty-soft:0 24px 72px rgba(0,0,0,.24)}}
.block-container{{max-width:1480px!important;padding-left:clamp(14px,3.4vw,54px)!important;padding-right:clamp(14px,3.4vw,54px)!important}}
/* Let each major region breathe; hierarchy comes from light and rhythm, not boxes. */
.snty-section-head{{margin-top:52px!important;padding:0 0 14px!important;border:0!important;position:relative}}
.snty-section-head:after{{content:"";display:block;width:min(340px,56vw);height:1px;margin-top:12px;background:linear-gradient(90deg,var(--snty-cyan),rgba(153,69,255,.48),transparent)}}
.snty-crystal-panel,.snty-cyan-panel,.snty-gold-panel,.substrate-card,.intel-hero,[data-testid="stExpander"]{{
 border:0!important;border-radius:26px 7px 26px 7px!important;
 background:radial-gradient(circle at 8% 0%,rgba(142,249,255,.055),transparent 38%),linear-gradient(128deg,rgba(11,8,29,.70),rgba(5,4,15,.50))!important;
 box-shadow:var(--snty-soft)!important;overflow:hidden;position:relative}}
.snty-crystal-panel:before,.snty-cyan-panel:before,.snty-gold-panel:before,.substrate-card:before{{
 content:"";position:absolute;left:0;top:0;width:42%;height:1px;background:linear-gradient(90deg,var(--snty-cyan),var(--snty-violet),transparent);opacity:.72}}
.snty-gold-panel:before{{background:linear-gradient(90deg,var(--snty-gold),rgba(153,69,255,.72),transparent)}}
/* Metrics become floating facets rather than boxed tiles. */
.snty-metric-card,.snty-gv-metric,[data-testid="stMetric"]{{
 border:0!important;border-radius:20px 5px 20px 5px!important;background:linear-gradient(140deg,rgba(142,249,255,.045),rgba(153,69,255,.026) 54%,rgba(255,215,0,.018))!important;
 box-shadow:inset 0 1px rgba(255,255,255,.025)!important}}
/* Dense tables and feeds retain truth but recede until read. */
[data-testid="stDataFrame"],.stDataFrame{{border:0!important;border-radius:18px 4px 18px 4px!important;background:rgba(3,3,12,.58)!important;box-shadow:inset 0 1px rgba(142,249,255,.08)!important}}
.sntFeedWrap{{border:0!important;border-radius:24px 6px 24px 6px!important;background:linear-gradient(135deg,rgba(4,3,14,.90),rgba(10,5,24,.58))!important;box-shadow:var(--snty-soft)!important}}
/* Council becomes a sanctum: one ambient plane, lighter cards, less scaffolding. */
.cncl-card{{border:0!important;border-radius:22px 5px 22px 5px!important;background:linear-gradient(135deg,rgba(153,69,255,.07),rgba(5,3,15,.62))!important;box-shadow:0 14px 44px rgba(0,0,0,.18)!important}}
/* Navigation and control rails are quiet, thin and architectural. */
.snty-v2-control{{border:0!important;border-radius:999px!important;padding:9px 16px!important;margin-bottom:24px!important;background:rgba(4,3,12,.62)!important;box-shadow:inset 0 0 0 1px rgba(142,249,255,.09)!important}}
.stButton>button{{min-height:2.25rem;border:0!important;background:linear-gradient(110deg,rgba(142,249,255,.07),rgba(153,69,255,.07))!important;box-shadow:inset 0 0 0 1px rgba(142,249,255,.12)!important}}
/* Gold is authority only; cyan flow, violet cognition, green validated motion. */
.snty-authority,.snty-command-truth{{background:linear-gradient(110deg,rgba(255,215,0,.055),rgba(153,69,255,.025))!important}}
.snty-flow,.snty-cognition{{background:linear-gradient(110deg,rgba(142,249,255,.045),rgba(20,241,149,.02))!important}}
/* Remove ornamental chrome that competes with the truth hierarchy. */
.snty-section-line,.substrate-line,[class*="decorative-line"]{{opacity:.15!important}}
hr{{border:0!important;height:1px!important;background:linear-gradient(90deg,transparent,rgba(142,249,255,.13),transparent)!important}}
/* Mobile is a composed instrument, not a scaled desktop poster. */
@media(max-width:760px){{
 .block-container{{padding:12px 13px 72px!important}}
 .snty-v2-control{{position:relative!important;top:auto!important;border-radius:18px 5px 18px 5px!important;align-items:flex-start!important;gap:8px!important}}
 .snty-v2-control__truth{{font-size:.66rem!important;letter-spacing:.07em!important}}.snty-v2-control__mode{{font-size:.52rem!important}}
 .snty-section-head{{margin-top:34px!important}}.snty-section-title{{font-size:.83rem!important;letter-spacing:.10em!important}}
 .snty-crystal-panel,.snty-cyan-panel,.snty-gold-panel,.substrate-card,[data-testid="stExpander"]{{border-radius:20px 5px 20px 5px!important;padding-left:12px!important;padding-right:12px!important}}
 [data-testid="stDataFrame"] *{{font-size:.76rem!important}}
 .snty-hero-word{{font-size:clamp(2.05rem,12vw,3.2rem)!important;letter-spacing:.12em!important}}
 .cncl-card{{min-height:auto!important}}
}}

</style>
<div class="snty-v2-control">
  <div class="snty-v2-control__truth">SOVEREIGN GLASSBOX &nbsp;·&nbsp;
    <b>{_safe(truth['gate'])}</b> &nbsp;·&nbsp; {_safe(truth['mode'])}</div>
  <div class="snty-v2-control__mode">VERTICAL TRUTH FABRIC</div>
</div>
""", unsafe_allow_html=True)
