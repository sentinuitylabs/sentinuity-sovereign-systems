# SENTINUITY_STATE_CONTRACT_LEDGER_TRUTH_V2_20260624
# Read-only UI balance contract. Solana paper truth comes from paper_positions,
# not stale launch/config aliases. Cash moves on OPEN by reserving stake;
# equity moves with closed realized PnL + current open unrealized PnL.
#
# V2 CHANGE (20260624): the dataclass schema was REVERTED to an older shape
# (paper_reserved / live_wallet / live_available) while master_console.py and
# sovereign_hub.py were already rewritten to read the newer names
# (paper_open_reserved / live_wallet_usd / live_start_usd / live_wallet_synced /
# live_available_usd / trading_mode). The first access (paper_open_reserved)
# raised AttributeError, which surfaced as
#   [wallet contract unavailable: 'BalanceTruth' object has no attribute 'paper_open_reserved']
# and silently zeroed the live wallet block. This version restores the full
# field set the consumers expect. It adds fields only; it removes nothing, so
# any code still reading the old names keeps working. NO trading logic here.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, Iterable, Optional, Set


@dataclass
class BalanceTruth:
    # ---- paper ----
    paper_start: float = 250.0
    paper_equity: float = 250.0
    paper_cash: float = 250.0
    paper_realized_pnl: float = 0.0
    paper_unrealized_pnl: float = 0.0
    paper_reserved: float = 0.0          # legacy name (kept for back-compat)
    paper_open_reserved: float = 0.0     # name the current consumers read
    paper_open_count: int = 0
    paper_closed_count: int = 0
    paper_roi_pct: float = 0.0
    paper_cash_roi_pct: float = 0.0
    # ---- live ----
    live_wallet: float = 0.0             # legacy name (kept for back-compat)
    live_wallet_usd: float = 0.0         # name the current consumers read
    live_equity: float = 0.0
    live_available: float = 0.0          # legacy name (kept for back-compat)
    live_available_usd: float = 0.0      # name the current consumers read
    live_cash: float = 0.0
    live_start_usd: float = 0.0
    live_wallet_synced: bool = False
    # ---- mode / meta ----
    trading_mode: str = "paper"
    reset_at: str = ""
    reset_respected: bool = False
    source: str = "paper_positions"
    updated_at: float = 0.0


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return float(default)
        x = float(v)
        return x if x == x else float(default)
    except Exception:
        return float(default)


def _ro_conn(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path).resolve()
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA query_only=ON")
        c.execute("PRAGMA busy_timeout=1500")
    except Exception:
        pass
    return c


def _table_exists(c: sqlite3.Connection, table: str) -> bool:
    try:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    except Exception:
        return False


def _cols(c: sqlite3.Connection, table: str) -> Set[str]:
    try:
        return {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _cfg(c: sqlite3.Connection) -> Dict[str, str]:
    if not _table_exists(c, "system_config"):
        return {}
    try:
        return {str(r[0]): str(r[1] if r[1] is not None else "") for r in c.execute("SELECT key,value FROM system_config").fetchall()}
    except Exception:
        return {}


def _first_num(map_: Dict[str, Any], keys: Iterable[str], default: float = 0.0) -> tuple[float, Optional[str]]:
    """Return (value, key_that_supplied_it). key is None if nothing matched."""
    for k in keys:
        if k in map_ and str(map_[k]).strip() != "":
            return _f(map_[k], default), k
    return float(default), None


def _state_row(c: sqlite3.Connection) -> Dict[str, Any]:
    if not _table_exists(c, "system_state"):
        return {}
    try:
        r = c.execute("SELECT * FROM system_state WHERE id=1 LIMIT 1").fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}


def _time_filter_sql(cols: Set[str], cfg: Dict[str, str]) -> tuple[str, list[Any], str, bool]:
    """By default, IGNORE PAPER_WALLET_RESET_AT so the UI/launcher does not
    amputate real paper history. To intentionally respect reset markers, set
    PAPER_LEDGER_RESPECT_RESET_AT=1 in system_config.
    """
    reset_at = cfg.get("PAPER_WALLET_RESET_AT") or cfg.get("SOLANA_PAPER_WALLET_RESET_AT") or ""
    respect = str(cfg.get("PAPER_LEDGER_RESPECT_RESET_AT", "0")).lower() in ("1", "true", "yes", "on")
    if not (respect and reset_at):
        return "", [], str(reset_at), False
    try:
        reset_num = float(reset_at)
    except Exception:
        return "", [], str(reset_at), False
    if "closed_at" in cols:
        return " AND COALESCE(closed_at, opened_at, 0) >= ?", [reset_num], str(reset_at), True
    if "opened_at" in cols:
        return " AND COALESCE(opened_at, 0) >= ?", [reset_num], str(reset_at), True
    return "", [], str(reset_at), False


