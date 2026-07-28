"""
services/copytrade_influence.py — SIGNOFF_COPYTRADE_PAPER_BONUS_20260613
=========================================================================
THE COPYTRADE INFLUENCE CONTRACT (single source of truth).

Closes the "stub/noise-safe" gap: copytrade now reports an explicit lane
state instead of silently looking active, and may grant a *bounded,
paper-only, fully-ledgered* confidence bonus.

LANE STATES (get_lane_state):
  DISABLED_CONFIG_MISSING  schema/conviction module unavailable
  NO_WALLETS               no wallet source configured (profiles/fingerprints/tracked)
  NO_DATA                  wallets exist but zero observed smart_wallet_trades
  OBSERVING                ingester/scanner alive, data flowing, no fresh signals
  PAPER_SHADOW_READY       fresh likelihood signals exist; bonus flag OFF
  PAPER_BONUS_ELIGIBLE     fresh signals + COPYTRADE_PAPER_BONUS_ENABLED=1
  LIVE_OBSERVE_ONLY        TRADING_MODE=live — copytrade may be read, never consumed

SAFETY CONTRACT — non-negotiable, enforced in code, not by convention:
  1. evaluate_paper_bonus() returns 0.0 unless COPYTRADE_PAPER_BONUS_ENABLED=1
     (system_config, default 0) — byte-identical to "no influence" until armed.
  2. TRADING_MODE=live  →  bonus is ALWAYS 0.0. Live observes; never consumes.
  3. Hard cap: bonus can never exceed HARD_BONUS_CAP (+0.03), regardless of
     any config value. Config can only lower it.
  4. Near-qualified rule: bonus is only granted when baseline confidence is
     already within HARD_BONUS_CAP of the supervisor floor. Copytrade can
     nudge a near-qualified candidate over the line; it cannot resurrect junk.
  5. Copytrade cannot bypass: price freshness, signal age, liquidity/mcap
     floors, max-open-positions, blacklist, or stale-price veto. The bonus is
     applied to confidence ONLY, upstream of gates that veto independently of
     confidence. (Enforced structurally: this module touches nothing else.)
  6. Evidence required: fresh wallet_entry_likelihood_signals row
     (age <= COPYTRADE_SIGNAL_MAX_AGE_SEC, default 300s), no veto_reason,
     and (matched_wallets >= 2 OR elite_wallets >= 1).
  7. Sell-imbalance veto: if observed smart-wallet SELLs >= BUYs on the mint
     in the last 15 minutes, no bonus.
  8. Every decision (granted or denied, throttled to one row / mint / 120s)
     is written to copytrade_influence_ledger with baseline, bonus, final,
     would_have_passed_without_copytrade, and wallet evidence — the A/B spine.
  9. Fail-safe everywhere: any exception → (0.0, "CT_ERROR", {}). This module
     must never break supervision, execution, or close.

Consumers:
  neural_supervisor.py   evaluate_paper_bonus()  (paper lane only)
  execution_engine.py    record_outcome()        (close path, fire-and-forget)
  sovereign_hub.py       get_lane_state(), summary_for_ui()
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ── Core wiring with graceful fallbacks (services/ vs flat layout) ───────────
try:
    from core.schema import DB_PATH as _DB_PATH, get_config_value as _cfg
except Exception:
    try:
        from schema import DB_PATH as _DB_PATH, get_config_value as _cfg  # type: ignore
    except Exception:
        _DB_PATH = Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"
        def _cfg(key, default=None):  # type: ignore
            return default

try:
    from core.gate_trace import trace as _trace
except Exception:
    try:
        from gate_trace import trace as _trace  # type: ignore
    except Exception:
        def _trace(**kw):  # type: ignore
            pass

LEDGER_TABLE        = "copytrade_influence_ledger"
HARD_BONUS_CAP      = 0.03      # directive: paper bonus max +0.03 initially
LIVE_BONUS          = 0.00      # directive: live bonus 0.00 for now (do not change here)
SIGNAL_MAX_AGE_DEF  = 300.0     # fresh smart-wallet signal window (seconds)
SELL_PRESSURE_WIN   = 900.0     # 15 min sell-imbalance lookback
LEDGER_THROTTLE_SEC = 120.0     # max one ledger row per mint per 2 min (anti-spam)
CONVICTION_FLOOR    = 0.40      # below this, signal is too weak for any bonus

_last_ledger_write: Dict[str, Tuple[float, str]] = {}   # mint -> (ts, decision)


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────
def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_rw() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_influence_ledger() -> None:
    """Additive only. The A/B measurement spine (directive Part A item 6)."""
    try:
        conn = _connect_rw()
        try:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    token_mint TEXT NOT NULL,
                    symbol TEXT DEFAULT '',
                    wallet_count INTEGER DEFAULT 0,
                    elite_wallet_count INTEGER DEFAULT 0,
                    buy_count INTEGER DEFAULT 0,
                    sell_count INTEGER DEFAULT 0,
                    recency_seconds REAL,
                    baseline_confidence REAL,
                    copytrade_bonus REAL DEFAULT 0,
                    final_confidence REAL,
                    decision TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    would_have_passed_without_copytrade INTEGER,
                    paper_trade_opened INTEGER DEFAULT 0,
                    paper_trade_id INTEGER,
                    outcome_pnl_usd REAL,
                    outcome_pnl_pct REAL,
                    max_favourable_pct REAL,
                    max_adverse_pct REAL,
                    exit_reason TEXT,
                    wallet_evidence_json TEXT DEFAULT '{{}}'
                )""")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ct_ledger_mint_ts"
                         f" ON {LEDGER_TABLE}(token_mint, ts)")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # fail-safe: ledger absence degrades to no-influence, never crashes


