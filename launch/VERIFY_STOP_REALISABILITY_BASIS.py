#!/usr/bin/env python3
"""
STOP_BASIS_REPAIR_20260804 — verifier.

Read-only. Proves the basis resolves, shows the forward cohort, and prints the
readiness verdict with its blocking reasons.

    python launch\\VERIFY_STOP_REALISABILITY_BASIS.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "sentinuity_matrix.db").exists() else HERE.parent
DB = ROOT / "sentinuity_matrix.db"
TABLE = "stop_realisability_ledger"
sys.path.insert(0, str(ROOT))

FAILURES = []


def ck(label, ok, detail=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))]


def main() -> int:
    print("=" * 70)
    print("STOP REALISABILITY BASIS — VERIFIER")
    print("=" * 70)

    try:
        import services.stop_realisability as SR
    except Exception as exc:
        print(f"[FAIL] import: {type(exc).__name__}: {exc}")
        return 2

    print("\n--- 1. module contract ---")
    ck("sol_usd_basis present", hasattr(SR, "sol_usd_basis"))
    ck("BASIS_VERSION == 2", getattr(SR, "BASIS_VERSION", None) == 2)
    src = (ROOT / "services" / "stop_realisability.py").read_text(encoding="utf-8")
    resolver = src[src.index("def sol_usd_basis"):src.index("def _sol_usd")]
    ck("resolver never uses the supplied connection", "conn.execute" not in resolver)
    ck("SOLANA_USD_PRICE is primary", "SOLANA_USD_PRICE" in src)
    for name, val in (("MIN_SAMPLES_ABSOLUTE", 50), ("MIN_QUOTE_COVERAGE_PCT", 95.0),
                      ("MAX_NO_ROUTE_PCT", 3.0), ("MAX_MEDIAN_STOP_PCT", -8.0),
                      ("MAX_P90_STOP_PCT", -15.0), ("MAX_WORST_STOP_PCT", -25.0),
                      ("MAX_MEDIAN_TRIGGER_TO_QUOTE_SEC", 1.5),
                      ("MAX_P90_TRIGGER_TO_QUOTE_SEC", 3.0)):
        ck(f"threshold {name} unchanged", getattr(SR, name) == val, f"= {getattr(SR, name)}")

    print("\n--- 2. live SOL/USD basis resolution ---")
    b = SR.sol_usd_basis(None)
    ck("basis resolves", b["value"] is not None,
       f"value={b['value']} source={b['source']} age={b['age_sec']}")
    if b["value"] is None:
        print(f"         error: {b['error']}")
        print("         Set SOLANA_USD_PRICE in system_config or ensure the oracle")
        print("         is writing wSOL ticks to sentinuity_intelligence.db.")

    if not DB.exists():
        print("\n[FAIL] matrix DB missing")
        return 3
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone():
            print(f"\n[INFO] {TABLE} not created yet — run the system to gather probes.")
            return 1 if FAILURES else 0

        cols = {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
        print("\n--- 3. schema ---")
        for c in ("sol_usd_source", "sol_usd_age_sec", "quote_start_ts", "quote_end_ts",
                  "pre_quote_setup_sec", "quote_network_sec", "basis_version",
                  "cohort_reason"):
            ck(f"column {c}", c in cols)

        print("\n--- 4. cohorts ---")
        for r in con.execute(
                f"SELECT COALESCE(basis_version,1) v, COUNT(*) n, "
                f"SUM(executable_pct IS NOT NULL) with_exec FROM {TABLE} "
                "GROUP BY v ORDER BY v"):
            tag = "LEGACY (excluded from readiness)" if r["v"] < 2 else "FORWARD"
            print(f"  basis_version={r['v']}  n={r['n']:5d}  "
                  f"executable_pct populated={r['with_exec']:5d}   {tag}")

        # Deliverable 9 — the readiness evidence query.
        print("\n--- 5. forward cohort readiness evidence ---")
        rows = con.execute(
            f"SELECT executable_pct, trigger_to_quote_sec, quote_network_sec, "
            f"probe_status, no_route, mint_address, sol_usd_source, integrity_status "
            f"FROM {TABLE} WHERE COALESCE(basis_version,1) >= 2").fetchall()
        n = len(rows)
        ex = [r["executable_pct"] for r in rows if r["executable_pct"] is not None]
        lat = [r["trigger_to_quote_sec"] for r in rows if r["trigger_to_quote_sec"] is not None]
        net = [r["quote_network_sec"] for r in rows if r["quote_network_sec"] is not None]
        print(f"  sample count                 : {n}")
        print(f"  non-null executable_pct      : {len(ex)}")
        if ex:
            print(f"  median / p90 / worst exec_pct: "
                  f"{pct(ex,0.50):.2f}% / {pct(ex,0.10):.2f}% / {min(ex):.2f}%")
        if lat:
            print(f"  median / p90 trigger->quote  : {pct(lat,0.50):.3f}s / {pct(lat,0.90):.3f}s")
        if net:
            print(f"  median / p90 quote network   : {pct(net,0.50):.3f}s / {pct(net,0.90):.3f}s")
        if n:
            ok_n = sum(1 for r in rows if r["probe_status"] == "ok")
            nr = sum(1 for r in rows if r["no_route"] == 1)
            print(f"  quote coverage               : {100.0*ok_n/n:.1f}%")
            print(f"  no-route                     : {100.0*nr/n:.1f}%")
            mints = {}
            for r in rows:
                mints[r["mint_address"]] = mints.get(r["mint_address"], 0) + 1
            top = max(mints.values()) if mints else 0
            print(f"  distinct mints / top share   : {len(mints)} / {100.0*top/n:.1f}%")
            print("  USD basis source distribution:")
            srcs = {}
            for r in rows:
                srcs[r["sol_usd_source"] or "(none)"] = srcs.get(r["sol_usd_source"] or "(none)", 0) + 1
            for k, v in sorted(srcs.items(), key=lambda kv: -kv[1]):
                print(f"      {v:5d}  {k}")
            print("  integrity status distribution:")
            ints = {}
            for r in rows:
                ints[r["integrity_status"] or "(none)"] = ints.get(r["integrity_status"] or "(none)", 0) + 1
            for k, v in sorted(ints.items(), key=lambda kv: -kv[1]):
                print(f"      {v:5d}  {k}")

        print("\n--- 6. readiness verdict ---")
        rd = SR.readiness(con)
        print(f"  status: {rd['status']}")
        for blk in rd.get("blocking", []):
            print(f"    - {blk}")
        if n == 0:
            print("  (expected immediately after install: the forward cohort is empty"
                  " until the system runs)")
        ck("readiness not blocked solely by null executable_pct",
           not (len(ex) == 0 and n > 0 and
                any("no executable_pct" in b for b in rd.get("blocking", []))
                and len(rd.get("blocking", [])) == 1),
           "")
    finally:
        con.close()

    print("\n" + "=" * 70)
    print("NO LIVE AUTHORITY WAS ENABLED BY THIS PACK.")
    print("No live flag, gate, threshold, sizing or sender path was modified.")
    print("=" * 70)
    if FAILURES:
        print(f"FAILURES: {len(FAILURES)} -> " + ", ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
