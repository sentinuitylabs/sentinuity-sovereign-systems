"""
ui/intelligence_tab.py  (v8.7 — SOVEREIGN FORGE / CONVERGENCE INTELLIGENCE LAYER)
====================================================================================
THE INTELLIGENCE SUBSTRATE — Sovereign Laboratory of the AI Council.

BUILD SEPARATION (PERMANENT DOCTRINE):
  NOT the primary copy-trading execution bot.
  The Council's war room for researching and forging new profit species.
  Copy-trade/wallet data = Smart Money Observatory (sensory substrate only; canonical intelligence ecology).

CONVERGENCE DOCTRINE:
  Edge = independent corroboration across multiple attention systems.
  Signal decay, independence weighting, regime classification are first-class.

PERMANENT LOCKS:
  1. Streamlit render loop NEVER calls external LLM APIs.
  2. All DB text passes through _safe_display() — zero substrate leaks.
  3. Background services perform cognition. This tab is the window only.
"""
from __future__ import annotations



import datetime as _dt
import html
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

C_PURPLE = "#9945FF"
C_GREEN  = "#14F195"
C_GOLD   = "#FFD700"
C_CYAN   = "#8EF9FF"
C_RED    = "#FF073A"
C_IVY    = "#FFB347"
C_VOID   = "#050210"
C_NUGGET = "#C19A6B"
C_DIM    = "#5E7280"  # BUGFIX_20260718: referenced below (flat px5) but never defined

# -----------------------------------------------------------------------------
# SCHEMA BOOTSTRAP
# -----------------------------------------------------------------------------

def _ensure_forge_tables() -> None:
    """UI verification only. Schema creation belongs to prelaunch."""
    return


# -----------------------------------------------------------------------------
# DATA HELPERS
# -----------------------------------------------------------------------------

def _qdb(query_db, sql: str, params=()):
    """Safe query wrapper that always returns a DataFrame."""
    try:
        result = query_db(sql, params)
        if isinstance(result, pd.DataFrame): return result
        if result is None: return pd.DataFrame()
        if isinstance(result, dict): return pd.DataFrame([result])
        if isinstance(result, (list, tuple)):
            if not result: return pd.DataFrame()
            return pd.DataFrame(result)
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def _safe_panel(name: str, fn, *args) -> None:
    """One broken intelligence panel must never blank the whole tab."""
    try:
        fn(*args)
    except Exception as exc:
        st.markdown(
            f"<div style='border:1px solid rgba(255,179,71,.35);border-left:3px solid #FFB347;border-radius:9px;padding:9px 11px;margin:7px 0;background:rgba(255,179,71,.035);font-family:Share Tech Mono,monospace;font-size:.72rem;color:#C9D4CC;'><b style='color:#FFB347'>{html.escape(name)}</b> · panel isolated · {html.escape(type(exc).__name__)}: {html.escape(str(exc)[:180])}</div>",
            unsafe_allow_html=True,
        )


def _ts(epoch) -> str:
    """Format epoch to readable short timestamp."""
    try:
        return _dt.datetime.fromtimestamp(float(epoch)).strftime("%m/%d %H:%M")
    except Exception:
        return "-"


