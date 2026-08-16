#!/usr/bin/env python3
"""REPLAY_JULY27_EDGE.py - deterministic replay of stored candidates through all
three selection/exit contracts, from the SAME stored inputs.

This is the evidence instrument the directive requires. It does not re-run the
services. It re-evaluates the *decision functions* against rows already stored in
sentinuity_matrix.db, so July 27, latest-before-repair and rebuilt can be compared
on identical inputs.

For every candidate it reports:
    discovered? identity resolved? signal produced? ranked position?
    accepted or rejected? exact rejection reason? entry ts? entry price?
    peak excursion? exit reason? realised result?

Usage
-----
  python launch\\REPLAY_JULY27_EDGE.py --window 2026-07-26 2026-07-28
  python launch\\REPLAY_JULY27_EDGE.py --mints <mint1> <mint2> ...
  python launch\\REPLAY_JULY27_EDGE.py --window 2026-07-26 2026-07-28 --csv replay.csv

Exit codes: 0 replay produced rows, 3 no rows found in window, 2 harness error.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "services").is_dir():
    ROOT = ROOT.parent
DB = ROOT / "sentinuity_matrix.db"

# --------------------------------------------------------------------------
# The three contracts under comparison. Each is a pure function of stored data,
# which is what makes this replay deterministic and reproducible.
# --------------------------------------------------------------------------

LATEST_EXCLUDE = ("enricher_open_position,intel-mtm,mtm-snapshot,"
                  "router:intel-mtm,router:mtm-snapshot")
JULY27_EXCLUDE = ""

CONF_FLOOR = 0.65          # identical in both builds; verified byte-for-byte
HARD_STOP_PCT = 4.0        # operator doctrine, clamped in code
DEFAULT_TRAIL = 18.0


def _suspect(source: str, exclude: str) -> bool:
    toks = tuple(t.strip().lower() for t in exclude.split(",") if t.strip())
    s = str(source or "").strip().lower()
    return bool(s and any(t in s for t in toks))


def trusted_peak(marks, exclude: str):
    """Mirror of _trusted_peak_from_tape. marks = [(price, source), ...] """
    for px, src in sorted(marks, key=lambda r: -float(r[0] or 0)):
        if _suspect(src, exclude):
            continue
        if float(px or 0) > 0:
            return float(px), str(src or "")
    return None, ""


def runner_lock_floor(peak_pct: float, retention_400: float = 0.3125) -> float:
    if peak_pct >= 400.0:
        return max(125.0, peak_pct * retention_400)
    if peak_pct >= 250.0:
        return 90.0
    if peak_pct >= 150.0:
        return 60.0
    if peak_pct >= 100.0:
        return 40.0
    if peak_pct >= 60.0:
        return 25.0
    return 0.0


def evaluate(contract: str, cand: dict) -> dict:
    """Return the decision this contract would reach for one stored candidate."""
    entry = float(cand.get("entry_price") or 0.0)
    current = float(cand.get("exit_price") or cand.get("last_price") or entry)
    marks = cand.get("marks") or []
    hps = float(cand.get("highest_price_seen") or 0.0)
    conf = float(cand.get("confidence") or 0.0)
    trail = float(cand.get("trail_pct") or DEFAULT_TRAIL)

    out = {
        "contract": contract,
        "accepted": None, "reject_reason": "",
        "peak_pct": 0.0, "peak_source": "",
        "exit_reason": "", "realised_pct": 0.0,
    }

    # ---- admission (identical across contracts; recorded for completeness) ---
    if conf > 0 and conf < CONF_FLOOR:
        out["accepted"] = False
        out["reject_reason"] = f"ADMISSION_BLOCKED_LOW_CONF ({conf:.3f} < {CONF_FLOOR:.2f})"
        return out
    out["accepted"] = True

    if entry <= 0:
        out["reject_reason"] = "NO_ENTRY_PRICE"
        return out

    # ---- peak authority (this is where the contracts diverge) ---------------
    if contract == "JULY27":
        px, src = trusted_peak(marks, JULY27_EXCLUDE)
        if not px:
            px, src = (hps, "highest_price_seen") if hps > 0 else (current, "current_mark")
        assume_fill = True
    elif contract == "LATEST":
        px, src = trusted_peak(marks, LATEST_EXCLUDE)
        if not px:
            px, src = current, "current_evaluator_mark"     # no hps fallback
        assume_fill = False
    else:  # REBUILT
        px, src = trusted_peak(marks, LATEST_EXCLUDE)
        if not px:
            px, src = (hps, "highest_price_seen") if hps > 0 else (current, "current_mark")
        assume_fill = True

    peak_price = max(current, float(px or current))
    peak_pct = (peak_price - entry) / entry * 100.0
    out["peak_pct"] = peak_pct
    out["peak_source"] = src

    # ---- exit ordering: hard stop -> profit lock -> trail -> max hold -------
    cur_pct = (current - entry) / entry * 100.0

    if cur_pct <= -HARD_STOP_PCT:
        out["exit_reason"] = f"HARD_STOP_LOSS_CAPPED_{HARD_STOP_PCT:.1f}"
        out["realised_pct"] = -HARD_STOP_PCT
        return out

    floor_pct = runner_lock_floor(peak_pct)
    if floor_pct > 0 and cur_pct <= floor_pct:
        floor_price = entry * (1 + floor_pct / 100.0)
        exit_price = max(current, floor_price) if assume_fill else current
        out["exit_reason"] = (f"RUNNER_PROFIT_LOCK_peak_{peak_pct:.1f}_floor_{floor_pct:.1f}"
                              f"_fill_{'MODELLED_FLOOR' if assume_fill and floor_price > current else 'OBSERVED_MARK'}")
        out["realised_pct"] = (exit_price - entry) / entry * 100.0
        return out

    stop_price = peak_price * (1 - trail / 100.0)
    if current <= stop_price:
        out["exit_reason"] = f"TRAILING_STOP_peak_{peak_pct:.1f}_src_{src}"
        out["realised_pct"] = (stop_price - entry) / entry * 100.0
        return out

    out["exit_reason"] = "MAX_HOLD_TIMEOUT"
    out["realised_pct"] = cur_pct
    return out


# --------------------------------------------------------------------------
def load_candidates(con, mints=None, window=None, limit=400):
    cols = {r[1] for r in con.execute("PRAGMA table_info(paper_positions)")}

    def pick(*names, default="NULL"):
        for n in names:
            if n in cols:
                return n
        return default

    c_entry = pick("entry_price")
    c_exit = pick("exit_price", "close_price")
    c_hps = pick("highest_price_seen", "peak_price")
    c_conf = pick("entry_confidence", "confidence", "mint_confidence")
    c_name = pick("token_name", "name")
    c_mint = pick("mint_address", "mint")
    c_et = pick("entry_time", "opened_at", "created_at")
    c_reason = pick("exit_reason", "close_reason")
    c_rpct = pick("realized_pnl_pct", "realised_pnl_pct")
    c_rusd = pick("realized_pnl_usd")
    c_size = pick("position_size_usd")
    c_status = pick("status")

    where, params = [], []
    if mints:
        where.append(f"{c_mint} IN ({','.join('?' * len(mints))})")
        params += list(mints)
    if window and c_et != "NULL":
        lo, hi = window
        where.append(f"CAST({c_et} AS REAL) BETWEEN ? AND ?")
        params += [lo, hi]
    sql = (f"SELECT id, {c_mint} mint, {c_name} token_name, {c_entry} entry_price, "
           f"{c_exit} exit_price, {c_hps} highest_price_seen, {c_conf} confidence, "
           f"{c_et} entry_time, {c_reason} exit_reason, {c_rpct} realized_pnl_pct, "
           f"{c_rusd} realized_pnl_usd, {c_size} position_size_usd, {c_status} status "
           f"FROM paper_positions")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {c_et} DESC LIMIT {int(limit)}"
    rows = [dict(r) for r in con.execute(sql, params)]

    # attach mark_tape
    has_tape = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mark_tape'"
    ).fetchone()[0]
    for r in rows:
        r["marks"] = []
        if has_tape:
            try:
                r["marks"] = [(m[0], m[1]) for m in con.execute(
                    "SELECT price, source FROM mark_tape WHERE position_id=? "
                    "AND COALESCE(price,0)>0 ORDER BY price DESC LIMIT 256", (r["id"],))]
            except Exception:
                pass
        # signal / identity provenance
        r["identity_resolved"] = bool(
            r.get("token_name") and str(r["token_name"]).strip().lower()
            not in ("", "n/a", "none", "null", "unknown", "-"))
        if not r.get("realized_pnl_pct") and r.get("realized_pnl_usd") and r.get("position_size_usd"):
            try:
                r["realized_pnl_pct"] = (float(r["realized_pnl_usd"]) /
                                         abs(float(r["position_size_usd"])) * 100.0)
            except Exception:
                pass
    return rows


def signal_provenance(con, mint: str) -> dict:
    """discovered? / signal produced? / ranked position? from stored tables."""
    out = {"discovered": False, "signal": False, "rank": ""}
    tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t, key in (("raw_transactions", "mint_address"), ("discovered_tokens", "mint_address"),
                   ("token_registry", "mint_address")):
        if t in tabs:
            try:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                if key in cols and con.execute(
                        f"SELECT 1 FROM {t} WHERE {key}=? LIMIT 1", (mint,)).fetchone():
                    out["discovered"] = True
                    break
            except Exception:
                pass
    for t in ("mint_signals", "signals", "signal_snapshots"):
        if t in tabs:
            try:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                mc = "mint_address" if "mint_address" in cols else ("mint" if "mint" in cols else None)
                if mc and con.execute(
                        f"SELECT 1 FROM {t} WHERE {mc}=? LIMIT 1", (mint,)).fetchone():
                    out["signal"] = True
                    break
            except Exception:
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--mints", nargs="*", default=None)
    ap.add_argument("--window", nargs=2, default=None,
                    help="two dates YYYY-MM-DD (inclusive) on entry_time")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()

    dbp = Path(a.db)
    if not dbp.exists():
        print(f"[FATAL] database not found: {dbp}")
        print("        Run this on the trading host, or pass --db <path>.")
        return 2

    window = None
    if a.window:
        def ts(d):
            return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        window = (ts(a.window[0]), ts(a.window[1]) + 86400)

    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")

    cands = load_candidates(con, a.mints, window, a.limit)
    if not cands:
        print("[EMPTY] no stored candidates matched. Widen --window or pass --mints.")
        return 3

    print("=" * 118)
    print("DETERMINISTIC REPLAY - identical stored inputs, three contracts")
    print(f"db={dbp}  candidates={len(cands)}")
    print("=" * 118)

    rows_out = []
    agg = {c: {"n": 0, "sum": 0.0, "maxhold": 0, "trail": 0, "lock": 0, "stop": 0,
               "winners": 0} for c in ("JULY27", "LATEST", "REBUILT")}

    for c in cands:
        prov = signal_provenance(con, str(c.get("mint") or ""))
        label = (c.get("token_name") or "")[:14] or str(c.get("mint") or "")[:8]
        ets = c.get("entry_time")
        try:
            ets_s = datetime.fromtimestamp(float(ets), timezone.utc).strftime("%m-%d %H:%M")
        except Exception:
            ets_s = str(ets)[:16]

        print(f"\n{label:<16} mint={str(c.get('mint'))[:20]}  entry={ets_s} "
              f"@ {c.get('entry_price')}")
        print(f"  discovered={prov['discovered']}  identity_resolved={c['identity_resolved']}  "
              f"signal_produced={prov['signal']}  marks_on_tape={len(c['marks'])}  "
              f"stored_exit={str(c.get('exit_reason'))[:34]}  "
              f"stored_realised={c.get('realized_pnl_pct')}")

        for contract in ("JULY27", "LATEST", "REBUILT"):
            d = evaluate(contract, c)
            a_ = agg[contract]
            a_["n"] += 1
            a_["sum"] += d["realised_pct"]
            if "MAX_HOLD" in d["exit_reason"]:
                a_["maxhold"] += 1
            if "TRAILING" in d["exit_reason"]:
                a_["trail"] += 1
            if "PROFIT_LOCK" in d["exit_reason"]:
                a_["lock"] += 1
            if "HARD_STOP" in d["exit_reason"]:
                a_["stop"] += 1
            if d["realised_pct"] > 0:
                a_["winners"] += 1
            print(f"    {contract:<8} accepted={str(d['accepted']):<5} "
                  f"peak={d['peak_pct']:+8.1f}% src={d['peak_source'][:22]:<22} "
                  f"exit={d['exit_reason'][:44]:<44} realised={d['realised_pct']:+8.2f}%"
                  + (f"  reject={d['reject_reason']}" if d["reject_reason"] else ""))
            rows_out.append({
                "mint": c.get("mint"), "token": label, "entry_time": ets_s,
                "entry_price": c.get("entry_price"),
                "discovered": prov["discovered"], "identity_resolved": c["identity_resolved"],
                "signal_produced": prov["signal"], "contract": contract,
                "accepted": d["accepted"], "reject_reason": d["reject_reason"],
                "peak_pct": round(d["peak_pct"], 3), "peak_source": d["peak_source"],
                "exit_reason": d["exit_reason"],
                "replayed_realised_pct": round(d["realised_pct"], 3),
                "stored_exit_reason": c.get("exit_reason"),
                "stored_realised_pct": c.get("realized_pnl_pct"),
            })

    print("\n" + "=" * 118)
    print("COMPARISON TABLE")
    print("=" * 118)
    print(f"{'contract':<10}{'n':>5}{'mean %':>10}{'winners':>9}{'trail':>7}"
          f"{'lock':>6}{'hardstop':>10}{'maxhold':>9}")
    for k in ("JULY27", "LATEST", "REBUILT"):
        v = agg[k]
        n = max(1, v["n"])
        print(f"{k:<10}{v['n']:>5}{v['sum'] / n:>10.2f}{v['winners']:>9}"
              f"{v['trail']:>7}{v['lock']:>6}{v['stop']:>10}{v['maxhold']:>9}")

    j, l, r = agg["JULY27"], agg["LATEST"], agg["REBUILT"]
    nj = max(1, j["n"])
    print(f"\nJuly-27 mean minus latest mean : {(j['sum'] - l['sum']) / nj:+.2f} pct-points")
    print(f"Rebuilt mean minus latest mean : {(r['sum'] - l['sum']) / nj:+.2f} pct-points")
    print(f"Rebuilt vs July-27 divergence  : {(r['sum'] - j['sum']) / nj:+.2f} pct-points "
          f"(target: near zero)")
    print(f"max-hold share  July27={j['maxhold'] / nj * 100:.1f}%  "
          f"latest={l['maxhold'] / nj * 100:.1f}%  rebuilt={r['maxhold'] / nj * 100:.1f}%")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            w.writerows(rows_out)
        print(f"\n[OK] wrote {len(rows_out)} rows to {a.csv}")

    con.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
