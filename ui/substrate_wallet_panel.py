from __future__ import annotations

import time
from typing import Any

try:
    import streamlit as st
except Exception:  # allows py_compile without streamlit in some test envs
    st = None

from wallets.substrate_wallet_schema import ensure_schema, connect, cfg_get, cfg_set
from wallets.substrate_wallet import snapshot, refresh_wallet_state

try:
    from services.substrate_opportunity_scanner import scan_once
except Exception:
    from substrate_opportunity_scanner import scan_once
try:
    from services.substrate_copytrade_bridge import ingest_copytrade_once
except Exception:
    from substrate_copytrade_bridge import ingest_copytrade_once
try:
    from services.substrate_portfolio_supervisor import supervise_once
except Exception:
    from substrate_portfolio_supervisor import supervise_once
try:
    from wallets.substrate_live_guard import stage_live_order_from_opportunity
except Exception:
    stage_live_order_from_opportunity = None


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        age = int(time.time()) - int(float(ts))
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age // 60}m ago"
        return f"{age // 3600}h ago"
    except Exception:
        return str(ts)


def _truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "enabled", "armed", "live")


def _save_wallet_gate(provider: str, family: str, address: str, allowed_chains: str,
                      size_usd: float, max_usd: float, live_enabled: bool,
                      live_armed: bool, require_shadow: bool) -> None:
    ensure_schema()
    con = connect()
    try:
        cfg_set(con, "SUBSTRATE_LIVE_PROVIDER", provider)
        cfg_set(con, "SUBSTRATE_LIVE_WALLET_FAMILY", family)
        cfg_set(con, "SUBSTRATE_LIVE_WALLET_ADDRESS", address.strip())
        cfg_set(con, "SUBSTRATE_LIVE_ALLOWED_CHAINS", allowed_chains.strip())
        cfg_set(con, "SUBSTRATE_LIVE_POSITION_SIZE_USD", f"{float(size_usd):.2f}")
        cfg_set(con, "SUBSTRATE_LIVE_MAX_POSITION_USD", f"{float(max_usd):.2f}")
        cfg_set(con, "SUBSTRATE_LIVE_MAX_OPEN", "1")
        cfg_set(con, "SUBSTRATE_LIVE_ENABLED", "1" if live_enabled else "0")
        cfg_set(con, "SUBSTRATE_LIVE_ARMED", "1" if live_armed else "0")
        cfg_set(con, "SUBSTRATE_LIVE_REQUIRE_PAPER_SHADOW", "1" if require_shadow else "0")
        cfg_set(con, "SUBSTRATE_LIVE_EXECUTION_MODE", "manual_sign")
        cfg_set(con, "SUBSTRATE_LIVE_AUTOSEND_ENABLED", "0")
        con.commit()
    finally:
        con.close()
    refresh_wallet_state()


def _cfg_snapshot() -> dict[str, str]:
    ensure_schema()
    con = connect()
    try:
        keys = [
            "SUBSTRATE_LIVE_PROVIDER", "SUBSTRATE_LIVE_WALLET_FAMILY", "SUBSTRATE_LIVE_WALLET_ADDRESS",
            "SUBSTRATE_LIVE_ALLOWED_CHAINS", "SUBSTRATE_LIVE_POSITION_SIZE_USD", "SUBSTRATE_LIVE_MAX_POSITION_USD",
            "SUBSTRATE_LIVE_ENABLED", "SUBSTRATE_LIVE_ARMED", "SUBSTRATE_LIVE_REQUIRE_PAPER_SHADOW",
        ]
        return {k: str(cfg_get(con, k, "")) for k in keys}
    finally:
        con.close()


