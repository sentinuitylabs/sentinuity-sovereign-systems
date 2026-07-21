#!/usr/bin/env python3
"""
launch/verify_substrate_lifecycle.py
===============================================================================
SUBSTRATE LIFECYCLE VERIFIER (SUBSTRATE_REAL_PRICE_20260721)

Deterministic, fully offline proof of the complete Substrate paper lifecycle
against a throwaway database (SENTINUITY_DB is pointed at a temp file — the
production DB is never touched). An injected fetch_json plays the price
provider, so every number below is reproducible.

PROVES (directive Phase 5):
  1. Scanned opportunities carry the PROVIDER's timestamp, never now().
  2. A paper position opens from a FRESH real price.
  3. Marks move with the market and record source + status.
  4. TAKE_PROFIT closes the position: realised PnL, cash restored with PnL,
     strategy score updated, audit trail written, opportunity PAPER_CLOSED.
  5. STOP_LOSS closes a losing position symmetrically.
  6. Provider outage: marks are skipped, the last real price and timestamp
     stay untouched, and no exit fires on stale data.
  7. SEED_MOCK rows are explicit (price_updated_at=0, status SEED_MOCK) and
     the ledger REFUSES to open a position from them.

Run:  python launch/verify_substrate_lifecycle.py
Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMPDIR = tempfile.mkdtemp(prefix="substrate_fixture_")
os.environ["SENTINUITY_DB"] = str(Path(_TMPDIR) / "fixture_matrix.db")

from wallets.substrate_wallet_schema import connect, ensure_schema, cfg_set  # noqa: E402
from wallets.substrate_paper_ledger import (  # noqa: E402
    open_paper_position_from_opportunity, close_paper_position,
)
from services.substrate_opportunity_scanner import scan_once  # noqa: E402
from services.substrate_portfolio_supervisor import (  # noqa: E402
    mark_open_positions, evaluate_exits,
)
from services import substrate_price_feed as feed  # noqa: E402
# Windows verifier console contract: force Unicode-safe output even when the
# parent console is cp1252. This changes presentation only, never test logic.
def _configure_verifier_console() -> None:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_configure_verifier_console()

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


class ScriptedProvider:
    """Plays CoinGecko. Prices and provider timestamps are fully scripted."""

    def __init__(self) -> None:
        self.prices = {"solana": 100.0, "weth": 3000.0,
                       "coinbase-wrapped-btc": 90000.0}
        self.last_updated_offset = 30.0   # provider ts = now - 30s → FRESH
        self.outage = False
        self.calls = 0

    def __call__(self, url: str, timeout: float) -> dict:
        self.calls += 1
        if self.outage:
            raise RuntimeError("scripted provider outage")
        if "coingecko" not in url:
            raise RuntimeError("scripted provider outage (jupiter disabled)")
        ts = time.time() - self.last_updated_offset
        return {cg_id: {"usd": px, "last_updated_at": ts}
                for cg_id, px in self.prices.items()}


def main() -> int:
    provider = ScriptedProvider()
    ensure_schema()
    con = connect()
    cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", "100.0")
    cfg_set(con, "SUBSTRATE_POSITION_SIZE_USD", "25.0")
    cfg_set(con, "SUBSTRATE_STOP_LOSS_PCT", "8")
    cfg_set(con, "SUBSTRATE_TAKE_PROFIT_PCT", "15")
    con.commit(); con.close()

    print("── 1. discovery from real provider timestamps ──────────────────")
    t0 = time.time()
    inserted = scan_once(fetch_json=provider)
    check("scanner inserted opportunities", inserted == 3, f"inserted={inserted}")
    con = connect()
    opp = dict(con.execute(
        "SELECT * FROM substrate_opportunities WHERE asset_symbol='SOL' "
        "ORDER BY id DESC LIMIT 1").fetchone())
    con.close()
    check("price is the provider's price", abs(opp["price_usd"] - 100.0) < 1e-9,
          str(opp["price_usd"]))
    check("price_updated_at is the PROVIDER ts (≈now-30s), never now",
          25.0 <= (t0 - opp["price_updated_at"]) <= 40.0,
          f"age={t0 - opp['price_updated_at']:.1f}s")
    check("price_status recorded FRESH", opp.get("price_status") == "FRESH")
    check("strategy attributed", opp.get("strategy_id") == "SUBSTRATE_CORE_SPOT_V1")

    print("── 2. paper open from a fresh real price ───────────────────────")
    res = open_paper_position_from_opportunity(int(opp["id"]))
    check("position opened", bool(res.get("ok")), str(res))
    pos_id = int(res.get("position_id") or 0)
    con = connect()
    cash_after_open = float(con.execute(
        "SELECT value FROM system_config WHERE key='SUBSTRATE_PAPER_CASH_USD'"
    ).fetchone()[0])
    con.close()
    check("cash debited by size", abs(cash_after_open - 75.0) < 1e-6,
          f"cash={cash_after_open}")

    print("── 3. marks move with the market ───────────────────────────────")
    provider.prices["solana"] = 103.0
    m = mark_open_positions(fetch_json=provider)
    check("one position marked", m["marked"] == 1, str(m))
    con = connect()
    row = dict(con.execute("SELECT * FROM substrate_positions WHERE id=?",
                           (pos_id,)).fetchone())
    con.close()
    check("current_price follows provider", abs(row["current_price"] - 103.0) < 1e-9)
    check("unrealized = (103-100)*qty", abs(row["unrealized_pnl"] - 3.0 * (25.0 / 100.0)) < 1e-6,
          f"upnl={row['unrealized_pnl']:.4f}")
    check("mark source + FRESH status recorded",
          row.get("mark_source") == "coingecko" and row.get("mark_status") == "FRESH")

    print("── 4. provider outage: stale is never dressed as healthy ───────")
    provider.outage = True
    before = row
    m2 = mark_open_positions(fetch_json=provider)
    con = connect()
    row2 = dict(con.execute("SELECT * FROM substrate_positions WHERE id=?",
                            (pos_id,)).fetchone())
    con.close()
    check("mark skipped during outage", m2["marked"] == 0 and m2["stale"] == 1,
          str({k: m2[k] for k in ('marked', 'stale')}))
    check("price untouched during outage",
          abs(row2["current_price"] - before["current_price"]) < 1e-12)
    check("marked_at untouched during outage",
          abs(float(row2["marked_at"]) - float(before["marked_at"])) < 1e-9)
    check("mark_status reports the truth",
          row2.get("mark_status") in ("STALE", "UNAVAILABLE"),
          str(row2.get("mark_status")))
    e0 = evaluate_exits(marks=m2.get("marks"))
    check("no exit fires on stale data", e0["closed"] == 0, str(e0))

    print("── 5. TAKE_PROFIT exit with attribution ────────────────────────")
    provider.outage = False
    provider.prices["solana"] = 116.0   # +16% ≥ +15% TP
    m3 = mark_open_positions(fetch_json=provider)
    e1 = evaluate_exits(marks=m3.get("marks"))
    check("TP closed exactly one position", e1["closed"] == 1, str(e1))
    con = connect()
    closed = dict(con.execute("SELECT * FROM substrate_positions WHERE id=?",
                              (pos_id,)).fetchone())
    cash_final = float(con.execute(
        "SELECT value FROM system_config WHERE key='SUBSTRATE_PAPER_CASH_USD'"
    ).fetchone()[0])
    score = con.execute(
        "SELECT * FROM substrate_strategy_scores WHERE strategy_id="
        "'SUBSTRATE_CORE_SPOT_V1'").fetchone()
    audits = con.execute(
        "SELECT COUNT(*) c FROM substrate_execution_audit WHERE allowed=1 "
        "AND reason LIKE 'paper_closed:TAKE_PROFIT%'").fetchone()["c"]
    opp_state = con.execute(
        "SELECT state FROM substrate_opportunities WHERE id=?",
        (int(opp["id"]),)).fetchone()[0]
    con.close()
    expected_pnl = (116.0 - 100.0) * (25.0 / 100.0)   # $4.00
    check("state CLOSED with TP reason",
          closed["state"] == "CLOSED"
          and str(closed["exit_reason"]).startswith("TAKE_PROFIT"))
    check("realised PnL = +$4.00", abs(closed["realized_pnl"] - expected_pnl) < 1e-6,
          f"{closed['realized_pnl']:+.4f}")
    check("cash = 100 + 4.00 (size returned + PnL)",
          abs(cash_final - (100.0 + expected_pnl)) < 1e-6, f"cash={cash_final}")
    check("strategy score updated (1 close, 1 win, +$4)",
          score is not None and score["closes"] == 1 and score["wins"] == 1
          and abs(score["realized_pnl"] - expected_pnl) < 1e-6)
    check("close audit row written", audits == 1)
    check("opportunity advanced to PAPER_CLOSED", opp_state == "PAPER_CLOSED")
    check("close is idempotent",
          close_paper_position(pos_id, 116.0, "DUP").get("reason") == "already_closed")

    print("── 6. STOP_LOSS symmetry ───────────────────────────────────────")
    provider.prices["weth"] = 3000.0
    con = connect()
    weth_opp = dict(con.execute(
        "SELECT * FROM substrate_opportunities WHERE asset_symbol='WETH' "
        "AND state='NEW' ORDER BY id DESC LIMIT 1").fetchone())
    con.close()
    res2 = open_paper_position_from_opportunity(int(weth_opp["id"]))
    check("WETH position opened", bool(res2.get("ok")), str(res2))
    provider.prices["weth"] = 3000.0 * 0.90   # -10% ≤ -8% stop
    m4 = mark_open_positions(fetch_json=provider)
    e2 = evaluate_exits(marks=m4.get("marks"))
    check("stop-loss closed the loser", e2["closed"] == 1 and e2["realized_pnl"] < 0,
          str(e2))

    print("── 7. SEED_MOCK is explicit and cannot open ────────────────────")
    con = connect()
    cfg_set(con, "SUBSTRATE_ALLOW_SEED_MOCK", "1")
    con.execute("DELETE FROM substrate_opportunities")   # fixture-only reset
    con.commit(); con.close()
    provider.outage = True
    inserted_mock = scan_once(fetch_json=provider)
    con = connect()
    mock = dict(con.execute(
        "SELECT * FROM substrate_opportunities WHERE asset_symbol='SOL' "
        "ORDER BY id DESC LIMIT 1").fetchone())
    con.close()
    check("mock rows inserted only under the explicit flag", inserted_mock == 3)
    check("mock labelled SEED_MOCK", mock.get("price_status") == "SEED_MOCK")
    check("mock carries NO market timestamp", float(mock["price_updated_at"]) == 0.0)
    res3 = open_paper_position_from_opportunity(int(mock["id"]))
    check("ledger refuses to open from SEED_MOCK",
          not res3.get("ok") and res3.get("reason") == "seed_mock_price_cannot_open",
          str(res3))

    print()
    if FAILURES:
        print(f"SUBSTRATE LIFECYCLE: FAIL ({len(FAILURES)}): {FAILURES}")
        return 1
    print("SUBSTRATE LIFECYCLE: PASS — discovery, open, real marks, honest "
          "staleness, TP/SL exits, attribution, mock quarantine all verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
