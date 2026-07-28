"""
services/paper_wallet_refresher.py — SIGNOFF_PAPER_WALLET_TRUTH_20260611
=========================================================================
Build item 5: the paper_wallet summary row froze on May 28
(updated_at=1779862897) while positions kept churning — every UI equity/ROI
figure read from it since has been STALE TRUTH.

This service recomputes the summary from the position ledger every cycle:
    realized   = SUM(realized_pnl_usd) over CLOSED paper_positions
    reserved   = SUM(position_size_usd) over OPEN paper_positions
    marked     = open value marked to latest known price
                 (entry value when no mark exists — honest, never invented)
    cash       = starting_balance + realized - reserved
    equity     = cash + marked
    updated_at = now    (the UI can finally show a truthful age)

Writes ONLY the existing columns of paper_wallet (schema-introspected).
Never touches positions, executions, config, or anything live.

Run:    python -m services.paper_wallet_refresher --once     (one-shot)
        python -m services.paper_wallet_refresher            (loop, 30s)
Kill switch: system_config PAPER_WALLET_REFRESH_ENABLED=0
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = next((ROOT / c for c in ("sentinuity_matrix.db", "data/sentinuity_matrix.db")
           if (ROOT / c).exists()), ROOT / "sentinuity_matrix.db")
SERVICE = "paper_wallet_refresher"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def refresh_once(verbose: bool = True) -> dict:
    now = time.time()
    with _conn() as c:
        flag = c.execute("SELECT value FROM system_config"
                         " WHERE key='PAPER_WALLET_REFRESH_ENABLED'").fetchone()
        if flag and str(flag[0]) == "0":
            return {"skipped": "disabled"}

        w = c.execute("SELECT * FROM paper_wallet LIMIT 1").fetchone()
        if not w:
            return {"skipped": "no paper_wallet row"}
        wallet = dict(w)
        starting = float(wallet.get("starting_balance") or 0)

        realized = float(c.execute(
            "SELECT COALESCE(SUM(realized_pnl_usd), 0) FROM paper_positions"
            " WHERE status='CLOSED'").fetchone()[0])
        open_rows = [dict(r) for r in c.execute(
            "SELECT position_size_usd, entry_price, current_price"
            " FROM paper_positions WHERE status='OPEN'")]
        reserved = sum(float(r["position_size_usd"] or 0) for r in open_rows)
        marked = 0.0
        for r in open_rows:
            size = float(r["position_size_usd"] or 0)
            e = float(r["entry_price"] or 0)
            x = float(r["current_price"] or 0)
            marked += size * (x / e) if (e > 0 and x > 0) else size  # no mark -> entry value, never invented
        cash = starting + realized - reserved
        equity = cash + marked

        cols = {r[1] for r in c.execute("PRAGMA table_info(paper_wallet)")}
        for add_col in ("reserved_stake", "open_value"):
            if add_col not in cols:
                c.execute(f"ALTER TABLE paper_wallet ADD COLUMN {add_col} REAL DEFAULT 0")
        cols = {r[1] for r in c.execute("PRAGMA table_info(paper_wallet)")}
        sets, vals = [], []
        for col, val in (("cash_balance", cash), ("equity", equity),
                         ("realized_pnl", realized), ("updated_at", now),
                         ("reserved_stake", reserved), ("open_value", marked)):
            if col in cols:
                sets.append(f"{col}=?")
                vals.append(val)
        c.execute(f"UPDATE paper_wallet SET {', '.join(sets)}"
                  f" WHERE wallet_name=?", vals + [wallet.get("wallet_name", "main")])
        
        # SENTINUITY_PAPER_WALLET_TRUTH_WRITER_20260623
        # The refresher is the sole display-key writer because it recomputes from ledger truth.
        _sent_unrealized = (float(marked or 0.0) - float(reserved or 0.0))
        for _k, _v in (
            # canonical equity aliases
            ("PAPER_EQUITY", equity),
            ("PAPER_EQUITY_USD", equity),
            ("PAPER_WALLET_EQUITY_USD", equity),
            ("SOLANA_PAPER_WALLET_EQUITY_USD", equity),
            # canonical cash/balance aliases
            ("PAPER_CASH", cash),
            ("PAPER_BALANCE_USD", cash),
            ("PAPER_WALLET_BALANCE", cash),
            ("PAPER_WALLET_BALANCE_USD", cash),
            ("PAPER_WALLET_CASH_USD", cash),
            ("SOLANA_PAPER_CASH_USD", cash),
            # reserved/open aliases
            ("PAPER_OPEN_RESERVED_USD", reserved),
            ("PAPER_RESERVED_USD", reserved),
            ("PAPER_WALLET_RESERVED_USD", reserved),
            # pnl aliases
            ("PAPER_REALIZED_PNL_USD", realized),
            ("PAPER_UNREALIZED_PNL_USD", _sent_unrealized),
        ):
            c.execute(
                "INSERT INTO system_config(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_k, f"{float(_v):.6f}"),
            )

        # Keep legacy system_state fields aligned with the same ledger truth.
        try:
            ss_cols = {r[1] for r in c.execute("PRAGMA table_info(system_state)")}
            ss_sets, ss_vals = [], []
            for _col, _val in (
                ("paper_equity", equity),
                ("paper_cash", cash),
                ("paper_reserved", reserved),
                ("paper_realized_pnl", realized),
                ("paper_unrealized_pnl", _sent_unrealized),
            ):
                if _col in ss_cols:
                    ss_sets.append(f"{_col}=?")
                    ss_vals.append(float(_val))
            # In paper mode only, keep wallet_balance non-negative for old gates,
            # but do not let it masquerade as live wallet truth.
            if "wallet_balance" in ss_cols:
                ss_sets.append("wallet_balance=?")
                ss_vals.append(float(cash))
            if "initial_capital" in ss_cols:
                ss_sets.append("initial_capital=?")
                ss_vals.append(float(starting))
            if ss_sets:
                c.execute("UPDATE system_state SET " + ", ".join(ss_sets) + " WHERE id=1", ss_vals)
        except Exception:
            pass
# heartbeat
        c.execute(
            "INSERT INTO system_heartbeat(service_name, status, note, last_pulse)"
            " VALUES(?, 'ALIVE', ?, ?)"
            " ON CONFLICT(service_name) DO UPDATE SET status='ALIVE',"
            " note=excluded.note, last_pulse=excluded.last_pulse",
            (SERVICE, f"cash=${cash:.2f} reserved=${reserved:.2f}"
                      f" marked=${marked:.2f} equity=${equity:.2f}"
                      f" realized=${realized:+.2f} open={len(open_rows)}", now))
        c.commit()
        out = {"cash": round(cash, 2), "reserved": round(reserved, 2),
               "marked": round(marked, 2), "equity": round(equity, 2),
               "realized": round(realized, 2), "open": len(open_rows)}
        if verbose:
            print(f"[refresh] {out}")
        return out


def main() -> None:
    if "--once" in sys.argv:
        refresh_once()
        return
    print(f"[start] {SERVICE} loop db={DB.name}")
    while True:
        try:
            refresh_once(verbose=False)
        except Exception as e:
            print(f"[warn] {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
