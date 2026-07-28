"""
ui/intelligence_command_layer.py

Streamlit render module for the Intelligence Tab dual-engine view.

Wire into sovereign_hub.py / ui.intelligence_tab.py:

    from ui.intelligence_command_layer import render_intelligence_command_layer
    render_intelligence_command_layer()
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

try:
    from services.intelligence_command_layer import (
        connect, build_status_snapshot, health_guardrail_summary,
        list_claim_cards, list_open_tasks, list_topics,
    )
except Exception:
    connect = None

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "sentinuity_matrix.db"

def _df(rows):
    if not rows:
        st.caption("No rows yet.")
        return
    try:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    except TypeError:
        st.dataframe(pd.DataFrame(rows))

def _cards(cards):
    if not cards:
        st.caption("No claim cards seeded yet.")
        return
    for c in cards:
        locked = c.get("public_visibility") == "LOCKED" or c.get("action_class") == "Locked_Review"
        icon = "🔒" if locked else "📄"
        title = c.get("topic_title") or c.get("topic_id") or "Research card"
        with st.expander(f"{icon} {title} — {c.get('risk_level','?')} — Grade {c.get('evidence_grade','?')}", expanded=False):
            st.write(c.get("claim_summary", ""))
            cols = st.columns(4)
            cols[0].metric("Evidence", c.get("evidence_grade", "?"))
            cols[1].metric("Risk", c.get("risk_level", "?"))
            cols[2].metric("Action", c.get("action_class", "?"))
            cols[3].metric("Visibility", c.get("public_visibility", "?"))
            if c.get("legal_status"):
                st.caption("Legal/status: " + str(c.get("legal_status")))
            if c.get("narrative_duality_note"):
                st.info(c.get("narrative_duality_note"))
            nodes = c.get("source_nodes") or []
            if nodes:
                st.caption("Source nodes: " + ", ".join(map(str, nodes)))

def render_intelligence_command_layer():
    st.markdown("## 🧠 Intelligence Command Layer")
    st.caption("Dual-engine command layer: Profit funds freedom. Health maps research safely.")

    if connect is None:
        st.error("Backend missing: services.intelligence_command_layer")
        st.code("Copy services/intelligence_command_layer.py and run: python intelligence_command_layer_seed.py")
        return
    if not DB_PATH.exists():
        st.warning("sentinuity_matrix.db not found yet.")
        st.code("python intelligence_command_layer_seed.py")
        return

    conn = connect(DB_PATH)
    try:
        snap = build_status_snapshot(DB_PATH)
        weights = snap.get("weights", {"profit": 70, "health": 30})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Profit Engine", f"{weights.get('profit', 70)}%")
        c2.metric("Health Engine", f"{weights.get('health', 30)}%")
        c3.metric("Health Mode", "READ ONLY" if str(snap.get("health_read_only", "1")) == "1" else "WRITE?")
        c4.metric("Locked Review", snap.get("locked_cards", 0))

        tab_profit, tab_health, tab_queue, tab_locked = st.tabs([
            "💰 Profitability Engine", "🌿 Health Intelligence Engine", "🧭 Council Queue", "🔒 Locked Review"
        ])

        with tab_profit:
            st.subheader("💰 Profitability Engine — Primary Mission")
            st.caption("Runner radar, smart wallet convergence, velocity, strategy expectancy, missed-gem autopsy.")
            st.markdown("### Topics")
            _df(list_topics(conn, "PROFIT"))
            st.markdown("### Active build tasks")
            _df(list_open_tasks(conn, "PROFIT"))
            st.markdown("### Cards")
            _cards(list_claim_cards(conn, "PROFIT", include_locked=False))

        with tab_health:
            st.subheader("🌿 Health Intelligence Engine — Read-only Research Map")
            for item in health_guardrail_summary():
                st.success("✅ " + item)
            st.markdown("### Topics")
            _df(list_topics(conn, "HEALTH"))
            st.markdown("### Active build tasks")
            _df(list_open_tasks(conn, "HEALTH"))
            st.markdown("### Visible cards")
            _cards(list_claim_cards(conn, "HEALTH", include_locked=False))

        with tab_queue:
            st.subheader("🧭 Intelligence Tab Council Queue")
            _df(list_open_tasks(conn, None))

        with tab_locked:
            st.subheader("🔒 Locked Review / Golden Lattice")
            try:
                rows = conn.execute("""SELECT id, created_at, item_type, item_id, reason, risk_level, status, decision
                                       FROM intelligence_locked_review ORDER BY created_at DESC LIMIT 50""").fetchall()
                _df([dict(r) for r in rows])
            except Exception as e:
                st.warning(f"Locked review unavailable: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    render_intelligence_command_layer()
