"""
ui/sovereign_chamber_v1.py
===========================
SOVEREIGN CHAMBER V1 — Forensic Isometric Council Workstation

The command sanctum for the Council.
Clinical, matte-black, high-agency.

Council nodes:
  👤 Architect  — HITL / Operator command seat
  🐻‍❄️ Polar     — Sovereign Mind (Logic Engine)
  🌍 Ivy        — World Intelligence (Pattern Recognition)
  ⚖️  Ivaris     — The Critic (Debate & Audit)
  ⬡  Axon       — Systems Logic (Network Spine)
  ☄️  Nugget     — The Spark (Launch Strategy)

V1 Constraints:
  - No schema mutations
  - All stats computed read-only
  - try/except on all DB calls
  - Forensic tone throughout
"""
import streamlit as st
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB   = ROOT / "sentinuity_matrix.db"

# ── COLOURS ───────────────────────────────────────────────────────────────────
C_GOLD   = "#FFD700"
C_GREEN  = "#14F195"
C_PURPLE = "#9945FF"
C_CYAN   = "#8EF9FF"
C_RED    = "#FF073A"
C_AMBER  = "#FFB347"
C_DIM    = "#333333"

STATUS_COLOURS = {
    "EXECUTING":    C_GREEN,
    "CRITIQUING":   C_RED,
    "RESEARCHING":  C_CYAN,
    "DEBATING":     C_PURPLE,
    "IDLE":         C_DIM,
    "ONLINE":       C_GREEN,
}

# ── COUNCIL NODES ─────────────────────────────────────────────────────────────
COUNCIL = [
    {
        "id":     "architect",
        "emoji":  "👤",
        "name":   "ARCHITECT",
        "role":   "HITL / Operator",
        "colour": C_CYAN,
        "desc":   "Central command. Primary input terminal. All sovereign decisions route here.",
        "hb_key": None,  # Human — no heartbeat
    },
    {
        "id":     "polar",
        "emoji":  "🐻‍❄️",
        "name":   "POLAR",
        "role":   "Sovereign Mind",
        "colour": C_CYAN,
        "desc":   "The fixed point. Logic engine. Watches every trade and proposes precise changes.",
        "hb_key": "polaris",
    },
    {
        "id":     "ivy",
        "emoji":  "🌍",
        "name":   "IVY",
        "role":   "World Intelligence",
        "colour": C_GREEN,
        "desc":   "Pattern recognition across global signals. Flags anomalies and emerging regimes.",
        "hb_key": "x_scout",
    },
    {
        "id":     "ivaris",
        "emoji":  "⚖️",
        "name":   "IVARIS",
        "role":   "The Critic",
        "colour": C_AMBER,
        "desc":   "Immune system. Finds every reason a proposal could fail before capital is risked.",
        "hb_key": "sovereign_governor",
    },
    {
        "id":     "axon",
        "emoji":  "⬡",
        "name":   "AXON",
        "role":   "Systems Logic",
        "colour": C_RED,
        "desc":   "Network spine. Validates all code changes via dry-run before staging.",
        "hb_key": "execution_engine",
    },
    {
        "id":     "nugget",
        "emoji":  "☄️",
        "name":   "NUGGET",
        "role":   "The Spark",
        "colour": C_GOLD,
        "desc":   "High-velocity audit. Independent consensus layer. Launch strategy intelligence.",
        "hb_key": "sovereign_governor",  # shares governor heartbeat
    },
]