def get_balance_truth(db_path: str | Path, fallback_initial: float = 250.0) -> BalanceTruth:
    bt = BalanceTruth(updated_at=time.time())
    try:
        with _ro_conn(db_path) as c:
            cfg = _cfg(c)
            state = _state_row(c)

            bt.trading_mode = str(cfg.get("TRADING_MODE", "paper") or "paper").strip().lower() or "paper"

            baseline_default = _f(fallback_initial, 250.0) or 250.0
            bt.paper_start, _ = _first_num(
                cfg,
                (
                    "PAPER_LEDGER_BASELINE_USD",
                    "PAPER_INITIAL_CAPITAL_USD",
                    "SOLANA_PAPER_INITIAL_CAPITAL_USD",
                    "PAPER_EQUITY_BASELINE_USD",
                    "PAPER_STARTING_BALANCE_USD",
                ),
                _f(state.get("initial_capital"), baseline_default),
            )

            if _table_exists(c, "paper_positions"):
                cols = _cols(c, "paper_positions")
                where_extra, params, reset_at, reset_respected = _time_filter_sql(cols, cfg)
                bt.reset_at = reset_at
                bt.reset_respected = reset_respected

                if "realized_pnl_usd" in cols:
                    try:
                        r = c.execute(
                            "SELECT COUNT(*) n, COALESCE(SUM(COALESCE(realized_pnl_usd,0)),0) pnl "
                            "FROM paper_positions WHERE UPPER(COALESCE(status,''))='CLOSED'" + where_extra,
                            params,
                        ).fetchone()
                        bt.paper_closed_count = int(r["n"] or 0)
                        bt.paper_realized_pnl = _f(r["pnl"], 0.0)
                    except Exception:
                        pass

                try:
                    r = c.execute(
                        "SELECT COUNT(*) n, COALESCE(SUM(COALESCE(position_size_usd,0)),0) reserved "
                        "FROM paper_positions WHERE UPPER(COALESCE(status,''))='OPEN'"
                    ).fetchone()
                    bt.paper_open_count = int(r["n"] or 0)
                    bt.paper_reserved = _f(r["reserved"], 0.0)
                except Exception:
                    pass

                if "unrealized_pnl_usd" in cols:
                    try:
                        r = c.execute(
                            "SELECT COALESCE(SUM(COALESCE(unrealized_pnl_usd,0)),0) u "
                            "FROM paper_positions WHERE UPPER(COALESCE(status,''))='OPEN'"
                        ).fetchone()
                        bt.paper_unrealized_pnl = _f(r["u"], 0.0)
                    except Exception:
                        pass

                if abs(bt.paper_unrealized_pnl) < 1e-12 and {"entry_price", "position_size_usd"}.issubset(cols):
                    px_col = "last_price" if "last_price" in cols else ("live_exec_price" if "live_exec_price" in cols else None)
                    if px_col:
                        try:
                            q = (
                                f"SELECT entry_price, {px_col} AS px, position_size_usd "
                                "FROM paper_positions WHERE UPPER(COALESCE(status,''))='OPEN'"
                            )
                            total = 0.0
                            for r in c.execute(q).fetchall():
                                ep = _f(r["entry_price"], 0.0)
                                px = _f(r["px"], 0.0)
                                size = _f(r["position_size_usd"], 0.0)
                                if ep > 0 and px > 0 and size:
                                    total += size * ((px - ep) / ep)
                            bt.paper_unrealized_pnl = total
                        except Exception:
                            pass

            bt.paper_equity = bt.paper_start + bt.paper_realized_pnl + bt.paper_unrealized_pnl
            bt.paper_cash = bt.paper_start + bt.paper_realized_pnl - bt.paper_reserved
            if bt.paper_start:
                bt.paper_roi_pct = ((bt.paper_equity - bt.paper_start) / bt.paper_start) * 100.0
                bt.paper_cash_roi_pct = ((bt.paper_cash - bt.paper_start) / bt.paper_start) * 100.0

            # ---- LIVE WALLET ----------------------------------------------------
            # Read ONLY from explicit live keys. We deliberately do NOT fall back to
            # system_state.wallet_balance, because that column has historically been
            # polluted with paper equity. If no live key is set, live is "not synced"
            # and the UI shows a not-synced state instead of a fake number.
            live_val, live_key = _first_num(
                cfg,
                (
                    "LIVE_WALLET_BALANCE_USD",
                    "SOLANA_LIVE_WALLET_USD",
                    "LIVE_WALLET_USD",
                    "PHANTOM_WALLET_BALANCE_USD",
                    "WALLET_BALANCE_USD",
                ),
                0.0,
            )
            live_avail, _ = _first_num(
                cfg,
                (
                    "LIVE_AVAILABLE_USD",
                    "SOLANA_LIVE_AVAILABLE_USD",
                    "LIVE_CASH_USD",
                    "PHANTOM_AVAILABLE_USD",
                ),
                live_val,
            )
            live_start, _ = _first_num(
                cfg,
                ("LIVE_START_USD", "LIVE_WALLET_START_USD", "SOLANA_LIVE_START_USD"),
                live_val,
            )

            bt.live_wallet = bt.live_equity = live_val
            bt.live_wallet_usd = live_val
            bt.live_available = bt.live_cash = live_avail
            bt.live_available_usd = live_avail
            bt.live_start_usd = live_start
            # "synced" == we found an explicit live key with a usable number.
            bt.live_wallet_synced = bool(live_key) and live_val > 0.0

            # Keep both reserved names consistent.
            bt.paper_open_reserved = bt.paper_reserved
            return bt
    except Exception:
        bt.source = "state_contract_error"
        bt.paper_start = _f(fallback_initial, 250.0) or 250.0
        bt.paper_equity = bt.paper_cash = bt.paper_start
        bt.paper_open_reserved = bt.paper_reserved
        bt.live_wallet_usd = bt.live_wallet
        bt.live_available_usd = bt.live_available
        return bt