def _table_exists(conn, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone() is not None
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Evidence gathering (read-only)
# ──────────────────────────────────────────────────────────────────────────────
def _latest_signal(conn, mint: str) -> Optional[sqlite3.Row]:
    if not _table_exists(conn, "wallet_entry_likelihood_signals"):
        return None
    return conn.execute(
        "SELECT signal_time, matched_wallet_count, elite_wallet_count,"
        " copy_conviction_score, veto_reason, token_symbol"
        " FROM wallet_entry_likelihood_signals WHERE token_mint=?"
        " ORDER BY signal_time DESC LIMIT 1", (mint,)).fetchone()


def _buy_sell_pressure(conn, mint: str, window_s: float = SELL_PRESSURE_WIN) -> Tuple[int, int]:
    """Observed smart-wallet buys vs sells on this mint in the window."""
    if not _table_exists(conn, "smart_wallet_trades"):
        return 0, 0
    cutoff = time.time() - window_s
    try:
        buys = conn.execute(
            "SELECT COUNT(*) FROM smart_wallet_trades"
            " WHERE token_mint=? AND buy_time >= ?", (mint, cutoff)).fetchone()[0]
        sells = conn.execute(
            "SELECT COUNT(*) FROM smart_wallet_trades"
            " WHERE token_mint=? AND sell_time >= ? AND sell_time > 0",
            (mint, cutoff)).fetchone()[0]
        return int(buys or 0), int(sells or 0)
    except Exception:
        return 0, 0


def _write_ledger(mint: str, decision: str, reason: str, *,
                  symbol: str = "", wallets: int = 0, elites: int = 0,
                  buys: int = 0, sells: int = 0, recency: Optional[float] = None,
                  baseline: Optional[float] = None, bonus: float = 0.0,
                  final: Optional[float] = None,
                  would_pass_without: Optional[bool] = None,
                  evidence: Optional[dict] = None) -> None:
    """Throttled, fail-safe ledger write."""
    now = time.time()
    prev = _last_ledger_write.get(mint)
    if prev and (now - prev[0]) < LEDGER_THROTTLE_SEC and prev[1] == decision:
        return  # identical decision inside throttle window — don't spam
    try:
        ensure_influence_ledger()
        conn = _connect_rw()
        try:
            conn.execute(
                f"INSERT INTO {LEDGER_TABLE}"
                " (ts, token_mint, symbol, wallet_count, elite_wallet_count,"
                "  buy_count, sell_count, recency_seconds, baseline_confidence,"
                "  copytrade_bonus, final_confidence, decision, reason,"
                "  would_have_passed_without_copytrade, wallet_evidence_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, mint, symbol[:24], wallets, elites, buys, sells, recency,
                 baseline, bonus, final, decision, reason[:160],
                 None if would_pass_without is None else int(would_pass_without),
                 json.dumps(evidence or {})[:1500]))
            conn.commit()
        finally:
            conn.close()
        _last_ledger_write[mint] = (now, decision)
        _trace(stage="COPYTRADE", gate="PAPER_BONUS", decision=decision,
               reason_code=reason[:64], mint=mint, value=bonus,
               threshold=HARD_BONUS_CAP,
               reason_detail=f"wallets={wallets} elites={elites} buys={buys} sells={sells}")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC: bounded paper bonus (supervisor hook)
