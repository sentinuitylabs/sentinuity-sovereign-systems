#!/usr/bin/env python3
"""
STOP_BASIS_REPAIR_20260804 — deterministic tests T1..T10.

Runs entirely on in-memory / temp SQLite with stubbed core.schema and
live_trading. Makes no network calls, opens no production database, and
imports nothing that can sign or submit a transaction.

    python -m pytest tests/test_stop_realisability_basis.py -q
    python tests/test_stop_realisability_basis.py          # no pytest needed
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WSOL = "So11111111111111111111111111111111111111112"

# ─────────────────────────────────────────────────────────────────────────────
# Stubs. Installed before importing the module under test.
# ─────────────────────────────────────────────────────────────────────────────
_CONFIG: dict = {}
_CONFIG_UPDATED: dict = {}


def _install_stubs():
    core = types.ModuleType("core")
    schema = types.ModuleType("core.schema")

    def get_config_value(key, default=None):
        return _CONFIG.get(key, default)

    class _NullConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table: system_config")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    schema.get_config_value = get_config_value
    schema.get_connection = lambda: _NullConn()
    core.schema = schema
    sys.modules["core"] = core
    sys.modules["core.schema"] = schema

    services = sys.modules.setdefault("services", types.ModuleType("services"))
    services.__path__ = [str(ROOT / "services")]

    lt = types.ModuleType("services.live_trading")
    lt._SOL_MINT = WSOL
    lt._get_token_decimals = lambda mint: 6
    lt._sell_slippage_tiers = lambda: [100, 300]
    lt.validate_jupiter_route = lambda q: True
    lt._get_jupiter_quote = lambda mint, out, raw, bps: {
        "outAmount": str(int(raw * 0.10)),
        "otherAmountThreshold": str(int(raw * 0.095)),
        "priceImpactPct": "0.01",
        "routePlan": [{"swapInfo": {"label": "Raydium"}}],
    }
    sys.modules["services.live_trading"] = lt
    return lt


_LT = _install_stubs()
import services.stop_realisability as SR  # noqa: E402


def _fresh_db():
    c = sqlite3.connect(":memory:")
    SR.ensure_schema(c)
    return c


def _reset():
    _CONFIG.clear()
    _CONFIG_UPDATED.clear()
    SR._last_probe.clear()


class Clock:
    """Deterministic time.time() replacement; holds the final value."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def __call__(self):
        v = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return v


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    return bool(cond)


# ─────────────────────────────────────────────────────────────────────────────
# T1 — SOLANA_USD_PRICE is the primary key
# ─────────────────────────────────────────────────────────────────────────────
def t1_config_key():
    _reset()
    _CONFIG["SOLANA_USD_PRICE"] = 180.0
    b = SR.sol_usd_basis(None)
    check("T1 SOLANA_USD_PRICE resolves", b["value"] == 180.0, f"value={b['value']}")
    check("T1 source labelled config", b["source"] == "config:SOLANA_USD_PRICE",
          f"source={b['source']}")


# ─────────────────────────────────────────────────────────────────────────────
# T2 — SOL_USD_PRICE retained as an explicitly-labelled legacy fallback
# ─────────────────────────────────────────────────────────────────────────────
def t2_legacy_key():
    _reset()
    _CONFIG["SOL_USD_PRICE"] = 175.0
    b = SR.sol_usd_basis(None)
    check("T2 legacy key still works", b["value"] == 175.0, f"value={b['value']}")
    check("T2 legacy label present", "legacy" in (b["source"] or ""), f"source={b['source']}")


# ─────────────────────────────────────────────────────────────────────────────
# T3 — falls back to the INTELLIGENCE database, not the supplied connection
# ─────────────────────────────────────────────────────────────────────────────
def t3_correct_database():
    _reset()
    tmp = Path(tempfile.mkdtemp())
    intel = tmp / SR.INTEL_DB_NAME
    ic = sqlite3.connect(str(intel))
    ic.execute("CREATE TABLE mtm_ticks (mint_address TEXT, price_usd REAL, ts_ms INTEGER)")
    ic.execute("INSERT INTO mtm_ticks VALUES (?,?,?)",
               (WSOL, 190.5, int(time.time() * 1000)))
    ic.commit()
    ic.close()

    orig = SR._intel_db_path
    SR._intel_db_path = lambda: intel
    try:
        b = SR.sol_usd_basis(None)
        check("T3 intelligence DB fallback resolves", b["value"] == 190.5, f"value={b['value']}")
        check("T3 source names intel db", "intel_db" in (b["source"] or ""),
              f"source={b['source']}")
        check("T3 age recorded", b["age_sec"] is not None and b["age_sec"] < 60,
              f"age={b['age_sec']}")
    finally:
        SR._intel_db_path = orig


