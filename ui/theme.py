"""
ui/theme.py — Sentinuity Sovereign Glassbox theme (SIGNOFF_FINAL_GATE_20260611)

World tokens + CSS injection + small HTML builders shared by all glassbox
panels. Pure presentation: this module performs ZERO DB/log reads and never
mutates anything. All data arrives from ui/data_sources.py.

Doctrine:
  - Gold means confidence/health >= 0.75. Never decorative.
  - Paper lane is green/gold. Live lane is purple/gold. They never merge.
  - Veto/stall is ember. Oracle/price truth is cyan.
  - Missing data renders as a 'not wired' chip — never a fake 0.00%.

TIER 1 (SIGNOFF_TIER1_DOCTRINE_20260716) — this module is CANONICAL:
  services/theme.py is now an explicit compatibility shim re-exporting this
  module. Two independently evolving doctrine files caused divergence (they
  had already drifted: radius 14px vs 8px, chip 0.72rem vs 0.68rem). One
  doctrine, one file, one import path: ui.theme.

  TYPOGRAPHY TOKENS — operational floor is SENT_MICRO (0.66rem). No readable
  operational state, number, reason, timestamp, trade field or agent field may
  render below it. Chart-internal SVG text scales with its viewBox and is
  governed by physical-size math in the chart module, not by these tokens.

  GLOW BUDGET — neutral crystalline glass is the default and carries no
  persistent bloom. Gold blooms only for granted capital authority. Red is
  split into three distinguishable treatments (break / exposure / service
  failure). DEGRADED is amber. Heartbeat animation is driven by real freshness
  state passed by the caller — a stale service must never pulse; use
  heartbeat_class(age_sec) so the class is recomputed on every render.
"""
from __future__ import annotations
import html as _html

# ── WORLD TOKENS ──────────────────────────────────────────────────────────────
FOREST_DEEP = "#0B1410"   # damp fungal forest floor (left world)
FOREST_MOSS = "#16241B"
VAULT_STEEL = "#11141C"   # machine vault (right world)
VAULT_EDGE  = "#1B2230"
SOL_PURPLE  = "#9945FF"   # live capital rail
SOL_GREEN   = "#14F195"   # paper proving rail
GOLD        = "#FFD700"   # health/confidence >= 0.75 ONLY
EMBER       = "#FF6B35"   # veto / stall / jam
BLOOD       = "#E2384D"   # dead / hard failure
CYAN        = "#38E1FF"   # oracle / price truth
MIST        = "#9DB5A8"   # body text on forest
STEEL_TXT   = "#AAB4C8"   # body text on vault

GOLD_THRESHOLD = 0.75

# ── TIER 1 TYPOGRAPHY TOKENS (operational floor = SENT_MICRO) ────────────────
SENT_HERO  = "1.35rem"
SENT_VALUE = "1.05rem"
SENT_BODY  = "0.82rem"
SENT_LABEL = "0.72rem"
SENT_MICRO = "0.66rem"   # HARD OPERATIONAL FLOOR — nothing readable below this

# Heartbeat freshness thresholds (seconds). Callers may override per service.
HEARTBEAT_FRESH_SEC = 90.0
HEARTBEAT_AGING_SEC = 300.0


def heartbeat_class(age_sec, fresh_sec: float = HEARTBEAT_FRESH_SEC,
                    aging_sec: float = HEARTBEAT_AGING_SEC) -> str:
    """
    Map a REAL heartbeat age to a semantic freshness class. Only FRESH pulses.
    Because callers rebuild the class on every render, a service that stops
    heartbeating automatically stops pulsing on the next render — the
    animation can never outlive the truth it represents.
    """
    try:
        a = float(age_sec)
    except (TypeError, ValueError):
        return "sent-heartbeat-stale"
    if a <= fresh_sec:
        return "sent-heartbeat-fresh"
    if a <= aging_sec:
        return "sent-heartbeat-aging"
    return "sent-heartbeat-stale"