# ──────────────────────────────────────────────────────────────────────────────
def evaluate_paper_bonus(mint: str, baseline_conf: float, min_confidence: float,
                         token_symbol: str = "") -> Tuple[float, str, Dict[str, Any]]:
    """
    Return (bonus, reason, evidence). bonus ∈ [0.0, HARD_BONUS_CAP].

    ALL of the following must hold for bonus > 0:
      COPYTRADE_PAPER_BONUS_ENABLED=1   (default 0 — observe only)
      TRADING_MODE != live              (live consumes nothing from copytrade)
      baseline_conf >= min_confidence - HARD_BONUS_CAP   (near-qualified only)
      fresh signal (<= COPYTRADE_SIGNAL_MAX_AGE_SEC), no veto_reason
      conviction >= CONVICTION_FLOOR
      matched_wallets >= 2 OR elite_wallets >= 1
      observed sells < buys on the mint in the last 15 min (when any observed)

    Price freshness / liquidity / mcap / max-open / blacklist gates are NOT
    this function's concern — they veto downstream independently of confidence
    and a confidence bonus cannot bypass them.
    """
    try:
        if str(_cfg("COPYTRADE_PAPER_BONUS_ENABLED", "0")).strip() != "1":
            return 0.0, "CT_BONUS_DISABLED", {}

        if str(_cfg("TRADING_MODE", "paper")).strip().lower() == "live":
            _write_ledger(mint, "DENIED", "CT_LIVE_OBSERVE_ONLY",
                          symbol=token_symbol, baseline=baseline_conf,
                          final=baseline_conf,
                          would_pass_without=baseline_conf >= min_confidence)
            return 0.0, "CT_LIVE_OBSERVE_ONLY", {}

        if baseline_conf < (min_confidence - HARD_BONUS_CAP):
            return 0.0, "CT_NOT_NEAR_QUALIFIED", {}

        max_age = float(_cfg("COPYTRADE_SIGNAL_MAX_AGE_SEC", SIGNAL_MAX_AGE_DEF) or SIGNAL_MAX_AGE_DEF)
        cfg_cap = float(_cfg("COPYTRADE_PAPER_BONUS_MAX", HARD_BONUS_CAP) or HARD_BONUS_CAP)
        cap = max(0.0, min(HARD_BONUS_CAP, cfg_cap))   # config may lower, never raise

        conn = _connect_ro()
        try:
            sig = _latest_signal(conn, mint)
            buys, sells = _buy_sell_pressure(conn, mint)
        finally:
            conn.close()

        if not sig:
            _write_ledger(mint, "DENIED", "CT_NO_SIGNAL", symbol=token_symbol,
                          baseline=baseline_conf, final=baseline_conf, buys=buys,
                          sells=sells,
                          would_pass_without=baseline_conf >= min_confidence)
            return 0.0, "CT_NO_SIGNAL", {}

        age        = time.time() - float(sig["signal_time"] or 0)
        wallets    = int(sig["matched_wallet_count"] or 0)
        elites     = int(sig["elite_wallet_count"] or 0)
        conviction = float(sig["copy_conviction_score"] or 0.0)
        veto       = str(sig["veto_reason"] or "")
        symbol     = token_symbol or str(sig["token_symbol"] or "")

        evidence = {"wallets": wallets, "elites": elites, "conviction": round(conviction, 4),
                    "signal_age_s": round(age, 1), "buys_15m": buys, "sells_15m": sells,
                    "veto": veto}

        def deny(reason: str) -> Tuple[float, str, Dict[str, Any]]:
            _write_ledger(mint, "DENIED", reason, symbol=symbol, wallets=wallets,
                          elites=elites, buys=buys, sells=sells, recency=age,
                          baseline=baseline_conf, final=baseline_conf,
                          would_pass_without=baseline_conf >= min_confidence,
                          evidence=evidence)
            return 0.0, reason, evidence

        if veto:
            return deny(f"CT_SIGNAL_VETOED:{veto[:40]}")
        if age > max_age:
            return deny(f"CT_SIGNAL_STALE_{int(age)}s")
        if conviction < CONVICTION_FLOOR:
            return deny("CT_CONVICTION_TOO_LOW")
        if wallets < 2 and elites < 1:
            return deny("CT_INSUFFICIENT_WALLET_EVIDENCE")
        if (buys + sells) > 0 and sells >= max(1, buys):
            return deny("CT_SELL_IMBALANCE")

        # Scale by conviction; hard cap regardless of config.
        bonus = round(min(cap, cap if conviction >= 0.70 else cap * 0.5), 4)
        if bonus <= 0:
            return deny("CT_BONUS_ZERO_BY_CONFIG")

        final = baseline_conf + bonus
        _write_ledger(mint, "BONUS_APPLIED", "CT_PAPER_BONUS_GRANTED",
                      symbol=symbol, wallets=wallets, elites=elites,
                      buys=buys, sells=sells, recency=age,
                      baseline=baseline_conf, bonus=bonus, final=final,
                      would_pass_without=baseline_conf >= min_confidence,
                      evidence=evidence)
        return bonus, "CT_PAPER_BONUS_GRANTED", evidence
    except Exception as exc:
        return 0.0, f"CT_ERROR:{type(exc).__name__}", {}


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC: outcome recorder (execution_engine close hook) — A/B measurement
# ──────────────────────────────────────────────────────────────────────────────
def record_outcome(mint: str, *, position_id: Optional[int] = None,
                   pnl_usd: Optional[float] = None,
                   pnl_pct: Optional[float] = None,
                   max_favourable_pct: Optional[float] = None,
                   max_adverse_pct: Optional[float] = None,
                   exit_reason: str = "") -> None:
    """Backfill the most recent un-resolved ledger row for this mint (24h).
    Fire-and-forget: never raises, never blocks a close."""
    try:
        conn = _connect_rw()
        try:
            if not _table_exists(conn, LEDGER_TABLE):
                return
            row = conn.execute(
                f"SELECT id FROM {LEDGER_TABLE}"
                " WHERE token_mint=? AND ts >= ? AND outcome_pnl_usd IS NULL"
                " AND decision='BONUS_APPLIED'"
                " ORDER BY ts DESC LIMIT 1",
                (mint, time.time() - 86400)).fetchone()
            if not row:
                row = conn.execute(
                    f"SELECT id FROM {LEDGER_TABLE}"
                    " WHERE token_mint=? AND ts >= ? AND outcome_pnl_usd IS NULL"
                    " ORDER BY ts DESC LIMIT 1",
                    (mint, time.time() - 86400)).fetchone()
            if not row:
                return
            conn.execute(
                f"UPDATE {LEDGER_TABLE} SET paper_trade_opened=1, paper_trade_id=?,"
                " outcome_pnl_usd=?, outcome_pnl_pct=?, max_favourable_pct=?,"
                " max_adverse_pct=?, exit_reason=? WHERE id=?",
                (position_id, pnl_usd, pnl_pct, max_favourable_pct,
                 max_adverse_pct, str(exit_reason or "")[:80], row["id"]))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC: lane state machine (UI / status bar / sign-off)
