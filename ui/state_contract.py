# SENTINUITY_STATE_CONTRACT_INSTRUMENT_TRUTH_V3_20260728
# Read-only UI balance contract.
#
# V3 CHANGE (20260728) - INSTRUMENT TRUTH. Fixes the proven defect where this
# module fabricated a healthy-looking state against a dead backend:
#
#   Before: bt = BalanceTruth(updated_at=time.time())   # stamped BEFORE any I/O
#           source defaulted to "paper_positions"
#           paper_start defaulted to 250.0
#   Result: a database with NO TABLES AT ALL returned
#           paper_start=250.0 paper_equity=250.0 source='paper_positions'
#           updated_at=<now>   -- indistinguishable from a healthy $250 float.
#
# V3 contract:
#   * updated_at is NEVER the UI request time. It is the newest row timestamp
#     actually read from paper_positions, or 0.0 when nothing was read.
#   * source is NEVER "paper_positions" unless a successful read from that
#     table occurred.
#   * data_available / source / error_reason make these five states mutually
#     distinguishable: missing db, unreadable db, missing schema, empty table,
#     populated table.
#
# Back-compat: fields are ADDED only. Every V2 field name is retained with its
# V2 meaning, so master_console.py and any older consumer keep working.
# NO trading logic here.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Set

CONTRACT_VERSION = "3.0.0-2026-07-28"

# ---- canonical source states -------------------------------------------------
SOURCE_UNAVAILABLE = "UNAVAILABLE"                 # no db file / cannot connect
SOURCE_SCHEMA_MISSING = "SCHEMA_MISSING"           # db opens, paper_positions absent
SOURCE_EMPTY = "PAPER_POSITIONS_EMPTY"             # table exists, zero rows
SOURCE_POPULATED = "paper_positions"               # real read occurred
SOURCE_ERROR = "state_contract_error"              # unexpected failure

# Candidate timestamp columns on paper_positions, most authoritative first.
_TS_COLUMN_CANDIDATES = ("updated_at", "closed_at", "opened_at", "last_update", "mark_ts", "ts")


@dataclass
class BalanceTruth:
    # ---- paper ----
    paper_start: float = 0.0
    paper_equity: float = 0.0
    paper_cash: float = 0.0
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
    # ---- V3 INSTRUMENT TRUTH -------------------------------------------------
    # Defaults describe a DEAD backend. A healthy state must be earned by an
    # actual successful read; it can never be arrived at by falling through.
    data_available: bool = False
    source: str = SOURCE_UNAVAILABLE
    updated_at: float = 0.0
    staleness_sec: Optional[float] = None
    error_reason: Optional[str] = "not_read"
    baseline_is_configured: bool = False   # True when paper_start came from config
    contract_version: str = CONTRACT_VERSION

    # ---- convenience for renderers ------------------------------------------
    @property
    def is_disconnected(self) -> bool:
        return self.source in (SOURCE_UNAVAILABLE, SOURCE_ERROR)

    @property
    def is_schema_error(self) -> bool:
        return self.source == SOURCE_SCHEMA_MISSING

    @property
    def is_empty(self) -> bool:
        return self.source == SOURCE_EMPTY

    def display_state(self) -> str:
        """Single token a renderer can switch on. Never returns a healthy token
        unless a real read happened."""
        if self.source in (SOURCE_UNAVAILABLE, SOURCE_ERROR):
            return "DISCONNECTED"
        if self.source == SOURCE_SCHEMA_MISSING:
            return "SCHEMA ERROR"
        if self.source == SOURCE_EMPTY:
            return "NO POSITIONS"
        return "LIVE"


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


def _newest_row_ts(c: sqlite3.Connection, cols: Set[str]) -> float:
    """Newest timestamp ACTUALLY PRESENT in paper_positions.

    This is the only legitimate source of `updated_at`. If the table carries no
    recognisable timestamp column we return 0.0 rather than inventing one --
    an unknown data age must read as unknown, never as fresh.
    """
    present: List[str] = [col for col in _TS_COLUMN_CANDIDATES if col in cols]
    if not present:
        return 0.0
    if len(present) == 1:
        inner = f"COALESCE({present[0]}, 0)"
    else:
        inner = "MAX(" + ", ".join(f"COALESCE({col}, 0)" for col in present) + ")"
    try:
        r = c.execute(f"SELECT MAX({inner}) AS newest FROM paper_positions").fetchone()
        return max(0.0, _f(r["newest"] if r else 0.0, 0.0))
    except Exception:
        return 0.0