# ─────────────────────────────────────────────────────────────────────────────
# T4 — regression: must NEVER read mtm_ticks through the matrix connection
# ─────────────────────────────────────────────────────────────────────────────
def t4_wrong_database_regression():
    _reset()
    # A decoy matrix connection that WOULD answer if the old bug returned.
    decoy = sqlite3.connect(":memory:")
    decoy.execute("CREATE TABLE mtm_ticks (mint_address TEXT, price_usd REAL, ts_ms INTEGER)")
    decoy.execute("INSERT INTO mtm_ticks VALUES (?,?,?)", (WSOL, 999.99, 1))
    decoy.commit()

    orig = SR._intel_db_path
    SR._intel_db_path = lambda: Path("/nonexistent/sentinuity_intelligence.db")
    try:
        b = SR.sol_usd_basis(decoy)
        check("T4 decoy matrix value NOT used", b["value"] != 999.99, f"value={b['value']}")
        check("T4 fails closed with reason", b["value"] is None and b["error"],
              f"error={b['error']}")
    finally:
        SR._intel_db_path = orig
        decoy.close()

    src = (ROOT / "services" / "stop_realisability.py").read_text(encoding="utf-8")
    body = src[src.index("def sol_usd_basis"):]
    check("T4 no conn.execute on mtm_ticks in resolver",
          "conn.execute" not in body.split("def _sol_usd")[0])


# ─────────────────────────────────────────────────────────────────────────────
# T5 — missing basis must not be recorded as a plain success
# ─────────────────────────────────────────────────────────────────────────────
def t5_missing_basis():
    _reset()
    orig = SR._intel_db_path
    SR._intel_db_path = lambda: Path("/nonexistent/x.db")
    c = _fresh_db()
    try:
        SR.probe_stop(c, position_id=1, mint="Mint1", quantity=1000.0,
                      entry_price=1e-6, trigger_mark_price=9.6e-7,
                      intended_stop_pct=-4.0, position_size_usd=25.0,
                      credited_stop_pct=-4.0)
        r = c.execute("SELECT probe_status, integrity_status, probe_error, "
                      "executable_pct FROM stop_realisability_ledger").fetchone()
        check("T5 probe_status not bare 'ok'", r[0] != "ok", f"status={r[0]}")
        check("T5 integrity explains", r[1] == "QUOTE_ONLY_NO_USD_BASIS", f"integ={r[1]}")
        check("T5 probe_error populated", bool(r[2]), f"err={r[2]}")
        check("T5 executable_pct still null", r[3] is None)
    finally:
        SR._intel_db_path = orig
        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# T6 — executable_pct and companions computed correctly
# ─────────────────────────────────────────────────────────────────────────────
def t6_executable_pct():
    _reset()
    c = _fresh_db()
    # quantity 1000 @ 6dp -> raw 1e9. Quote returns min_out = 0.095 * raw
    # = 9.5e7 lamports = 0.095 SOL. At $200 -> gross $19.00.
    # fees: 0.000005 + 0.0005 SOL = 0.000505 SOL = $0.101 -> net $18.899.
    # size $25 -> executable_pct = (18.899-25)/25*100 = -24.404%
    SR.probe_stop(c, position_id=7, mint="MintX", quantity=1000.0,
                  entry_price=1e-6, trigger_mark_price=9.6e-7,
                  intended_stop_pct=-4.0, position_size_usd=25.0,
                  credited_stop_pct=-4.0, sol_usd=200.0)
    r = c.execute("SELECT executable_pct, executable_loss_usd, net_proceeds_usd, "
                  "gross_proceeds_usd, expected_exec_price, realisability_gap_pct, "
                  "probe_status, integrity_status, sol_usd_source, basis_version "
                  "FROM stop_realisability_ledger WHERE position_id=7").fetchone()
    ok = r[0] is not None and abs(r[0] - (-24.404)) < 0.01
    check("T6 executable_pct correct", ok, f"got={r[0]}")
    check("T6 executable_loss_usd", abs(r[1] - (-6.101)) < 0.01, f"got={r[1]}")
    check("T6 net_proceeds_usd", abs(r[2] - 18.899) < 0.01, f"got={r[2]}")
    check("T6 gross_proceeds_usd", abs(r[3] - 19.0) < 0.01, f"got={r[3]}")
    check("T6 expected_exec_price", r[4] is not None and abs(r[4] - 0.019) < 1e-6,
          f"got={r[4]}")
    check("T6 realisability_gap_pct", abs(r[5] - (-20.404)) < 0.01, f"got={r[5]}")
    check("T6 probe_status ok", r[6] == "ok", f"got={r[6]}")
    check("T6 integrity EXECUTABLE_MEASURED", r[7] == "EXECUTABLE_MEASURED", f"got={r[7]}")
    check("T6 basis_version stamped", r[9] == SR.BASIS_VERSION, f"got={r[9]}")
    c.close()


