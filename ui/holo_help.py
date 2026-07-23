"""
ui/holo_help.py — HOLOGRAPHIC SECTION HELP (HOLO_HELP_20260611)

A pre-encoded help registry + a subtle holographic "?" glyph for every major
section. Pure HTML/CSS (<details>) — no JS, no reruns, zero regression risk.
The glyph idles at 40% opacity with the conic holo ring; tapping it unfolds a
gold-tinted glass tooltip with a concise operator-grade rundown.

Pre-encoded (not council-prompted) by design: we KNOW what each section is
for; descriptions must be instant, deterministic, and cost nothing.
"""
from __future__ import annotations
import html as _h

HELP: dict[str, tuple[str, str]] = {
 "pipeline_truth": ("Pipeline Truth / Price Handoff",
  "Live census of the signal pipeline: fresh snapshots, qualified vs priced, execution-ready and calibration coverage. The CALIBRATION STARVED warning here is the canary for entry blockage — no calibrated confidence means the floor vetoes everything."),
 "flow_engine": ("Sovereign Flow Engine",
  "The organism's spine in seven beats: raw signals → market tide → matrix scoring → latch → execution → memory. Each tile is a stage count; a zero where its neighbour is hot shows exactly where pressure dies."),
 "pressure_core": ("Organism Pressure Core",
  "Six doctrine meters. Conviction/confidence earn gold only at ≥75%; resonance is cyan (price truth); evolution purple; live risk escalates ember→blood. These are the organism's vital signs, not decoration."),
 "motor_feed": ("AXON Motor Feed — Buy/Sell",
  "Every motor action the executor actually took: entries, exits, PnL, lane (PAPER/LIVE), and the terminal reason for each close. This is ground truth — if it's not here, it didn't trade."),
 "vitalities": ("System Vitalities & Freshness",
  "Per-service health: enforcer, prelaunch guard, intelligence, oracle writes, governor, signal gate, lattice queue, WAL. Amber/red here explains stale data downstream before you look anywhere else."),
 "copytrade": ("Smart-Money Mycelium / Copytrade",
  "Wallet-intelligence absorption: scout → extract → fingerprint → score → absorb → influence. Currently OBSERVE-ONLY: conviction is hard-coded to never touch entries until the wiring is separately signed off."),
 "edge_arena": ("Edge Candidate Arena",
  "Momentum-ranked candidates still in flight: per-token momentum, curve, age. Where you watch a spore become (or fail to become) a latch."),
 "doctrine": ("Doctrine State — Parameters",
  "The live risk constitution: confidence floor, TP/SL, liquidity minimum, sizing, trail, drawdown. The floor here is what the supervisor enforces — if entries stall, check it against candidate confidence first."),
 "mycelial_signal_wilds": ("Mycelial Signal Wilds — Sovereign Intelligence Ecology",
  "The organism’s external sensory biome: verified smart-wallet constellations, trusted channel pulses and oracle nutrient health. It reveals whether outside intelligence is being absorbed into Council memory or whether the ecology is honestly dormant."),
 "glassbox": ("Sovereign Glassbox",
  "The consolidated ops cluster: pulse, final gate, twin rails, price truth, council, copytrade — every meter traces to a declared backend source; missing renders as 'not wired', never faked."),
 "pulse": ("Sovereign Pulse",
  "Heartbeat age per service plus guardian restarts and DB lock pressure. Green <40s, gold <90s, ember <300s, blood beyond. An active-but-stale executor pill is the classic starvation signature."),
 "final_gate": ("Final Gate Glassbox",
  "The forge: Phase A passes flow through momentum states (shadow / demoted / insufficient / live-only / terminal) and split into the twin rails. Zero momentum vetoes + zero opens means the blocker is elsewhere — usually confidence."),
 "price_truth": ("Price Truth",
  "Entry audit chain: qualify vs final price, source (router=executable truth, upgraded=fresher snapshot, qualify=original), price/signal ages. 'Unmeasurable' is shown rather than a fake 0.00%."),
 "arena": ("Dual-Lane Execution Arena",
  "Paper (green/gold proving rail) and Live (purple vault rail) are never merged. A blocked live lane dims with its real reason while paper keeps proving — that's doctrine, not a bug."),
 "council": ("Council",
  "Grounded narration: Polaris/Ivaris/Nugget/AXON summarize the same telemetry the panels show. Lore never invents numbers."),
 "substrate_node": ("Substrate Node — Quant Command Layer",
  "The autonomous build lab: council roster, open build queue, model assignments and the Substrate Trading Desk — a paper-proving alt/native desk where council-approved targets (≥75% conviction) auto-deploy paper capital."),
 "maintenance_trace": ("Live Maintenance Trace",
  "The hero claim, proven: timestamped real maintenance actions from heartbeat notes and logs. If nothing happened, it says 'at rest' instead of animating fakes."),
 "diagnostic": ("Diagnostic Console",
  "One truth for raw evidence: faults with suggested actions, errors, raw logs, gate/lane/copytrade audits, config — every block copyable, plus a one-click AI handoff pack."),
 "sanctum": ("The Sovereign Sanctum",
  "The live debate chamber: agents argue proposals with verdicts, confidence and next actions. Inconclusive rounds defer rather than force a bad ruling."),
 "constellation": ("Live Gate Constellation — Capital Deployment Sequence",
  "Two layers must align before real capital moves. TECHNICAL UNDERLAY (green circuit): the mechanical hard gates — MODE, EXECUTOR, PRICE, HOUR, WALLET, CAPACITY — every one must pass; red opens the circuit at the exact blocker. PATTERN OVERLAY (gold charge): the behavioural doctrine — 2 independent realised SIM successes arm a half-size canary, 3 confirm it, one realised loss resets — pattern never bypasses the circuit, it only sets SIZE (0×, 0.5×, 1× parity-capped). The interlock reads: FIRE = circuit closed AND pattern ≥ ARMED. CANDIDATE FLOW (cyan) is observation-only telemetry, not a gate. The ALIGNMENT CHARGE meter and NEXT-CANDIDATE banner are advisory renderings of these same truths. FINAL FIRE is read verbatim from the executor's decision contract — the UI never recomputes readiness. UNAVAILABLE means the executor hasn't spoken, which is honest, not broken."),
 "matrix_rain": ("Truth Fabric Rain",
  "The vertical rain behind the hub is the machine's own operating telemetry: sanitised recent cognition-log lines, live service heartbeat ages and gate counters, interleaved with the fixed contract vocabulary. Gold leading glyphs mark authority-grade streams. Secrets, keys, wallet addresses and URLs are stripped or masked before anything reaches the canvas — it is a glassbox, not a leak."),
}