def _submit_petition(petition: str, priority: str = "normal") -> bool:
    """Write operator petition to research_queue. No API call. Background daemon picks it up."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO research_queue (petition, petitioner, priority, status, created_at) VALUES (?, 'OPERATOR', ?, 'pending', ?)",
            (petition.strip(), priority, time.time())
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# SECTION RENDERERS
# -----------------------------------------------------------------------------

def _pill(txt: str, color: str) -> str:
    return (
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;"
        f"padding:3px 8px;border-radius:999px;border:1px solid {color}55;background:{color}12;color:{color};"
        f"margin-right:6px;display:inline-block;'>{html.escape(str(txt))}</span>"
    )


def _mask_intent(raw: str, fallback: str = "Strategic council initiative.") -> tuple:
    """
    Convert raw SQL/code/HTML to a human-readable intent badge.
    Returns (display_text, color).
    Never lets implementation substrate reach the operator view.
    """
    import re as _re
    if not raw:
        return fallback, "rgba(255,255,255,0.7)"
    first = raw.splitlines()[0].strip()
    fu = first.upper()
    if any(fu.startswith(s) for s in ('INSERT','UPDATE','SELECT','DELETE','CREATE','DROP','ALTER')):
        return "[DATABASE_MUTATION]", "#8EF9FF"
    if any(fu.startswith(s) for s in ('DEF ','CLASS ','IMPORT ','FROM ')):
        return "[LOGIC_ASCENSION]", "#9945FF"
    if '<' in first or 'style=' in first.lower():
        return "[INTERFACE_EVOLUTION]", "#FFD700"
    clean = _re.sub(r'<[^>]+>', '', raw)[:160].strip()
    if not clean:
        return fallback, "rgba(255,255,255,0.7)"
    return html.escape(clean), "rgba(255,255,255,0.85)"



def _safe_display(text: Any, max_len: int = 180) -> str:
    """
    ZERO-LEAK SHIELD — every DB text field must pass through here.
    Blocks: SQL, Python code, HTML/CSS, JSON dumps, stack traces.
    Returns a safe human-readable string or a narrative badge.
    """
    if not text:
        return "—"
    s = str(text).strip()
    if not s:
        return "—"
    first = s.splitlines()[0].strip().upper()
    # Block SQL
    if any(first.startswith(w) for w in (
        'INSERT','UPDATE','SELECT','DELETE','CREATE','DROP','ALTER',
        'PATCH','REPLACE','PRAGMA','BEGIN','COMMIT',
    )):
        return "[DATABASE_MUTATION]"
    # Block Python code
    if any(first.startswith(w) for w in ('DEF ','CLASS ','IMPORT ','FROM ','ASYNC DEF')):
        return "[LOGIC_ASCENSION]"
    # Block HTML/CSS
    if '<' in first[:20] or first.startswith(('STYLE=','CLASS=','<DIV','<SPAN')):
        return "[INTERFACE_EVOLUTION]"
    # Block JSON blobs
    if (first.startswith('{') or first.startswith('[')) and len(s) > 80:
        return "[STRUCTURED_PAYLOAD]"
    # Strip any remaining HTML tags
    import re as _re
    clean = _re.sub(r'<[^>]+>', '', s)
    return html.escape(clean[:max_len])

def _render_substrate_header(pending_petitions: int) -> None:
    petition_badge = (
        f"&nbsp;&nbsp;&#9670;&nbsp;&nbsp;<span style='color:{C_GOLD};font-family:Orbitron,sans-serif;'>"
        f"{pending_petitions} PETITIONS QUEUED</span>"
        if pending_petitions > 0 else ""
    )
    st.markdown(
        f"<div style='padding:20px 24px 16px;border-bottom:1px solid rgba(153,69,255,0.3);"
        f"margin-bottom:20px;background:radial-gradient(ellipse at 30% 0%,rgba(153,69,255,0.08) 0%,transparent 60%);'>"
        f"<div style='font-family:Orbitron,sans-serif;font-size:1.4rem;letter-spacing:6px;color:{C_PURPLE};"
        f"text-shadow:0 0 20px {C_PURPLE},0 0 40px {C_PURPLE}66;'>THE INTELLIGENCE SUBSTRATE</div>"
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.7rem;letter-spacing:3px;"
        f"color:rgba(142,249,255,0.6);margin-top:6px;'>"
        f"SOVEREIGN COGNITIVE MEMBRANE &mdash; COUNCIL FORGE ACTIVE"
        f"{petition_badge}</div></div>",
        unsafe_allow_html=True
    )


def _render_current_state_panel(debate_df: pd.DataFrame, proposals_df: pd.DataFrame) -> None:
    """Current state panel — fully modular, no giant HTML strings, zero leak risk."""

    # Section header
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;"
        f"letter-spacing:4px;color:{C_CYAN};margin-bottom:10px;'>CURRENT STATE</div>",
        unsafe_allow_html=True
    )

    if debate_df.empty and proposals_df.empty:
        st.markdown(
            f"<div style='border:1px solid rgba(142,249,255,0.1);border-radius:10px;"
            f"padding:16px;text-align:center;'>"
            f"<div style='font-family:Orbitron,sans-serif;font-size:.8rem;"
            f"letter-spacing:3px;color:rgba(142,249,255,0.4);'>"
            f"// CHAMBER IDLE — AWAITING COGNITIVE IGNITION //</div></div>",
            unsafe_allow_html=True
        )
        return

    latest_debate = debate_df.iloc[0] if not debate_df.empty else None
    latest_prop   = proposals_df.iloc[0] if not proposals_df.empty else None

    speaker  = str(latest_debate.get("speaker",  "SYSTEM")).upper() if latest_debate is not None else "SYSTEM"
    action   = str(latest_debate.get("action",   "idle"))           if latest_debate is not None else "idle"
    thinking = str(latest_debate.get("thinking_state", "")).upper() if latest_debate is not None else ""
    verdict  = str(latest_debate.get("verdict_type",   "")).upper() if latest_debate is not None else ""
    message  = str(latest_debate.get("message", "") or "")          if latest_debate is not None else ""
    logged_at = _ts(latest_debate.get("logged_at", 0))              if latest_debate is not None else "-"

    # Sanitise message — strip any HTML tags to prevent leak
    import re as _re
    message_clean = _re.sub(r'<[^>]+>', '', message)[:220]
    message_safe  = html.escape(message_clean) if message_clean else "Awaiting council signal."

    prop_type   = str(latest_prop.get("proposal_type", "UNKNOWN")).upper() if latest_prop is not None else "UNKNOWN"
    prop_status = str(latest_prop.get("status", "open")).upper()           if latest_prop is not None else "OPEN"
    prop_conf   = float(latest_prop.get("confidence", 0) or 0)            if latest_prop is not None else 0.0
    prop_updated = _ts(latest_prop.get("last_seen_at") or latest_prop.get("created_at", 0)) if latest_prop is not None else "-"

    # Mask intent — never show raw SQL or HTML
    _raw = str(latest_prop.get("suggested_action", "") or latest_prop.get("proposal_text", "") or "") if latest_prop is not None else ""
    _first = _raw.splitlines()[0].strip() if _raw else ""
    _SQL = ('INSERT','UPDATE','SELECT','DELETE','CREATE','PATCH','ALTER','DROP')
    _CODE = ('<', 'def ', 'class ', 'import ', 'style=')
    if any(_first.upper().startswith(s) for s in _SQL):
        prop_intent = "[DATABASE_MUTATION]"
        intent_col  = C_CYAN
    elif any(c in _first for c in _CODE):
        prop_intent = "[INTERFACE_EVOLUTION]"
        intent_col  = C_GOLD
    else:
        _clean = _re.sub(r'<[^>]+>', '', _raw)[:160]
        prop_intent = html.escape(_clean) if _clean else "Strategic council initiative."
        intent_col  = "rgba(255,255,255,0.85)"

    state_col = (
        C_RED  if "BLOCK" in thinking or "FAIL" in verdict or "STALLED" in verdict else
        C_GOLD if "REJECT" in verdict or "EVALU" in thinking else
        C_GREEN if "CONSENSUS" in thinking or "APPROVED" in prop_status else
        C_CYAN
    )

    # ── Render as separate small markdown calls — no giant concatenated HTML ──
    # Outer wrapper open
    st.markdown(
        "<div style='border:1px solid rgba(142,249,255,0.16);border-radius:14px;"
        "padding:14px 16px;margin-bottom:12px;"
        "background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(0,0,0,0.1));'>"
        "<div style='display:flex;gap:12px;flex-wrap:wrap;'>",
        unsafe_allow_html=True
    )

    # Left: debate state
    debate_pills = (
        _pill(speaker, state_col)
        + _pill(action.upper()[:20], C_PURPLE)
        + (_pill(thinking[:20], state_col) if thinking else "")
        + (_pill(verdict[:20], C_GOLD) if verdict else "")
    )
    st.markdown(
        f"<div style='flex:1 1 55%;min-width:200px;'>"
        f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;'>{debate_pills}</div>"
        f"<div style='font-family:Rajdhani,sans-serif;font-size:.88rem;line-height:1.5;"
        f"color:rgba(255,255,255,0.88);'>{message_safe}</div>"
        f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
        f"color:rgba(142,249,255,0.3);margin-top:6px;'>updated {logged_at}</div>"
        f"</div>",
        unsafe_allow_html=True
    )

    # Right: proposal state
    prop_pills = (
        _pill(prop_type[:20], C_CYAN)
        + _pill(prop_status[:15], C_GOLD if prop_status == "OPEN" else C_GREEN)
        + _pill(f"CONF {prop_conf:.2f}", C_PURPLE)
    )
    st.markdown(
        f"<div style='flex:1 1 40%;min-width:160px;"
        f"border-left:1px solid rgba(142,249,255,0.1);padding-left:12px;'>"
        f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;'>{prop_pills}</div>"
        f"<div style='font-family:Rajdhani,sans-serif;font-size:.85rem;line-height:1.5;"
        f"color:{intent_col};'>{prop_intent}</div>"
        f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
        f"color:rgba(142,249,255,0.3);margin-top:6px;'>updated {prop_updated}</div>"
        f"</div>",
        unsafe_allow_html=True
    )

    # Outer wrapper close
    st.markdown("</div></div>", unsafe_allow_html=True)

def _render_synthesis_documents(forge_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_PURPLE};margin-bottom:12px;'>ACTIVE SYNTHESIS DOCUMENTS - POLARIS SUBSTRATE</div>",
        unsafe_allow_html=True
    )
    if forge_df.empty:
        st.markdown(
            f"<div style='border:1px solid rgba(153,69,255,0.2);border-radius:12px;padding:28px;"
            f"text-align:center;background:rgba(153,69,255,0.03);'>"
            f"<div style='font-family:Orbitron,sans-serif;font-size:.9rem;letter-spacing:4px;"
            f"color:rgba(153,69,255,0.4);'>SUBSTRATE INITIALIZING - AWAITING FIRST COGNITION CYCLE</div>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;"
            f"color:rgba(142,249,255,0.3);margin-top:8px;'>Background cognition cycles are running.<br>"
            f"Synthesis documents will surface here as Polaris completes research cycles.</div>"
            f"</div>",
            unsafe_allow_html=True
        )
        return

    for _, doc in forge_df.iterrows():
        doc_type = str(doc.get("doc_type", "research")).upper()
        title    = html.escape(str(doc.get("title", "Untitled Research")))
        author   = str(doc.get("author", "POLARIS")).upper()
        status   = str(doc.get("status", "active")).upper()
        content  = str(doc.get("content_md", ""))
        updated  = _ts(doc.get("updated_at") or doc.get("created_at", 0))
        tags     = str(doc.get("tags", "") or "")

        status_col = C_GREEN if status == "COMPLETE" else (C_GOLD if status == "BUILDING" else C_PURPLE)
        author_col = C_CYAN if author == "IVARIS" else C_PURPLE

        st.markdown(
            f"<div style='border:1px solid rgba(153,69,255,0.25);border-radius:14px;padding:18px 20px;"
            f"margin-bottom:14px;background:radial-gradient(ellipse at 0% 0%, rgba(153,69,255,0.06) 0%, transparent 50%);"
            f"position:relative;'>",
            unsafe_allow_html=True
        )
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;'>"
            f"<div>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;color:{author_col};'>{doc_type} - {author}</span><br>"
            f"<span style='font-family:Orbitron,sans-serif;font-size:.9rem;color:#FFF;letter-spacing:2px;'>{title}</span>"
            f"</div>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{status_col};"
            f"border:1px solid {status_col}44;padding:3px 8px;border-radius:10px;background:{status_col}0A;'>{status}</span>"
            f"</div>",
            unsafe_allow_html=True
        )
        if content:
            preview = content[:280] + ("-" if len(content) > 280 else "")
            st.markdown(
                f"<div style='font-family:Rajdhani,sans-serif;font-size:.88rem;color:rgba(200,216,232,0.75);"
                f"line-height:1.6;border-left:2px solid rgba(153,69,255,0.3);padding-left:12px;margin-bottom:10px;'>"
                f"{html.escape(preview)}</div>",
                unsafe_allow_html=True
            )
        if tags:
            for tag in tags.split(",")[:5]:
                st.markdown(
                    f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;"
                    f"color:rgba(153,69,255,0.6);border:1px solid rgba(153,69,255,0.2);padding:2px 8px;"
                    f"border-radius:10px;margin-right:6px;background:rgba(153,69,255,0.05);'>{html.escape(tag.strip())}</span>",
                    unsafe_allow_html=True
                )
        st.markdown(
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(142,249,255,0.3);"
            f"margin-top:8px;'>last updated {updated}</div>",
            unsafe_allow_html=True
        )
        st.markdown("</div>", unsafe_allow_html=True)


def _render_live_research_feed(proposals_df: pd.DataFrame, debate_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_CYAN};margin-bottom:12px;'>LIVE RESEARCH FEED</div>",
        unsafe_allow_html=True
    )

    if not proposals_df.empty:
        for _, row in proposals_df.head(8).iterrows():
            ptype   = str(row.get("proposal_type", "")).upper()
            _ra = str(row.get("suggested_action", row.get("proposal_text", "")) or "")
            ptxt, _ptxt_col = _mask_intent(_ra, "Council research signal.")
            ptxt = ptxt[:120]
            conf    = float(row.get("confidence", 0) or 0)
            status  = str(row.get("status", "pending")).upper()
            created = _ts(row.get("created_at", 0))
            s_col   = C_GREEN if status in ("APPROVED", "APPLIED") else (C_GOLD if status == "DEBATE" else C_PURPLE)

            st.markdown(
                f"<div style='border-left:3px solid {s_col};padding:10px 12px;margin-bottom:8px;"
                f"background:rgba(255,255,255,0.02);border-radius:0 8px 8px 0;'>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{s_col};"
                f"letter-spacing:2px;margin-bottom:4px;'>{ptype} - {status} - conf={conf:.2f}</div>"
                f"<div style='color:#FFF;font-family:Rajdhani,sans-serif;font-size:.85rem;"
                f"line-height:1.5;'>{ptxt}</div>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
                f"color:rgba(142,249,255,0.3);margin-top:4px;'>{created}</div>"
                f"</div>",
                unsafe_allow_html=True
            )
    else:
        st.markdown(
            f"<div style='color:rgba(153,69,255,0.35);font-family:Share Tech Mono,monospace;"
            f"font-size:.68rem;letter-spacing:2px;padding:16px;text-align:center;'>"
            f"NO ACTIVE RESEARCH NODES<br><span style='opacity:.5;font-size:0.66rem;'>"
            f"Polaris will populate this as she identifies signal patterns</span></div>",
            unsafe_allow_html=True
        )

    if not debate_df.empty:
        st.markdown(
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;"
            f"color:rgba(153,69,255,0.6);margin:14px 0 8px;border-top:1px solid rgba(153,69,255,0.15);"
            f"padding-top:12px;'>RECENT SYNTHESIS FRAGMENTS</div>",
            unsafe_allow_html=True
        )
        for _, row in debate_df.head(5).iterrows():
            speaker = str(row.get("speaker", "POLARIS")).upper()
            text    = html.escape(str(row.get("verdict_text", row.get("message", "")))[:100])
            sp_col  = C_IVY if speaker == "IVARIS" else C_CYAN
            st.markdown(
                f"<div style='font-size:.72rem;border-left:2px solid {sp_col}22;padding:6px 10px;"
                f"margin-bottom:6px;background:rgba(255,255,255,0.01);border-radius:0 6px 6px 0;'>"
                f"<span style='color:{sp_col};font-family:Share Tech Mono,monospace;font-size:0.66rem;"
                f"letter-spacing:1px;'>{speaker}</span><br>"
                f"<span style='color:rgba(200,216,232,0.6);font-family:Rajdhani,sans-serif;'>{text}</span>"
                f"</div>",
                unsafe_allow_html=True
            )


def _render_patch_timeline(patch_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_GOLD};margin-bottom:12px;'>MUTATION TIMELINE</div>",
        unsafe_allow_html=True
    )
    if patch_df.empty:
        st.markdown(
            f"<div style='color:rgba(255,215,0,0.25);font-family:Share Tech Mono,monospace;"
            f"font-size:.68rem;letter-spacing:2px;padding:12px;text-align:center;'>"
            f"NO MUTATIONS RECORDED YET</div>",
            unsafe_allow_html=True
        )
        return

    for _, ph in patch_df.head(10).iterrows():
        outcome  = str(ph.get("outcome", "pending")).upper()
        param    = str(ph.get("param_key", "") or "")
        old_v    = str(ph.get("old_value", "") or "")
        new_v    = str(ph.get("new_value", "") or "")
        ptype    = str(ph.get("proposal_type", ""))[:30]
        applied  = _ts(ph.get("applied_at", 0))
        out_col  = C_GREEN if outcome == "IMPROVED" else (C_RED if outcome == "DEGRADED" else C_GOLD)
        change   = f"{param}: {old_v} - {new_v}" if param and old_v and new_v else ptype

        st.markdown(
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"padding:7px 10px;margin-bottom:4px;border-radius:6px;"
            f"background:rgba(255,255,255,0.02);border-left:2px solid {out_col}44;'>"
            f"<div>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#FFF;'>"
            f"{html.escape(change[:40])}</span><br>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:rgba(142,249,255,0.35);'>{applied}</span>"
            f"</div>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{out_col};"
            f"border:1px solid {out_col}44;padding:2px 8px;border-radius:8px;"
            f"background:{out_col}0A;'>{outcome}</span>"
            f"</div>",
            unsafe_allow_html=True
        )


def _render_petition_membrane(queue_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='border:1px solid rgba(255,215,0,0.3);border-radius:16px;padding:20px 22px;"
        f"margin-top:20px;background:radial-gradient(ellipse at 50% 0%, rgba(255,215,0,0.06) 0%, transparent 50%);'>",
        unsafe_allow_html=True
    )
    st.markdown(
        f"<div style='font-family:Orbitron,sans-serif;font-size:.9rem;letter-spacing:5px;"
        f"color:{C_GOLD};margin-bottom:4px;'>OPERATOR PETITION MEMBRANE - DIRECTED SYNTHESIS</div>"
        f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;"
        f"color:rgba(255,215,0,0.5);margin-bottom:14px;'>DIRECT POLARIS & IVARIS ATTENTION - QUEUED TO RESEARCH SUBSTRATE - "
        f"NO BLOCKING API CALL - BACKGROUND DAEMON HANDLES SYNTHESIS</div>",
        unsafe_allow_html=True
    )

    col_input, col_priority = st.columns([4, 1])
    with col_input:
        petition_text = st.text_area(
            "Petition",
            key="forge_petition_input",
            label_visibility="collapsed",
            placeholder="Direct Polaris and IVARIS to research a specific topic, audit a signal, or build something...",
            height=80
        )
    with col_priority:
        priority = st.selectbox(
            "Priority",
            options=["normal", "urgent", "background"],
            key="forge_petition_priority",
            label_visibility="collapsed"
        )

    with st.container():
        if st.button("QUEUE DIRECTED SYNTHESIS", key="forge_petition_submit"):
            if petition_text and petition_text.strip():
                if _submit_petition(petition_text, priority):
                    st.success("PETITION QUEUED - Background daemon will pick this up on next cycle")
                else:
                    st.error("QUEUE WRITE FAILED - Check DB connection")
            else:
                st.warning("Enter a petition before submitting")

    if not queue_df.empty:
        st.markdown(
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;"
            f"color:rgba(255,215,0,0.4);margin-top:14px;margin-bottom:8px;'>RECENT PETITIONS</div>",
            unsafe_allow_html=True
        )
        for _, row in queue_df.head(5).iterrows():
            status   = str(row.get("status", "pending")).upper()
            petition = html.escape(str(row.get("petition", ""))[:90])
            created  = _ts(row.get("created_at", 0))
            s_col    = C_GREEN if status == "RESOLVED" else (C_GOLD if status == "PROCESSING" else C_PURPLE)
            st.markdown(
                f"<div style='font-size:.7rem;padding:6px 10px;margin-bottom:4px;"
                f"border-left:2px solid {s_col}44;background:rgba(255,255,255,0.01);"
                f"border-radius:0 6px 6px 0;'>"
                f"<span style='color:{s_col};font-family:Share Tech Mono,monospace;"
                f"font-size:0.66rem;'>{status}</span>"
                f"<span style='color:rgba(142,249,255,0.3);font-family:Share Tech Mono,monospace;"
                f"font-size:0.66rem;margin-left:8px;'>{created}</span><br>"
                f"<span style='color:rgba(200,216,232,0.7);font-family:Rajdhani,sans-serif;'>{petition}</span>"
                f"</div>",
                unsafe_allow_html=True
            )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_improvement_queue(iq_df: pd.DataFrame) -> None:
    if iq_df.empty:
        return
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:rgba(153,69,255,0.6);margin:16px 0 10px;border-top:1px solid rgba(153,69,255,0.15);"
        f"padding-top:14px;'>IMPROVEMENT SIGNALS FROM SERVICES</div>",
        unsafe_allow_html=True
    )
    for _, row in iq_df.head(6).iterrows():
        source   = str(row.get("source", "")).upper()
        category = str(row.get("category", "")).upper()
        payload  = row.get("payload_json", "")
        created  = _ts(row.get("created_at", 0))
        try:
            payload_data = json.loads(payload) if payload else {}
            payload_text = _safe_display(str(payload_data), 100)
        except Exception:
            payload_text = _safe_display(str(payload), 100)

        st.markdown(
            f"<div style='border-left:2px solid rgba(153,69,255,0.3);padding:8px 12px;"
            f"margin-bottom:6px;background:rgba(153,69,255,0.02);border-radius:0 8px 8px 0;'>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:{C_PURPLE};letter-spacing:1px;'>{source} - {category}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:rgba(142,249,255,0.3);margin-left:8px;'>{created}</span><br>"
            f"<span style='color:rgba(200,216,232,0.6);font-family:Rajdhani,sans-serif;"
            f"font-size:.82rem;'>{payload_text}</span>"
            f"</div>",
            unsafe_allow_html=True
        )


def _render_cognitive_stream(cognition_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_CYAN};margin:18px 0 10px;'>COGNITIVE STREAM - POLARIS OWNED</div>",
        unsafe_allow_html=True
    )
    if cognition_df.empty:
        st.markdown(
            f"<div style='border:1px dashed rgba(142,249,255,0.2);border-radius:12px;padding:18px;"
            f"text-align:center;color:rgba(142,249,255,0.35);font-family:Share Tech Mono,monospace;'>"
            f"NO X_SCOUT SIGNALS YET - WAITING ON LIVE COGNITION</div>",
            unsafe_allow_html=True
        )
        return

    xscout = cognition_df[cognition_df["stage"].astype(str).str.upper().isin(["X_SCOUT", "SENSORY_SCOUT"])] if "stage" in cognition_df.columns else cognition_df
    feed = xscout if not xscout.empty else cognition_df

    for _, row in feed.head(30).iterrows():
        stage = str(row.get("stage", "OBSERVED")).upper()
        token = html.escape(str(row.get("token", ""))[:24])
        msg = _safe_display(str(row.get("message", "") or ""), 220)
        conf = float(row.get("confidence", 0) or 0)
        ts = _ts(row.get("timestamp", 0))
        s_col = C_GREEN if stage in {"EXECUTOR", "SUPERVISOR"} else (C_GOLD if stage == "DEBATE" else C_CYAN)
        st.markdown(
            f"<div style='border-left:3px solid {s_col};padding:10px 12px;margin-bottom:8px;"
            f"background:rgba(255,255,255,0.02);border-radius:0 8px 8px 0;'>"
            f"<div style='display:flex;justify-content:space-between;gap:12px;align-items:center;'>"
            f"<div><span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;color:{s_col};'>{stage}</span> "
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(255,255,255,0.45);'>{token}</span></div>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(142,249,255,0.3);'>{ts}</span>"
            f"</div>"
            f"<div style='font-family:Rajdhani,sans-serif;color:rgba(255,255,255,0.86);line-height:1.5;margin-top:4px;'>{msg}</div>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(142,249,255,0.3);margin-top:4px;'>conf={conf:.2f}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_forensic_audit(debate_df: pd.DataFrame, patch_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_NUGGET};margin:18px 0 10px;'>FORENSIC AUDIT - NUGGET</div>",
        unsafe_allow_html=True,
    )
    recent = patch_df.head(12) if not patch_df.empty else pd.DataFrame()
    if recent.empty and debate_df.empty:
        st.markdown(
            f"<div style='border:1px dashed rgba(193,154,106,0.25);border-radius:12px;padding:18px;"
            f"text-align:center;color:rgba(193,154,106,0.4);font-family:Share Tech Mono,monospace;'>"
            f"NO FORENSIC EVENTS YET</div>",
            unsafe_allow_html=True,
        )
        return
    for _, row in recent.iterrows():
        outcome = str(row.get("outcome", "PENDING")).upper()
        applied = _ts(row.get("applied_at", 0))
        st.markdown(
            f"<div style='border-left:3px solid {C_NUGGET};padding:9px 12px;margin-bottom:7px;"
            f"background:rgba(193,154,106,0.05);border-radius:0 8px 8px 0;'>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{C_NUGGET};letter-spacing:2px;'>"
            f"{html.escape(str(row.get('proposal_type', 'UNKNOWN')).upper())} • {html.escape(outcome)}"
            f"</div>"
            f"<div style='font-family:Rajdhani,sans-serif;color:rgba(255,255,255,0.82);line-height:1.5;'>"
            f"{html.escape(str(row.get('param_key', '') or '')[:120])}"
            f"</div>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(193,154,106,0.55);'>{applied}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_alpha_queue(proposals_df: pd.DataFrame, iq_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_IVY};margin:18px 0 10px;'>ALPHA & RESEARCH QUEUE - ORACLE</div>",
        unsafe_allow_html=True,
    )
    if proposals_df.empty and iq_df.empty:
        st.markdown(
            f"<div style='border:1px dashed rgba(255,180,71,0.25);border-radius:12px;padding:18px;"
            f"text-align:center;color:rgba(255,180,71,0.4);font-family:Share Tech Mono,monospace;'>"
            f"NO ALPHA ITEMS YET</div>",
            unsafe_allow_html=True,
        )
        return
    for _, row in proposals_df.head(10).iterrows():
        ptype = str(row.get("proposal_type", "UNKNOWN")).upper()
        _ra2 = str(row.get("suggested_action", row.get("proposal_text", "")) or "")
        txt, _txt_col = _mask_intent(_ra2, "Council research signal.")
        txt = txt[:150]
        conf = float(row.get("confidence", 0) or 0)
        status = str(row.get("status", "open")).upper()
        st.markdown(
            f"<div style='border-left:3px solid {C_IVY};padding:9px 12px;margin-bottom:7px;"
            f"background:rgba(255,180,71,0.05);border-radius:0 8px 8px 0;'>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{C_IVY};letter-spacing:2px;'>"
            f"{ptype} • {status} • conf={conf:.2f}</div>"
            f"<div style='font-family:Rajdhani,sans-serif;color:rgba(255,255,255,0.82);line-height:1.5;'>{txt}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    if not iq_df.empty:
        st.markdown(
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;color:rgba(255,180,71,0.55);margin-top:8px;'>SERVICE SIGNALS</div>",
            unsafe_allow_html=True,
        )
        for _, row in iq_df.head(5).iterrows():
            payload = html.escape(str(row.get("payload_json", ""))[:120])
            st.markdown(
                f"<div style='border-left:2px solid rgba(255,180,71,0.25);padding:7px 10px;margin-bottom:5px;"
                f"background:rgba(255,180,71,0.03);border-radius:0 8px 8px 0;'>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(255,180,71,0.7);letter-spacing:1px;'>"
                f"{html.escape(str(row.get('source', '')).upper())} • {html.escape(str(row.get('category', '')).upper())}</div>"
                f"<div style='font-family:Rajdhani,sans-serif;color:rgba(255,255,255,0.76);line-height:1.45;'>{payload}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


def _render_narrative_layer(forge_df: pd.DataFrame, debate_df: pd.DataFrame) -> None:
    """
    NARRATIVE LAYER — RHIZA synthesis from debate context only.
    Does NOT re-render forge documents (that's synthesis_documents job).
    Shows only grok_narrative field from debate_log.
    """
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
        f"letter-spacing:3px;color:rgba(142,249,255,0.5);margin:14px 0 6px;'>"
        f"NARRATIVE LAYER — RHIZA</div>",
        unsafe_allow_html=True
    )

    # Only show grok_narrative from recent debate entries — not forge docs
    narratives = []
    if not debate_df.empty and "grok_narrative" in debate_df.columns:
        for _, row in debate_df.head(3).iterrows():
            narr = str(row.get("grok_narrative") or "").strip()
            if narr and narr != "nan" and len(narr) > 20:
                narratives.append(narr)

    if not narratives:
        st.markdown(
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
            f"color:#333;padding:8px 0;'>"
            f"// RHIZA NARRATIVE PENDING — AWAITING DEBATE SYNTHESIS //</div>",
            unsafe_allow_html=True
        )
        return

    for narr in narratives:
        st.markdown(
            f"<div style='border-left:2px solid rgba(153,69,255,0.4);"
            f"padding:8px 12px;margin-bottom:8px;"
            f"background:rgba(153,69,255,0.05);border-radius:0 6px 6px 0;'>"
            f"<div style='font-family:Rajdhani,sans-serif;font-size:.82rem;"
            f"color:rgba(255,255,255,0.7);line-height:1.5;'>"
            f"{_safe_display(narr, 300)}</div></div>",
            unsafe_allow_html=True
        )

def _render_execution_log_panel(debate_df: pd.DataFrame, patch_df: pd.DataFrame) -> None:
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;letter-spacing:4px;"
        f"color:{C_RED};margin:18px 0 10px;'>EXECUTION LOG - AXON</div>",
        unsafe_allow_html=True,
    )
    if patch_df.empty and debate_df.empty:
        st.markdown(
            f"<div style='border:1px dashed rgba(255,7,58,0.25);border-radius:12px;padding:18px;"
            f"text-align:center;color:rgba(255,7,58,0.4);font-family:Share Tech Mono,monospace;'>"
            f"NO EXECUTION EVENTS YET</div>",
            unsafe_allow_html=True,
        )
        return
    for _, row in patch_df.head(8).iterrows():
        applied = _ts(row.get("applied_at", 0))
        outcome = str(row.get("outcome", "PENDING")).upper()
        st.markdown(
            f"<div style='border-left:3px solid {C_RED};padding:9px 12px;margin-bottom:7px;"
            f"background:rgba(255,7,58,0.04);border-radius:0 8px 8px 0;'>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{C_RED};letter-spacing:2px;'>"
            f"{html.escape(str(row.get('proposal_type', 'UNKNOWN')).upper())} • {html.escape(outcome)}"
            f"</div>"
            f"<div style='font-family:Rajdhani,sans-serif;color:rgba(255,255,255,0.82);line-height:1.5;'>"
            f"{html.escape(str(row.get('param_key', '') or '')[:140])}"
            f"</div>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(255,7,58,0.55);'>{applied}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    if not debate_df.empty:
        st.markdown(
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;color:rgba(255,7,58,0.55);margin-top:8px;'>"
            f"EXECUTION TRACE FROM DEBATE</div>",
            unsafe_allow_html=True,
        )
        for _, row in debate_df.head(4).iterrows():
            txt = _safe_display(str(row.get("message", "") or ""), 120)
            st.markdown(
                f"<div style='border-left:2px solid rgba(255,7,58,0.25);padding:7px 10px;margin-bottom:5px;"
                f"background:rgba(255,7,58,0.03);border-radius:0 8px 8px 0;'>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(255,7,58,0.7);letter-spacing:1px;'>"
                f"{html.escape(str(row.get('speaker', '')).upper())} • {html.escape(str(row.get('verdict_type', '')).upper())}</div>"
                f"<div style='font-family:Rajdhani,sans-serif;color:rgba(255,255,255,0.76);line-height:1.45;'>{txt}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


# -----------------------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------------------



def _render_golden_lattice(query_db) -> None:
    """
    GOLDEN LATTICE — Paper Proving Ground.
    Shows approved proposals running in shadow paper mode.
    Displays: proposal, proposed change, shadow PnL vs current doctrine PnL.
    Operator Seal section for approving proven proposals.
    """
    import streamlit as st, time as _t, html as _html, sqlite3 as _sq3

    C_GREEN  = "#14F195"
    C_GOLD   = "#FFD700"
    C_RED    = "#FF073A"
    C_PURPLE = "#9945FF"
    C_CYAN   = "#8EF9FF"

    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;"
        f"letter-spacing:4px;color:{C_GOLD};margin:20px 0 10px;'>"
        f"⬡ THE GOLDEN LATTICE — PAPER PROVING GROUND</div>",
        unsafe_allow_html=True,
    )

    # Fetch approved/applied proposals
    try:
        rows = query_db("""
            SELECT id, proposal_type, proposal_text, suggested_action,
                   confidence, status, created_at
            FROM polaris_proposals
            WHERE status IN ('approved','applied','forge_complete','debate')
            ORDER BY created_at DESC LIMIT 6
        """)
        proposals = rows.to_dict('records') if not rows.empty else []
    except Exception:
        proposals = []

    if not proposals:
        st.markdown(
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
            f"color:#333;letter-spacing:2px;padding:10px 0;'>"
            f"// NO PROPOSALS IN PROVING GROUND — AWAITING DEBATE COMPLETION //</div>",
            unsafe_allow_html=True,
        )
        return

    for p in proposals:
        pid    = p.get('id', 0)
        ptype  = str(p.get('proposal_type',''))
        ptext  = str(p.get('proposal_text',''))[:120]
        status = str(p.get('status',''))
        conf   = float(p.get('confidence') or 0)
        sc     = C_GREEN if status == 'applied' else (C_GOLD if status == 'approved' else C_PURPLE)
        badge  = {'applied':'APPLIED','approved':'APPROVED','forge_complete':'FORGE READY','debate':'DEBATING'}.get(status, status.upper())

        st.markdown(
            f"<div style='padding:12px 16px;margin-bottom:8px;"
            f"border:1px solid {sc}44;border-left:3px solid {sc};"
            f"border-radius:10px;background:rgba(5,2,16,0.7);'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>"
            f"<span style='font-family:Share Tech Mono;font-size:0.66rem;letter-spacing:2px;color:{sc};'>"
            f"{badge}</span>"
            f"<span style='font-family:Share Tech Mono;font-size:0.66rem;color:#444;'>"
            f"{ptype} | conf:{conf:.2f}</span>"
            f"</div>"
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;color:#AAA;line-height:1.5;'>"
            f"{_html.escape(ptext)}...</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Operator Seal for forge_complete proposals
        if status == 'forge_complete':
            col1, col2 = st.columns([3,1])
            with col1:
                st.markdown(
                    f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
                    f"color:{C_GOLD};padding:6px 0;'>⚡ MASTERPIECE FORGED — READY FOR INTEGRATION</div>",
                    unsafe_allow_html=True,
                )
            with col2:
                seal = st.text_input(f"SEAL CODE", key=f"seal_{pid}", placeholder="6-digit code")
                if st.button(f"INTEGRATE", key=f"integrate_{pid}"):
                    if seal and len(seal) == 6 and seal.isdigit():
                        try:
                            _c = _sq3.connect(str(DB_PATH), timeout=3)
                            _c.row_factory = _sq3.Row
                            # 1. Flip proposal to applied
                            _c.execute(
                                "UPDATE polaris_proposals SET status='applied' WHERE id=?",
                                (pid,)
                            )
                            # 2. Deterministic patch handoff — create code_patches row
                            # if rewritten_code exists and not already patched
                            _prop = _c.execute(
                                "SELECT rewritten_code, axon_passed, project_key, "
                                "proposal_type, suggested_action FROM polaris_proposals "
                                "WHERE id=?", (pid,)
                            ).fetchone()
                            _already = _c.execute(
                                "SELECT COUNT(*) n FROM code_patches WHERE proposal_id=?",
                                (pid,)
                            ).fetchone()["n"]
                            if _prop and not _already:
                                import re as _re, time as _t
                                _rw = (_prop["rewritten_code"] or "").strip()
                                _act = (_prop["suggested_action"] or "")
                                _tgt = None
                                _code = None
                                if _rw:
                                    _m = _re.search(r'TARGET:\s*([^\n]+)', _rw)
                                    if _m:
                                        _tgt = _m.group(1).strip()
                                    if not _tgt:
                                        _m2 = _re.search(r'TARGET:\s*([^\n]+)', _act)
                                        if _m2:
                                            _tgt = _m2.group(1).strip()
                                    _code = _rw
                                elif _act:
                                    _m3 = _re.search(r'TARGET:\s*([^\n]+)', _act)
                                    if _m3:
                                        _tgt = _m3.group(1).strip()
                                    _cm = _re.search(r'```python\n(.*?)```', _act, _re.DOTALL)
                                    if _cm:
                                        _code = _cm.group(1).strip()
                                if _tgt and _code:
                                    _c.execute("""
                                        INSERT INTO code_patches
                                            (proposal_id, project_key, target_file, new_code,
                                             description, author_agent, status, created_at)
                                        VALUES (?,?,?,?,?,?,?,?)
                                    """, (
                                        pid,
                                        _prop["project_key"] or "unknown",
                                        _tgt, _code,
                                        f"Sealed by operator: proposal #{pid} "
                                        f"({_prop['proposal_type']})",
                                        "operator_seal",
                                        "pending",
                                        _t.time()
                                    ))
                            _c.commit()
                            _c.close()
                            st.success("Sealed and integrated. Patch queued for AXON.")
                        except Exception as e:
                            st.error(f"Integration failed: {e}")
                    else:
                        st.warning("Enter valid 6-digit seal code.")


def _render_nim_call_log(query_db) -> None:
    """NIM Call Log — live feed of specialist model usage."""
    import streamlit as st, html as _html

    C_PURPLE = "#9945FF"
    C_GREEN  = "#14F195"
    C_NVIDIA = "#76B900"

    MODEL_COLOURS = {
        "deepseek": "#4FC3F7",
        "nemotron": "#FF6B6B",
        "llama":    "#14F195",
        "mixtral":  "#FFD700",
        "mistral":  "#9945FF",
    }

    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.7rem;"
        f"letter-spacing:4px;color:{C_NVIDIA};margin:18px 0 8px;'>"
        f"⬡ NIM SPECIALIST LOG</div>",
        unsafe_allow_html=True,
    )

    try:
        rows = query_db("""
            SELECT ts, model, mode, reason, latency_ms, success, task_hash
            FROM nim_call_log
            ORDER BY ts DESC LIMIT 12
        """)
        log_rows = rows.to_dict('records') if not rows.empty else []
    except Exception:
        log_rows = []

    if not log_rows:
        st.markdown(
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
            f"color:#333;letter-spacing:2px;padding:6px 0;'>"
            f"// NIM LOG EMPTY — NO SPECIALIST CALLS YET //</div>",
            unsafe_allow_html=True,
        )
        return

    import time as _t
    now = _t.time()
    for r in log_rows:
        model    = str(r.get('model',''))
        mode     = str(r.get('mode',''))
        reason   = str(r.get('reason',''))[:40]
        lat      = float(r.get('latency_ms') or 0)
        ok       = bool(r.get('success',1))
        ts       = float(r.get('ts') or 0)
        age      = round((now - ts)/60, 1) if ts else '?'

        # Model colour by name
        mc = next((v for k,v in MODEL_COLOURS.items() if k in model.lower()), "#888")
        short_model = model.split('/')[-1][:20] if '/' in model else model[:20]

        st.markdown(
            f"<div style='display:flex;align-items:center;gap:10px;"
            f"padding:4px 8px;margin-bottom:2px;font-family:Share Tech Mono;"
            f"font-size:0.66rem;border-left:2px solid {mc}55;'>"
            f"<span style='color:{mc};flex:0 0 auto;'>{short_model}</span>"
            f"<span style='color:#555;flex:0 0 40px;'>[{mode[:5]}]</span>"
            f"<span style='color:#888;flex:1;overflow:hidden;text-overflow:ellipsis;"
            f"white-space:nowrap;'>{_html.escape(reason)}</span>"
            f"<span style='color:{C_GREEN if ok else '#FF073A'};flex:0 0 auto;'>"
            f"{'OK' if ok else 'ERR'}</span>"
            f"<span style='color:#444;flex:0 0 50px;text-align:right;'>{lat:.0f}ms</span>"
            f"<span style='color:#333;flex:0 0 40px;text-align:right;'>{age}m</span>"
            f"</div>",
            unsafe_allow_html=True,
        )



def _render_copy_trade_panel(query_db) -> None:
    """Render the Smart Money Observatory from canonical smart-wallet and copy-trade tables.

    This is an observatory, not an execution surface.  It reads persisted backend
    truth only and clearly separates roster coverage, observed trade history,
    convergence signals and paper-only influence.
    """
    import time as _t
    import json as _json

    now = _t.time()

    def _df(sql: str, params=()):
        try:
            result = query_db(sql, params)
            return result if result is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    def _num(frame: pd.DataFrame, column: str, default: float = 0.0) -> float:
        try:
            return float(frame.iloc[0][column] or 0) if not frame.empty and column in frame.columns else default
        except Exception:
            return default

    # Canonical backend truth.  Legacy watched_wallets is only a final fallback.
    roster = _df("""
        SELECT wallet_address, source_name, source_rank, realized_pnl, win_rate,
               total_trades, median_winner_x, hit_rate_2x, hit_rate_3x,
               hit_rate_5x, late_entry_failure_rate, rug_exposure_rate,
               last_seen, raw_json
        FROM smart_wallet_profiles
        ORDER BY COALESCE(source_rank,0) DESC, realized_pnl DESC
        LIMIT 12
    """)
    fingerprints = _df("""
        SELECT wallet_address, wallet_style, wallet_quality_score,
               copyability_score, median_safe_x, hit_rate_2x, hit_rate_3x,
               hit_rate_5x, late_copy_failure_rate, rug_exposure_rate, updated_at
        FROM wallet_entry_fingerprints
        ORDER BY copyability_score DESC, wallet_quality_score DESC
        LIMIT 50
    """)
    signals = _df("""
        SELECT token_mint, token_symbol, signal_time, matched_wallet_count,
               elite_wallet_count, wallet_entry_likelihood,
               copy_conviction_score, median_safe_x, copy_latency_risk,
               veto_reason, mode
        FROM wallet_entry_likelihood_signals
        ORDER BY signal_time DESC LIMIT 10
    """)
    recent_events = _df("""
        SELECT event_time, event_type, token_mint, message
        FROM smart_wallet_events ORDER BY event_time DESC LIMIT 8
    """)
    counts = _df("""
        SELECT
          (SELECT COUNT(*) FROM smart_wallet_profiles) AS profiles,
          (SELECT COUNT(*) FROM smart_wallet_profiles WHERE last_seen > ?) AS fresh_profiles,
          (SELECT COUNT(*) FROM smart_wallet_trades) AS wallet_trades,
          (SELECT COUNT(*) FROM wallet_entry_fingerprints) AS fingerprints,
          (SELECT COUNT(*) FROM wallet_entry_likelihood_signals) AS signals,
          (SELECT COUNT(*) FROM smart_wallet_events) AS events,
          (SELECT COUNT(*) FROM copytrade_influence_ledger) AS influence_rows
    """, (now - 86400,))

    profiles_n = int(_num(counts, "profiles"))
    fresh_n = int(_num(counts, "fresh_profiles"))
    trades_n = int(_num(counts, "wallet_trades"))
    fp_n = int(_num(counts, "fingerprints"))
    signals_n = int(_num(counts, "signals"))
    events_n = int(_num(counts, "events"))
    influence_n = int(_num(counts, "influence_rows"))

    # Source health is derived from canonical heartbeats, not UI assumptions.
    source_health = _df("""
        SELECT service_name, status, timestamp, note
        FROM system_heartbeat
        WHERE service_name IN (
          'wallet_scout','gmgn_wallet_roster_refresh','smart_wallet_trade_ingester',
          'copytrade_shadow_scanner','substrate_copytrade_bridge'
        )
        ORDER BY timestamp DESC
    """)
    healthy_sources = 0
    for _, row in source_health.iterrows() if not source_health.empty else []:
        age = now - float(row.get("timestamp") or 0)
        if age <= 600 and str(row.get("status") or "").upper() not in {"ERROR", "FAILED", "DEAD"}:
            healthy_sources += 1

    st.markdown(
        f"<div style='margin:22px 0 12px;padding:18px 20px;border:1px solid rgba(20,241,149,.28);"
        f"border-radius:16px;background:linear-gradient(135deg,rgba(20,241,149,.08),rgba(5,2,16,.88) 42%,rgba(153,69,255,.07));"
        f"box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 0 28px rgba(20,241,149,.05);'>"
        f"<div style='display:flex;justify-content:space-between;gap:12px;align-items:flex-end;flex-wrap:wrap;'>"
        f"<div><div style='font-family:Orbitron,sans-serif;font-size:1.05rem;letter-spacing:5px;color:{C_GREEN};'>"
        f"SMART MONEY OBSERVATORY</div><div style='font-family:Share Tech Mono;font-size:.66rem;letter-spacing:2px;"
        f"color:rgba(142,249,255,.58);margin-top:5px;'>SMART-MONEY OBSERVATORY · PAPER INFLUENCE ONLY</div></div>"
        f"<div>{_pill(f'{healthy_sources}/5 SOURCES HEALTHY', C_GREEN if healthy_sources >= 3 else C_GOLD)}"
        f"{_pill('LIVE INFLUENCE OFF', C_RED)}{_pill('COUNCIL SENSORY LANE', C_PURPLE)}</div>"
        f"</div></div>", unsafe_allow_html=True)

    # Command-truth strip.
    metric_cols = st.columns(6, gap="small")
    # SIGNOFF_OBSERVATORY_ORDER_20260718: metrics follow the sensory pipeline
    # (register -> freshness -> observed history -> learned models -> convergence
    # -> paper response) and the colour doctrine hierarchy: purple=structure,
    # green=alive/go, cyan=observed flow truth, blue=learning, gold=earned
    # convergence apex ONLY while signals exist, bronze=ledger substrate.
    C_BLUE = "#378ADD"
    metrics = [
        ("ROSTER", profiles_n, "ranked wallets", C_PURPLE),
        ("FRESH 24H", fresh_n, "recently observed", C_GREEN),
        ("TRADE HISTORY", trades_n, "wallet executions", C_CYAN),
        ("FINGERPRINTS", fp_n, "copyability models", C_BLUE),
        ("CONVERGENCE", signals_n, "entry signals", C_GOLD if signals_n > 0 else "#52606E"),
        ("PAPER INFLUENCE", influence_n, "ledger rows", C_NUGGET),
    ]
    for col, (label, value, sub, color) in zip(metric_cols, metrics):
        with col:
            st.markdown(
                f"<div style='min-height:94px;padding:11px 10px;border-radius:12px;border:1px solid {color}33;"
                f"background:rgba(5,2,16,.72);text-align:center;'>"
                f"<div style='font-family:Share Tech Mono;font-size:.56rem;letter-spacing:1.4px;color:{color};'>{label}</div>"
                f"<div style='font-family:Orbitron;font-size:1.35rem;color:#F1F7FF;margin:5px 0 2px;'>{value:,}</div>"
                f"<div style='font-family:Rajdhani;font-size:.65rem;color:#5E7082;'>{sub}</div></div>",
                unsafe_allow_html=True)

    left, right = st.columns([1.45, 1], gap="medium")
    with left:
        st.markdown(f"<div style='font-family:Share Tech Mono;font-size:.67rem;letter-spacing:2px;color:{C_GREEN};margin:13px 0 8px;'>APEX WALLET REGISTER · QUALITY / COPYABILITY</div>", unsafe_allow_html=True)
        if roster.empty:
            st.markdown("<div style='padding:24px;border:1px solid rgba(20,241,149,.14);border-radius:12px;color:#617080;font-family:Share Tech Mono;text-align:center;'>NO CANONICAL SMART-WALLET PROFILES YET</div>", unsafe_allow_html=True)
        else:
            fp_map = {}
            if not fingerprints.empty:
                fp_map = {str(r.get('wallet_address')): r for _, r in fingerprints.iterrows()}
            for _, w in roster.iterrows():
                addr = str(w.get("wallet_address") or "")
                raw = {}
                try: raw = _json.loads(str(w.get("raw_json") or "{}"))
                except Exception: raw = {}
                fp = fp_map.get(addr, {})
                wr = float(w.get("win_rate") or raw.get("winrate_7d") or raw.get("winrate_30d") or 0)
                tx = int(w.get("total_trades") or raw.get("txs_7d") or raw.get("txs_30d") or 0)
                pnl = float(w.get("realized_pnl") or raw.get("pnl_7d") or 0)
                copy = float(fp.get("copyability_score") or 0) if hasattr(fp, 'get') else 0
                quality = float(fp.get("wallet_quality_score") or 0) if hasattr(fp, 'get') else 0
                style = str(fp.get("wallet_style") or raw.get("tags") or "OBSERVED") if hasattr(fp, 'get') else str(raw.get("tags") or "OBSERVED")
                if isinstance(raw.get("tags"), list) and (not hasattr(fp,'get') or not fp.get("wallet_style")):
                    style = " · ".join(str(x).upper() for x in raw.get("tags", [])[:2]) or "OBSERVED"
                age_h = (now - float(w.get("last_seen") or 0)) / 3600 if float(w.get("last_seen") or 0) > 0 else 99999
                state_col = C_GREEN if age_h <= 24 else (C_GOLD if age_h <= 168 else "#52606E")
                display = addr[:6] + "…" + addr[-5:] if len(addr) > 14 else addr
                st.markdown(
                    f"<div style='display:grid;grid-template-columns:1.25fr .9fr .58fr .58fr .62fr;gap:8px;align-items:center;"
                    f"padding:8px 10px;margin-bottom:5px;border-radius:9px;border-left:2px solid {state_col};background:rgba(255,255,255,.018);'>"
                    f"<div><div style='font-family:Share Tech Mono;font-size:.65rem;color:#DCE8F4;'>{html.escape(display)}</div>"
                    f"<div style='font-family:Rajdhani;font-size:.61rem;color:#607286;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{html.escape(style[:28])}</div></div>"
                    f"<div><div style='font-family:Share Tech Mono;font-size:.58rem;color:{C_PURPLE};'>COPY {copy:.0f}</div>"
                    f"<div style='height:3px;background:#17202B;border-radius:4px;margin-top:4px;'><div style='height:3px;width:{max(0,min(100,copy)):.0f}%;background:{C_PURPLE};border-radius:4px;'></div></div></div>"
                    f"<div style='font-family:Orbitron;font-size:.66rem;color:{C_GREEN if wr >= .55 else C_GOLD};'>{wr:.0%}<br><span style='font-family:Rajdhani;color:#536171;font-size:.56rem;'>WR</span></div>"
                    f"<div style='font-family:Orbitron;font-size:.66rem;color:{C_CYAN};'>{tx:,}<br><span style='font-family:Rajdhani;color:#536171;font-size:.56rem;'>TX</span></div>"
                    f"<div style='font-family:Orbitron;font-size:.66rem;color:{C_GOLD if pnl >= 0 else C_RED};'>{pnl:+.2f}<br><span style='font-family:Rajdhani;color:#536171;font-size:.56rem;'>PNL</span></div>"
                    f"</div>", unsafe_allow_html=True)

    with right:
        st.markdown(f"<div style='font-family:Share Tech Mono;font-size:.67rem;letter-spacing:2px;color:{C_CYAN};margin:13px 0 8px;'>CONVERGENCE RADAR</div>", unsafe_allow_html=True)
        if signals.empty:
            st.markdown(
                f"<div style='padding:20px;border:1px solid rgba(142,249,255,.14);border-radius:12px;text-align:center;'>"
                f"<div style='font-family:Orbitron;font-size:.78rem;letter-spacing:2px;color:#526779;'>NO FRESH CONVERGENCE</div>"
                f"<div style='font-family:Rajdhani;font-size:.68rem;color:#425161;margin-top:6px;'>Roster exists; trade ingestion must produce wallet-entry signals.</div></div>",
                unsafe_allow_html=True)
        else:
            for _, sig in signals.head(6).iterrows():
                conv = float(sig.get("copy_conviction_score") or sig.get("wallet_entry_likelihood") or 0)
                if conv <= 1: conv *= 100
                mint = str(sig.get("token_symbol") or sig.get("token_mint") or "UNKNOWN")
                mint = mint if len(mint) <= 16 else mint[:7] + "…" + mint[-5:]
                age_m = max(0, (now - float(sig.get("signal_time") or 0))/60)
                veto = str(sig.get("veto_reason") or "")
                color = C_GREEN if conv >= 75 and not veto else (C_GOLD if conv >= 55 else C_RED)
                veto_html = (
                    f"<div style='font-family:Share Tech Mono;font-size:.54rem;color:{C_RED};margin-top:3px;'>"
                    f"VETO {html.escape(veto[:70])}</div>"
                    if veto else ""
                )
                st.markdown(
                    f"<div style='padding:8px 10px;margin-bottom:6px;border:1px solid {color}2A;border-radius:9px;background:{color}08;'>"
                    f"<div style='display:flex;justify-content:space-between;gap:8px;'><span style='font-family:Share Tech Mono;font-size:.64rem;color:#D7E7F5;'>{html.escape(mint)}</span>"
                    f"<span style='font-family:Orbitron;font-size:.62rem;color:{color};'>{conv:.0f}</span></div>"
                    f"<div style='font-family:Rajdhani;font-size:.61rem;color:#5A6D7E;margin-top:3px;'>{int(sig.get('elite_wallet_count') or 0)} elite · {int(sig.get('matched_wallet_count') or 0)} matched · {age_m:.0f}m · {html.escape(str(sig.get('mode') or 'OBSERVE'))}</div>"
                    f"{veto_html}</div>", unsafe_allow_html=True)

        st.markdown(f"<div style='font-family:Share Tech Mono;font-size:.67rem;letter-spacing:2px;color:{C_IVY};margin:14px 0 8px;'>SENSORY TAPE ({events_n})</div>", unsafe_allow_html=True)
        if recent_events.empty:
            st.markdown("<div style='font-family:Rajdhani;font-size:.68rem;color:#4B5A68;padding:9px 0;'>No persisted wallet events.</div>", unsafe_allow_html=True)
        else:
            for _, ev in recent_events.head(5).iterrows():
                msg = _safe_display(ev.get("message"), 88)
                age_m = max(0, (now-float(ev.get("event_time") or 0))/60)
                st.markdown(f"<div style='border-left:2px solid {C_IVY}55;padding:5px 8px;margin-bottom:5px;'>"
                            f"<div style='font-family:Share Tech Mono;font-size:.56rem;color:{C_IVY};'>{html.escape(str(ev.get('event_type') or 'EVENT').upper())} · {age_m:.0f}m</div>"
                            f"<div style='font-family:Rajdhani;font-size:.66rem;color:#778899;'>{msg}</div></div>", unsafe_allow_html=True)

    # Operator truth: explain exactly why a roster can be populated while signals remain zero.
    if profiles_n > 0 and trades_n == 0:
        st.markdown(
            f"<div style='margin-top:10px;padding:10px 13px;border-radius:9px;border:1px solid {C_GOLD}33;background:{C_GOLD}08;"
            f"font-family:Share Tech Mono;font-size:.6rem;color:{C_GOLD};'>"
            f"ROSTER RESTORED ({profiles_n}) · TRADE HISTORY EMPTY — WALLET INGESTER HAS NOT YET MATERIALISED PER-TRADE OBSERVATIONS. "
            f"OBSERVATORY REMAINS OBSERVE-ONLY UNTIL CONVERGENCE SIGNALS ARE PERSISTED.</div>", unsafe_allow_html=True)

def _first_int(df: pd.DataFrame, default: int = 0) -> int:
    """Safely read the first scalar from a query result."""
    try:
        if df is None or df.empty:
            return default
        return int(float(df.iloc[0, 0] or 0))
    except Exception:
        return default


def _first_str(df: pd.DataFrame, default: str = "") -> str:
    """Safely read the first scalar as text from a query result."""
    try:
        if df is None or df.empty:
            return default
        return str(df.iloc[0, 0] or default)
    except Exception:
        return default


def _render_genesis_macro_map(query_db) -> None:
    """
    GENESIS TIMELINE — the Machine Realm macro-map.

    Tracks the council's multi-day autonomous build mission using live DB
    heuristics. This is intentionally strategic/macro-scale. The main hub keeps
    the operational micro-map for individual proposals and patch lifecycles.
    """
    # ── live data heuristics; never hardcode fake progress ───────────────────
    p1_docs = _first_int(_qdb(
        query_db,
        "SELECT COUNT(*) FROM intelligence_forge WHERE COALESCE(doc_type,'research')='research'"
    ))
    p2_debates = _first_int(_qdb(
        query_db,
        "SELECT COUNT(*) FROM debate_log WHERE UPPER(COALESCE(speaker,'')) IN ('GROK','IVARIS')"
    ))
    if p2_debates == 0:
        p2_debates = _first_int(_qdb(query_db, "SELECT COUNT(*) FROM debate_log"))

    p3_proofs = _first_int(_qdb(query_db, "SELECT COUNT(*) FROM nim_call_log"))
    p4_sims = _first_int(_qdb(query_db, "SELECT COUNT(*) FROM paper_executions"))

    trading_mode = _first_str(_qdb(
        query_db,
        "SELECT value FROM system_config WHERE key='TRADING_MODE' LIMIT 1"
    ), "paper").strip().lower()
    is_live = trading_mode == "live"

    blocker_msg = _first_str(_qdb(
        query_db,
        "SELECT message FROM cognition_log WHERE stage IN ('BUILD_BLOCKER','OPERATOR_NEEDED','HITL_REQUIRED') ORDER BY COALESCE(ts,timestamp,0) DESC LIMIT 1"
    ), "")

    # Conservative phase thresholds. These show real motion without pretending
    # full readiness too early.
    p1_pct = min(100, int((p1_docs / 10) * 100))
    p2_pct = min(100, int((p2_debates / 15) * 100)) if p1_pct >= 25 else 0
    p3_pct = min(100, int((p3_proofs / 20) * 100)) if p2_pct >= 25 else 0
    p4_pct = min(100, int((p4_sims / 50) * 100)) if p3_pct >= 25 else 0
    p5_pct = 100 if is_live else (35 if p4_pct >= 50 else 0)

    phase_pcts = [p1_pct, p2_pct, p3_pct, p4_pct, p5_pct]
    total_pct = sum(phase_pcts) / max(1, len(phase_pcts))

    # Active phase is the first phase not complete. If a blocker exists, it pins
    # onto the active node and tells the operator where to respond.
    active_idx = next((i for i, pct in enumerate(phase_pcts) if pct < 100), len(phase_pcts) - 1)
    eta_hours = max(1, int(round((100 - total_pct) / 100 * 72))) if total_pct < 100 else 0
    if eta_hours >= 48:
        eta_text = f"{eta_hours // 24}d {eta_hours % 24}h"
    elif eta_hours > 0:
        eta_text = f"{eta_hours}h"
    else:
        eta_text = "READY"

    phases = [
        {
            "name": "INGESTION & SCRAPING",
            "pct": p1_pct,
            "desc": "X, GitHub, Telegram, OpenClaw public-evidence research intake",
            "agents": "ORACLE / GROK / POLARIS",
            "metric": f"{p1_docs} research docs",
        },
        {
            "name": "ARCHITECTURAL DEBATE",
            "pct": p2_pct,
            "desc": "Market selection, strategy critique, IVARIS/GROK opposition",
            "agents": "IVARIS / GROK / POLARIS",
            "metric": f"{p2_debates} debate rounds",
        },
        {
            "name": "MATHEMATICAL PROOFING",
            "pct": p3_pct,
            "desc": "NIM/Axiom logic generation, edge model, risk doctrine",
            "agents": "AXIOM / NIM",
            "metric": f"{p3_proofs} specialist calls",
        },
        {
            "name": "PAPER SIMULATION",
            "pct": p4_pct,
            "desc": "Backtest, replay validation, paper execution proving ground",
            "agents": "AXON / RHIZA / NUGGET",
            "metric": f"{p4_sims} paper executions",
        },
        {
            "name": "SOVEREIGN DEPLOYMENT",
            "pct": p5_pct,
            "desc": "Live-readiness, approval gate, capital authorization",
            "agents": "GOVERNOR / OPERATOR",
            "metric": f"mode: {trading_mode.upper() or 'PAPER'}",
        },
    ]

    st.markdown(
        """
        <style>
        @keyframes genPulseBlock {
            0% { box-shadow: 0 0 10px rgba(255,69,0,.65), inset 0 0 8px rgba(255,69,0,.12); }
            100% { box-shadow: 0 0 34px rgba(255,69,0,.95), inset 0 0 18px rgba(255,69,0,.32); }
        }
        @keyframes genFlow {
            0% { background-position: 0% 50%; }
            100% { background-position: 200% 50%; }
        }
        @keyframes genBreathe {
            0%,100% { filter: drop-shadow(0 0 8px rgba(255,0,255,.25)); }
            50% { filter: drop-shadow(0 0 18px rgba(0,255,102,.25)); }
        }
        .gen-wrap {
            background:
                radial-gradient(circle at 12% 0%, rgba(89,0,255,.20), transparent 34%),
                radial-gradient(circle at 100% 12%, rgba(255,0,255,.12), transparent 30%),
                linear-gradient(180deg, rgba(2,0,5,.98), rgba(5,2,16,.92));
            border: 1px solid rgba(89,0,255,.65);
            border-radius: 18px;
            padding: 22px 22px 18px;
            margin: 0 0 22px;
            box-shadow: 0 0 40px rgba(89,0,255,.16), inset 0 0 28px rgba(255,0,255,.045);
            overflow: hidden;
        }
        .gen-head {
            display:flex;
            justify-content:space-between;
            align-items:flex-end;
            gap:16px;
            padding-bottom:14px;
            border-bottom:1px solid rgba(255,0,255,.26);
            margin-bottom:18px;
        }
        .gen-title {
            font-family: Orbitron, sans-serif;
            font-size: 1.05rem;
            letter-spacing: 6px;
            color: #FF00FF;
            text-shadow: 0 0 16px rgba(255,0,255,.72), 0 0 34px rgba(89,0,255,.42);
            animation: genBreathe 4s ease-in-out infinite;
        }
        .gen-sub {
            font-family: Share Tech Mono, monospace;
            font-size: 0.66rem;
            letter-spacing: 2px;
            color: rgba(255,255,255,.42);
            margin-top: 7px;
        }
        .gen-eta {
            font-family: Share Tech Mono, monospace;
            font-size: .68rem;
            letter-spacing: 2px;
            color:#00FF66;
            border:1px solid rgba(0,255,102,.30);
            border-radius:999px;
            padding:6px 10px;
            background:rgba(0,255,102,.06);
            white-space:nowrap;
        }
        .gen-cascade {
            display:grid;
            grid-template-columns: 1fr;
            gap: 11px;
        }
        .gen-row {
            display:flex;
            align-items:stretch;
            gap:10px;
            position:relative;
        }
        .gen-num {
            flex:0 0 42px;
            font-family: Share Tech Mono, monospace;
            color:#5900FF;
            font-size:0.66rem;
            letter-spacing:2px;
            padding-top:12px;
            text-align:right;
        }
        .gen-card {
            flex:1;
            border:1px solid rgba(89,0,255,.38);
            border-radius:12px;
            padding:11px 13px 12px;
            background:rgba(89,0,255,.055);
            position:relative;
            overflow:hidden;
        }
        .gen-card::before {
            content:"";
            position:absolute;
            top:0; left:-60%;
            width:80%; height:100%;
            background:linear-gradient(90deg, transparent, rgba(255,0,255,.10), transparent);
            opacity:.45;
            animation: genFlow 5s linear infinite;
        }
        .gen-card.done {
            border-color:rgba(0,255,102,.55);
            background:rgba(0,255,102,.055);
        }
        .gen-card.active {
            border-color:rgba(255,0,255,.80);
            background:rgba(255,0,255,.075);
            box-shadow:0 0 20px rgba(255,0,255,.18);
        }
        .gen-card.blocked {
            border-color:#FF4500;
            background:rgba(255,69,0,.13);
            animation: genPulseBlock 1.25s ease-in-out infinite alternate;
        }
        .gen-topline {
            display:flex;
            justify-content:space-between;
            align-items:center;
            gap:12px;
            position:relative;
            z-index:1;
        }
        .gen-name {
            font-family:Share Tech Mono, monospace;
            font-size:.72rem;
            letter-spacing:2px;
            color:#DDD;
            font-weight:700;
        }
        .gen-agent {
            font-family:Share Tech Mono, monospace;
            font-size:0.66rem;
            letter-spacing:1px;
            color:rgba(255,255,255,.34);
            white-space:nowrap;
        }
        .gen-desc, .gen-metric {
            position:relative;
            z-index:1;
            font-family:Rajdhani, sans-serif;
            font-size:.72rem;
            line-height:1.35;
            color:rgba(255,255,255,.62);
            margin-top:4px;
        }
        .gen-metric {
            font-family:Share Tech Mono, monospace;
            font-size:0.66rem;
            letter-spacing:1px;
            color:rgba(142,249,255,.48);
        }
        .gen-track {
            position:relative;
            z-index:1;
            height:5px;
            background:rgba(255,255,255,.07);
            border-radius:999px;
            overflow:hidden;
            margin-top:9px;
        }
        .gen-fill {
            height:100%;
            border-radius:999px;
            background:linear-gradient(90deg, #5900FF, #FF00FF, #00FF66);
            background-size:200% auto;
            animation:genFlow 2.4s linear infinite;
            box-shadow:0 0 12px rgba(255,0,255,.55);
        }
        .gen-fill.done {
            background:#00FF66;
            box-shadow:0 0 12px rgba(0,255,102,.65);
        }
        .gen-blocker {
            position:relative;
            z-index:1;
            margin-top:10px;
            padding:8px 10px;
            border-radius:7px;
            background:#FF4500;
            color:#020005;
            font-family:Share Tech Mono, monospace;
            font-size:0.66rem;
            letter-spacing:1px;
            font-weight:800;
            line-height:1.35;
        }
        @media (max-width: 760px) {
            .gen-wrap { padding:16px 12px; }
            .gen-head { flex-direction:column; align-items:flex-start; }
            .gen-title { font-size:.86rem; letter-spacing:4px; }
            .gen-row { margin-left:0 !important; }
            .gen-num { flex-basis:34px; font-size:0.66rem; }
            .gen-topline { flex-direction:column; align-items:flex-start; gap:3px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    rows_html = []
    for i, ph in enumerate(phases):
        pct = max(0, min(100, int(ph["pct"])))
        is_done = pct >= 100
        is_active = i == active_idx and not is_done
        is_blocked = bool(blocker_msg) and is_active
        cls = "blocked" if is_blocked else ("done" if is_done else ("active" if is_active else ""))
        accent = "#FF4500" if is_blocked else ("#00FF66" if is_done else ("#FF00FF" if is_active else "#BBBBBB"))
        margin = min(i * 24, 96)
        blocker_html = ""
        if is_blocked:
            blocker_html = (
                "<div class='gen-blocker'>⚠️ WAITING ON OPERATOR INPUT: "
                + html.escape(blocker_msg[:160])
                + "<br>→ Submit required data via the OPERATOR PETITION MEMBRANE below.</div>"
            )
        rows_html.append(
            "<div class='gen-row' style='margin-left:{margin}px;'>"
            "<div class='gen-num'>PH {num}</div>"
            "<div class='gen-card {cls}'>"
            "<div class='gen-topline'>"
            "<div class='gen-name' style='color:{accent};'>{name}</div>"
            "<div class='gen-agent'>{agents}</div>"
            "</div>"
            "<div class='gen-desc'>{desc}</div>"
            "<div class='gen-metric'>{metric} · {pct}%</div>"
            "<div class='gen-track'><div class='gen-fill {done_cls}' style='width:{pct}%;'></div></div>"
            "{blocker}"
            "</div></div>".format(
                margin=margin,
                num=i + 1,
                cls=cls,
                accent=accent,
                name=html.escape(ph["name"]),
                agents=html.escape(ph["agents"]),
                desc=html.escape(ph["desc"]),
                metric=html.escape(ph["metric"]),
                pct=pct,
                done_cls="done" if is_done else "",
                blocker=blocker_html,
            )
        )

    st.markdown(
        "<div class='gen-wrap'>"
        "<div class='gen-head'>"
        "<div>"
        "<div class='gen-title'>◈ GENESIS MACRO-MAP</div>"
        "<div class='gen-sub'>MACHINE REALM · MULTI-DAY AUTONOMOUS BUILD TIMELINE · DB-DERIVED</div>"
        "</div>"
        "<div class='gen-eta'>EST. PAPER READINESS: " + html.escape(eta_text) + "</div>"
        "</div>"
        "<div class='gen-cascade'>" + "".join(rows_html) + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════
# EDGE CANDIDATE ARENA — Council's sovereign market interface forge
# Replaces "Copy Trade Intelligence" as centrepiece
# Each candidate = one possible profitable machine the council may build
# ══════════════════════════════════════════════════════════════


def _render_edge_candidate_arena(proposals_df: pd.DataFrame, query_db) -> None:
    """
    Edge Candidate Arena — the Council's all-out sovereign forge.
    Represents new profit species the Council is researching.
    Highest heat candidate gets gold glow.
    Heat = proposal_count × average_confidence.
    """
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:.75rem;"
        f"letter-spacing:4px;color:{C_GOLD};margin:18px 0 8px;'>"
        f"◈ EDGE CANDIDATE ARENA — PROFIT SPECIES FORGE</div>",
        unsafe_allow_html=True
    )
    st.markdown(
        f"<div style='font-family:Rajdhani;font-size:.78rem;"
        f"color:rgba(255,255,255,0.35);margin-bottom:14px;'>"
        f"The Council researches every possible intelligence lane. "
        f"Evidence decides which machine gets built.</div>",
        unsafe_allow_html=True
    )

    candidates = [
        {"name": "EVENT INTELLIGENCE NEXUS", "icon": "📡", "lane": ["event","news","regulatory","onchain","intelligence"],
         "desc": "Non-wagering evidence engine: public news, regulatory, macro and on-chain events scored for relevance with provenance and uncertainty. Research and paper strategies only.", "color": "#9945FF"},
        {"name": "QUANT / GRID ALPHA",    "icon": "⚡", "lane": ["quant","grid","systematic","mathematical"],
         "desc": "Systematic momentum extraction via grid logic.",  "color": "#FFD700"},
        {"name": "WALLET LINEAGE HUNTER", "icon": "👁️", "lane": ["wallet","lineage","sybil","mule","cluster"],
         "desc": "Sybil detection, funding-origin clustering.",    "color": "#14F195"},
        {"name": "POST-GRAD ENGINE",      "icon": "🚀", "lane": ["graduation","post-grad","continuation","dex"],
         "desc": "Post-bonding-curve momentum continuation.",      "color": "#8EF9FF"},
        {"name": "PAID BOOST TRAP",       "icon": "🪤", "lane": ["boost","paid","kol","influencer","trap"],
         "desc": "Detect predatory retail trending & KOL traps.", "color": "#FF6B35"},
        {"name": "MOMENTUM CONVERGENCE",  "icon": "🎯", "lane": ["convergence","momentum","attention","signal"],
         "desc": "Independent attention alignment scoring.",       "color": "#C0C0C0"},
    ]

    # Calculate heat from real proposals
    heats = {}
    if not proposals_df.empty and "proposal_text" in proposals_df.columns:
        for c in candidates:
            matches = proposals_df[
                proposals_df["proposal_text"].str.lower().str.contains(
                    '|'.join(c["lane"]), na=False
                )
            ]
            cnt = len(matches)
            avg_conf = float(matches["confidence"].dropna().astype(float).mean()) if cnt > 0 else 0.0
            heats[c["name"]] = round(cnt * avg_conf, 2)
    else:
        heats = {c["name"]: 0.0 for c in candidates}

    top_name = max(heats, key=heats.get) if any(v > 0 for v in heats.values()) else None

    cols = st.columns(3)
    for i, cand in enumerate(candidates):
        heat = heats.get(cand["name"], 0.0)
        is_top = cand["name"] == top_name and heat > 0
        col = cand["color"]
        heat_pct = min(100, int(heat * 15))

        border = (
            f"border:2px solid {C_GOLD};box-shadow:0 0 20px {C_GOLD}55,0 0 40px {C_GOLD}22;"
            if is_top else
            f"border:1px solid {col}33;"
        )

        top_badge = (
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
            f"color:{C_GOLD};letter-spacing:1px;margin-bottom:4px;'>⭐ HIGHEST HEAT</div>"
            if is_top else ""
        )

        with cols[i % 3]:
            st.markdown(
                f"<div style='{border}border-radius:12px;padding:12px;"
                f"margin-bottom:10px;background:rgba(5,2,16,0.85);'>"
                f"{top_badge}"
                f"<div style='font-size:1.1rem;margin-bottom:4px;'>{cand['icon']}</div>"
                f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
                f"letter-spacing:1px;color:{col};margin-bottom:5px;'>{cand['name']}</div>"
                f"<div style='font-family:Rajdhani;font-size:.75rem;"
                f"color:rgba(255,255,255,0.55);line-height:1.4;margin-bottom:8px;"
                f"height:36px;overflow:hidden;'>{cand['desc']}</div>"
                f"<div style='height:2px;background:rgba(255,255,255,0.05);"
                f"border-radius:1px;margin-bottom:6px;'>"
                f"<div style='width:{heat_pct}%;height:100%;background:{col};"
                f"border-radius:1px;'></div></div>"
                f"<div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;color:{col};'>"
                f"HEAT {heat:.1f}</span>"
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;"
                f"color:rgba(255,255,255,0.2);'>{'RESEARCHING' if heat > 0 else 'UNEXPLORED'}</span>"
                f"</div></div>",
                unsafe_allow_html=True
            )


def _render_specialist_routing_matrix(query_db) -> None:
    """Shows which model handled which task and why."""
    st.markdown(
        "<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
        "letter-spacing:3px;color:#9945FF;padding:10px 0 4px 0;'>"
        "⚡ SPECIALIST ROUTING MATRIX</div>",
        unsafe_allow_html=True
    )

    try:
        df = _qdb(query_db, """
            SELECT model, mode, reason, latency_ms, success, ts
            FROM nim_call_log
            ORDER BY ts DESC LIMIT 20
        """)
        if df.empty:
            st.markdown(
                "<div style='font-family:Share Tech Mono;font-size:0.66rem;"
                "color:#333;padding:8px 0;'>// NO NIM ROUTING RECORDED YET //</div>",
                unsafe_allow_html=True
            )
            return

        for _, row in df.iterrows():
            model = str(row.get("model", ""))
            mode = str(row.get("mode", "CODE_FIRST"))
            reason = str(row.get("reason", ""))[:60]
            latency = float(row.get("latency_ms", 0) or 0)
            success = int(row.get("success", 1))
            ok_col = "#14F195" if success else "#FF4444"
            mode_col = {
                "RESEARCH_FIRST": "#8EF9FF",
                "DESIGN_FIRST": "#FFD700",
                "AUDIT_FIRST": "#FF6B35",
                "CODE_FIRST": "#9945FF",
            }.get(mode, "#888")

            # Short model label
            model_short = model.split("/")[-1][:25] if "/" in model else model[:25]

            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;"
                f"padding:5px 8px;margin-bottom:4px;"
                f"border-left:2px solid {ok_col};"
                f"background:rgba(5,2,16,0.6);border-radius:4px;'>"
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;"
                f"color:{ok_col};'>{'✓' if success else '✗'}</span>"
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;"
                f"color:rgba(255,255,255,0.8);flex:1;'>{model_short}</span>"
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;"
                f"color:{mode_col};'>{mode}</span>"
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;"
                f"color:rgba(255,255,255,0.3);'>{latency:.0f}ms</span>"
                f"</div>",
                unsafe_allow_html=True
            )
            if reason:
                st.markdown(
                    f"<div style='font-family:Rajdhani;font-size:0.72rem;"
                    f"color:rgba(255,255,255,0.4);padding:0 8px 4px 18px;'>"
                    f"{html.escape(reason)}</div>",
                    unsafe_allow_html=True
                )
    except Exception as e:
        st.markdown(
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
            f"color:#555;'>// ROUTING MATRIX OFFLINE: {str(e)[:60]} //</div>",
            unsafe_allow_html=True
        )


# ══════════════════════════════════════════════════════════════
# LIVING PULSE — DB-derived organism vitality indicators
# Every pulse corresponds to real DB activity, never faked
# ══════════════════════════════════════════════════════════════

def _render_living_pulse(query_db, proposals_df: pd.DataFrame, debate_df: pd.DataFrame) -> None:
    """Living pulse — all values derived from real DB state."""
    import time as _time

    now = _time.time()

    # Proposal velocity — proposals created in last hour
    prop_velocity = 0
    if not proposals_df.empty and "created_at" in proposals_df.columns:
        prop_velocity = int((proposals_df["created_at"].astype(float) > now - 3600).sum())

    # Debate pressure — debate entries in last 10 min
    debate_pressure = 0
    if not debate_df.empty and "logged_at" in debate_df.columns:
        debate_pressure = int((debate_df["logged_at"].astype(float) > now - 600).sum())

    # Consensus convergence — approved / total proposals ratio
    total_props = max(1, len(proposals_df))
    approved = len(proposals_df[proposals_df["status"].astype(str).isin(["approved","applied"])]) if not proposals_df.empty else 0
    consensus_pct = min(100, int((approved / total_props) * 100))

    # Research freshness — newest proposal age
    newest_age = 9999
    if not proposals_df.empty and "created_at" in proposals_df.columns:
        newest_age = int(now - float(proposals_df["created_at"].max() or 0))

    # Background daemon activity — heartbeats in last 2 min
    daemon_active = 0
    try:
        hb = _qdb(query_db, f"SELECT COUNT(*) n FROM system_heartbeat WHERE last_pulse > {now-120}")
        daemon_active = int(hb.iloc[0]["n"]) if not hb.empty else 0
    except Exception:
        pass

    # Cognition throughput — cognition_log entries in last 5 min
    cognition_rate = 0
    try:
        cog = _qdb(query_db, f"SELECT COUNT(*) n FROM cognition_log WHERE CAST(timestamp AS REAL) > {now-300}")
        cognition_rate = int(cog.iloc[0]["n"]) if not cog.empty else 0
    except Exception:
        pass

    st.markdown(
        "<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
        "letter-spacing:3px;color:#14F195;padding:10px 0 4px 0;'>"
        "❤ LIVING PULSE — ORGANISM VITALITY</div>",
        unsafe_allow_html=True
    )

    def _pulse_bar(label: str, value: int, max_val: int, color: str, unit: str = "") -> str:
        pct = min(100, int((value / max(1, max_val)) * 100))
        return (
            f"<div style='margin-bottom:8px;'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"font-family:Share Tech Mono;font-size:0.66rem;margin-bottom:3px;'>"
            f"<span style='color:rgba(255,255,255,0.6);'>{label}</span>"
            f"<span style='color:{color};'>{value}{unit}</span></div>"
            f"<div style='height:3px;background:rgba(255,255,255,0.08);border-radius:2px;'>"
            f"<div style='width:{pct}%;height:100%;background:{color};"
            f"border-radius:2px;transition:width 0.3s;'></div></div></div>"
        )

    freshness_label = f"{newest_age}s ago" if newest_age < 9999 else "none"
    stale_warn = " ⚠" if daemon_active < 3 else ""

    # Council Resonance — moving average of recent proposal confidence
    council_resonance = 0
    if not proposals_df.empty and "confidence" in proposals_df.columns:
        recent_confs = proposals_df["confidence"].dropna().astype(float).tail(10)
        council_resonance = int(recent_confs.mean() * 100) if len(recent_confs) > 0 else 0

    pulse_html = (
        "<div style='border:1px solid rgba(20,241,149,0.15);border-radius:10px;"
        "padding:14px 16px;background:rgba(5,2,16,0.7);margin-bottom:12px;"
        "animation:none;'>"
        + _pulse_bar("PROPOSAL VELOCITY", prop_velocity, 10, "#8EF9FF", "/hr")
        + _pulse_bar("DEBATE PRESSURE", debate_pressure, 30, "#9945FF", " turns")
        + _pulse_bar("CONSENSUS CONVERGENCE", consensus_pct, 100, "#14F195", "%")
        + _pulse_bar("COGNITION THROUGHPUT", cognition_rate, 50, "#FFD700", "/5m")
        + _pulse_bar("DAEMON ACTIVITY", daemon_active, 15, "#FF6B35", f" active{stale_warn}")
        + _pulse_bar("COUNCIL RESONANCE", council_resonance, 100, "#9945FF", "%")
        + f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
        f"color:rgba(255,255,255,0.3);margin-top:6px;'>"
        f"RESEARCH FRESHNESS: {freshness_label}</div>"
        + "</div>"
    )
    st.markdown(pulse_html, unsafe_allow_html=True)




def _render_legal_alpha_banner() -> None:
    """LEGAL MODE banner — always shown at top of intelligence tab."""
    st.markdown(
        f"<div style='background:linear-gradient(90deg,rgba(20,241,149,0.06),rgba(153,69,255,0.04),transparent);"
        f"border:1px solid rgba(20,241,149,0.25);border-radius:8px;padding:8px 16px;margin-bottom:14px;"
        f"display:flex;justify-content:space-between;align-items:center;'>"
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;"
        f"color:#14F195;'>⬡ LEGAL MODE ACTIVE</span>"
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;"
        f"color:rgba(20,241,149,0.6);'>Prediction-market &amp; wagering integrations disabled · "
        f"Spot-market intelligence only · AU-compliant</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_legal_event_alpha_tab(query_db) -> None:
    """
    LEGAL EVENT-ALPHA — spot-market intelligence for lawful early token discovery.
    No prediction markets. No event contracts. No wagering.
    Lanes: Alpha Radar, Fresh Tokens, Liquidity, Holder/Whale Flow,
           Social Velocity, Risk Wall, Paper Replay Grade, Live Eligibility Gate.
    """
    import sqlite3 as _sq3
    import time as _time
    import math as _math

    now = _time.time()

    # ── Section header ────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='border:1px solid rgba(153,69,255,0.4);border-radius:14px;"
        f"padding:16px 20px 12px;margin:16px 0 14px;"
        f"background:radial-gradient(ellipse at 0% 0%,rgba(153,69,255,0.07),transparent 55%);"
        f"box-shadow:0 0 30px rgba(153,69,255,0.06);'>"
        f"<div style='font-family:Orbitron,sans-serif;font-size:1rem;letter-spacing:5px;"
        f"color:#9945FF;text-shadow:0 0 16px rgba(153,69,255,0.6);'>"
        f"◈ LEGAL EVENT-ALPHA</div>"
        f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;"
        f"color:rgba(142,249,255,0.5);margin-top:5px;'>"
        f"EARLY TOKEN DISCOVERY · MOMENTUM CONFIRMATION · LIQUIDITY QUALITY · WHALE FLOW · "
        f"SOCIAL VELOCITY · RUG AVOIDANCE · PAPER-FIRST · COMPLIANT EXECUTION ONLY</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── Fetch candidates from DB ──────────────────────────────────────────────
    candidates = []
    try:
        try:
            _db_path = DB_PATH
        except NameError:
            _db_path = Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"

        conn = _sq3.connect(str(_db_path), timeout=3)
        conn.row_factory = _sq3.Row

        # Fetch recent qualified candidates that are still fresh
        rows = conn.execute("""
            SELECT
                mint_address, token_name,
                COALESCE(candidate_state,'pending') as candidate_state,
                COALESCE(confidence,0) as confidence,
                COALESCE(mint_confidence,0) as mint_confidence,
                COALESCE(liquidity_usd,0) as liquidity_usd,
                COALESCE(volume_1m_usd,0) as volume_1m_usd,
                COALESCE(volume_5m_usd,0) as volume_5m_usd,
                COALESCE(price_usd,0) as price_usd,
                COALESCE(price_change_5m_pct,0) as price_change_5m_pct,
                COALESCE(holder_count,0) as holder_count,
                COALESCE(token_age_seconds,0) as token_age_seconds,
                COALESCE(signal_age_seconds,0) as signal_age_seconds,
                COALESCE(price_age_seconds,0) as price_age_seconds,
                COALESCE(slippage_pct,0) as slippage_pct,
                COALESCE(is_tradeable,0) as is_tradeable,
                COALESCE(latched,0) as latched,
                COALESCE(execution_ready,0) as execution_ready,
                COALESCE(quality_status,'pending') as quality_status,
                COALESCE(runner_tier,'') as runner_tier,
                COALESCE(created_at,0) as created_at,
                COALESCE(updated_at,0) as updated_at
            FROM market_snapshots
            WHERE candidate_state NOT IN ('vetoed','exited','expired_stale','executed')
              AND COALESCE(created_at, 0) > ?
            ORDER BY COALESCE(confidence,0) DESC, COALESCE(updated_at,0) DESC
            LIMIT 20
        """, (now - 3600,)).fetchall()  # last hour only

        for r in rows:
            candidates.append(dict(r))
        conn.close()
    except Exception as _e:
        st.markdown(
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;"
            f"color:rgba(255,7,58,0.6);padding:8px;'>"
            f"// ALPHA RADAR DB READ ERROR: {html.escape(str(_e)[:80])} //</div>",
            unsafe_allow_html=True,
        )

    # ── Inline alpha scoring (no external module dependency) ─────────────────
    def _inline_alpha_score(cand: dict) -> dict:
        """
        Lightweight inline alpha score. Self-contained. No side modules.
        Returns dict with: score (0-1), rug_safety (0-1), live_eligible (bool),
        blocking (list[str]), why_live (list[str]).
        """
        import math as _math
        liq = float(cand.get("liquidity_usd") or 0)
        vol5m = float(cand.get("volume_5m_usd") or 0)
        conf = float(cand.get("confidence") or cand.get("mint_confidence") or 0)
        tok_age = float(cand.get("token_age_seconds") or 0)
        sig_age = float(cand.get("signal_age_seconds") or 0)
        price_age = float(cand.get("price_age_seconds") or 0)
        px = float(cand.get("price_usd") or 0)
        hc = int(cand.get("holder_count") or 0)
        slip = float(cand.get("slippage_pct") or 0)
        top10 = float(cand.get("top10_holder_pct") or 0)
        has_mint_auth = bool(cand.get("has_mint_authority", True))
        is_rug = bool(cand.get("known_rug_pattern") or cand.get("is_rug_risk"))
        honeypot = bool(cand.get("honeypot_risk"))

        # Freshness (0-1)
        if tok_age < 60: fresh = 1.0
        elif tok_age < 300: fresh = 0.85
        elif tok_age < 1800: fresh = 0.55
        else: fresh = max(0.05, 0.55 - (tok_age - 1800) / 7200)
        if sig_age > 300: fresh *= max(0.2, 1 - (sig_age - 300) / 900)

        # Liquidity (0-1)
        if liq <= 0: liq_s = 0.0
        else: liq_s = min(1.0, _math.log10(max(1, liq / 3000)) / _math.log10(70))

        # Volume (0-1)
        vol_s = min(1.0, _math.log10(max(1, vol5m / 100)) / 4.0) if vol5m > 0 else 0.0

        # Rug safety (inverted: 1=safe)
        rug_risk = 0.0
        if is_rug: rug_risk += 0.8
        if honeypot: rug_risk += 0.7
        if has_mint_auth: rug_risk += 0.25
        rug_s = max(0.0, 1.0 - rug_risk)

        # Holder distribution (1=good spread)
        hold_s = max(0.0, 1.0 - (top10 * 0.8)) if top10 > 0 else 0.5
        if hc > 200: hold_s = min(1.0, hold_s + 0.1)
        elif hc < 20: hold_s = max(0.0, hold_s - 0.15)

        # Slippage (1=good)
        slip_s = 1.0 if slip <= 0 else max(0.0, 1.0 - slip / 5.0)

        # Weighted final score
        score = (
            fresh   * 0.20 +
            liq_s   * 0.25 +
            vol_s   * 0.15 +
            rug_s   * 0.20 +
            hold_s  * 0.12 +
            slip_s  * 0.08
        )
        if conf > 0:
            score = score * 0.7 + conf * 0.3

        # Stale penalty
        if price_age > 120: score *= max(0.5, 1 - (price_age - 120) / 600)

        # Gates
        blocking = []
        why_live = []
        if score < 0.45: blocking.append(f"SCORE_LOW ({score:.2f})")
        else: why_live.append(f"score {score:.2f}")
        if liq < 3000: blocking.append(f"LIQ_LOW (${liq:.0f})")
        else: why_live.append("liquidity ok")
        if rug_s < 0.3: blocking.append("RUG_RISK_HIGH")
        else: why_live.append("rug ok")
        if is_rug or honeypot: blocking.append("DANGER_PATTERN")
        if px <= 0: blocking.append("NO_PRICE")
        if price_age > 120: blocking.append(f"STALE_PRICE ({price_age:.0f}s)")
        if conf > 0 and conf < 0.50: blocking.append(f"CONF_LOW ({conf:.2f})")
        elif conf >= 0.50: why_live.append(f"conf {conf:.2f}")

        return {
            "score": round(min(1.0, max(0.0, score)), 3),
            "rug_safety": round(rug_s, 3),
            "live_eligible": len(blocking) == 0,
            "blocking": blocking,
            "why_live": why_live,
        }

    score_results = {}
    for cand in candidates:
        mint = cand.get("mint_address", "")
        score_results[mint] = _inline_alpha_score(cand)

    # ── Alpha Radar header ────────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:0.7rem;"
        f"letter-spacing:4px;color:{C_GOLD};margin:14px 0 8px;'>"
        f"⬡ ALPHA RADAR — {len(candidates)} CANDIDATES TRACKED</div>",
        unsafe_allow_html=True,
    )

    if not candidates:
        st.markdown(
            f"<div style='border:1px dashed rgba(153,69,255,0.2);border-radius:10px;"
            f"padding:20px;text-align:center;'>"
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;letter-spacing:3px;"
            f"color:rgba(153,69,255,0.4);'>// ALPHA RADAR — NO FRESH CANDIDATES — ORGANISM HUNTING //</div>"
            f"<div style='font-family:Rajdhani;font-size:0.72rem;color:rgba(255,255,255,0.3);"
            f"margin-top:6px;'>Market intelligence services are scanning. Candidates surface as signals qualify.</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        return

    # ── Candidate cards ───────────────────────────────────────────────────────
    def _age_str(sec: float) -> str:
        if sec <= 0: return "?"
        if sec < 60: return f"{int(sec)}s"
        if sec < 3600: return f"{int(sec/60)}m"
        return f"{sec/3600:.1f}h"

    def _risk_badge(score: float | None, label: str) -> str:
        if score is None:
            return f"<span style='color:rgba(255,255,255,0.2);font-size:0.66rem;'>{label}: n/a</span>"
        if score >= 0.7:
            col, txt = C_GREEN, "LOW"
        elif score >= 0.45:
            col, txt = C_GOLD, "MED"
        else:
            col, txt = C_RED, "HIGH"
        return (
            f"<span style='font-family:Share Tech Mono;font-size:0.66rem;letter-spacing:1px;"
            f"color:{col};border:1px solid {col}44;border-radius:4px;padding:1px 5px;"
            f"background:{col}0A;'>{label}: {txt}</span> "
        )

    cols = st.columns(2)
    for idx, cand in enumerate(candidates[:12]):
        mint = str(cand.get("mint_address") or "")
        name = html.escape(str(cand.get("token_name") or "UNKNOWN")[:20])
        state = str(cand.get("candidate_state") or "pending").upper()
        conf = float(cand.get("confidence") or cand.get("mint_confidence") or 0)
        liq = float(cand.get("liquidity_usd") or 0)
        vol5m = float(cand.get("volume_5m_usd") or 0)
        px = float(cand.get("price_usd") or 0)
        px5 = float(cand.get("price_change_5m_pct") or 0)
        hc = int(cand.get("holder_count") or 0)
        tok_age = float(cand.get("token_age_seconds") or 0)
        sig_age = float(cand.get("signal_age_seconds") or 0)
        is_live_elig = bool(cand.get("is_tradeable") and cand.get("quality_status") == "qualified")
        runner_tier = str(cand.get("runner_tier") or "")

        # Score data from inline scorer (always a dict now)
        sr = score_results.get(mint, {})
        alpha_score = sr.get("score", conf)
        rug_safety = sr.get("rug_safety", None)
        live_eligible = sr.get("live_eligible", is_live_elig)
        blocking = sr.get("blocking", [])[:2]
        why_live = sr.get("why_live", [])[:2]

        # Card accent
        if live_eligible:
            card_border = f"border:2px solid rgba(20,241,149,0.6);box-shadow:0 0 16px rgba(20,241,149,0.08);"
            gate_badge = f"<span style='color:{C_GREEN};font-size:0.66rem;letter-spacing:1px;border:1px solid rgba(20,241,149,0.4);border-radius:4px;padding:2px 6px;background:rgba(20,241,149,0.06);'>LIVE ELIGIBLE</span>"
        elif blocking:
            card_border = f"border:1px solid rgba(255,7,58,0.3);"
            gate_badge = f"<span style='color:{C_RED};font-size:0.66rem;letter-spacing:1px;border:1px solid rgba(255,7,58,0.3);border-radius:4px;padding:2px 6px;background:rgba(255,7,58,0.06);'>PAPER ONLY</span>"
        else:
            card_border = f"border:1px solid rgba(153,69,255,0.3);"
            gate_badge = f"<span style='color:{C_PURPLE};font-size:0.66rem;letter-spacing:1px;border:1px solid rgba(153,69,255,0.3);border-radius:4px;padding:2px 6px;'>WATCHING</span>"

        # Alpha score bar width
        score_w = int(alpha_score * 100)
        score_col = C_GREEN if alpha_score >= 0.6 else (C_GOLD if alpha_score >= 0.4 else C_RED)

        # Price direction
        px5_col = C_GREEN if px5 > 0 else (C_RED if px5 < 0 else C_DIM)
        px5_str = f"{'+'if px5>0 else ''}{px5:.1f}%"

        # Short mint
        short_mint = (mint[:8] + "…" + mint[-6:]) if len(mint) > 16 else mint

        # Blocking gate reason (max 1)
        block_html = ""
        if blocking:
            block_html = (
                f"<div style='font-family:Share Tech Mono;font-size:0.66rem;letter-spacing:1px;"
                f"color:{C_RED};margin-top:3px;'>⛔ {html.escape(blocking[0][:50])}</div>"
            )
        elif why_live:
            block_html = (
                f"<div style='font-family:Share Tech Mono;font-size:0.66rem;letter-spacing:1px;"
                f"color:rgba(20,241,149,0.5);margin-top:3px;'>✓ {html.escape(why_live[0][:50])}</div>"
            )

        runner_html = ""
        if runner_tier:
            runner_html = (
                f"<span style='font-family:Share Tech Mono;font-size:0.66rem;color:{C_GOLD};"
                f"border:1px solid rgba(255,215,0,0.4);border-radius:4px;padding:1px 5px;"
                f"background:rgba(255,215,0,0.06);margin-left:6px;'>🏃 {html.escape(runner_tier)}</span>"
            )

        card_html = (
            f"<div style='{card_border}border-radius:12px;padding:12px 14px;"
            f"margin-bottom:10px;background:rgba(5,2,16,0.9);'>"
            # Header
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>"
            f"<div>"
            f"<span style='font-family:Share Tech Mono;font-size:0.68rem;color:#FFF;letter-spacing:1px;'>{name}</span>"
            f"{runner_html}"
            f"<div style='font-family:Share Tech Mono;font-size:0.66rem;color:rgba(255,255,255,0.2);"
            f"margin-top:2px;'>{html.escape(short_mint)}</div>"
            f"</div>"
            f"{gate_badge}"
            f"</div>"
            # Score bar
            f"<div style='height:3px;background:rgba(255,255,255,0.06);border-radius:2px;margin-bottom:8px;'>"
            f"<div style='width:{score_w}%;height:100%;background:{score_col};"
            f"border-radius:2px;box-shadow:0 0 6px {score_col}55;'></div></div>"
            # Metrics row
            f"<div style='display:flex;flex-wrap:wrap;gap:10px;font-family:Share Tech Mono;"
            f"font-size:0.66rem;color:rgba(255,255,255,0.5);margin-bottom:6px;'>"
            f"<span>SCORE <span style='color:{score_col};'>{alpha_score:.2f}</span></span>"
            f"<span>LIQ <span style='color:{C_CYAN};'>${liq:,.0f}</span></span>"
            f"<span>VOL5m <span style='color:{C_CYAN};'>${vol5m:,.0f}</span></span>"
            f"<span>5m <span style='color:{px5_col};'>{px5_str}</span></span>"
            f"<span>HLDR {hc}</span>"
            f"<span>AGE {_age_str(tok_age)}</span>"
            f"<span>SIG {_age_str(sig_age)}</span>"
            f"</div>"
            # Risk badges
            f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:4px;'>"
            f"{_risk_badge(rug_safety, 'RUG')}"
            f"</div>"
            f"{block_html}"
            f"</div>"
        )

        with cols[idx % 2]:
            st.markdown(card_html, unsafe_allow_html=True)

    # ── Source health footer ─────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
        f"letter-spacing:2px;color:rgba(255,255,255,0.18);margin-top:6px;'>"
        f"SOURCES: CHAIN/DEX/SPOT · NO PREDICTION MARKETS · NO EVENT CONTRACTS · "
        f"PAPER-FIRST GATING ENFORCED</div>",
        unsafe_allow_html=True,
    )


def _render_organism_pressure_core(query_db, proposals_df, debate_df) -> None:
    """
    Organism Pressure Core — enrichment pipeline vitals + supervisor health.
    All values DB-derived. Shows ENRICHMENT_BOTTLENECK when organism is blind.
    """
    import time as _t, sqlite3 as _sq3
    now = _t.time()

    # ── Live DB queries ────────────────────────────────────────────────────
    pending_qual = 0; mtm_pending = 0; mcap_missing = 0
    conf_missing = 0; supervisor_visible = 0; open_pos = 0; stale_count = 0

    try:
        _c = _sq3.connect(str(DB_PATH), timeout=3)
        _c.row_factory = _sq3.Row

        pending_qual = int((_c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE quality_status='pending' "
            "AND candidate_state='pending'"
        ).fetchone() or [0])[0])

        mtm_pending = int((_c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE candidate_state='mtm' "
            "AND quality_status='pending'"
        ).fetchone() or [0])[0])

        mcap_missing = int((_c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE quality_status='pending' "
            "AND (market_cap_usd IS NULL OR market_cap_usd=0)"
        ).fetchone() or [0])[0])

        conf_missing = int((_c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE quality_status='pending' "
            "AND (mint_confidence IS NULL OR mint_confidence=0)"
        ).fetchone() or [0])[0])

        supervisor_visible = int((_c.execute("""
            SELECT COUNT(*) FROM market_snapshots
            WHERE latched=0 AND execution_ready != 2
              AND quality_status='qualified' AND price_status='priced'
              AND is_tradeable=1
              AND candidate_state NOT IN ('vetoed','exited','expired_stale','executed','mtm')
        """).fetchone() or [0])[0])

        open_pos = int((_c.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
        ).fetchone() or [0])[0])

        stale_count = int((_c.execute(
            f"SELECT COUNT(*) FROM market_snapshots WHERE quality_status='pending' "
            f"AND COALESCE(first_seen_at, created_at, 0) < {now - 300}"
        ).fetchone() or [0])[0])

        _c.close()
    except Exception:
        pass

    # Council resonance
    resonance = 0
    if not proposals_df.empty and "confidence" in proposals_df.columns:
        vals = proposals_df["confidence"].dropna().astype(float).tail(10)
        resonance = int(vals.mean() * 100) if len(vals) > 0 else 0

    # Debate pressure
    pressure = 0
    if not debate_df.empty and "logged_at" in debate_df.columns:
        pressure = int((debate_df["logged_at"].astype(float) > now - 600).sum())

    # Enrichment health
    is_bottlenecked = pending_qual > 50 or mcap_missing > 20 or supervisor_visible == 0

    # Header
    alert_col = C_RED if is_bottlenecked else C_GREEN
    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
        f"letter-spacing:3px;color:{alert_col};margin:12px 0 6px;'>"
        f"◈ ORGANISM PRESSURE CORE"
        f"{'  ⚠ ENRICHMENT_BOTTLENECK — ORGANISM BLIND' if is_bottlenecked else '  ✓ PIPELINE_CLEAR'}"
        f"</div>",
        unsafe_allow_html=True
    )

    def _bar(label, val, max_v, col, unit=""):
        pct = min(100, int(val / max(1, max_v) * 100))
        return (
            f"<div style='margin-bottom:6px;'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"font-family:Share Tech Mono;font-size:0.66rem;margin-bottom:2px;'>"
            f"<span style='color:rgba(255,255,255,0.5);'>{label}</span>"
            f"<span style='color:{col};'>{val}{unit}</span></div>"
            f"<div style='height:3px;background:rgba(255,255,255,0.06);border-radius:2px;'>"
            f"<div style='width:{pct}%;height:100%;background:{col};border-radius:2px;'>"
            f"</div></div></div>"
        )

    bars_left = (
        _bar("PENDING QUALIFY",    pending_qual,      100, C_RED if pending_qual > 50 else C_CYAN)
        + _bar("MTM BLIND",        mtm_pending,       100, C_RED if mtm_pending > 20 else C_GOLD)
        + _bar("MCAP MISSING",     mcap_missing,       50, C_RED if mcap_missing > 10 else "#666")
        + _bar("STALE BEFORE QUAL",stale_count,        50, C_RED if stale_count > 10 else "#555")
    )
    bars_right = (
        _bar("SUPERVISOR VISIBLE", supervisor_visible, 20, C_GREEN if supervisor_visible > 0 else C_RED)
        + _bar("OPEN POSITIONS",   open_pos,           10, C_GREEN)
        + _bar("COUNCIL RESONANCE",resonance,          100, "#9945FF", "%")
        + _bar("DEBATE PRESSURE",  pressure,            30, "#8EF9FF", " turns")
    )

    st.markdown(
        f"<div style='border:1px solid rgba(255,255,255,0.08);border-radius:10px;"
        f"padding:12px 14px;background:rgba(5,2,16,0.8);margin-bottom:14px;"
        f"display:grid;grid-template-columns:1fr 1fr;gap:16px;'>"
        f"<div>{bars_left}</div><div>{bars_right}</div>"
        f"</div>",
        unsafe_allow_html=True
    )