# Service-specific heartbeat cadence doctrine. Long-cycle research services
# must not be shown as dead merely because the global 90s animation threshold
# is appropriate for execution/oracle services. Values are (fresh, aging).
SERVICE_HEARTBEAT_THRESHOLDS = {
    "execution_engine": (45.0, 120.0),
    "ws_price_oracle": (30.0, 90.0),
    "system_guardian": (90.0, 240.0),
    "live_wallet_sync": (90.0, 240.0),
    "council_build_orchestrator": (300.0, 900.0),
    "council_execution_spine": (300.0, 900.0),
    "polaris": (180.0, 600.0),
    "github_scout": (900.0, 3600.0),
    "gmgn_wallet_roster_refresh": (1800.0, 7200.0),
}

def service_heartbeat_thresholds(service_name: str) -> tuple[float, float]:
    return SERVICE_HEARTBEAT_THRESHOLDS.get(
        str(service_name or "").strip().lower(),
        (HEARTBEAT_FRESH_SEC, HEARTBEAT_AGING_SEC),
    )

def service_heartbeat_class(service_name: str, age_sec) -> str:
    fresh, aging = service_heartbeat_thresholds(service_name)
    return heartbeat_class(age_sec, fresh, aging)

def semantic_css() -> str:
    """
    The single definition of Tier 1 semantic classes. Injected by the active
    hub. Neutral glass is the default; everything below is a strict semantic
    exception, never decoration.
    """
    return """
<style>
:root{
  --sent-hero:1.35rem; --sent-value:1.05rem; --sent-body:0.82rem;
  --sent-label:0.72rem; --sent-micro:0.66rem;
}
/* Neutral crystalline glass — the DEFAULT state. No persistent bloom. */
.sent-glass{background:linear-gradient(150deg,rgba(255,255,255,.035),rgba(9,6,24,.86) 40%,rgba(5,2,16,.93));
  border:1px solid rgba(142,249,255,.13);border-radius:12px;box-shadow:none;
  backdrop-filter:blur(12px) saturate(1.2);-webkit-backdrop-filter:blur(12px) saturate(1.2);}
/* Gold authority bloom — ONLY when canonical capital authority is granted. */
.sent-authority-sealed{border-color:rgba(255,215,0,.55)!important;
  box-shadow:0 0 14px rgba(255,215,0,.22),inset 0 0 10px rgba(255,215,0,.06)!important;}
/* Red family — three DISTINGUISHABLE treatments, never interchangeable. */
.sent-critical-break{border:1px dashed rgba(255,7,58,.75)!important;
  box-shadow:inset 0 0 12px rgba(255,7,58,.12)!important;}          /* mandatory veto / broken contract */
.sent-danger-exposure{border-left:3px solid #FF073A!important;
  background:linear-gradient(90deg,rgba(255,7,58,.10),transparent 55%)!important;} /* dangerous open live exposure */
.sent-service-failure{border:1px solid rgba(255,7,58,.45)!important;
  border-top:3px solid #FF073A!important;box-shadow:none!important;} /* operational service outage */
/* DEGRADED is amber, not red. */
.sent-degraded{border:1px solid rgba(255,179,71,.5)!important;
  box-shadow:inset 0 0 10px rgba(255,179,71,.08)!important;}
/* Heartbeat freshness — ONLY fresh may pulse; stale freezes. */
.sent-heartbeat-fresh{animation:sentPulse 1.8s ease-in-out infinite;}
.sent-heartbeat-aging{animation:none!important;opacity:.75;filter:saturate(.6);}
.sent-heartbeat-stale{animation:none!important;opacity:.5;background:#FFB347!important;
  box-shadow:none!important;}
.sent-heartbeat-stale.sent-failed{background:#FF073A!important;}
@keyframes sentPulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.4);opacity:.65}}
@media (prefers-reduced-motion: reduce){
  .sent-heartbeat-fresh{animation:none!important;outline:2px solid rgba(20,241,149,.55);outline-offset:1px;}
}
</style>"""