def render_substrate_wallet_panel() -> None:
    if st is None:
        raise RuntimeError("streamlit is required to render the substrate wallet panel")

    ensure_schema()
    refresh_wallet_state()
    snap = snapshot()
    state = snap["state"]
    bal = snap["balance"]
    cfg = _cfg_snapshot()

    st.markdown("## 🧬 Substrate Wallet — Standalone")
    st.caption(
        "Separate from the main Solana pump lane. Paper runs continuously; live is a manual-sign gate for tiny test orders. "
        "No private keys, seed phrases, or autosend path are stored here."
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Mode", state.get("mode", "—"))
    c2.metric("Provider", state.get("provider", "—"))
    c3.metric("Paper Cash", f"${float(bal.get('cash_usd', 0.0)):,.2f}")
    c4.metric("Live Orders", str(bal.get("live_orders", 0)))
    c5.metric("Live Block", state.get("live_block_reason", "—"))

    with st.expander("🔐 Wallet connection + live manual-sign gate", expanded=True):
        st.markdown(
            "**Recommended split:** Phantom stays on the Solana/pump page. Coinbase Wallet or MetaMask belongs here for the "
            "EVM/Base/ETH-style Substrate assets. Phantom can still be selected for Solana-only Substrate routes, but EVM "
            "routes must use an EVM wallet address."
        )
        provider_options = ["coinbase_wallet", "metamask", "phantom"]
        current_provider = cfg.get("SUBSTRATE_LIVE_PROVIDER", "coinbase_wallet") or "coinbase_wallet"
        provider = st.selectbox(
            "Substrate wallet provider",
            provider_options,
            index=provider_options.index(current_provider) if current_provider in provider_options else 0,
            key="substrate_wallet_provider",
        )
        family_default = "solana" if provider == "phantom" else "evm"
        family = st.selectbox(
            "Wallet family",
            ["evm", "solana"],
            index=0 if (cfg.get("SUBSTRATE_LIVE_WALLET_FAMILY") or family_default) == "evm" else 1,
            key="substrate_wallet_family",
        )
        address = st.text_input(
            "Wallet address only — never paste seed/private key",
            value=cfg.get("SUBSTRATE_LIVE_WALLET_ADDRESS", ""),
            key="substrate_wallet_address",
        )
        chains_default = "solana" if family == "solana" else "base,ethereum,arbitrum,optimism,polygon"
        allowed_chains = st.text_input(
            "Allowed chains for this Substrate live gate",
            value=cfg.get("SUBSTRATE_LIVE_ALLOWED_CHAINS") or chains_default,
            key="substrate_allowed_chains",
        )
        s1, s2, s3 = st.columns(3)
        size_usd = s1.number_input(
            "Live test size USD",
            min_value=1.0, max_value=25.0,
            value=float(cfg.get("SUBSTRATE_LIVE_POSITION_SIZE_USD") or 10.0), step=1.0,
            key="substrate_live_size_usd",
        )
        max_usd = s2.number_input(
            "Hard cap USD",
            min_value=1.0, max_value=25.0,
            value=float(cfg.get("SUBSTRATE_LIVE_MAX_POSITION_USD") or 25.0), step=1.0,
            key="substrate_live_cap_usd",
        )
        require_shadow = s3.checkbox(
            "Require paper shadow first",
            value=_truthy(cfg.get("SUBSTRATE_LIVE_REQUIRE_PAPER_SHADOW", "1")),
            key="substrate_require_shadow",
        )
        e1, e2, e3 = st.columns(3)
        live_enabled = e1.checkbox("Enable Substrate live lane", value=_truthy(cfg.get("SUBSTRATE_LIVE_ENABLED", "0")), key="substrate_live_enabled")
        live_armed = e2.checkbox("Arm manual-sign live test", value=_truthy(cfg.get("SUBSTRATE_LIVE_ARMED", "0")), key="substrate_live_armed")
        e3.markdown("`AUTOSEND = OFF`  \\nmanual wallet confirmation required")
        b1, b2 = st.columns(2)
        if b1.button("Save Substrate wallet gate", key="substrate_save_wallet_gate"):
            _save_wallet_gate(provider, family, address, allowed_chains, size_usd, max_usd, live_enabled, live_armed, require_shadow)
            st.success("Substrate wallet gate saved. Live orders still require manual wallet signature.")
        if b2.button("Emergency disarm Substrate live", key="substrate_disarm_live"):
            _save_wallet_gate(provider, family, address, allowed_chains, size_usd, max_usd, False, False, True)
            st.warning("Substrate live disabled and disarmed.")

        newest = next((o for o in snap["opportunities"] if str(o.get("state", "")).upper() in ("NEW", "READY", "PROMOTED", "PAPER_OPENED")), None)
        if newest and stage_live_order_from_opportunity:
            if st.button("Stage newest eligible opportunity for manual live signature", key="substrate_stage_live_order"):
                res = stage_live_order_from_opportunity(int(newest["id"]))
                if res.get("ok"):
                    st.success(f"Live test order staged: {res}")
                else:
                    st.warning(f"Live stage blocked: {res.get('reason')}")
        elif not newest:
            st.info("No eligible Substrate opportunity to stage yet. Run scanner/supervisor first.")

    with st.expander("Execution phases — data fetching → council research → copytrade → paper/live", expanded=True):
        phase_cols = st.columns(5)
        phase_cols[0].markdown("**1. Fetch**  \\nScanner collects chain/asset candidates.")
        phase_cols[1].markdown("**2. Council**  \\nVotes on spread and thesis.")
        phase_cols[2].markdown("**3. Copytrade**  \\nWallet signals promote into opportunities.")
        phase_cols[3].markdown("**4. Risk Guard**  \\nFreshness, route, chain, asset, size.")
        phase_cols[4].markdown("**5. Execute**  \\nPaper opens now; live stages manual-sign orders only.")

        b1, b2, b3 = st.columns(3)
        if b1.button("Run scanner now", key="substrate_run_scanner"):
            n = scan_once()
            st.success(f"Scanner inserted {n} opportunities and council votes.")
        if b2.button("Fetch copytrade intel now", key="substrate_run_copytrade"):
            n = ingest_copytrade_once()
            st.success(f"Copytrade bridge ingested {n} signal(s).")
        if b3.button("Run supervisor now", key="substrate_run_supervisor"):
            res = supervise_once()
            st.success(f"Supervisor result: {res}")

    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("### Council allocation research")
        votes = snap["votes"]
        if votes:
            st.dataframe(
                [
                    {
                        "member": v["council_member"],
                        "phase": v["phase"],
                        "chain": v["chain"],
                        "asset": v["asset_symbol"],
                        "alloc %": v["allocation_pct"],
                        "conf": v["confidence"],
                        "thesis": v["thesis"],
                        "age": _fmt_ts(v["created_at"]),
                    }
                    for v in votes[:12]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No council votes yet. Press Run scanner now.")

        st.markdown("### Live opportunity bus")
        opps = snap["opportunities"]
        if opps:
            st.dataframe(
                [
                    {
                        "state": o["state"],
                        "source": o["source"],
                        "chain": o["chain"],
                        "asset": o["asset_symbol"],
                        "native/wrapped": o["native_or_wrapped"],
                        "conf": round(float(o["confidence"] or 0), 3),
                        "edge": round(float(o["expected_edge"] or 0), 3),
                        "liq": int(float(o["liquidity_usd"] or 0)),
                        "price_age": _fmt_ts(o["price_updated_at"]),
                        "created": _fmt_ts(o["created_at"]),
                    }
                    for o in opps[:20]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No opportunities yet. Press Run scanner now.")

    with right:
        st.markdown("### Open Substrate paper positions")
        positions = snap["open_positions"]
        if positions:
            st.dataframe(
                [
                    {
                        "asset": p.get("asset_symbol") or p.get("symbol"),
                        "chain": p.get("chain"),
                        "size": p.get("size_usd") or p.get("position_size"),
                        "entry": p.get("entry_price_usd") or p.get("entry_price"),
                        "qty": p.get("quantity"),
                        "source": p.get("source"),
                        "age": _fmt_ts(p.get("opened_at")),
                    }
                    for p in positions
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No Substrate paper positions open yet.")

        st.markdown("### Manual-sign live orders")
        live_orders = snap.get("live_orders", [])
        if live_orders:
            st.dataframe(
                [
                    {
                        "state": o.get("state"),
                        "chain": o.get("chain"),
                        "asset": o.get("asset_symbol"),
                        "provider": o.get("provider"),
                        "size": o.get("size_usd"),
                        "age": _fmt_ts(o.get("created_at")),
                    }
                    for o in live_orders[:10]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No live orders staged. This is expected until you enable + arm the manual-sign gate.")

        st.markdown("### Provider health")
        if snap["provider_health"]:
            st.dataframe(
                [
                    {
                        "provider": h["provider"],
                        "mode": h["mode"],
                        "ready": bool(h["ready"]),
                        "last_error": h["last_error"],
                        "updated": _fmt_ts(h["updated_at"]),
                    }
                    for h in snap["provider_health"]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Provider health not written yet.")

        st.markdown("### Execution audit")
        audit = snap["audit"]
        if audit:
            st.dataframe(
                [
                    {
                        "allowed": bool(a["allowed"]),
                        "reason": a["reason"],
                        "source": a["source"],
                        "asset": a["asset_symbol"],
                        "chain": a["chain"],
                        "conf": a["confidence"],
                        "age": _fmt_ts(a["created_at"]),
                    }
                    for a in audit[:20]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No execution decisions yet.")


if __name__ == "__main__":
    if st is None:
        print("streamlit not available")
    else:
        render_substrate_wallet_panel()