def render_intelligence_tab(query_db) -> None:
    """Canonical fail-soft Intelligence surface. Reads only."""
    # Accept both query_db(sql) and query_db(sql, params) contracts. Several
    # older Intelligence panels called the one-argument form while the active
    # hub adapter requires two arguments; normalise once at the boundary.
    _raw_query_db = query_db
    def query_db(sql, params=()):
        try:
            return _raw_query_db(sql, params)
        except TypeError:
            return _raw_query_db(sql)
    _ensure_forge_tables()
    st.markdown("""
<style>
.intel-hero{position:relative;padding:22px 24px 20px;margin:0 0 22px;border:0;border-radius:28px 7px 28px 7px;background:radial-gradient(circle at 12% 0%,rgba(142,249,255,.11),transparent 42%),linear-gradient(120deg,rgba(8,8,25,.92),rgba(10,4,25,.72) 52%,rgba(4,3,11,.88));box-shadow:0 22px 70px rgba(0,0,0,.28);overflow:hidden}.intel-hero:before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#8EF9FF,#9945FF 55%,#FFD700);height:1px;opacity:.72}.intel-hero:after{content:"RESEARCH / DEBATE / EXPERIMENT / MEMORY";position:absolute;right:22px;bottom:10px;font:600 .48rem Orbitron;letter-spacing:.22em;color:rgba(142,249,255,.30)}
.intel-title{font:900 .92rem Orbitron,sans-serif;letter-spacing:.16em;color:#8EF9FF}.intel-sub{font:500 .72rem/1.5 'Share Tech Mono',monospace;color:#8d8aa0;margin-top:6px}
@media(max-width:720px){.intel-title{font-size:.78rem;letter-spacing:.10em}.intel-sub{font-size:.78rem}}
</style>
<div class="intel-hero"><div class="intel-title">INTELLIGENCE · RESEARCH & SELF-EVOLUTION</div>
<div class="intel-sub">Council evidence → debate → experiment → accepted artifact. One panel may degrade without taking the laboratory offline.</div></div>
""", unsafe_allow_html=True)

    proposals_df = _qdb(query_db, "SELECT id, proposal_type, proposal_text, suggested_action, confidence, status, created_at, last_seen_at FROM polaris_proposals ORDER BY created_at DESC LIMIT 20")
    debate_df = _qdb(query_db, "SELECT speaker, action, message, content_json, logged_at, thinking_state, verdict_type, transcript_json, approved_by, proposal_id, grok_narrative FROM debate_log ORDER BY COALESCE(logged_at, 0) DESC LIMIT 40")
    cognition_df = _qdb(query_db, "SELECT timestamp, stage, token, message, confidence FROM cognition_log ORDER BY timestamp DESC LIMIT 200")
    patch_df = _qdb(query_db, "SELECT applied_at, proposal_type, param_key, old_value, new_value, outcome FROM patch_history ORDER BY applied_at DESC LIMIT 20")
    forge_df = _qdb(query_db, "SELECT id, title, author, doc_type, status, content_md, tags, created_at, updated_at FROM intelligence_forge ORDER BY updated_at DESC, created_at DESC LIMIT 10")
    queue_df = _qdb(query_db, "SELECT id, petition, status, priority, created_at FROM research_queue ORDER BY created_at DESC LIMIT 10")
    iq_df = _qdb(query_db, "SELECT source, category, payload_json, created_at FROM improvement_queue ORDER BY created_at DESC LIMIT 10")
    pending_petitions = int((queue_df["status"].astype(str).str.lower() == "pending").sum()) if not queue_df.empty and "status" in queue_df.columns else 0

    _safe_panel("Identity", _render_substrate_header, pending_petitions)
    _safe_panel("Legal alpha boundary", _render_legal_alpha_banner)
    _safe_panel("Genesis macro map", _render_genesis_macro_map, query_db)
    _safe_panel("Current council state", _render_current_state_panel, debate_df, proposals_df)
    _safe_panel("Legal event-alpha", _render_legal_event_alpha_tab, query_db)
    _safe_panel("Edge candidate arena", _render_edge_candidate_arena, proposals_df, query_db)
    _safe_panel("Living research pulse", _render_living_pulse, query_db, proposals_df, debate_df)
    _safe_panel("Research feed", _render_live_research_feed, proposals_df, debate_df)
    _safe_panel("Specialist routing", _render_specialist_routing_matrix, query_db)
    with st.expander("◈ RAW COGNITIVE STREAM — POLARIS MIND EXHAUST", expanded=False):
        _safe_panel("Cognitive stream", _render_cognitive_stream, cognition_df)
    _safe_panel("Golden lattice", _render_golden_lattice, query_db)
    _safe_panel("Synthesis documents", _render_synthesis_documents, forge_df)
    _safe_panel("Narrative layer", _render_narrative_layer, forge_df, debate_df)
    _safe_panel("Smart Money Observatory", _render_copy_trade_panel, query_db)
    _safe_panel("Alpha queue", _render_alpha_queue, proposals_df, iq_df)
    with st.expander("⬡ POST-EXECUTION DIAGNOSTICS — AXON FORENSIC LAYER", expanded=False):
        _safe_panel("Execution log", _render_execution_log_panel, debate_df, patch_df)
        _safe_panel("NIM call log", _render_nim_call_log, query_db)
        _safe_panel("Forensic audit", _render_forensic_audit, debate_df, patch_df)
    _safe_panel("Operator petition membrane", _render_petition_membrane, queue_df)

