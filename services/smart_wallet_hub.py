"""
services/smart_wallet_hub.py
=============================
Smart Wallet Conviction Matrix - UI Panel Only

Streamlit-only render function. Imports backend from smart_wallet_conviction.
Collapsed by default. Safe if tables missing.
"""
from __future__ import annotations

from contextlib import closing
import json
import time
from pathlib import Path
from typing import Any, Dict

try:
    from .smart_wallet_conviction import _connect, ensure_smart_wallet_schema
except ImportError:
    from smart_wallet_conviction import _connect, ensure_smart_wallet_schema


def _fmt_age(seconds: float) -> str:
    """Format age in seconds to human-readable string."""
    try:
        s = max(0, int(seconds))
    except Exception:
        return "unknown"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m"
    return f"{s//3600}h"


def _load_matrix_data(db_path: str | Path, limit: int = 12) -> Dict[str, Any]:
    """Load Smart Wallet data for UI. Schema-tolerant."""
    ensure_smart_wallet_schema(db_path)
    now = time.time()
    
    result = {
        "sources": [],
        "signals": [],
        "fingerprints": 0,
        "events": [],
        "profiles": 0,
        "observed_trades": 0,
        "now": now,
    }
    
    try:
        with closing(_connect(db_path)) as conn:
            # Sources
            try:
                sources = [dict(r) for r in conn.execute(
                    "SELECT * FROM smart_wallet_sources ORDER BY last_run_at DESC LIMIT 8"
                )]
                for s in sources:
                    s["freshness"] = now - float(s.get("last_success_at") or 0)
                result["sources"] = sources
            except Exception:
                pass
            
            # Signals
            try:
                signals = [dict(r) for r in conn.execute(
                    "SELECT * FROM wallet_entry_likelihood_signals "
                    "ORDER BY signal_time DESC LIMIT ?", (limit,)
                )]
                result["signals"] = signals
            except Exception:
                pass
            
            # Fingerprints count
            try:
                fp_row = conn.execute(
                    "SELECT COUNT(*) c FROM wallet_entry_fingerprints"
                ).fetchone()
                result["fingerprints"] = int(fp_row["c"]) if fp_row else 0
            except Exception:
                pass
            
            try:
                row = conn.execute("SELECT COUNT(*) c FROM smart_wallet_profiles").fetchone()
                result["profiles"] = int(row["c"]) if row else 0
            except Exception:
                pass
            try:
                row = conn.execute("SELECT COUNT(*) c FROM smart_wallet_trades").fetchone()
                result["observed_trades"] = int(row["c"]) if row else 0
            except Exception:
                pass

            # Events
            try:
                events = [dict(r) for r in conn.execute(
                    "SELECT * FROM smart_wallet_events "
                    "ORDER BY event_time DESC LIMIT 8"
                )]
                result["events"] = events
            except Exception:
                pass
    except Exception:
        pass
    
    return result


def render_smart_wallet_conviction_matrix(db_path: str | Path = "sentinuity_matrix.db") -> None:
    """
    Render Smart Wallet Conviction Matrix panel.
    
    Collapsed by default. OBSERVE/PAPER mode only.
    Shows fingerprints, signals, conviction scores, veto reasons.
    """
    import streamlit as st
    
    try:
        # Cache inside Streamlit
        @st.cache_data(ttl=12, show_spinner=False)
        def cached_load(path: str) -> Dict[str, Any]:
            return _load_matrix_data(path)
        
        data = cached_load(str(db_path))
    except Exception as exc:
        with st.expander("📡 SMART WALLET CONVICTION MATRIX", expanded=False):
            st.warning(f"Smart wallet matrix unavailable: {exc}")
        return
    
    with st.expander("📡 SMART WALLET CONVICTION MATRIX", expanded=False):
        sources = data["sources"]
        signals = data["signals"]
        
        st.caption("Observe/Paper layer only. No live copy trading. No hard-gate bypass.")
        
        # Metrics
        c1, c2, c3, c4 = st.columns(4)
        healthy_statuses = {"OK", "ALIVE"}
        fresh_sources = [s for s in sources
                        if float(s.get("freshness", 999999)) <= 21600
                        and str(s.get("status") or "").upper() in healthy_statuses]
        c1.metric("Sources fresh", len(fresh_sources))
        c2.metric("Roster wallets", int(data.get("profiles", 0)))
        c3.metric("Observed trades", int(data.get("observed_trades", 0)))
        c4.metric("Conviction signals", len(signals))
        
        # Sources
        if sources:
            st.markdown("**Sources**")
            for idx, src in enumerate(sources[:4]):
                age = _fmt_age(float(src.get("freshness", 999999)))
                status = src.get("status", "UNKNOWN")
                st.write(
                    f"`{src.get('source_name')}` — **{status}** — "
                    f"fresh {age} — seen {src.get('records_seen', 0)}",
                    key=f"wallet_source_{src.get('source_name', 'src')}_{idx}"
                )
        else:
            st.info("No smart-wallet sources imported yet.")
        
        # Signals
        if signals:
            st.markdown("**Latest conviction signals**")
            for idx, sig in enumerate(signals[:10]):
                token = sig.get("token_symbol") or (sig.get("token_mint") or "")[:8]
                veto = sig.get("veto_reason") or "none"
                conv = float(sig.get("copy_conviction_score") or 0)
                likelihood = float(sig.get("wallet_entry_likelihood") or 0)
                safe_x = float(sig.get("median_safe_x") or 0)
                latency = sig.get("copy_latency_risk", "UNKNOWN")
                
                st.markdown(
                    f"- **{token}** conviction `{conv:.2f}` | "
                    f"entry likelihood `{likelihood:.2f}` | "
                    f"safe-X `{safe_x:.2f}` | "
                    f"latency `{latency}` | veto `{veto}`"
                )
        else:
            st.info("No wallet-entry likelihood signals yet.")
        
        # Events
        if data["events"]:
            st.markdown("**Verified world events**")
            for idx, ev in enumerate(data["events"][:5]):
                age_str = _fmt_age(data["now"] - float(ev.get("event_time", 0)))
                st.caption(
                    f"{age_str} ago — {ev.get('event_type')} — {ev.get('message')}"
                )
