# coding: utf-8
"""
PAPER_LIVE_WALLET_SEPARATION_PERM_20260702_SIGNOFF

Keeps paper and live wallets separate while preventing old paper-mode gates from
blocking because legacy wallet_balance/WALLET_BALANCE_USD is zero.

Rules:
- Paper wallet truth: PAPER_* keys, paper_wallet, system_state.paper_*.
- Live wallet truth: LIVE_* / REAL_LIVE_* / SOLANA_LIVE_* keys from live sync.
- This guard never writes LIVE_* wallet keys.
- In paper-only mode, it sets legacy wallet_balance/WALLET_BALANCE_USD to paper
  cash for compatibility with old gates only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

PAPER_KEYS_CASH = (
    "PAPER_WALLET_CASH_USD",
    "SOLANA_PAPER_CASH_USD",
    "PAPER_CASH",
    "PAPER_BALANCE_USD",
)

PAPER_KEYS_EQUITY = (
    "PAPER_WALLET_EQUITY_USD",
    "PAPER_EQUITY_USD",
    "PAPER_EQUITY",
)

LIVE_KEYS = (
    "LIVE_WALLET_BALANCE_USD",
    "LIVE_WALLET_USD",
    "REAL_LIVE_WALLET_USD",
    "SOLANA_LIVE_WALLET_USD",
    "LIVE_AVAILABLE_USD",
    "LIVE_BALANCE_USD",
    "LAST_REAL_WALLET_USD",
)

def _safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default

def _get_cfg(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key=? LIMIT 1", (key,)).fetchone()
        if row and row[0] not in (None, ""):
            return str(row[0])
    except Exception:
        pass
    return default

def _upsert_cfg(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("""
        INSERT INTO system_config(key,value)
        VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))

def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

def _read_paper_cash_equity(conn: sqlite3.Connection, default_usd: float = 450.0) -> tuple[float, float, str]:
    cash = 0.0
    equity = 0.0

    for key in PAPER_KEYS_CASH:
        v = _safe_float(_get_cfg(conn, key), None)
        if v is not None and v > 0:
            cash = float(v)
            break

    for key in PAPER_KEYS_EQUITY:
        v = _safe_float(_get_cfg(conn, key), None)
        if v is not None and v > 0:
            equity = float(v)
            break

    try:
        cols = _cols(conn, "system_state")
        row = conn.execute("SELECT * FROM system_state WHERE id=1 LIMIT 1").fetchone()
        if row:
            if cash <= 0 and "paper_cash" in cols:
                cash = float(row["paper_cash"] or 0.0)
            if equity <= 0 and "paper_equity" in cols:
                equity = float(row["paper_equity"] or 0.0)
    except Exception:
        pass

    try:
        cols = _cols(conn, "paper_wallet")
        if cols and "wallet_name" in cols:
            row = conn.execute("SELECT * FROM paper_wallet WHERE wallet_name='main' LIMIT 1").fetchone()
            if row:
                if cash <= 0 and "cash_balance" in cols:
                    cash = float(row["cash_balance"] or 0.0)
                if equity <= 0 and "equity" in cols:
                    equity = float(row["equity"] or 0.0)
    except Exception:
        pass

    if cash <= 0 and equity > 0:
        cash = equity
    if equity <= 0 and cash > 0:
        equity = cash
    if cash <= 0 and equity <= 0:
        cash = equity = float(default_usd)

    return float(cash), float(equity), "paper_ledger"