# ─────────────────────────────────────────────────────────────────────────────
# T7 — latency semantics with deterministic timestamps
# ─────────────────────────────────────────────────────────────────────────────
def t7_latency():
    _reset()
    c = _fresh_db()
    real = time.time
    SR.time.time = Clock([100.0, 100.4, 101.6, 101.9])
    try:
        SR.probe_stop(c, position_id=9, mint="MintT", quantity=1000.0,
                      entry_price=1e-6, trigger_mark_price=9.6e-7,
                      intended_stop_pct=-4.0, position_size_usd=25.0,
                      sol_usd=200.0)
    finally:
        SR.time.time = real
    r = c.execute("SELECT pre_quote_setup_sec, quote_network_sec, "
                  "trigger_to_quote_sec, quote_age_sec "
                  "FROM stop_realisability_ledger WHERE position_id=9").fetchone()
    check("T7 pre_quote_setup_sec=0.4", abs(r[0] - 0.4) < 1e-6, f"got={r[0]}")
    check("T7 quote_network_sec=1.2", abs(r[1] - 1.2) < 1e-6, f"got={r[1]}")
    check("T7 trigger_to_quote_sec=1.6", abs(r[2] - 1.6) < 1e-6, f"got={r[2]}")
    check("T7 quote_age_sec=0.3", abs(r[3] - 0.3) < 1e-6, f"got={r[3]}")
    c.close()


# ─────────────────────────────────────────────────────────────────────────────
# T8 — a clean cohort no longer fails on "no executable_pct measured"
# ─────────────────────────────────────────────────────────────────────────────
def _seed(c, n, pct, version, status="ok", lat=0.5, mints=8):
    for i in range(n):
        c.execute(
            "INSERT INTO stop_realisability_ledger "
            "(position_id, mint_address, trigger_ts, probe_status, no_route, "
            " executable_pct, trigger_to_quote_sec, basis_version, created_at) "
            "VALUES (?,?,?,?,0,?,?,?,?)",
            (i, f"mint{i % mints}", time.time(), status, pct, lat, version, time.time()))
    c.commit()


def t8_readiness_clean():
    _reset()
    c = _fresh_db()
    _seed(c, 120, -5.0, SR.BASIS_VERSION)
    r = SR.readiness(c)
    blocking = " | ".join(r["blocking"])
    check("T8 no 'no executable_pct measured'",
          "no executable_pct measured" not in blocking, blocking or "(none)")
    check("T8 status READY", r["status"] == SR.STATUS_READY,
          f"{r['status']} :: {blocking}")
    c.close()


# ─────────────────────────────────────────────────────────────────────────────
# T9 — a basis-less cohort stays blocked, for the right reason
# ─────────────────────────────────────────────────────────────────────────────
def t9_readiness_fail_closed():
    _reset()
    c = _fresh_db()
    _seed(c, 120, None, SR.BASIS_VERSION, status="ok_no_usd_basis")
    r = SR.readiness(c)
    blocking = " | ".join(r["blocking"])
    check("T9 still blocked", r["status"] != SR.STATUS_READY, r["status"])
    check("T9 reason mentions executable_pct",
          "no executable_pct measured" in blocking, blocking)

    # Legacy rows must not be able to satisfy the sample floor.
    c2 = _fresh_db()
    _seed(c2, 140, None, SR.LEGACY_BASIS_VERSION)
    r2 = SR.readiness(c2)
    check("T9 legacy cohort excluded from sample",
          r2["stats"]["n"] == 0, f"n={r2['stats']['n']}")
    check("T9 legacy cohort cannot pass", r2["status"] != SR.STATUS_READY, r2["status"])
    c.close()
    c2.close()


# ─────────────────────────────────────────────────────────────────────────────
# T10 — this pack alters no live authority
# ─────────────────────────────────────────────────────────────────────────────
def t10_no_live_submission():
    src = (ROOT / "services" / "stop_realisability.py").read_text(encoding="utf-8")
    banned = ["send_transaction", "sign_transaction", "_live_buy", "_live_sell",
              "execute_live", "submit(", "Keypair", "sendRawTransaction",
              "LIVE_ARMED", "LIVE_TRADING_ENABLED", "EXECUTION_ARMED",
              "MODE_B", "may_fire_canary", "reserve_attempt",
              "LIVE_POSITION_SIZE_USD"]
    hits = [b for b in banned if b in src]
    check("T10 no live/sender/arming symbols", not hits, f"found={hits}")
    for name, val in (("MIN_SAMPLES_ABSOLUTE", 50), ("MIN_SAMPLES_PREFERRED", 100),
                      ("MIN_QUOTE_COVERAGE_PCT", 95.0), ("MAX_NO_ROUTE_PCT", 3.0),
                      ("MAX_MEDIAN_STOP_PCT", -8.0), ("MAX_P90_STOP_PCT", -15.0),
                      ("MAX_WORST_STOP_PCT", -25.0),
                      ("MAX_MEDIAN_TRIGGER_TO_QUOTE_SEC", 1.5),
                      ("MAX_P90_TRIGGER_TO_QUOTE_SEC", 3.0),
                      ("MAX_MINT_CONCENTRATION_PCT", 25.0)):
        check(f"T10 threshold {name} unchanged", getattr(SR, name) == val,
              f"got={getattr(SR, name)}")


def main():
    print("STOP_BASIS_REPAIR_20260804 — deterministic tests\n")
    for fn in (t1_config_key, t2_legacy_key, t3_correct_database,
               t4_wrong_database_regression, t5_missing_basis, t6_executable_pct,
               t7_latency, t8_readiness_clean, t9_readiness_fail_closed,
               t10_no_live_submission):
        print(fn.__name__)
        fn()
        print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("=" * 62)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} assertions passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