def _mark_unavailable(bt: BalanceTruth, source: str, reason: str) -> BalanceTruth:
    """Force the dead-backend shape. Never leaves a plausible balance behind."""
    bt.data_available = False
    bt.source = source
    bt.error_reason = reason
    bt.updated_at = 0.0
    bt.staleness_sec = None
    bt.paper_start = 0.0
    bt.paper_equity = 0.0
    bt.paper_cash = 0.0
    bt.paper_realized_pnl = 0.0
    bt.paper_unrealized_pnl = 0.0
    bt.paper_reserved = 0.0
    bt.paper_open_reserved = 0.0
    bt.paper_open_count = 0
    bt.paper_closed_count = 0
    bt.paper_roi_pct = 0.0
    bt.paper_cash_roi_pct = 0.0
    bt.baseline_is_configured = False
    return bt


def _read_live_block(bt: BalanceTruth, cfg: Dict[str, str]) -> None:
    """LIVE WALLET. Read ONLY from explicit live keys. We deliberately do NOT
    fall back to system_state.wallet_balance, because that column has
    historically been polluted with paper equity. If no live key is set, live is
    "not synced" and the UI shows a not-synced state instead of a fake number.
    """
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
        ("LIVE_AVAILABLE_USD", "SOLANA_LIVE_AVAILABLE_USD", "LIVE_CASH_USD", "PHANTOM_AVAILABLE_USD"),
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
    bt.live_wallet_synced = bool(live_key) and live_val > 0.0


