"""
ui/cinematic_overlay.py
========================
EXPRESSION LAYER — makes the organism understandable by watching.

Two non-blocking @st.fragment components:

1. render_cinematic_overlay()
   Shows one high-signal event at a time. Auto-fades 4s.
   Never blocks UI. Never full-screen.
   Sources: cognition_log, polaris_proposals, code_patches

2. render_lifecycle_visual()
   Shows open position lifecycle states with pnl + trailing stop.
   Sources: paper_positions, cognition_log

Performance rules (LOCKED):
  - @st.fragment only
  - DB timeout <= 2s
  - busy_timeout <= 2000
  - READ-ONLY — zero writes
  - No polling loops
  - No full-screen blackout
  - Fail silently — never crash parent page
"""
from __future__ import annotations
import time
import sqlite3
import streamlit as st
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

# ── Colours ───────────────────────────────────────────────────────────────────
_C = {
    "entry":     "#14F195",   # green    — ENTRY OPENED
    "mode_b":    "#00E5FF",   # cyan     — MODE B FIRE
    "bomb":      "#FF6B35",   # orange   — BOMB SIGNATURE
    "forge":     "#FFD700",   # gold     — MASTERPIECE FORGED
    "harmonic":  "#9945FF",   # purple   — HARMONIC CONVERGENCE
    "patch":     "#4FC3F7",   # blue     — PATCH APPLIED
    "heal":      "#76B900",   # nvidia   — AUTO-HEAL FIRED
    "operator":  "#FF4444",   # red      — OPERATOR REQUIRED
    "scale_in":  "#14F195",
    "harvest":   "#FFD700",
    "scale_out": "#FF4444",
    "running":   "#14F195",
    "defending": "#FF9900",
}

# ── Event type map: log stage → (label, colour_key, min_priority) ─────────────
_EVENT_MAP = {
    "EXECUTION_OPEN":       ("ENTRY OPENED",         "entry",    1),
    "MODE_B_PASS":          ("MODE B FIRE",           "mode_b",   1),
    "BOMB_SIGNATURE":       ("BOMB SIGNATURE",        "bomb",     1),
    "FORGE_COMPLETE":       ("MASTERPIECE FORGED",    "forge",    1),
    "HARMONIC_CONVERGENCE": ("HARMONIC CONVERGENCE",  "harmonic", 1),
    "PATCH_APPLIED":        ("PATCH APPLIED",         "patch",    2),
    "AUTO_HEAL":            ("AUTO-HEAL FIRED",       "heal",     2),
    "GUARDIAN_HEAL":        ("AUTO-HEAL FIRED",       "heal",     2),
    "HITL_REQUIRED":        ("OPERATOR REQUIRED",     "operator", 1),
    "FORGE_WRITER":         ("MASTERPIECE FORGED",    "forge",    2),
    "CODE_PATCH_CREATED":   ("PATCH APPLIED",         "patch",    2),
}

# Lifecycle state map
_LC_MAP = {
    "SCALE_IN":      ("SCALE IN",   "scale_in",  "▲"),
    "PARTIAL_PROFIT":("HARVEST",    "harvest",   "◆"),
    "EXIT":          ("SCALE OUT",  "scale_out", "▼"),
    "HOLD":          ("RUNNING",    "running",   "●"),
    "DEFENDING":     ("DEFENDING",  "defending", "◉"),
}