def assert_wallet_separation(default_paper_usd: float = 450.0, verbose: bool = True) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    out: dict[str, Any] = {}
    try:
        mode = str(_get_cfg(conn, "TRADING_MODE", "paper") or "paper").strip().lower()
        paper_enabled = str(_get_cfg(conn, "PAPER_TRADING_ENABLED", "1") or "1").strip().lower() in ("1","true","yes","on")
        live_enabled = str(_get_cfg(conn, "LIVE_TRADING_ENABLED", "0") or "0").strip().lower() in ("1","true","yes","on")
        live_money = str(_get_cfg(conn, "LIVE_MONEY_MODE", "0") or "0").strip().lower() in ("1","true","yes","on")

        cash, equity, source = _read_paper_cash_equity(conn, default_paper_usd=default_paper_usd)
        now = time.time()

        for key in PAPER_KEYS_CASH:
            _upsert_cfg(conn, key, f"{cash:.6f}")
        for key in PAPER_KEYS_EQUITY:
            _upsert_cfg(conn, key, f"{equity:.6f}")
        _upsert_cfg(conn, "PAPER_WALLET_BALANCE_USD", f"{equity:.6f}")
        _upsert_cfg(conn, "PAPER_WALLET_SEPARATION_GUARD", "1")
        _upsert_cfg(conn, "PAPER_WALLET_SEPARATION_GUARD_AT", f"{now:.3f}")
        _upsert_cfg(conn, "PAPER_WALLET_TRUTH_SOURCE", source)
        _upsert_cfg(conn, "PAPER_WALLET_TRACKS_LIVE", "0")

        if mode == "paper" or paper_enabled:
            _upsert_cfg(conn, "TRADING_MODE", "paper")
            _upsert_cfg(conn, "PAPER_TRADING_ENABLED", "1")

        if mode == "paper" and not live_enabled and not live_money:
            _upsert_cfg(conn, "LIVE_TRADING_ENABLED", "0")
            _upsert_cfg(conn, "LIVE_MONEY_MODE", "0")
            _upsert_cfg(conn, "LIVE_ARMED", "0")
            _upsert_cfg(conn, "EXECUTION_ARMED", "0")
            _upsert_cfg(conn, "WALLET_BALANCE_USD", f"{cash:.6f}")
            _upsert_cfg(conn, "LEGACY_WALLET_BALANCE_COMPAT_MODE", "paper_only")
            _upsert_cfg(conn, "LEGACY_WALLET_BALANCE_COMPAT_SOURCE", "paper_cash_not_live")

        try:
            conn.execute("CREATE TABLE IF NOT EXISTS system_state(id INTEGER PRIMARY KEY)")
            cols = _cols(conn, "system_state")
            for col, typ in [
                ("paper_cash", "REAL DEFAULT 0"),
                ("paper_equity", "REAL DEFAULT 0"),
                ("paper_reserved", "REAL DEFAULT 0"),
                ("paper_unrealized_pnl", "REAL DEFAULT 0"),
                ("wallet_balance", "REAL DEFAULT 0"),
                ("initial_capital", "REAL DEFAULT 0"),
                ("updated_at", "TEXT"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE system_state ADD COLUMN {col} {typ}")
            conn.execute("INSERT OR IGNORE INTO system_state(id) VALUES(1)")

            sets = [
                "paper_cash=?",
                "paper_equity=?",
                "paper_reserved=COALESCE(paper_reserved,0)",
                "paper_unrealized_pnl=COALESCE(paper_unrealized_pnl,0)",
                "updated_at=?",
            ]
            vals: list[Any] = [cash, equity, str(now)]

            if mode == "paper" and not live_enabled and not live_money:
                sets.append("wallet_balance=?")
                vals.append(cash)
                sets.append("initial_capital=?")
                vals.append(equity)

            vals.append(1)
            conn.execute("UPDATE system_state SET " + ", ".join(sets) + " WHERE id=?", vals)
        except Exception as exc:
            out["system_state_error"] = str(exc)

        try:
            cols = _cols(conn, "paper_wallet")
            if cols and "wallet_name" in cols:
                conn.execute("INSERT OR IGNORE INTO paper_wallet(wallet_name) VALUES('main')")
                sets = []
                vals = []
                if "cash_balance" in cols:
                    sets.append("cash_balance=?"); vals.append(cash)
                if "equity" in cols:
                    sets.append("equity=?"); vals.append(equity)
                if "updated_at" in cols:
                    sets.append("updated_at=?"); vals.append(now)
                if "reserved_stake" in cols:
                    sets.append("reserved_stake=COALESCE(reserved_stake,0)")
                if "open_value" in cols:
                    sets.append("open_value=COALESCE(open_value,0)")
                if sets:
                    vals.append("main")
                    conn.execute("UPDATE paper_wallet SET " + ", ".join(sets) + " WHERE wallet_name=?", vals)
        except Exception as exc:
            out["paper_wallet_error"] = str(exc)

        live_snapshot = {k: _get_cfg(conn, k, "") for k in LIVE_KEYS}
        conn.commit()

        out.update({
            "mode": mode,
            "paper_enabled": paper_enabled,
            "live_enabled": live_enabled,
            "live_money_mode": live_money,
            "paper_cash": cash,
            "paper_equity": equity,
            "legacy_wallet_compat": bool(mode == "paper" and not live_enabled and not live_money),
            "live_keys_untouched": live_snapshot,
        })

        if verbose:
            print("[WALLET_SEPARATION] mode=%s paper_enabled=%s live_enabled=%s live_money=%s" % (mode, paper_enabled, live_enabled, live_money))
            print("[WALLET_SEPARATION] paper_cash=%.6f paper_equity=%.6f source=%s" % (cash, equity, source))
            if out["legacy_wallet_compat"]:
                print("[WALLET_SEPARATION] legacy wallet_balance/WALLET_BALANCE_USD set to paper cash for PAPER-ONLY compatibility")
            print("[WALLET_SEPARATION] LIVE_* wallet truth not modified by this guard")
            print("[WALLET_SEPARATION] done")
        return out
    finally:
        conn.close()

if __name__ == "__main__":
    assert_wallet_separation()