def get_balance_truth(db_path: str | Path, fallback_initial: float = 250.0) -> BalanceTruth:
    """Read-only paper/live balance truth.

    Contract (V3): the returned object always states whether it is backed by a
    real read. It never stamps freshness from the UI request time and never
    claims source='paper_positions' without having read that table.
    """
    bt = BalanceTruth()   # defaults = DEAD. Health must be earned.

    # ---- gate 1: does the file exist at all? --------------------------------
    try:
        p = Path(db_path)
    except Exception as exc:
        return _mark_unavailable(bt, SOURCE_UNAVAILABLE, f"bad_db_path:{exc}")
    if not p.exists():
        return _mark_unavailable(bt, SOURCE_UNAVAILABLE, f"database_file_missing:{p}")

    # ---- gate 2: can we open it read-only? ---------------------------------
    try:
        c = _ro_conn(p)
    except Exception as exc:
        return _mark_unavailable(bt, SOURCE_UNAVAILABLE, f"connect_failed:{type(exc).__name__}:{exc}")

    try:
        with c:
            # Integrity probe: a corrupt or locked db must fail closed here
            # rather than silently returning defaults further down.
            try:
                c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            except Exception as exc:
                return _mark_unavailable(bt, SOURCE_UNAVAILABLE, f"unreadable:{type(exc).__name__}:{exc}")

            cfg = _cfg(c)
            state = _state_row(c)
            bt.trading_mode = str(cfg.get("TRADING_MODE", "paper") or "paper").strip().lower() or "paper"

            # ---- gate 3: schema present? ------------------------------------
            if not _table_exists(c, "paper_positions"):
                return _mark_unavailable(bt, SOURCE_SCHEMA_MISSING, "paper_positions_table_missing")

            cols = _cols(c, "paper_positions")

            # ---- baseline: only meaningful once schema exists ---------------
            baseline_default = _f(fallback_initial, 250.0) or 250.0
            bt.paper_start, baseline_key = _first_num(
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
            bt.baseline_is_configured = baseline_key is not None

            # ---- gate 4: any rows? ------------------------------------------
            try:
                row_count = int(c.execute("SELECT COUNT(*) n FROM paper_positions").fetchone()["n"] or 0)
            except Exception as exc:
                return _mark_unavailable(bt, SOURCE_SCHEMA_MISSING, f"paper_positions_unreadable:{exc}")

            if row_count == 0:
                # Distinct, legitimate state: the backend IS connected, the
                # schema IS correct, there is simply no trading history yet.
                # paper_start is the CONFIGURED starting value, explicitly
                # flagged as such -- not a fabricated balance.
                bt.data_available = True
                bt.source = SOURCE_EMPTY
                bt.error_reason = None
                bt.updated_at = 0.0
                bt.staleness_sec = None
                bt.paper_equity = bt.paper_start
                bt.paper_cash = bt.paper_start
                bt.paper_open_reserved = bt.paper_reserved
                _read_live_block(bt, cfg)
                return bt

            # ---- populated: real read from here on --------------------------
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

            _read_live_block(bt, cfg)
            bt.paper_open_reserved = bt.paper_reserved

            # ---- FRESHNESS: derived from data, never from the clock ---------
            newest = _newest_row_ts(c, cols)
            bt.updated_at = newest
            bt.staleness_sec = max(0.0, time.time() - newest) if newest > 0 else None

            bt.data_available = True
            bt.source = SOURCE_POPULATED
            bt.error_reason = None
            return bt

    except Exception as exc:
        return _mark_unavailable(bt, SOURCE_ERROR, f"{type(exc).__name__}:{exc}")


def load_world_state(db_path: str | Path) -> Dict[str, Any]:
    """Living World state contract (SENTINUITY_WORLD_STATE_20260712).

    Delegates to ui.world_state.load_world_state -- the read-only, schema-
    tolerant loader that feeds the canonical six-realm World tab
    (ui/sovereign_world.html via window.applySwState). Any failure degrades
    to {} so the world boots empty rather than crashing the hub.
    """
    try:
        from ui.world_state import load_world_state as _lws
        return _lws(db_path) or {}
    except Exception:
        return {}


def _age_text(bt: BalanceTruth) -> str:
    if bt.staleness_sec is None:
        return "age unknown"
    s = bt.staleness_sec
    if s < 60:
        return f"{s:.0f}s ago"
    if s < 3600:
        return f"{s/60:.0f}m ago"
    return f"{s/3600:.1f}h ago"


def render_balance_capsule(bt: BalanceTruth) -> None:
    """Compatibility renderer retained for older hub imports.

    V3: a fallback/degraded balance can never visually resemble canonical
    current truth. Disconnected and schema-error states render as an
    unmistakable red banner with NO dollar figures at all.
    """
    try:
        import streamlit as st
    except Exception:
        return

    try:
        if not getattr(bt, "data_available", False):
            label = bt.display_state()
            st.markdown(
                "<div style='padding:10px 12px;border:2px solid #FF4B4B;border-radius:12px;"
                "background:rgba(255,75,75,.12);font-family:Share Tech Mono,monospace;color:#FF9C9C;'>"
                f"<b style='color:#FF4B4B;'>&#9888; {label}</b> &nbsp; balance truth unavailable"
                f"<br><span style='font-size:11px;opacity:.85;'>source={bt.source} &nbsp; reason={bt.error_reason}</span>"
                "</div>",
                unsafe_allow_html=True,
            )
            return

        if bt.source == SOURCE_EMPTY:
            st.markdown(
                "<div style='padding:10px 12px;border:1px dashed #8EF9FF88;border-radius:12px;"
                "background:rgba(142,249,255,.06);font-family:Share Tech Mono,monospace;color:#8EF9FF;'>"
                "<b>NO POSITIONS</b> &nbsp; connected, no trading history yet"
                f"<br><span style='font-size:11px;opacity:.85;'>configured start ${bt.paper_start:,.2f} "
                "(configured value, not a traded balance)</span>"
                "</div>",
                unsafe_allow_html=True,
            )
            return

        st.markdown(
            f"<div style='padding:10px 12px;border:1px solid #FFD70055;border-radius:12px;background:rgba(255,215,0,.07);font-family:Share Tech Mono,monospace;'>"
            f"<b style='color:#FFD700;'>PAPER EQUITY</b> ${bt.paper_equity:,.2f} &nbsp; "
            f"<b style='color:#8EF9FF;'>CASH</b> ${bt.paper_cash:,.2f} &nbsp; "
            f"<b style='color:#14F195;'>OPEN RESERVED</b> ${bt.paper_open_reserved:,.2f}"
            f"<br><span style='font-size:11px;opacity:.75;'>{_age_text(bt)} &nbsp; source={bt.source}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# --- SENTINUITY LIVE WALLET TRUTH WRAPPER V4 ---
# One canonical funded-wallet source: live_wallet_state, written by
# services.live_wallet_sync from the SOLANA_PRIVATE_KEY-derived address.
#
# V3 NOTE: this wrapper must NEVER upgrade a dead-backend result into a
# healthy-looking one. If the underlying read failed, we return it untouched.
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
    # Dead backend stays dead. The live-wallet overlay is additive telemetry and
    # must not resurrect a result that failed its own read.
    if not getattr(bal, "data_available", False):
        return bal
    dbp = args[0] if args else kwargs.get("db_path")
    try:
        from services.live_wallet_truth import read_live_wallet_truth
        truth = read_live_wallet_truth(dbp, max_age_sec=180.0)
        return _sentinuity_patch_balance_obj_v4(bal, truth)
    except Exception:
        return bal
# --- END SENTINUITY LIVE WALLET TRUTH WRAPPER V4 ---