# ── DB HELPERS ────────────────────────────────────────────────────────────────
def _db():
    try:
        conn = sqlite3.connect(str(DB), timeout=3)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _safe(sql, params=()):
    try:
        c = _db()
        if not c: return []
        rows = c.execute(sql, params).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _safe_one(sql, params=()):
    r = _safe(sql, params)
    return r[0] if r else {}


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
def _load_forge_credits() -> dict:
    """Compute forge credits from paper_executions + polaris_proposals (read-only)."""
    now = time.time()
    try:
        # Credits from successful trades (wins)
        trade_wins = _safe_one(
            "SELECT COUNT(*) n FROM paper_positions WHERE win_loss='WIN' AND status='CLOSED'"
        ).get("n", 0)
        # Credits from approved proposals
        approved = _safe_one(
            "SELECT COUNT(*) n FROM polaris_proposals WHERE status IN ('approved','applied','completed')"
        ).get("n", 0)
        # Credits from open proposals (partial)
        open_props = _safe_one(
            "SELECT COUNT(*) n FROM polaris_proposals WHERE status='open'"
        ).get("n", 0)
        total_closed = _safe_one(
            "SELECT COUNT(*) n FROM paper_positions WHERE status='CLOSED'"
        ).get("n", 0)
        total_credits = (trade_wins * 10) + (approved * 50) + (open_props * 2)
        return {
            "total":        total_credits,
            "trade_wins":   trade_wins,
            "approved":     approved,
            "open_props":   open_props,
            "total_closed": total_closed,
            "level":        min(10, total_credits // 100 + 1),
        }
    except Exception:
        return {"total": 0, "level": 1}


def _load_heartbeats() -> dict:
    """Load all service heartbeats."""
    rows = _safe("SELECT service_name, last_pulse, note FROM system_heartbeat")
    result = {}
    now = time.time()
    for r in rows:
        age = now - float(r.get("last_pulse") or 0)
        result[r["service_name"]] = {
            "age":  age,
            "note": r.get("note", ""),
            "alive": age < 60,
        }
    return result


def _get_node_status(node: dict, heartbeats: dict) -> tuple[str, str]:
    """Return (status_label, colour) for a council node."""
    if node["hb_key"] is None:
        return "ONLINE", C_CYAN

    hb = heartbeats.get(node["hb_key"], {})
    if not hb or not hb.get("alive"):
        return "IDLE", C_DIM

    note = str(hb.get("note", "")).upper()
    if any(x in note for x in ("DEBATE", "CRITIQ", "BLOCKED")):
        return "CRITIQUING", C_RED
    if any(x in note for x in ("RESEARCH", "SCOUT", "SEARCH")):
        return "RESEARCHING", C_CYAN
    if any(x in note for x in ("EXEC", "OPEN", "LATCHED")):
        return "EXECUTING", C_GREEN
    if any(x in note for x in ("DEBATE", "GOVERN")):
        return "DEBATING", C_PURPLE
    return "ONLINE", C_GREEN


def _load_mind_exhaust(limit: int = 25) -> list[dict]:
    """Load recent debate + cognition log entries."""
    rows = _safe(f"""
        SELECT 'DEBATE' as src, speaker as stage, verdict_text as message, logged_at as ts
        FROM debate_log
        WHERE logged_at IS NOT NULL
        UNION ALL
        SELECT 'COGNITION', stage, message, timestamp
        FROM cognition_log
        WHERE timestamp IS NOT NULL
        ORDER BY ts DESC
        LIMIT {limit}
    """)
    return rows


def _load_recent_trades(limit: int = 8) -> list[dict]:
    """Recent closed trades."""
    return _safe(f"""
        SELECT SUBSTR(COALESCE(token_name,mint_address),1,16) name,
               win_loss, realized_pnl_usd, final_exec_pct,
               exit_category, closed_at
        FROM paper_positions WHERE status='CLOSED'
        ORDER BY closed_at DESC LIMIT {limit}
    """)


# ── RENDER FUNCTIONS ──────────────────────────────────────────────────────────
def _node_bay(node: dict, status: str, status_col: str, expanded: bool = False) -> str:
    col = node["colour"]
    emoji = node["emoji"]
    name  = node["name"]
    role  = node["role"]

    border = f"2px solid {col}" if status not in ("IDLE",) else f"1px solid {C_DIM}"
    glow   = f"box-shadow:0 0 12px {col}55;" if status == "EXECUTING" else ""

    return f"""
<div style='border:{border};border-radius:10px;background:rgba(5,2,16,0.8);
    padding:12px 10px;text-align:center;{glow}cursor:pointer;min-height:120px;'>
  <div style='font-size:1.6rem;'>{emoji}</div>
  <div style='font-family:Orbitron,monospace;font-size:0.6rem;color:{col};
      letter-spacing:2px;margin:4px 0 2px;font-weight:700;'>{name}</div>
  <div style='font-size:0.55rem;color:#555;font-family:Share Tech Mono,monospace;
      margin-bottom:6px;'>{role}</div>
  <span style='font-size:0.5rem;padding:2px 6px;border-radius:3px;
      background:{status_col}22;color:{status_col};
      border:1px solid {status_col}44;letter-spacing:1px;'>{status}</span>
</div>"""


def render_sovereign_chamber(query_db=None) -> None:
    """Main render — call from sovereign_hub or standalone."""

    now = time.time()

    # Load all data
    credits  = _load_forge_credits()
    beats    = _load_heartbeats()
    exhaust  = _load_mind_exhaust(30)
    trades   = _load_recent_trades(8)

    # ── HEADER ────────────────────────────────────────────────────────────────
    st.markdown(f"""
<div style='padding:10px 0 6px;'>
  <div style='font-family:Orbitron,sans-serif;font-size:1rem;font-weight:900;
      color:{C_GOLD};letter-spacing:6px;text-shadow:0 0 16px {C_GOLD}66;'>
      SOVEREIGN CHAMBER</div>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.6rem;
      color:{C_GOLD}66;letter-spacing:3px;'>
      FORENSIC ISOMETRIC COUNCIL WORKSTATION · LEVEL {credits['level']}</div>
</div>
""", unsafe_allow_html=True)

    # ── FORGE CREDITS HUD ─────────────────────────────────────────────────────
    level   = credits["level"]
    total_c = credits["total"]
    next_lv = (level * 100)
    prog    = min(100, int((total_c % 100)))
    bar_col = C_GOLD if level >= 5 else C_GREEN

    st.markdown(f"""
<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px;
    padding:10px 14px;border:1px solid {C_GOLD}33;border-radius:10px;
    background:rgba(255,215,0,0.03);'>
  <div style='flex:1;min-width:120px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.55rem;
        color:{C_GOLD}88;letter-spacing:2px;margin-bottom:2px;'>FORGE CREDITS</div>
    <div style='font-family:Orbitron,monospace;font-size:1.2rem;
        font-weight:900;color:{C_GOLD};'>{total_c:,}</div>
    <div style='font-size:0.55rem;color:#555;'>
        {credits['trade_wins']} wins · {credits['approved']} approved proposals</div>
  </div>
  <div style='flex:1;min-width:120px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.55rem;
        color:{C_GOLD}88;letter-spacing:2px;margin-bottom:4px;'>
        LEVEL {level} → {level+1}</div>
    <div style='height:6px;background:#111;border-radius:3px;overflow:hidden;margin-bottom:2px;'>
      <div style='height:100%;width:{prog}%;background:{bar_col};border-radius:3px;'></div>
    </div>
    <div style='font-size:0.5rem;color:#555;'>{prog}/100 credits to next level</div>
  </div>
  <div style='flex:1;min-width:120px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.55rem;
        color:{C_RED}88;letter-spacing:2px;margin-bottom:2px;'>LEGAL FORTIFICATION</div>
    <div style='height:6px;background:#111;border-radius:3px;overflow:hidden;margin-bottom:2px;'>
      <div style='height:100%;width:{min(100,credits["approved"]*5)}%;
          background:{C_RED};border-radius:3px;'></div>
    </div>
    <div style='font-size:0.5rem;color:#555;'>
        {credits["approved"]} defensive logic units absorbed</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── COUNCIL BAYS ──────────────────────────────────────────────────────────
    st.markdown(f"""<div style='font-family:Share Tech Mono,monospace;font-size:0.6rem;
        color:{C_PURPLE};letter-spacing:3px;margin-bottom:8px;'>
        ◈ COUNCIL ROSTER — HARDWARE BAYS</div>""", unsafe_allow_html=True)

    cols = st.columns(3, gap="small")
    for i, node in enumerate(COUNCIL):
        status, scol = _get_node_status(node, beats)
        with cols[i % 3]:
            st.markdown(_node_bay(node, status, scol), unsafe_allow_html=True)
            # Expandable detail
            with st.expander("", expanded=False):
                st.markdown(f"""
<div style='font-size:0.65rem;color:#888;line-height:1.6;
    font-family:Rajdhani,sans-serif;'>{node['desc']}</div>
<div style='margin-top:8px;font-family:Share Tech Mono,monospace;
    font-size:0.55rem;color:{scol};'>STATUS: {status}</div>
""", unsafe_allow_html=True)
                if node["hb_key"] and node["hb_key"] in beats:
                    hb_note = beats[node["hb_key"]].get("note", "")[:80]
                    age_s   = beats[node["hb_key"]].get("age", 999)
                    st.markdown(
                        f"<div style='font-size:0.55rem;color:#555;"
                        f"font-family:Share Tech Mono,monospace;margin-top:4px;'>"
                        f"pulse: {age_s:.0f}s ago<br>{hb_note}</div>",
                        unsafe_allow_html=True
                    )

    st.markdown("<div style='margin:12px 0 4px;'></div>", unsafe_allow_html=True)

    # ── SPLIT: MIND EXHAUST + RECENT TRADES ───────────────────────────────────
    left, right = st.columns([3, 2], gap="medium")

    with left:
        st.markdown(f"""<div style='font-family:Share Tech Mono,monospace;
            font-size:0.6rem;color:{C_PURPLE};letter-spacing:3px;
            margin-bottom:8px;'>⬡ MIND EXHAUST — LIVE COUNCIL FEED</div>""",
            unsafe_allow_html=True)
        exhaust_html = (
            "<div style='height:320px;overflow-y:auto;padding:10px;font-family:"
            "Share Tech Mono,monospace;background:rgba(5,2,16,0.6);"
            "border:1px solid rgba(153,69,255,0.15);border-radius:10px;'>"
        )
        stage_cols = {
            "POLARIS": C_CYAN, "IVARIS": C_AMBER, "NUGGET": C_GOLD,
            "ORACLE": C_GREEN, "AXON": C_RED, "SYSTEM": C_PURPLE,
            "SUPERVISOR": C_CYAN, "EXECUTOR": C_GREEN, "DEBATE": C_PURPLE,
        }
        if exhaust:
            for e in exhaust[:25]:
                stage  = str(e.get("stage","SYS")).upper()[:12]
                msg    = str(e.get("message",""))[:80]
                scol   = stage_cols.get(stage, "#555")
                exhaust_html += (
                    f"<div style='margin-bottom:6px;font-size:0.6rem;'>"
                    f"<span style='color:{scol};'>[{stage}]</span>"
                    f"<span style='color:#888;'> — </span>"
                    f"<span style='color:#aaa;'>{msg}</span>"
                    f"</div>"
                )
        else:
            exhaust_html += (
                "<div style='color:#333;text-align:center;padding:40px;"
                "font-size:0.6rem;'>// SUBSTRATE SILENT — NO COGNITION YET //</div>"
            )
        exhaust_html += "</div>"
        st.markdown(exhaust_html, unsafe_allow_html=True)

    with right:
        st.markdown(f"""<div style='font-family:Share Tech Mono,monospace;
            font-size:0.6rem;color:{C_GREEN};letter-spacing:3px;
            margin-bottom:8px;'>⬡ RECENT EXECUTIONS</div>""",
            unsafe_allow_html=True)
        if trades:
            for t in trades:
                wl    = str(t.get("win_loss",""))
                pnl   = float(t.get("realized_pnl_usd") or 0)
                pct   = float(t.get("final_exec_pct") or 0)
                name  = str(t.get("name",""))[:16]
                exit_ = str(t.get("exit_category",""))[:12]
                col   = C_GREEN if wl == "WIN" else C_RED
                flag  = "✓" if wl == "WIN" else "✗"
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;"
                    f"padding:5px 8px;margin-bottom:3px;border-radius:5px;"
                    f"background:rgba(255,255,255,0.02);"
                    f"border-left:2px solid {col};font-size:0.6rem;'>"
                    f"<span style='color:{col};'>{flag}</span>"
                    f"<span style='color:#ccc;font-family:Share Tech Mono,monospace;'>"
                    f"{name}</span>"
                    f"<span style='color:{col};'>{pct:+.0f}%</span>"
                    f"<span style='color:#555;'>{exit_}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                "<div style='color:#333;font-size:0.6rem;"
                "font-family:Share Tech Mono,monospace;'>// no executions yet //</div>",
                unsafe_allow_html=True,
            )

        # Organism conviction
        total_closed = credits.get("total_closed", 0)
        wins         = credits.get("trade_wins", 0)
        wr           = (wins / total_closed * 100) if total_closed > 0 else 0
        st.markdown(f"""
<div style='margin-top:12px;padding:8px 12px;border:1px solid {C_GOLD}33;
    border-radius:8px;background:rgba(255,215,0,0.03);'>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.55rem;
      color:{C_GOLD};letter-spacing:2px;margin-bottom:4px;'>ORGANISM METRICS</div>
  <div style='font-size:0.6rem;color:#888;font-family:Share Tech Mono,monospace;'>
    {total_closed} closed · {wr:.1f}% WR · {wins} wins
  </div>
</div>
""", unsafe_allow_html=True)