# ──────────────────────────────────────────────────────────────────────────────
def get_lane_state() -> Dict[str, Any]:
    """Explicit copytrade lane state — no silent 'active' claims."""
    out: Dict[str, Any] = {"state": "DISABLED_CONFIG_MISSING", "detail": "",
                           "live_influence": "OFF"}
    try:
        trading_mode = str(_cfg("TRADING_MODE", "paper")).strip().lower()
        bonus_armed = str(_cfg("COPYTRADE_PAPER_BONUS_ENABLED", "0")).strip() == "1"
        conn = _connect_ro()
        try:
            have_signals_tbl = _table_exists(conn, "wallet_entry_likelihood_signals")
            have_trades_tbl  = _table_exists(conn, "smart_wallet_trades")
            if not (have_signals_tbl or have_trades_tbl):
                out["detail"] = "copytrade schema not initialised"
                return out

            wallet_count = 0
            for tbl, col in (("wallet_entry_fingerprints", "wallet_address"),
                             ("wallet_profiles", "wallet_address"),
                             ("tracked_wallets", "wallet_address")):
                if _table_exists(conn, tbl):
                    try:
                        wallet_count += int(conn.execute(
                            f"SELECT COUNT(DISTINCT {col}) FROM {tbl}").fetchone()[0] or 0)
                    except Exception:
                        pass
            out["wallets_tracked"] = wallet_count

            trades_total = 0
            recent_trade_ts = 0.0
            if have_trades_tbl:
                r = conn.execute(
                    "SELECT COUNT(*), MAX(MAX(COALESCE(buy_time,0)),"
                    " MAX(COALESCE(sell_time,0))) FROM smart_wallet_trades").fetchone()
                trades_total = int(r[0] or 0)
                recent_trade_ts = float(r[1] or 0)
            out["observed_trades_total"] = trades_total
            out["last_observed_trade_age_s"] = (
                round(time.time() - recent_trade_ts, 0) if recent_trade_ts else None)

            fresh_signals = 0
            if have_signals_tbl:
                fresh_signals = int(conn.execute(
                    "SELECT COUNT(*) FROM wallet_entry_likelihood_signals"
                    " WHERE signal_time >= ?", (time.time() - 3600,)).fetchone()[0] or 0)
            out["fresh_signals_1h"] = fresh_signals

            hb_age = None
            if _table_exists(conn, "system_heartbeat"):
                try:
                    hb = conn.execute(
                        "SELECT MAX(last_heartbeat) FROM system_heartbeat WHERE"
                        " service_name IN ('copytrade_shadow_scanner',"
                        " 'smart_wallet_trade_ingester')").fetchone()
                    if hb and hb[0]:
                        hb_age = round(time.time() - float(hb[0]), 0)
                except Exception:
                    pass
            out["scanner_heartbeat_age_s"] = hb_age
        finally:
            conn.close()

        if trading_mode == "live":
            out["state"] = "LIVE_OBSERVE_ONLY"
            out["detail"] = "TRADING_MODE=live — copytrade is read-only by contract"
        elif wallet_count == 0:
            out["state"] = "NO_WALLETS"
            out["detail"] = "no wallet source configured (manual/GMGN/discovered)"
        elif trades_total == 0:
            out["state"] = "NO_DATA"
            out["detail"] = f"{wallet_count} wallets tracked, zero observed trades yet"
        elif bonus_armed and fresh_signals > 0:
            out["state"] = "PAPER_BONUS_ELIGIBLE"
            out["detail"] = f"bonus armed, cap +{HARD_BONUS_CAP:.2f}, {fresh_signals} fresh signals/1h"
        elif fresh_signals > 0:
            out["state"] = "PAPER_SHADOW_READY"
            out["detail"] = f"{fresh_signals} fresh signals/1h; bonus flag OFF"
        else:
            out["state"] = "OBSERVING"
            out["detail"] = f"{trades_total} trades observed; no fresh conviction signals"
        return out
    except Exception as exc:
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out


