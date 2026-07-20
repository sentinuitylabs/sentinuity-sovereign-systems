

"""
services/smart_wallet_trade_ingester.py — SIGNOFF_WALLET_TRADE_INGEST_20260611
===============================================================================
THE MISSING COPYTRADE LINK (Fable audit, build item 2).

Chain before this service:
  profiles(92) + fingerprints(45, all sub-elite) -> conviction fetches
  fingerprints WHERE quality>=50 AND copyability>=40 -> zero pass ->
  NO_ELITE_WALLETS forever. Root cause: nothing observes what the profiled
  wallets actually DO, so smart_wallet_trades=0 and fingerprint scores are
  un-evidenced skeletons.

This service closes the loop, OBSERVE-ONLY:
  JOB 1  OBSERVE   poll recent signatures for profiled/fingerprinted/tracked
                   wallets via the existing RPC env providers
                   (QUICKNODE_RPC / HELIUS_RPC / SOLANA_RPC_URL — same envs
                   the resolver uses), parse token buys/sells from
                   pre/postTokenBalances, write smart_wallet_trades
                   (+ smart_wallet_events, + wallet_entry_snapshots when the
                   table's columns permit).
  JOB 2  RESCORE   recompute wallet_entry_fingerprints quality/copyability
                   from OBSERVED completed trades only (>=3 completed in 14d).
                   Formulas documented in reasons_json. No evidence -> the
                   wallet honestly stays sub-elite.
  JOB 3  OVERLAP   when an observed BUY lands on a mint currently active in
                   market_snapshots, write a wallet_entry_likelihood_signals
                   row (mode=OBSERVE) + a smart_wallet_events row carrying
                   reasons_json (matched wallets, scores, risk).
  JOB 4  HYGIENE   the 5 forever-pending wallet_research_tasks are marked
                   stale with an explicit reason (no consumer exists; this
                   service supersedes them with on-chain evidence).

SAFETY CONTRACT
  - OBSERVE ONLY: zero engine imports, zero entry influence, zero latch/
    config/size writes. SMART_MONEY_READER_ENABLED stays wherever it is.
  - Budgeted RPC: WALLETS_PER_CYCLE x SIGS_PER_WALLET with spacing; provider
    rotation on failure; fail-open everywhere.
  - Non-destructive: INSERT OR IGNORE / guarded UPDATE only; own cursor table.
  - Kill switch: system_config SWTI_ENABLED=0 idles the loop instantly.

Run (BAT or manual):  python -m services.smart_wallet_trade_ingester
One observation pass: python -m services.smart_wallet_trade_ingester --once
"""
from __future__ import annotations


# SIGNOFF_ENV_LOAD_20260611
# Load repo .env when this service is run directly with:
#   python -m services.smart_wallet_trade_ingester
def _signoff_load_repo_env():
    import os
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass
    try:
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        return

_signoff_load_repo_env()
# END SIGNOFF_ENV_LOAD_20260611

import json
import os
import sqlite3
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = next((ROOT / c for c in ("sentinuity_matrix.db", "data/sentinuity_matrix.db")
                if (ROOT / c).exists()), ROOT / "sentinuity_matrix.db")

SERVICE = "smart_wallet_trade_ingester"
WSOL = "So11111111111111111111111111111111111111112"

WALLETS_PER_CYCLE = 12
SIGS_PER_WALLET = 15
REQUEST_SPACING_S = 0.25
CYCLE_SLEEP_S = 90
COMPLETED_MIN_FOR_SCORE = 3
SCORE_WINDOW_DAYS = 14
OVERLAP_WINDOW_S = 900  # observed buys within 15 min match active snapshots

_PROVIDERS = [(n, (os.environ.get(e) or "").strip()) for n, e in
              (("QUICKNODE", "QUICKNODE_RPC"), ("HELIUS", "HELIUS_RPC"),
               ("SOLANA", "SOLANA_RPC_URL"))]
_PROVIDERS = [(n, u) for n, u in _PROVIDERS if u]
_provider_idx = 0


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def _cfg(c: sqlite3.Connection, key: str, default: str) -> str:
    try:
        r = c.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return str(r["value"]) if r and r["value"] is not None else default
    except Exception:
        return default