# ── DB helper — fail fast, read-only ─────────────────────────────────────────
def _db_read(sql: str, params: tuple = (), n: int = 1):
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchmany(n)
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── 1. CINEMATIC OVERLAY ─────────────────────────────────────────────────────
@st.fragment(run_every=4)
def render_cinematic_overlay() -> None:
    """
    Shows ONE high-signal event at a time. Auto-cycles every 4s.
    Non-blocking. Read-only. Never full-screen.
    Falls silent when nothing notable is happening.
    """
    try:
        now = time.time()
        cutoff = now - 30   # only events from last 30s

        # Fetch most recent notable cognition event
        rows = _db_read("""
            SELECT stage, message, timestamp
            FROM cognition_log
            WHERE timestamp > ?
            ORDER BY timestamp DESC LIMIT 20
        """, (cutoff,), n=20)

        # Find highest priority event
        event = None
        for row in rows:
            stage = (row.get("stage") or "").upper()
            msg   = row.get("message") or ""

            # Check direct stage match
            if stage in _EVENT_MAP:
                event = (*_EVENT_MAP[stage], row)
                break

            # Check message content for key patterns
            for key, mapping in _EVENT_MAP.items():
                if key in msg.upper() or key in stage:
                    event = (*mapping, row)
                    break
            if event:
                break

        # Also check for OPERATOR REQUIRED in proposals
        if not event:
            op_rows = _db_read("""
                SELECT proposal_type, created_at
                FROM polaris_proposals
                WHERE status IN ('forge_complete', 'HITL_REQUIRED', 'hitl_pending')
                  AND created_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (now - 300,), n=1)
            if op_rows:
                event = ("OPERATOR REQUIRED", "operator", 1,
                         {"stage": "OPERATOR", "message": "Seal required",
                          "timestamp": op_rows[0]["created_at"]})

        if not event:
            return  # Nothing notable — stays invisible

        label, color_key, priority, row = event
        color = _C.get(color_key, "#888")
        age   = int(now - float(row.get("timestamp") or now))
        msg   = (row.get("message") or "")[:80]

        # Fade factor — newer = brighter
        alpha = max(0.3, 1.0 - (age / 30.0))
        border_alpha = int(alpha * 255)

        st.markdown(
            f"""
            <div style="
                position:relative;
                margin:4px 0;
                padding:10px 16px;
                border-left:3px solid {color};
                border-radius:0 8px 8px 0;
                background:rgba(5,2,16,{0.7 * alpha:.2f});
                opacity:{alpha:.2f};
                font-family:Share Tech Mono,monospace;
                transition:opacity 0.5s;
            ">
              <div style="display:flex;align-items:center;gap:10px;">
                <span style="
                    font-size:10px;
                    font-weight:700;
                    color:{color};
                    letter-spacing:3px;
                    text-shadow:0 0 8px {color}88;
                ">{label}</span>
                <span style="font-size:9px;color:#444;font-family:monospace;">
                  {age}s ago
                </span>
              </div>
              <div style="
                  font-size:9px;
                  color:#666;
                  font-family:monospace;
                  margin-top:3px;
                  white-space:nowrap;
                  overflow:hidden;
                  text-overflow:ellipsis;
              ">{msg}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    except Exception:
        pass  # Never crash the parent page


# ── 2. LIFECYCLE VISUAL LAYER ────────────────────────────────────────────────
@st.fragment(run_every=20)
def render_lifecycle_visual() -> None:
    """
    Shows open position lifecycle states.
    Read-only. Non-blocking. Fail-silent.

    States:
      SCALE IN   → green pulse
      HARVEST    → gold pulse
      SCALE OUT  → red fade
      RUNNING    → stable green
      DEFENDING  → amber
    """
    try:
        now = time.time()

        # Fetch open positions
        positions = _db_read("""
            SELECT id, token_name, entry_price, last_price,
                   position_size_usd, unrealized_pnl_usd,
                   highest_price_seen, opened_at, confidence,
                   peak_confidence, trail_stop_price
            FROM paper_positions
            WHERE status = 'OPEN'
            ORDER BY opened_at DESC LIMIT 5
        """, n=5)

        if not positions:
            return  # Nothing open — invisible

        # Fetch recent PLI actions from cognition log
        lc_actions: dict[str, str] = {}
        lc_rows = _db_read("""
            SELECT message, timestamp FROM cognition_log
            WHERE stage = 'PLI' AND timestamp > ?
            ORDER BY timestamp DESC LIMIT 20
        """, (now - 120,), n=20)
        for lr in lc_rows:
            msg = lr.get("message") or ""
            for action in ("SCALE_IN", "PARTIAL_PROFIT", "EXIT"):
                if action in msg.upper():
                    # Extract position token from message if possible
                    parts = msg.split()
                    token = parts[-1] if parts else "?"
                    lc_actions[token] = action
                    break

        st.markdown(
            f'<div style="font-size:9px;color:#333;font-family:monospace;'
            f'letter-spacing:2px;margin:6px 0 4px;">◈ POSITION LIFECYCLE</div>',
            unsafe_allow_html=True,
        )

        for pos in positions:
            token    = (pos.get("token_name") or "?")[:12]
            entry    = float(pos.get("entry_price") or 0)
            last     = float(pos.get("last_price") or entry)
            size     = float(pos.get("position_size_usd") or 0)
            pnl_usd  = float(pos.get("unrealized_pnl_usd") or 0)
            trail    = pos.get("trail_stop_price")
            hold_s   = int(now - float(pos.get("opened_at") or now))
            pnl_pct  = ((last - entry) / entry * 100) if entry > 0 else 0
            conf     = float(pos.get("confidence") or 0)
            peak_c   = float(pos.get("peak_confidence") or conf)

            # Determine lifecycle state
            lc_action = lc_actions.get(token, "HOLD")
            if lc_action not in _LC_MAP:
                # Infer from data if not in log
                if pnl_pct > 15:
                    lc_action = "PARTIAL_PROFIT"
                elif peak_c > 0 and conf > 0 and (peak_c - conf) > 0.25:
                    lc_action = "EXIT"
                elif pnl_pct < -3:
                    lc_action = "DEFENDING"
                else:
                    lc_action = "HOLD"

            lc_label, lc_col_key, lc_icon = _LC_MAP.get(
                lc_action, ("RUNNING", "running", "●")
            )
            color = _C.get(lc_col_key, "#888")

            pnl_col  = "#14F195" if pnl_usd >= 0 else "#FF4444"
            pnl_sign = "+" if pnl_usd >= 0 else ""

            trail_str = ""
            if trail:
                t = float(trail)
                if t > 0 and last > 0:
                    trail_pct = ((last - t) / last * 100)
                    trail_str = f"trail {trail_pct:.1f}%"

            st.markdown(
                f"""
                <div style="
                    display:flex;
                    align-items:center;
                    gap:10px;
                    padding:6px 10px;
                    margin:2px 0;
                    border-radius:6px;
                    border-left:2px solid {color};
                    background:rgba(5,2,16,0.6);
                    font-family:Share Tech Mono,monospace;
                ">
                  <span style="font-size:11px;color:{color};min-width:14px;">
                    {lc_icon}
                  </span>
                  <span style="font-size:10px;color:#ccc;min-width:90px;
                               font-weight:600;letter-spacing:1px;">
                    {token}
                  </span>
                  <span style="font-size:9px;color:{color};min-width:80px;
                               letter-spacing:1px;">
                    {lc_label}
                  </span>
                  <span style="font-size:10px;color:{pnl_col};min-width:60px;">
                    {pnl_sign}{pnl_usd:.2f}
                  </span>
                  <span style="font-size:9px;color:{pnl_col};">
                    {pnl_pct:+.1f}%
                  </span>
                  <span style="font-size:8px;color:#333;margin-left:auto;">
                    {trail_str}
                  </span>
                  <span style="font-size:8px;color:#2a2a2a;">
                    {hold_s}s
                  </span>
                </div>
                """,
                unsafe_allow_html=True,
            )

    except Exception:
        pass  # Never crash the parent page