def load_world_state(db_path: str | Path) -> Dict[str, Any]:
    """Living World state contract (SENTINUITY_WORLD_STATE_20260712).

    Delegates to ui.world_state.load_world_state — the read-only, schema-
    tolerant loader that feeds the canonical six-realm World tab
    (ui/sovereign_world.html via window.applySwState). Any failure degrades
    to {} so the world boots empty rather than crashing the hub.
    """
    try:
        from ui.world_state import load_world_state as _lws
        return _lws(db_path) or {}
    except Exception:
        return {}


def render_balance_capsule(bt: BalanceTruth) -> None:
    """Compatibility renderer retained for older hub imports."""
    try:
        import streamlit as st
        st.markdown(
            f"<div style='padding:10px 12px;border:1px solid #FFD70055;border-radius:12px;background:rgba(255,215,0,.07);font-family:Share Tech Mono,monospace;'>"
            f"<b style='color:#FFD700;'>PAPER EQUITY</b> ${bt.paper_equity:,.2f} &nbsp; "
            f"<b style='color:#8EF9FF;'>CASH</b> ${bt.paper_cash:,.2f} &nbsp; "
            f"<b style='color:#14F195;'>OPEN RESERVED</b> ${bt.paper_open_reserved:,.2f}"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# --- SENTINUITY LIVE WALLET TRUTH WRAPPER V4 ---
# One canonical funded-wallet source: live_wallet_state, written by
# services.live_wallet_sync from the SOLANA_PRIVATE_KEY-derived address.
_sentinuity_original_get_balance_truth = get_balance_truth


def _sentinuity_patch_balance_obj_v4(bal, truth):
    updates = {
        "live_wallet": float(truth.get("balance_usd") or 0.0),
        "live_equity": float(truth.get("balance_usd") or 0.0),
        "live_wallet_usd": float(truth.get("balance_usd") or 0.0),
        "live_available": float(truth.get("available_usd") or 0.0),
        "live_cash": float(truth.get("available_usd") or 0.0),
        "live_available_usd": float(truth.get("available_usd") or 0.0),
        "live_wallet_synced": bool(truth.get("synced")),
    }
    try:
        import dataclasses
        if dataclasses.is_dataclass(bal):
            names = {f.name for f in dataclasses.fields(bal)}
            return dataclasses.replace(bal, **{k: v for k, v in updates.items() if k in names})
    except Exception:
        pass
    for key, value in updates.items():
        try:
            setattr(bal, key, value)
        except Exception:
            pass
    return bal


def get_balance_truth(*args, **kwargs):
    bal = _sentinuity_original_get_balance_truth(*args, **kwargs)
    dbp = args[0] if args else kwargs.get("db_path")
    try:
        from services.live_wallet_truth import read_live_wallet_truth
        truth = read_live_wallet_truth(dbp, max_age_sec=180.0)
        return _sentinuity_patch_balance_obj_v4(bal, truth)
    except Exception:
        return bal
# --- END SENTINUITY LIVE WALLET TRUTH WRAPPER V4 ---