def summary_for_ui() -> Dict[str, Any]:
    """Everything the Sovereign Hub copytrade card needs in one fail-safe call."""
    s = get_lane_state()
    try:
        conn = _connect_ro()
        try:
            cutoff = time.time() - 3600
            if _table_exists(conn, "smart_wallet_trades"):
                s["recent_buys_1h"] = int(conn.execute(
                    "SELECT COUNT(*) FROM smart_wallet_trades WHERE buy_time>=?",
                    (cutoff,)).fetchone()[0] or 0)
                s["recent_sells_1h"] = int(conn.execute(
                    "SELECT COUNT(*) FROM smart_wallet_trades WHERE sell_time>=? AND sell_time>0",
                    (cutoff,)).fetchone()[0] or 0)
            if _table_exists(conn, LEDGER_TABLE):
                day = time.time() - 86400
                s["bonuses_24h"] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {LEDGER_TABLE}"
                    " WHERE ts>=? AND decision='BONUS_APPLIED'", (day,)).fetchone()[0] or 0)
                s["denials_24h"] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {LEDGER_TABLE}"
                    " WHERE ts>=? AND decision='DENIED'", (day,)).fetchone()[0] or 0)
                last = conn.execute(
                    f"SELECT decision, reason, ts, copytrade_bonus, symbol"
                    f" FROM {LEDGER_TABLE} ORDER BY ts DESC LIMIT 1").fetchone()
                if last:
                    s["last_decision"] = str(last["decision"])
                    s["last_reason"] = str(last["reason"])
                    s["last_decision_age_s"] = round(time.time() - float(last["ts"]), 0)
                top = conn.execute(
                    f"SELECT symbol, token_mint, COUNT(*) n FROM {LEDGER_TABLE}"
                    " WHERE ts>=? GROUP BY token_mint ORDER BY n DESC LIMIT 3",
                    (day,)).fetchall()
                s["top_overlap"] = [
                    {"symbol": (r["symbol"] or r["token_mint"][:8]), "hits": int(r["n"])}
                    for r in top]
        finally:
            conn.close()
    except Exception:
        pass
    return s
