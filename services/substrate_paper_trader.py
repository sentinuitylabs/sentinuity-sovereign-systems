"""
services/substrate_paper_trader.py — SUBSTRATE LANE PAPER EXECUTOR
==================================================================
The Substrate desk's own executor: deploys PAPER capital into council-
approved spot targets (alts/natives), marks them to market, exits on
TP/SL/time, and keeps a real ledger. Mirrors the main lane's paper/live
split — but the live side of this lane DOES NOT EXIST in code. There is
no live order path here to misconfigure. SUBSTRATE_LIVE_ENABLED is read
only to display intent; it cannot cause an order.

Council flow it honours:
  1. Council/Polaris research lands rows in substrate_targets
     (status='proposed', conviction 0..1, council_votes JSON).
  2. A target becomes deployable when status='approved_paper'
     (operator CLI or council governor flips it) — OR, if
     SUBSTRATE_COUNCIL_AUTO_APPROVE=1, when conviction >=
     SUBSTRATE_MIN_COUNCIL_CONVICTION (the "Polaris/council believes
     it's a solid play → auto deploy paper" mode you asked for).
  3. Auto-deploy opens a paper position sized SUBSTRATE_POSITION_SIZE_USD
     from SUBSTRATE_PAPER_CASH_USD, capped at SUBSTRATE_MAX_OPEN.
  4. Every action emits: system_heartbeat pulse, cognition_log
     (stage='SUBSTRATE'), substrate_trade_log row — so the hub's
     maintenance trace and the desk panel show the real organism working.

Pricing (legal public sources only):
  - price_source='matrix'           → market_snapshots/mtm by mint (own DB)
  - price_source='coingecko:<id>'   → CoinGecko free simple/price API,
                                      throttled to >= 60s per asset.
No scraping, no login bypass, no wagering or restricted market integrations.

Run:  python services/substrate_paper_trader.py          (loop, 30s)
      python services/substrate_paper_trader.py --once   (single pass)
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import time
from pathlib import Path

try:
    import requests as _rq
except Exception:                                    # offline-tolerant
    _rq = None

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
SERVICE = "substrate_paper_trader"
LOOP_S = 30
CG_URL = "https://api.coingecko.com/api/v3/simple/price"
_cg_cache: dict[str, tuple[float, float]] = {}       # id -> (price, fetched_at)


def _conn():
    c = sqlite3.connect(str(DB), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory = sqlite3.Row
    return c


def cfg(db, key, default=None, cast=str):
    r = db.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    if r is None or r[0] is None:
        return default
    try:
        return cast(r[0])
    except (TypeError, ValueError):
        return default


def set_cfg(db, key, value):
    db.execute("INSERT INTO system_config (key,value) VALUES (?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))


def heartbeat(db, note: str):
    try:
        db.execute("INSERT INTO system_heartbeat (service_name, last_pulse, note) "
                   "VALUES (?,?,?) ON CONFLICT(service_name) DO UPDATE SET "
                   "last_pulse=excluded.last_pulse, note=excluded.note",
                   (SERVICE, time.time(), note[:140]))
    except Exception:
        # heartbeat schema variant without note / unique key — degrade quietly
        try:
            db.execute("UPDATE system_heartbeat SET last_pulse=? WHERE "
                       "service_name=?", (time.time(), SERVICE))
        except Exception:
            pass


def trace(db, event: str, detail: str):
    ts = time.time()
    try:
        db.execute("INSERT INTO substrate_trade_log (ts,event,detail) VALUES (?,?,?)",
                   (ts, event, detail[:300]))
    except Exception:
        pass
    try:
        db.execute("INSERT INTO cognition_log (timestamp, stage, message) "
                   "VALUES (?,?,?)", (ts, "SUBSTRATE", f"{event}: {detail}"[:240]))
    except Exception:
        pass
    print(f"[{time.strftime('%H:%M:%S')}] {SERVICE}.{event} → {detail}")


# ── pricing ──────────────────────────────────────────────────────────────────
def price_matrix(db, mint: str) -> float | None:
    if not mint:
        return None
    for sql, args in (
        ("SELECT observed_price p, COALESCE(price_updated_at,updated_at,0) t "
         "FROM market_snapshots WHERE mint_address=? AND observed_price>0 "
         "ORDER BY t DESC LIMIT 1", (mint,)),
        ("SELECT price p, ts t FROM mtm WHERE mint_address=? "
         "ORDER BY ts DESC LIMIT 1", (mint,)),
    ):
        try:
            r = db.execute(sql, args).fetchone()
            if r and r["p"] and float(r["p"]) > 0:
                if time.time() - float(r["t"] or 0) <= 900:   # 15m max staleness
                    return float(r["p"])
        except Exception:
            continue
    return None


def price_coingecko(cg_id: str) -> float | None:
    if _rq is None:
        return None
    now = time.time()
    hit = _cg_cache.get(cg_id)
    if hit and now - hit[1] < 60:                       # throttle: 1/min/asset
        return hit[0]
    try:
        resp = _rq.get(CG_URL, params={"ids": cg_id, "vs_currencies": "usd"},
                       timeout=4, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return hit[0] if hit else None
        p = float(resp.json().get(cg_id, {}).get("usd") or 0)
        if p > 0:
            _cg_cache[cg_id] = (p, now)
            return p
    except Exception:
        pass
    return hit[0] if hit else None


def resolve_price(db, price_source: str, mint: str | None) -> float | None:
    src = (price_source or "").strip().lower()
    if src == "matrix":
        return price_matrix(db, mint or "")
    if src.startswith("coingecko:"):
        return price_coingecko(src.split(":", 1)[1])
    return None


# ── desk passes ──────────────────────────────────────────────────────────────
def expire_stale_pass(db):
    """Entry-freshness enforcer (substrate lane).

    Expires proposed/approved targets older than SUBSTRATE_SIGNAL_TTL_MIN so they
    (a) never deploy on a dead signal, and (b) unblock macro_channel to re-propose
    a fresh signal for that asset (macro_channel skips assets with an active target).
    """
    ttl_min = cfg(db, "SUBSTRATE_SIGNAL_TTL_MIN", 90.0, float)
    cutoff  = time.time() - ttl_min * 60.0
    rows = db.execute(
        "SELECT id, asset_symbol, status, created_at FROM substrate_targets "
        "WHERE status IN ('proposed','approved_paper') AND created_at < ?",
        (cutoff,)).fetchall()
    for r in rows:
        age_min = (time.time() - float(r["created_at"] or 0)) / 60.0
        db.execute("UPDATE substrate_targets SET status='expired', updated_at=? "
                   "WHERE id=?", (time.time(), r["id"]))
        trace(db, "signal_expired",
              f"target={r['id']} {r['asset_symbol']} age={age_min:.0f}min "
              f">ttl {ttl_min:.0f}min — expired, asset freed for fresh proposal")


def auto_approve_pass(db):
    """Council-conviction auto-approval (only if explicitly enabled)."""
    if cfg(db, "SUBSTRATE_COUNCIL_AUTO_APPROVE", "0") != "1":
        return
    thr = cfg(db, "SUBSTRATE_MIN_COUNCIL_CONVICTION", 0.75, float)
    rows = db.execute("SELECT id, asset_symbol, conviction FROM substrate_targets "
                      "WHERE status='proposed' AND conviction>=?", (thr,)).fetchall()
    for r in rows:
        db.execute("UPDATE substrate_targets SET status='approved_paper', "
                   "updated_at=?, review_note=COALESCE(review_note,'')||' | "
                   "council auto-approved conviction>=thr' WHERE id=?",
                   (time.time(), r["id"]))
        trace(db, "council_auto_approve",
              f"target={r['id']} {r['asset_symbol']} conv={r['conviction']:.2f}>=thr {thr}")


def deploy_pass(db):
    if cfg(db, "SUBSTRATE_AUTO_DEPLOY_PAPER", "1") != "1":
        return
    thr = cfg(db, "SUBSTRATE_MIN_COUNCIL_CONVICTION", 0.75, float)
    size = cfg(db, "SUBSTRATE_POSITION_SIZE_USD", 25.0, float)
    max_open = cfg(db, "SUBSTRATE_MAX_OPEN", 3, int)
    cash = cfg(db, "SUBSTRATE_PAPER_CASH_USD", 0.0, float)

    open_n = db.execute("SELECT COUNT(*) FROM substrate_paper_positions "
                        "WHERE status='OPEN'").fetchone()[0]
    if open_n >= max_open:
        return
    targets = db.execute(
        "SELECT * FROM substrate_targets WHERE status='approved_paper' AND "
        "conviction>=? ORDER BY conviction DESC", (thr,)).fetchall()
    for t in targets:
        if open_n >= max_open:
            break
        if cash < size:
            trace(db, "deploy_blocked", f"insufficient paper cash "
                  f"${cash:.2f} < ${size:.2f} for {t['asset_symbol']}")
            break
        already = db.execute("SELECT 1 FROM substrate_paper_positions WHERE "
                             "target_id=? AND status='OPEN'", (t["id"],)).fetchone()
        if already:
            continue
        _ttl_min = cfg(db, "SUBSTRATE_SIGNAL_TTL_MIN", 90.0, float)
        _age_min = (time.time() - float(t["created_at"] or 0)) / 60.0
        if _age_min > _ttl_min:
            db.execute("UPDATE substrate_targets SET status='expired', "
                       "updated_at=? WHERE id=?", (time.time(), t["id"]))
            trace(db, "deploy_blocked",
                  f"target={t['id']} {t['asset_symbol']} stale age={_age_min:.0f}min "
                  f">ttl {_ttl_min:.0f}min — refusing to enter a dead signal")
            continue
        px = resolve_price(db, t["price_source"], t["mint_address"])
        if not px or px <= 0:
            db.execute("UPDATE substrate_targets SET status='blocked_no_price', "
                       "updated_at=? WHERE id=?", (time.time(), t["id"]))
            trace(db, "deploy_blocked",
                  f"target={t['id']} {t['asset_symbol']} no fresh price from "
                  f"{t['price_source']} — honest block, nothing faked")
            continue
        # Phase 2: price-drift gate — refuse to enter after the move is made
        _ref = t["signal_ref_price"] if "signal_ref_price" in t.keys() else None
        if _ref and float(_ref) > 0:
            _drift = (px - float(_ref)) / float(_ref) * 100.0
            _max_drift = cfg(db, "SUBSTRATE_MAX_ENTRY_DRIFT_PCT", 1.5, float)
            if abs(_drift) > _max_drift:
                trace(db, "deploy_blocked",
                      f"target={t['id']} {t['asset_symbol']} price drifted {_drift:+.2f}% "
                      f"from signal ref ${float(_ref):.4f} (>{_max_drift:.1f}%) — "
                      f"move already made, not chasing into spread")
                continue
        qty = size / px
        now = time.time()
        db.execute(
            "INSERT INTO substrate_paper_positions (target_id, asset_symbol, "
            "price_source, status, opened_at, entry_price, qty, size_usd, "
            "last_price, last_price_at, peak_price, tp_pct, sl_pct, max_hold_sec) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["id"], t["asset_symbol"], t["price_source"], "OPEN", now, px, qty,
             size, px, now, px,
             cfg(db, "SUBSTRATE_TP_PCT", 20.0, float),
             cfg(db, "SUBSTRATE_SL_PCT", 8.0, float),
             cfg(db, "SUBSTRATE_MAX_HOLD_HOURS", 72.0, float) * 3600))
        cash -= size
        set_cfg(db, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        db.execute("INSERT INTO substrate_ledger (ts,event,amount_usd,"
                   "balance_after,ref) VALUES (?,?,?,?,?)",
                   (now, "OPEN", -size, cash, f"target:{t['id']}:{t['asset_symbol']}"))
        db.execute("UPDATE substrate_targets SET status='deployed_paper', "
                   "updated_at=? WHERE id=?", (now, t["id"]))
        open_n += 1
        trace(db, "paper_open", f"SUBSTRATE_PAPER_OPENED target={t['id']} "
              f"{t['asset_symbol']} @ {px:.6g} size=${size:.2f} conv={t['conviction']:.2f}")


def _ensure_writeoff_columns(db):
    """Idempotent: audit columns for system-inactivity write-offs."""
    try:
        cols = {r[1] for r in db.execute(
            "PRAGMA table_info(substrate_paper_positions)").fetchall()}
        if "writeoff" not in cols:
            db.execute("ALTER TABLE substrate_paper_positions "
                       "ADD COLUMN writeoff INTEGER DEFAULT 0")
        if "writeoff_market_pnl_usd" not in cols:
            db.execute("ALTER TABLE substrate_paper_positions "
                       "ADD COLUMN writeoff_market_pnl_usd REAL")
    except Exception:
        pass


def mark_and_exit_pass(db):
    # SYSTEM_INACTIVITY_WRITEOFF_20260723 — same doctrine as the Solana
    # Guardian's PRICE_COVERAGE_LOST scratch: a close forced by *our* absence
    # is an operational artifact, not strategy evidence, and must never
    # poison PnL, win-rate or strategy scores.
    #
    #   window still alive at restart  -> trade resumes, counts normally.
    #   window expired during blackout -> close as labelled scratch
    #                                     (exit==entry, pnl 0), real market
    #                                     PnL preserved in audit columns.
    now = time.time()
    _ensure_writeoff_columns(db)
    _last_pass = cfg(db, "SUBSTRATE_TRADER_LAST_PASS_TS", 0.0, float)
    _grace = cfg(db, "SUBSTRATE_INACTIVITY_GRACE_SEC",
                 max(3 * LOOP_S, 180.0), float)
    _blackout = bool(_last_pass > 0 and (now - _last_pass) > _grace)
    set_cfg(db, "SUBSTRATE_TRADER_LAST_PASS_TS", f"{now:.3f}")
    for p in db.execute("SELECT * FROM substrate_paper_positions "
                        "WHERE status='OPEN'").fetchall():
        # Deadline that fell inside a blackout: intended capture window is
        # gone; the desk never had the chance to sell inside it.
        _deadline = float(p["opened_at"]) + float(p["max_hold_sec"] or 1e12)
        if _blackout and _last_pass < _deadline <= now:
            entry = float(p["entry_price"]); qty = float(p["qty"])
            _mkt_px = resolve_price(
                db, p["price_source"],
                db.execute("SELECT mint_address FROM substrate_targets "
                           "WHERE id=?", (p["target_id"],)).fetchone()["mint_address"]
                if p["target_id"] else None)
            _mkt_pnl = ((_mkt_px - entry) * qty) if (_mkt_px and _mkt_px > 0) else None
            proceeds = float(p["size_usd"])          # scratch: capital back at entry
            cash = cfg(db, "SUBSTRATE_PAPER_CASH_USD", 0.0, float) + proceeds
            set_cfg(db, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
            db.execute("UPDATE substrate_paper_positions SET status='CLOSED', "
                       "closed_at=?, exit_price=?, exit_reason=?, pnl_usd=0, "
                       "pnl_pct=0, writeoff=1, writeoff_market_pnl_usd=? "
                       "WHERE id=?",
                       (now, entry, "TIME_EXIT_SYSTEM_INACTIVE", _mkt_pnl, p["id"]))
            db.execute("INSERT INTO substrate_ledger (ts,event,amount_usd,"
                       "balance_after,ref) VALUES (?,?,?,?,?)",
                       (now, "CLOSE_WRITEOFF", proceeds, cash,
                        f"pos:{p['id']}:{p['asset_symbol']}:SYSTEM_INACTIVE"))
            if p["target_id"]:
                db.execute("UPDATE substrate_targets SET status='closed', "
                           "updated_at=? WHERE id=?", (now, p["target_id"]))
            trace(db, "paper_writeoff",
                  f"SUBSTRATE_INACTIVITY_WRITEOFF pos={p['id']} "
                  f"{p['asset_symbol']} window ended during "
                  f"{(now - _last_pass)/60:.0f}min blackout — scratch close, "
                  f"market pnl ${(_mkt_pnl if _mkt_pnl is not None else 0):+.2f} "
                  f"quarantined (not counted)")
            continue
        px = resolve_price(db, p["price_source"],
                           db.execute("SELECT mint_address FROM substrate_targets "
                                      "WHERE id=?", (p["target_id"],)
                                      ).fetchone()["mint_address"]
                           if p["target_id"] else None)
        if not px or px <= 0:
            continue                                    # keep last real mark
        entry = float(p["entry_price"]); qty = float(p["qty"])
        peak = max(float(p["peak_price"] or px), px)
        pnl_pct = (px - entry) / entry * 100 if entry else 0.0
        db.execute("UPDATE substrate_paper_positions SET last_price=?, "
                   "last_price_at=?, peak_price=? WHERE id=?",
                   (px, now, peak, p["id"]))
        reason = None
        if pnl_pct >= float(p["tp_pct"] or 1e9):
            reason = "TAKE_PROFIT"
        elif pnl_pct <= -float(p["sl_pct"] or 1e9):
            reason = "STOP_LOSS"
        elif now - float(p["opened_at"]) >= float(p["max_hold_sec"] or 1e12):
            reason = "TIME_EXIT"
        if reason:
            pnl_usd = (px - entry) * qty
            proceeds = float(p["size_usd"]) + pnl_usd
            cash = cfg(db, "SUBSTRATE_PAPER_CASH_USD", 0.0, float) + proceeds
            set_cfg(db, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
            db.execute("UPDATE substrate_paper_positions SET status='CLOSED', "
                       "closed_at=?, exit_price=?, exit_reason=?, pnl_usd=?, "
                       "pnl_pct=? WHERE id=?",
                       (now, px, reason, pnl_usd, pnl_pct, p["id"]))
            db.execute("INSERT INTO substrate_ledger (ts,event,amount_usd,"
                       "balance_after,ref) VALUES (?,?,?,?,?)",
                       (now, "CLOSE", proceeds, cash,
                        f"pos:{p['id']}:{p['asset_symbol']}:{reason}"))
            if p["target_id"]:
                db.execute("UPDATE substrate_targets SET status='closed', "
                           "updated_at=? WHERE id=?", (now, p["target_id"]))
            trace(db, "paper_close", f"SUBSTRATE_PAPER_CLOSED pos={p['id']} "
                  f"{p['asset_symbol']} {reason} pnl=${pnl_usd:+.2f} ({pnl_pct:+.1f}%)")


def one_pass():
    db = _conn()
    try:
        # Live manual-sign staging is handled by substrate_portfolio_supervisor /
        # wallets.substrate_live_guard. This paper trader never stores keys or
        # sends transactions, so paper learning continues safely beside live tests.
        if cfg(db, "SUBSTRATE_LIVE_ENABLED", "0") == "1":
            trace(db, "live_gate_seen", "SUBSTRATE_LIVE_ENABLED=1; paper trader remains paper-only, live staging is manual-sign in substrate_live_orders")
        expire_stale_pass(db)
        auto_approve_pass(db)
        deploy_pass(db)
        mark_and_exit_pass(db)
        open_n = db.execute("SELECT COUNT(*) FROM substrate_paper_positions "
                            "WHERE status='OPEN'").fetchone()[0]
        cash = cfg(db, "SUBSTRATE_PAPER_CASH_USD", 0.0, float)
        heartbeat(db, f"substrate desk ok — open={open_n} cash=${cash:.2f} paper + manual-sign-live-gate")
        db.commit()
    finally:
        db.close()


def _ensure_ref_price_column(db):
    """Idempotent: add signal_ref_price to substrate_targets if missing."""
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(substrate_targets)").fetchall()}
        if "signal_ref_price" not in cols:
            db.execute("ALTER TABLE substrate_targets ADD COLUMN signal_ref_price REAL")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if not DB.exists():
        print(f"[{SERVICE}] DB not found at {DB} — run tools/init_substrate_desk.py "
              "from the bot root first")
        return
    # LOCK_FIX_20260624: startup schema-touch must not crash main() if
    # the shared DB is momentarily locked - retry briefly, then proceed.
    for _attempt in range(6):
        try:
            _ec = _conn()
            try:
                _ensure_ref_price_column(_ec); _ec.commit()
            finally:
                _ec.close()
            break
        except Exception as _se:
            if "lock" in str(_se).lower() and _attempt < 5:
                time.sleep(2.0); continue
            print(f"[{SERVICE}] startup schema note (continuing): {_se}")
            break
    # early heartbeat so the service registers ALIVE even before its
    # first full pass (a lock-delayed pass no longer looks like a crash).
    try:
        _hb = _conn()
        try:
            heartbeat(_hb, "substrate desk starting - paper + manual-sign live gate"); _hb.commit()
        finally:
            _hb.close()
    except Exception:
        pass
    print(f"[{SERVICE}] starting — paper executor; live orders are manual-sign gated elsewhere; loop {LOOP_S}s")
    # PHASE1_LOCK_DISCIPLINE_20260621: non-critical scout — on DB lock, back off
    # (skip-extend the sleep) instead of retrying at full rate and starving the
    # executor's price/mark writes.
    _lock_backoff = 0.0
    while True:
        try:
            one_pass()
            _lock_backoff = 0.0
        except Exception as e:
            _msg = str(e).lower()
            if "database is locked" in _msg or "database table is locked" in _msg:
                _lock_backoff = min(300.0, max(LOOP_S * 2, _lock_backoff * 2 or LOOP_S * 2))
                print(f"[{SERVICE}] DB_LOCK_BACKOFF sleep={_lock_backoff:.0f}s (yielding to executor)")
            else:
                print(f"[{SERVICE}] pass error (continuing): {type(e).__name__}: {e}")
        if a.once:
            break
        time.sleep(LOOP_S + _lock_backoff)


if __name__ == "__main__":
    main()