def _table_cols(c: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _heartbeat(note: str, work: int = 0) -> None:
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO system_heartbeat(service_name, status, note, last_pulse,"
                " work_processed) VALUES(?, 'ALIVE', ?, ?, ?)"
                " ON CONFLICT(service_name) DO UPDATE SET status='ALIVE', note=excluded.note,"
                " last_pulse=excluded.last_pulse,"
                " work_processed=COALESCE(system_heartbeat.work_processed,0)+excluded.work_processed",
                (SERVICE, note[:300], time.time(), work))
            c.commit()
    except Exception:
        pass


# ── RPC layer (patchable for tests) ─────────────────────────────────────────
def _rpc(method: str, params: list, timeout: float = 12.0) -> Optional[Any]:
    global _provider_idx
    if not _PROVIDERS:
        return None
    for attempt in range(len(_PROVIDERS)):
        name, url = _PROVIDERS[(_provider_idx + attempt) % len(_PROVIDERS)]
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1,
                               "method": method, "params": params}).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode())
            if "result" in out:
                _provider_idx = (_provider_idx + attempt) % len(_PROVIDERS)
                return out["result"]
        except Exception:
            continue
    return None


# ── schema (own state only; wallet tables ensured via conviction module) ────
def _ensure_schema() -> None:
    try:
        sys.path.insert(0, str(ROOT))
        from services.smart_wallet_conviction import ensure_smart_wallet_schema
        ensure_smart_wallet_schema(DB_PATH)
    except Exception:
        pass
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS swti_wallet_cursor (
            wallet_address TEXT PRIMARY KEY,
            last_signature TEXT NOT NULL DEFAULT '',
            last_polled REAL NOT NULL DEFAULT 0,
            polls INTEGER NOT NULL DEFAULT 0)""")
        c.commit()


def _wallet_roster(c: sqlite3.Connection, limit: int = 200) -> List[str]:
    addrs: list[str] = []
    for table, col in (("smart_wallet_profiles", "wallet_address"),
                       ("wallet_entry_fingerprints", "wallet_address"),
                       ("tracked_wallets", "wallet_address"),
                       ("watched_wallets", "wallet_address")):
        if col not in _table_cols(c, table):
            continue
        try:
            for r in c.execute(f"SELECT DISTINCT {col} FROM {table} LIMIT ?", (limit,)):
                a = str(r[0] or "").strip()
                if 30 < len(a) < 50 and a not in addrs:
                    addrs.append(a)
        except Exception:
            continue
    return addrs


# ── tx parsing (pure; unit-testable) ─────────────────────────────────────────
def parse_wallet_token_deltas(tx: Dict[str, Any], wallet: str) -> List[Dict[str, Any]]:
    """Extract this wallet's token buys/sells from a parsed transaction.
    Returns [{mint, side, token_delta, sol_delta, est_price, block_time}]."""
    out: list[dict] = []
    try:
        meta = tx.get("meta") or {}
        if meta.get("err") is not None:
            return out
        block_time = float(tx.get("blockTime") or 0)
        msg = (tx.get("transaction") or {}).get("message") or {}
        keys = [k.get("pubkey") if isinstance(k, dict) else k
                for k in (msg.get("accountKeys") or [])]
        sol_delta = 0.0
        if wallet in keys:
            i = keys.index(wallet)
            pre = meta.get("preBalances") or []
            post = meta.get("postBalances") or []
            if i < len(pre) and i < len(post):
                sol_delta = (float(post[i]) - float(pre[i])) / 1e9

        def amounts(side_key: str) -> Dict[str, float]:
            res: Dict[str, float] = {}
            for b in meta.get(side_key) or []:
                if str(b.get("owner") or "") != wallet:
                    continue
                mint = str(b.get("mint") or "")
                if not mint or mint == WSOL:
                    continue
                ui = ((b.get("uiTokenAmount") or {}).get("uiAmount"))
                res[mint] = res.get(mint, 0.0) + float(ui or 0)
            return res

        pre_t, post_t = amounts("preTokenBalances"), amounts("postTokenBalances")
        for mint in set(pre_t) | set(post_t):
            delta = post_t.get(mint, 0.0) - pre_t.get(mint, 0.0)
            if abs(delta) < 1e-9:
                continue
            side = "BUY" if delta > 0 else "SELL"
            est_price = (abs(sol_delta) / abs(delta)) if (abs(delta) > 0 and
                         ((side == "BUY" and sol_delta < 0) or
                          (side == "SELL" and sol_delta > 0))) else 0.0
            out.append({"mint": mint, "side": side, "token_delta": delta,
                        "sol_delta": sol_delta, "est_price": est_price,
                        "block_time": block_time})
    except Exception:
        return out
    return out


# ── JOB 1: observe ───────────────────────────────────────────────────────────
def observe_wallets(max_wallets: int = WALLETS_PER_CYCLE) -> Dict[str, int]:
    stats = {"wallets": 0, "txs": 0, "buys": 0, "sells": 0, "completed": 0}
    with _conn() as c:
        roster = _wallet_roster(c)
        if not roster:
            return stats
        cur = {r["wallet_address"]: dict(r) for r in
               c.execute("SELECT * FROM swti_wallet_cursor")}
        roster.sort(key=lambda a: cur.get(a, {}).get("last_polled", 0))
        batch = roster[:max_wallets]

    for wallet in batch:
        stats["wallets"] += 1
        last_sig = cur.get(wallet, {}).get("last_signature", "")
        params = [wallet, {"limit": SIGS_PER_WALLET}]
        if last_sig:
            params[1]["until"] = last_sig
        sigs = _rpc("getSignaturesForAddress", params) or []
        newest_sig = sigs[0]["signature"] if sigs else last_sig
        for s in sigs:
            time.sleep(REQUEST_SPACING_S)
            tx = _rpc("getTransaction", [s["signature"],
                      {"encoding": "jsonParsed",
                       "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            stats["txs"] += 1
            for ev in parse_wallet_token_deltas(tx, wallet):
                _record_event(wallet, s["signature"], ev, stats)
        with _conn() as c:
            c.execute("INSERT INTO swti_wallet_cursor(wallet_address, last_signature,"
                      " last_polled, polls) VALUES(?,?,?,1)"
                      " ON CONFLICT(wallet_address) DO UPDATE SET"
                      " last_signature=excluded.last_signature,"
                      " last_polled=excluded.last_polled, polls=polls+1",
                      (wallet, newest_sig, time.time()))
            c.commit()
        time.sleep(REQUEST_SPACING_S)
    return stats


def _record_event(wallet: str, sig: str, ev: Dict[str, Any],
                  stats: Dict[str, int]) -> None:
    now = time.time()
    bt = ev["block_time"] or now
    with _conn() as c:
        try:
            if ev["side"] == "BUY":
                c.execute(
                    "INSERT OR IGNORE INTO smart_wallet_trades"
                    " (wallet_address, token_mint, token_symbol, buy_time, entry_price,"
                    "  source_name, ingested_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (wallet, ev["mint"], ev["mint"][:10], bt,
                     ev["est_price"], "onchain_observer", now))
                stats["buys"] += 1
            else:
                row = c.execute(
                    "SELECT id, buy_time, entry_price FROM smart_wallet_trades"
                    " WHERE wallet_address=? AND token_mint=? AND sell_time=0"
                    "   AND source_name='onchain_observer'"
                    " ORDER BY buy_time ASC LIMIT 1", (wallet, ev["mint"])).fetchone()
                if row:
                    entry = float(row["entry_price"] or 0)
                    realized_x = (ev["est_price"] / entry) if (entry > 0 and
                                                               ev["est_price"] > 0) else 0.0
                    c.execute(
                        "UPDATE smart_wallet_trades SET sell_time=?, exit_price=?,"
                        " realized_x=?, hold_seconds=? WHERE id=?",
                        (bt, ev["est_price"], realized_x,
                         max(0.0, bt - float(row["buy_time"] or bt)), row["id"]))
                    stats["completed"] += 1
                stats["sells"] += 1
            c.execute("INSERT INTO smart_wallet_events(event_time, event_type,"
                      " token_mint, message) VALUES(?,?,?,?)",
                      (now, f"OBS_{ev['side']}", ev["mint"],
                       json.dumps({"wallet": wallet, "sig": sig[:24],
                                   "token_delta": round(ev["token_delta"], 4),
                                   "sol_delta": round(ev["sol_delta"], 6),
                                   "est_price": ev["est_price"]})))
            # wallet_entry_snapshots: write only columns that exist (schema varies).
            # Live schema has NOT NULL snapshot_offset_seconds/source_name/ingested_at,
            # so include those to prevent BUY events rolling back on constraint failure.
            snap_cols = _table_cols(c, "wallet_entry_snapshots")
            cand = {"wallet_address": wallet, "token_mint": ev["mint"],
                    "snapshot_offset_seconds": 0, "snapshot_time": now,
                    "source_name": "onchain_observer", "ingested_at": now,
                    "source_freshness_seconds": max(0.0, now - bt),
                    "raw_json": json.dumps({"sig": sig, "side": ev["side"],
                                             "est_price": ev["est_price"],
                                             "basis": "wallet token balance delta"})}
            use = {k: v for k, v in cand.items() if k in snap_cols}
            if use and ev["side"] == "BUY":
                cols = ",".join(use)
                ph = ",".join("?" * len(use))
                c.execute(f"INSERT OR IGNORE INTO wallet_entry_snapshots({cols})"
                          f" VALUES({ph})", list(use.values()))
            c.commit()
        except Exception:
            c.rollback()


# ── JOB 2: fingerprint rescore from observed evidence ───────────────────────
def rescore_fingerprints() -> int:
    cutoff = time.time() - SCORE_WINDOW_DAYS * 86400
    updated = 0
    with _conn() as c:
        wallets = [r[0] for r in c.execute(
            "SELECT DISTINCT wallet_address FROM smart_wallet_trades"
            " WHERE source_name='onchain_observer' AND sell_time > 0"
            "   AND buy_time >= ?", (cutoff,))]
        for w in wallets:
            xs = [float(r["realized_x"] or 0) for r in c.execute(
                "SELECT realized_x FROM smart_wallet_trades"
                " WHERE wallet_address=? AND sell_time>0 AND buy_time>=?"
                "   AND source_name='onchain_observer'", (w, cutoff))]
            n = len(xs)
            if n < COMPLETED_MIN_FOR_SCORE:
                continue
            hit2 = sum(1 for x in xs if x >= 2) / n
            hit3 = sum(1 for x in xs if x >= 3) / n
            hit5 = sum(1 for x in xs if x >= 5) / n
            rug = sum(1 for x in xs if 0 < x <= 0.1) / n
            losers = sum(1 for x in xs if x < 0.5) / n
            med = statistics.median(xs)
            win = sum(1 for x in xs if x > 1.0) / n
            quality = max(0.0, min(100.0,
                          45 * win + 25 * min(med / 3.0, 1.0) +
                          15 * hit2 + 15 * (1 - rug)))
            copyability = max(0.0, min(100.0, 100 * (1 - losers) * (1 - rug)))
            reasons = {"basis": "observed_onchain_trades",
                       "window_days": SCORE_WINDOW_DAYS, "completed": n,
                       "win_share": round(win, 3), "median_x": round(med, 3),
                       "hit2x": round(hit2, 3), "rug_exposure": round(rug, 3),
                       "loser_share": round(losers, 3),
                       "formula": "quality=45*win+25*min(med/3,1)+15*hit2+15*(1-rug);"
                                  " copyability=100*(1-losers)*(1-rug)"}
            c.execute(
                "INSERT INTO wallet_entry_fingerprints(wallet_address, chain,"
                " wallet_style, wallet_quality_score, copyability_score,"
                " median_safe_x, hit_rate_2x, hit_rate_3x, hit_rate_5x,"
                " late_copy_failure_rate, rug_exposure_rate, updated_at, reasons_json)"
                " VALUES(?, 'solana', 'OBSERVED', ?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(wallet_address, chain) DO UPDATE SET"
                " wallet_quality_score=excluded.wallet_quality_score,"
                " copyability_score=excluded.copyability_score,"
                " median_safe_x=excluded.median_safe_x,"
                " hit_rate_2x=excluded.hit_rate_2x, hit_rate_3x=excluded.hit_rate_3x,"
                " hit_rate_5x=excluded.hit_rate_5x,"
                " late_copy_failure_rate=excluded.late_copy_failure_rate,"
                " rug_exposure_rate=excluded.rug_exposure_rate,"
                " updated_at=excluded.updated_at, reasons_json=excluded.reasons_json",
                (w, quality, copyability, med, hit2, hit3, hit5,
                 losers, rug, time.time(), json.dumps(reasons)))
            updated += 1
        c.commit()
    return updated


# ── JOB 3: live overlap signals ──────────────────────────────────────────────
def write_overlap_signals() -> int:
    now = time.time()
    written = 0
    with _conn() as c:
        rows = c.execute("""
            SELECT t.token_mint, COUNT(DISTINCT t.wallet_address) n,
                   GROUP_CONCAT(DISTINCT t.wallet_address) wallets
            FROM smart_wallet_trades t
            WHERE t.source_name='onchain_observer' AND t.buy_time >= ?
            GROUP BY t.token_mint""", (now - OVERLAP_WINDOW_S,)).fetchall()
        for r in rows:
            mint = r["token_mint"]
            active = c.execute(
                "SELECT 1 FROM market_snapshots WHERE mint_address=?"
                " AND COALESCE(candidate_state,'') NOT IN"
                " ('vetoed','exited','expired_stale','mtm') LIMIT 1", (mint,)).fetchone()
            if not active:
                continue
            wl = (r["wallets"] or "").split(",")[:10]
            fps = c.execute(
                "SELECT AVG(wallet_quality_score) q, AVG(copyability_score) cp,"
                " SUM(CASE WHEN wallet_quality_score>=50 AND copyability_score>=40"
                " THEN 1 ELSE 0 END) elite"
                " FROM wallet_entry_fingerprints WHERE wallet_address IN (%s)"
                % ",".join("?" * len(wl)), wl).fetchone()
            elite = int(fps["elite"] or 0)
            conv = min(1.0, (int(r["n"]) / 5.0) * (float(fps["q"] or 0) / 100.0))
            try:
                c.execute(
                    "INSERT OR IGNORE INTO wallet_entry_likelihood_signals"
                    " (token_mint, token_symbol, signal_time, matched_wallet_count,"
                    "  elite_wallet_count, wallet_entry_likelihood,"
                    "  copy_conviction_score, copy_latency_risk, veto_reason, mode)"
                    " VALUES(?,?,?,?,?,?,?,?,?, 'OBSERVE')",
                    (mint, mint[:10], now, int(r["n"]), elite,
                     min(1.0, int(r["n"]) / 3.0), conv,
                     "LOW" if elite else "UNKNOWN",
                     "" if elite else "MATCHED_BUT_SUB_ELITE"))
                c.execute("INSERT INTO smart_wallet_events(event_time, event_type,"
                          " token_mint, message) VALUES(?, 'OVERLAP_SIGNAL', ?, ?)",
                          (now, mint, json.dumps({
                              "reasons": {"matched_wallets": wl,
                                          "avg_quality": round(float(fps["q"] or 0), 1),
                                          "avg_copyability": round(float(fps["cp"] or 0), 1),
                                          "elite_count": elite,
                                          "window_s": OVERLAP_WINDOW_S,
                                          "basis": "direct on-chain buy overlap"}})))
                written += 1
            except Exception:
                pass
        c.commit()
    return written


# ── JOB 4: stuck research-task hygiene ───────────────────────────────────────
def retire_stuck_research_tasks() -> int:
    with _conn() as c:
        try:
            n = c.execute(
                "UPDATE wallet_research_tasks SET status='stale',"
                " result='No consumer service exists for browser-based research"
                " tasks; superseded by on-chain smart_wallet_trade_ingester"
                " (SIGNOFF_WALLET_TRADE_INGEST_20260611)', completed_at=?"
                " WHERE status='pending'", (time.time(),)).rowcount
            c.commit()
            return n or 0
        except Exception:
            return 0


def main() -> None:
    once = "--once" in sys.argv
    _ensure_schema()
    retired = retire_stuck_research_tasks()
    if retired:
        print(f"[hygiene] retired {retired} forever-pending wallet_research_tasks")
    if not _PROVIDERS:
        _heartbeat("NO RPC PROVIDERS (set QUICKNODE_RPC/HELIUS_RPC/SOLANA_RPC_URL)")
        print("[fatal-soft] no RPC env providers — heartbeat set, exiting")
        return
    print(f"[start] {SERVICE} OBSERVE-ONLY providers={[n for n, _ in _PROVIDERS]} "
          f"db={DB_PATH.name}")
    # PHASE1_LOCK_DISCIPLINE_20260621: non-critical observer — wrap the cycle so a
    # DB lock backs off instead of crashing the loop or hammering the DB and
    # starving the executor's price/mark writes.
    _lock_backoff = 0.0
    while True:
        try:
            with _conn() as c:
                if _cfg(c, "SWTI_ENABLED", "1") != "1":
                    _heartbeat("disabled via SWTI_ENABLED=0")
                    if once:
                        return
                    time.sleep(CYCLE_SLEEP_S)
                    continue
            s = observe_wallets()
            fp = rescore_fingerprints()
            ov = write_overlap_signals()
            note = (f"wallets={s['wallets']} txs={s['txs']} buys={s['buys']} "
                    f"sells={s['sells']} completed={s['completed']} "
                    f"fp_rescored={fp} overlap_signals={ov} mode=OBSERVE")
            _heartbeat(note, work=s["txs"])
            print(f"[cycle] {note}")
            _lock_backoff = 0.0
        except Exception as e:
            _msg = str(e).lower()
            if "database is locked" in _msg or "database table is locked" in _msg:
                _lock_backoff = min(300.0, max(CYCLE_SLEEP_S * 2, _lock_backoff * 2 or CYCLE_SLEEP_S * 2))
                print(f"[{SERVICE}] DB_LOCK_BACKOFF sleep={_lock_backoff:.0f}s (yielding to executor)")
                try:
                    _heartbeat(f"THROTTLED_DB_LOCK backoff={_lock_backoff:.0f}s")
                except Exception:
                    pass
            else:
                print(f"[{SERVICE}] cycle error (continuing): {type(e).__name__}: {e}")
        if once:
            return
        time.sleep(CYCLE_SLEEP_S + _lock_backoff)


if __name__ == "__main__":
    main()