def health_color(ratio: float | None) -> str:
    """Map a 0..1 health/confidence ratio to doctrine colors."""
    if ratio is None:
        return MIST
    if ratio >= GOLD_THRESHOLD:
        return GOLD
    if ratio >= 0.45:
        return SOL_GREEN
    if ratio >= 0.2:
        return EMBER
    return BLOOD


def inject(st) -> None:
    """Inject the glassbox stylesheet once per session."""
    if st.session_state.get("_glassbox_css"):
        return
    st.session_state["_glassbox_css"] = True
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=JetBrains+Mono:wght@400;600&display=swap');

.gbx-world {{
  border-radius: 8px;
  padding: 16px 18px;
  margin-bottom: 14px;
  background: linear-gradient(100deg,
      {FOREST_DEEP} 0%, {FOREST_MOSS} 38%,
      #0E1A18 50%,
      {VAULT_STEEL} 62%, {VAULT_EDGE} 100%);
  border: 1px solid rgba(142,249,255,0.12);
  box-shadow: none;
  backdrop-filter: blur(16px);
}}
.gbx-title {{
  font-family: 'Cinzel', serif;
  letter-spacing: 0.14em;
  font-size: 0.95rem;
  color: {MIST};
  text-transform: uppercase;
  margin-bottom: 6px;
}}
.gbx-mono, .gbx-chip, .gbx-lane {{ font-family: 'JetBrains Mono', monospace; }}
.gbx-chip {{
  display: inline-block;
  padding: 2px 10px;
  margin: 2px 4px 2px 0;
  border-radius: 999px;
  font-size: 0.68rem;
  border: 1px solid color-mix(in srgb, currentColor 45%, transparent);
  background: rgba(5,3,13,0.46);
}}
.gbx-src {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.66rem;  /* TIER1: floored from 0.62rem — data-source provenance is operational text */
  color: #5C6F66;
  margin-top: 4px;
}}
.gbx-lane {{
  border-radius: 6px;
  padding: 12px 14px;
  margin: 6px 0;
  border: 1px solid;
  background: rgba(5,3,13,0.34);
  box-shadow: none;
  font-size: 0.8rem;
}}
.gbx-lane.paper {{ border-color: {SOL_GREEN}; color: {SOL_GREEN}; }}
.gbx-lane.live  {{ border-color: {SOL_PURPLE}; color: {SOL_PURPLE}; }}
.gbx-lane.closed {{ opacity: 0.55; border-style: dashed; }}
.gbx-big {{ font-size: 1.4rem; font-weight: 600; }}
.gbx-dim {{ color: #6E7F76; font-size: 0.72rem; }}
@media (prefers-reduced-motion: no-preference) {{
  .gbx-pulse {{ animation: gbxpulse 2.4s ease-in-out infinite; }}
  @keyframes gbxpulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.55; }} }}
}}
</style>
""", unsafe_allow_html=True)


def chip(label: str, color: str, pulse: bool = False, title: str = "") -> str:
    cls = "gbx-chip gbx-pulse" if pulse else "gbx-chip"
    t = f' title="{_html.escape(title)}"' if title else ""
    return f'<span class="{cls}" style="color:{color}"{t}>{_html.escape(str(label))}</span>'


def src(text: str) -> str:
    """Traceability footer: which backend field/marker powers this tile."""
    return f'<div class="gbx-src">src: {_html.escape(text)}</div>'


def not_wired(what: str) -> str:
    return chip(f"{what}: not wired", MIST, title="No backend source found — nothing is faked.")


def lane_card(kind: str, open_now: bool, lines: list[str], reason: str = "") -> str:
    """kind: 'paper'|'live'. Closed lanes dim but NEVER disappear."""
    cls = f"gbx-lane {kind}" + ("" if open_now else " closed")
    name = "PAPER · proving rail" if kind == "paper" else "LIVE · vault rail"
    body = "<br>".join(_html.escape(s) for s in lines)
    rs = (f'<div class="gbx-dim">blocked: {_html.escape(reason)}</div>'
          if (reason and not open_now) else "")
    return f'<div class="{cls}"><b>{name}</b><br>{body}{rs}</div>'


def gate_rail_svg(gates: list[dict], paper_open: bool, live_open: bool,
                  paper_reason: str = "", live_reason: str = "") -> str:
    """
    Signature element: the Final Gate rail. Each gate dict:
      {name, state in {'pass','shadow','demoted','veto','idle'}, count}
    Renders Phase A → momentum forge → lane split into twin rails.
    All states come from real log counters — this function only draws.
    """
    state_color = {"pass": SOL_GREEN, "shadow": MIST, "demoted": CYAN,
                   "veto": EMBER, "idle": "#3A4A40"}
    w, gate_w = 760, 132
    parts = [f'<svg viewBox="0 0 {w} 190" width="100%" role="img" '
             f'aria-label="final gate rail" xmlns="http://www.w3.org/2000/svg">',
             f'<rect x="0" y="0" width="{w}" height="190" rx="12" fill="#0D1512"/>',
             # mycelium inflow (left) and vault wall (right)
             f'<path d="M0 95 C 40 60, 40 130, 80 95" stroke="{SOL_GREEN}" '
             f'stroke-width="2" fill="none" opacity="0.5"/>',
             f'<rect x="{w-18}" y="10" width="8" height="170" fill="{VAULT_EDGE}"/>']
    x = 90
    for g in gates:
        c = state_color.get(g.get("state", "idle"), MIST)
        parts.append(
            f'<g font-family="JetBrains Mono, monospace">'
            f'<rect x="{x}" y="55" width="{gate_w}" height="80" rx="8" '
            f'fill="#0A0F0C" stroke="{c}" stroke-width="2"/>'
            f'<text x="{x + gate_w/2}" y="80" text-anchor="middle" '
            f'fill="{c}" font-size="11">{_html.escape(g["name"])}</text>'
            f'<text x="{x + gate_w/2}" y="112" text-anchor="middle" '
            f'fill="{c}" font-size="20" font-weight="600">{g.get("count", 0)}</text>'
            f'<text x="{x + gate_w/2}" y="128" text-anchor="middle" '
            f'fill="{MIST}" font-size="10">{_html.escape(g.get("state",""))}</text></g>')
        x += gate_w + 16
        if g is not gates[-1]:
            parts.append(f'<line x1="{x-16}" y1="95" x2="{x}" y2="95" '
                         f'stroke="{MIST}" stroke-width="2" opacity="0.6"/>')
    # lane split
    pc = SOL_GREEN if paper_open else "#2C4A3C"
    lc = SOL_PURPLE if live_open else "#3A2C52"
    parts.append(f'<path d="M{x} 95 C {x+24} 95, {x+24} 60, {x+52} 60" '
                 f'stroke="{pc}" stroke-width="4" fill="none"/>'
                 f'<text x="{x+58}" y="64" fill="{pc}" font-size="11" '
                 f'font-family="JetBrains Mono, monospace">PAPER'
                 f'{"" if paper_open else " ✕"}</text>')
    parts.append(f'<path d="M{x} 95 C {x+24} 95, {x+24} 130, {x+52} 130" '
                 f'stroke="{lc}" stroke-width="4" fill="none"/>'
                 f'<text x="{x+58}" y="134" fill="{lc}" font-size="11" '
                 f'font-family="JetBrains Mono, monospace">LIVE'
                 f'{"" if live_open else " ✕"}</text>')
    if paper_reason and not paper_open:
        parts.append(f'<text x="{x+58}" y="46" fill="{EMBER}" font-size="10" '
                     f'font-family="JetBrains Mono, monospace">'
                     f'{_html.escape(paper_reason[:42])}</text>')
    if live_reason and not live_open:
        parts.append(f'<text x="{x+58}" y="148" fill="{EMBER}" font-size="10" '
                     f'font-family="JetBrains Mono, monospace">'
                     f'{_html.escape(live_reason[:42])}</text>')
    parts.append("</svg>")
    return "".join(parts)