_CSS = """
<style>
details.holoq{display:inline-block;position:relative;vertical-align:middle;margin-left:7px}
details.holoq summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;
 justify-content:center;width:15px;height:15px;border-radius:50%;font-size:.58rem;
 font-family:'Share Tech Mono',monospace;color:rgba(255,215,0,.55);
 border:1px solid rgba(255,215,0,.30);background:rgba(4,8,6,.5);opacity:.45;
 transition:all .3s;text-shadow:0 0 6px rgba(255,215,0,.4)}
details.holoq summary::-webkit-details-marker{display:none}
details.holoq summary:hover{opacity:1;box-shadow:0 0 10px rgba(255,215,0,.45),0 0 4px rgba(153,69,255,.5);
 color:#FFD700;border-color:#FFD700}
details.holoq[open] summary{opacity:1;color:#FFD700;border-color:#FFD700}
details.holoq .holoq-body{position:absolute;z-index:99;top:20px;left:-8px;width:min(320px,72vw);
 padding:10px 13px;border-radius:12px;font-family:Rajdhani,sans-serif;font-size:.78rem;
 line-height:1.5;color:#C9D4CC;text-transform:none;letter-spacing:normal;font-weight:400;
 background:linear-gradient(115deg,rgba(255,215,0,.05),rgba(153,69,255,.04) 60%,rgba(8,14,11,.96)),rgba(8,11,9,.96);
 backdrop-filter:blur(14px);border:1px solid rgba(255,215,0,.35);
 box-shadow:inset 0 1px 0 rgba(255,255,255,.1),0 8px 26px rgba(0,0,0,.7),0 0 18px rgba(153,69,255,.25)}
details.holoq .holoq-title{font-family:Orbitron,sans-serif;font-size:.6rem;font-weight:700;
 letter-spacing:.16em;color:#FFD700;margin-bottom:5px;text-transform:uppercase}
</style>"""

def glyph(key: str) -> str:
    """Inline holographic ? for embedding inside any header markdown.
    CSS_RERUN_FIX_20260612: CSS ships with every glyph (tiny, idempotent) —
    the once-flag broke styling after Streamlit reruns."""
    title, body = HELP.get(key, ("Section", "No rundown encoded yet for this section."))
    css = _CSS
    return (css + f'<details class="holoq"><summary>?</summary>'
            f'<div class="holoq-body"><div class="holoq-title">{_h.escape(title)}</div>'
            f'{_h.escape(body)}</div></details>')
