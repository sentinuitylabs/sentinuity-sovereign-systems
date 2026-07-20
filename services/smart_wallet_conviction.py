"""
services/smart_wallet_conviction.py
====================================
CONSOLIDATED Smart Wallet Conviction Layer - Backend Engine

Single-file backend containing:
- Schema migration
- Data structures
- Ingestion helpers (JSON/CSV)
- Wallet fingerprint engine
- Signal generation
- Event emission
- CLI entrypoint

OBSERVE/PAPER staging only. Zero live execution influence.

Public API:
- ensure_smart_wallet_schema()
- build_fingerprint_for_wallet()
- generate_signal_for_token()
- confidence_bonus()
- emit_smart_wallet_event()
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import csv
import json
import sqlite3
import statistics
import time

# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA MIGRATION
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_DB_PATH = Path("sentinuity_matrix.db")

DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS smart_wallet_sources (
        source_name TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'UNKNOWN',
        last_run_at REAL NOT NULL DEFAULT 0,
        last_success_at REAL NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        records_seen INTEGER NOT NULL DEFAULT 0,
        records_inserted INTEGER NOT NULL DEFAULT 0,
        records_updated INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS smart_wallet_profiles (
        wallet_address TEXT NOT NULL,
        chain TEXT NOT NULL DEFAULT 'solana',
        source_name TEXT NOT NULL DEFAULT 'manual',
        source_rank INTEGER,
        realized_pnl REAL NOT NULL DEFAULT 0,
        win_rate REAL NOT NULL DEFAULT 0,
        total_trades INTEGER NOT NULL DEFAULT 0,
        median_winner_x REAL NOT NULL DEFAULT 0,
        p50_x REAL NOT NULL DEFAULT 0,
        p70_x REAL NOT NULL DEFAULT 0,
        p90_x REAL NOT NULL DEFAULT 0,
        hit_rate_2x REAL NOT NULL DEFAULT 0,
        hit_rate_3x REAL NOT NULL DEFAULT 0,
        hit_rate_5x REAL NOT NULL DEFAULT 0,
        late_entry_failure_rate REAL NOT NULL DEFAULT 0,
        rug_exposure_rate REAL NOT NULL DEFAULT 0,
        last_seen REAL NOT NULL DEFAULT 0,
        ingested_at REAL NOT NULL DEFAULT 0,
        raw_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(wallet_address, chain, source_name)
    )""",
    """CREATE TABLE IF NOT EXISTS smart_wallet_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT NOT NULL,
        token_mint TEXT NOT NULL,
        token_symbol TEXT NOT NULL DEFAULT '',
        buy_time REAL NOT NULL DEFAULT 0,
        sell_time REAL NOT NULL DEFAULT 0,
        entry_price REAL NOT NULL DEFAULT 0,
        exit_price REAL NOT NULL DEFAULT 0,
        realized_x REAL NOT NULL DEFAULT 0,
        realized_pnl REAL NOT NULL DEFAULT 0,
        hold_seconds REAL NOT NULL DEFAULT 0,
        time_to_2x REAL NOT NULL DEFAULT 0,
        time_to_3x REAL NOT NULL DEFAULT 0,
        time_to_5x REAL NOT NULL DEFAULT 0,
        late_entry_60s_result REAL NOT NULL DEFAULT 0,
        source_name TEXT NOT NULL DEFAULT 'manual',
        ingested_at REAL NOT NULL DEFAULT 0,
        UNIQUE(wallet_address, token_mint, buy_time, source_name)
    )""",
    """CREATE TABLE IF NOT EXISTS wallet_entry_fingerprints (
        wallet_address TEXT NOT NULL,
        chain TEXT NOT NULL DEFAULT 'solana',
        wallet_style TEXT NOT NULL DEFAULT 'UNKNOWN',
        wallet_quality_score REAL NOT NULL DEFAULT 0,
        copyability_score REAL NOT NULL DEFAULT 0,
        median_safe_x REAL NOT NULL DEFAULT 0,
        hit_rate_2x REAL NOT NULL DEFAULT 0,
        hit_rate_3x REAL NOT NULL DEFAULT 0,
        hit_rate_5x REAL NOT NULL DEFAULT 0,
        late_copy_failure_rate REAL NOT NULL DEFAULT 0,
        rug_exposure_rate REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL DEFAULT 0,
        reasons_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(wallet_address, chain)
    )""",
    """CREATE TABLE IF NOT EXISTS wallet_entry_likelihood_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_mint TEXT NOT NULL,
        token_symbol TEXT NOT NULL DEFAULT '',
        signal_time REAL NOT NULL DEFAULT 0,
        matched_wallet_count INTEGER NOT NULL DEFAULT 0,
        elite_wallet_count INTEGER NOT NULL DEFAULT 0,
        wallet_entry_likelihood REAL NOT NULL DEFAULT 0,
        copy_conviction_score REAL NOT NULL DEFAULT 0,
        median_safe_x REAL NOT NULL DEFAULT 0,
        hit_rate_2x REAL NOT NULL DEFAULT 0,
        hit_rate_3x REAL NOT NULL DEFAULT 0,
        hit_rate_5x REAL NOT NULL DEFAULT 0,
        copy_latency_risk TEXT NOT NULL DEFAULT 'UNKNOWN',
        veto_reason TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'OBSERVE',
        UNIQUE(token_mint, signal_time)
    )""",
    """CREATE TABLE IF NOT EXISTS smart_wallet_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_time REAL NOT NULL,
        event_type TEXT NOT NULL,
        token_mint TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS smart_wallet_performance_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT NOT NULL,
        captured_at REAL NOT NULL,
        period TEXT NOT NULL DEFAULT '7d',
        source_name TEXT NOT NULL DEFAULT 'gmgn_api',
        source_rank INTEGER,
        realized_pnl REAL NOT NULL DEFAULT 0,
        win_rate REAL NOT NULL DEFAULT 0,
        total_trades INTEGER NOT NULL DEFAULT 0,
        median_winner_x REAL NOT NULL DEFAULT 0,
        p50_x REAL NOT NULL DEFAULT 0,
        p70_x REAL NOT NULL DEFAULT 0,
        p90_x REAL NOT NULL DEFAULT 0,
        hit_rate_2x REAL NOT NULL DEFAULT 0,
        hit_rate_3x REAL NOT NULL DEFAULT 0,
        hit_rate_5x REAL NOT NULL DEFAULT 0,
        raw_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(wallet_address, captured_at, period, source_name)
    )""",
)


def _connect(db_path: Path | str = None, timeout: float = 2.0) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def ensure_smart_wallet_schema(db_path: Path | str = None) -> None:
    """Create smart wallet tables if they don't exist. Safe to call repeatedly."""
    with closing(_connect(db_path)) as conn:
        for ddl in DDL_STATEMENTS:
            conn.execute(ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_wallet_trades_wallet ON smart_wallet_trades(wallet_address)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_signals_token_time ON wallet_entry_likelihood_signals(token_mint, signal_time DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_perf_snapshots_time ON smart_wallet_performance_snapshots(captured_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_perf_snapshots_wallet ON smart_wallet_performance_snapshots(wallet_address, captured_at DESC)")
        conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WalletFingerprint:
    wallet_address: str
    chain: str = "solana"
    wallet_style: str = "UNKNOWN"
    wallet_quality_score: float = 0.0
    copyability_score: float = 0.0
    median_safe_x: float = 0.0
    hit_rate_2x: float = 0.0
    hit_rate_3x: float = 0.0
    hit_rate_5x: float = 0.0
    late_copy_failure_rate: float = 0.0
    rug_exposure_rate: float = 0.0
    updated_at: float = 0.0
    reasons_json: str = "[]"


@dataclass(frozen=True)
class EntryLikelihoodSignal:
    token_mint: str
    token_symbol: str = ""
    signal_time: float = 0.0
    matched_wallet_count: int = 0
    elite_wallet_count: int = 0
    wallet_entry_likelihood: float = 0.0
    copy_conviction_score: float = 0.0
    median_safe_x: float = 0.0
    hit_rate_2x: float = 0.0
    hit_rate_3x: float = 0.0
    hit_rate_5x: float = 0.0
    copy_latency_risk: str = "UNKNOWN"
    veto_reason: str = ""
    mode: str = "OBSERVE"


# ══════════════════════════════════════════════════════════════════════════════
# INGESTION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def import_wallet_profiles_json(json_path: str, db_path: Path | str = None) -> Tuple[int, int]:
    """Import wallet profiles from JSON. Returns (seen, inserted)."""
    ensure_smart_wallet_schema(db_path)
    with open(json_path, encoding="utf-8") as f:
        profiles = json.load(f)
    
    seen, inserted = 0, 0
    with closing(_connect(db_path)) as conn:
        for p in profiles:
            seen += 1
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO smart_wallet_profiles 
                    (wallet_address, chain, source_name, realized_pnl, win_rate, total_trades,
                     median_winner_x, p50_x, p70_x, p90_x, hit_rate_2x, hit_rate_3x, hit_rate_5x,
                     late_entry_failure_rate, rug_exposure_rate, last_seen, ingested_at, raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    p.get('wallet_address', ''), p.get('chain', 'solana'), p.get('source_name', 'manual'),
                    float(p.get('realized_pnl', 0)), float(p.get('win_rate', 0)), int(p.get('total_trades', 0)),
                    float(p.get('median_winner_x', 0)), float(p.get('p50_x', 0)), float(p.get('p70_x', 0)),
                    float(p.get('p90_x', 0)), float(p.get('hit_rate_2x', 0)), float(p.get('hit_rate_3x', 0)),
                    float(p.get('hit_rate_5x', 0)), float(p.get('late_entry_failure_rate', 0)),
                    float(p.get('rug_exposure_rate', 0)), time.time(), time.time(), json.dumps(p)
                ))
                inserted += 1
            except Exception:
                pass
        conn.commit()
    return seen, inserted


def import_wallet_trades_csv(csv_path: str, db_path: Path | str = None) -> Tuple[int, int]:
    """Import wallet trades from CSV. Returns (seen, inserted)."""
    ensure_smart_wallet_schema(db_path)
    seen, inserted = 0, 0
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with closing(_connect(db_path)) as conn:
            for row in reader:
                seen += 1
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO smart_wallet_trades
                        (wallet_address, token_mint, token_symbol, buy_time, sell_time,
                         entry_price, exit_price, realized_x, realized_pnl, hold_seconds,
                         time_to_2x, time_to_3x, time_to_5x, late_entry_60s_result, source_name, ingested_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        row.get('wallet_address', ''), row.get('token_mint', ''), row.get('token_symbol', ''),
                        float(row.get('buy_time', 0)), float(row.get('sell_time', 0)),
                        float(row.get('entry_price', 0)), float(row.get('exit_price', 0)),
                        float(row.get('realized_x', 0)), float(row.get('realized_pnl', 0)),
                        float(row.get('hold_seconds', 0)), float(row.get('time_to_2x', 0)),
                        float(row.get('time_to_3x', 0)), float(row.get('time_to_5x', 0)),
                        float(row.get('late_entry_60s_result', 0)),
                        row.get('source_name', 'manual'), time.time()
                    ))
                    inserted += 1
                except Exception:
                    pass
            conn.commit()
    return seen, inserted


# ══════════════════════════════════════════════════════════════════════════════
# WALLET FINGERPRINT ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def build_fingerprint_for_wallet(wallet_address: str, db_path: Path | str = None) -> Optional[WalletFingerprint]:
    """
    Build wallet fingerprint from historical trades.
    Returns None if <5 trades (insufficient data).
    
    Scores by:
    - Median X (not raw PnL)
    - p50/p70/p90 percentiles
    - 2x/3x/5x hit rates
    - Late-entry failure rate
    - Rug exposure
    - Wallet style classification
    """
    ensure_smart_wallet_schema(db_path)
    
    with closing(_connect(db_path)) as conn:
        trades = [dict(r) for r in conn.execute("""
            SELECT * FROM smart_wallet_trades
            WHERE wallet_address = ?
              AND (COALESCE(max_x_after_entry,0) > 0 OR COALESCE(realized_x,0) > 0)
            ORDER BY buy_time DESC
        """, (wallet_address,))]
    
    if len(trades) < 5:
        return None
    
    # Quality scoring (repeatability, not raw PnL)
    # OUTCOME_SOURCE_PATCH_20260616: prefer forward-measured max_x_after_entry
    # (computed by backfill_wallet_trade_outcomes.py), fall back to realized_x.
    def _outcome_x(t):
        mx = float(t.get('max_x_after_entry', 0) or 0)
        rx = float(t.get('realized_x', 0) or 0)
        return mx if mx > 0 else rx
    xs = [_outcome_x(t) for t in trades if _outcome_x(t) > 0]
    if not xs:
        return None
    median_x = statistics.median(xs)
    p50_x = statistics.median(xs)
    p70_x = statistics.quantiles(xs, n=10)[6] if len(xs) >= 10 else median_x
    p90_x = statistics.quantiles(xs, n=10)[8] if len(xs) >= 10 else median_x
    
    hit_2x = sum(1 for x in xs if x >= 2.0) / len(xs)
    hit_3x = sum(1 for x in xs if x >= 3.0) / len(xs)
    hit_5x = sum(1 for x in xs if x >= 5.0) / len(xs)
    
    # Rug exposure
    rug_trades = sum(1 for t in trades if _outcome_x(t) > 0 and _outcome_x(t) < 0.5)
    rug_exposure = rug_trades / len(trades)
    
    # Late entry failure rate
    late_fail = sum(1 for t in trades if float(t.get('late_entry_60s_result', 0)) < 0)
    late_failure_rate = late_fail / len(trades)
    
    # Wallet style classification
    if late_failure_rate > 0.5:
        style = "LATE_PUMPER_CHASER"
    elif rug_exposure > 0.3:
        style = "INSIDER_OR_BUNDLE_RISK"
    elif hit_2x > 0.5:
        style = "EARLY_SNIPER"
    else:
        style = "MOMENTUM_CONFIRMATION"
    
    # Quality score (0-100)
    quality = 0.0
    if hit_2x > 0.6: quality += 40
    elif hit_2x > 0.4: quality += 25
    elif hit_2x > 0.2: quality += 10
    
    if rug_exposure < 0.1: quality += 20
    elif rug_exposure < 0.25: quality += 10
    
    if len(trades) >= 50: quality += 20
    elif len(trades) >= 20: quality += 12
    elif len(trades) >= 10: quality += 6
    
    if median_x >= 5.0: quality += 20
    elif median_x >= 3.0: quality += 12
    elif median_x >= 2.0: quality += 6
    
    # Copyability (inverse of late failure)
    copyability = max(0, 100 - (late_failure_rate * 100))
    
    fp = WalletFingerprint(
        wallet_address=wallet_address,
        wallet_style=style,
        wallet_quality_score=quality,
        copyability_score=copyability,
        median_safe_x=median_x,
        hit_rate_2x=hit_2x,
        hit_rate_3x=hit_3x,
        hit_rate_5x=hit_5x,
        late_copy_failure_rate=late_failure_rate,
        rug_exposure_rate=rug_exposure,
        updated_at=time.time(),
        reasons_json=json.dumps([style, f"quality={quality:.0f}", f"copyable={copyability:.0f}"])
    )
    
    # Persist
    with closing(_connect(db_path)) as conn:
        fp_dict = asdict(fp)
        cols = ', '.join(fp_dict.keys())
        vals = ', '.join('?' * len(fp_dict))
        conn.execute(f"INSERT OR REPLACE INTO wallet_entry_fingerprints ({cols}) VALUES ({vals})",
                    tuple(fp_dict.values()))
        conn.commit()
    
    emit_smart_wallet_event(
        "WALLET_ENTRY_FINGERPRINT_BUILT",
        f"Built fingerprint for {wallet_address[:8]} — {style}, quality={quality:.0f}",
        db_path=db_path
    )
    
    return fp


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Normalize nullable/dirty telemetry before numeric gate comparisons."""
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def generate_signal_for_token(
    token_mint: str,
    token_symbol: str = "",
    current_metrics: Dict[str, Any] = None,
    mode: str = "OBSERVE",
    db_path: Path | str = None,
) -> EntryLikelihoodSignal:
    """
    Compare current token vs elite wallet fingerprints.
    Returns signal with entry likelihood, conviction, veto reason.
    
    mode: OBSERVE (no influence), PAPER (annotate), PAPER_MODIFIER (bounded boost)
    """
    ensure_smart_wallet_schema(db_path)
    current_metrics = current_metrics or {}
    now = time.time()
    
    # Fetch elite fingerprints (quality ≥ 50, copyability ≥ 40)
    with closing(_connect(db_path)) as conn:
        fps = [dict(r) for r in conn.execute("""
            SELECT * FROM wallet_entry_fingerprints
            WHERE wallet_quality_score >= 50 AND copyability_score >= 40
            ORDER BY wallet_quality_score DESC LIMIT 50
        """)]
    
    if not fps:
        sig = EntryLikelihoodSignal(
            token_mint=token_mint,
            token_symbol=token_symbol,
            signal_time=now,
            veto_reason="NO_ELITE_WALLETS",
            mode=mode
        )
        _persist_signal(sig, db_path)
        return sig
    
    # Match current token to fingerprints (simplified scoring)
    matched = []
    for fp in fps:
        score = 0.0
        # Basic matching heuristic
        if _safe_float(current_metrics.get('holder_growth_rate'), 0.0) > 0.05:
            score += 30
        if _safe_float(current_metrics.get('volume_acceleration'), 0.0) > 2.0:
            score += 20
        if score >= 25:
            matched.append((fp, score))
    
    if not matched:
        sig = EntryLikelihoodSignal(
            token_mint=token_mint,
            token_symbol=token_symbol,
            signal_time=now,
            elite_wallet_count=len(fps),
            veto_reason="NO_FINGERPRINT_MATCH",
            mode=mode
        )
        _persist_signal(sig, db_path)
        return sig
    
    # Aggregate matched fingerprints
    matched = sorted(matched, key=lambda x: x[1], reverse=True)[:10]
    median_xs = [_safe_float(fp.get('median_safe_x'), 0.0) for fp, _ in matched]
    hit_2xs = [_safe_float(fp.get('hit_rate_2x'), 0.0) for fp, _ in matched]
    hit_3xs = [_safe_float(fp.get('hit_rate_3x'), 0.0) for fp, _ in matched]
    hit_5xs = [_safe_float(fp.get('hit_rate_5x'), 0.0) for fp, _ in matched]
    late_fails = [_safe_float(fp.get('late_copy_failure_rate'), 0.0) for fp, _ in matched]
    
    median_safe_x = statistics.median(median_xs)
    hit_2x = sum(hit_2xs) / len(hit_2xs)
    hit_3x = sum(hit_3xs) / len(hit_3xs)
    hit_5x = sum(hit_5xs) / len(hit_5xs)
    avg_late_fail = sum(late_fails) / len(late_fails)
    
    entry_likelihood = min(1.0, len(matched) / 10.0)
    elite_similarity = sum(_safe_float(fp.get('wallet_quality_score'), 0.0) for fp, _ in matched) / (len(matched) * 100)
    conviction = entry_likelihood * elite_similarity * (1.0 - avg_late_fail)
    
    # Veto logic
    veto_reason = ""
    if _safe_float(current_metrics.get('rug_score'), 0.0) > 0.6:
        veto_reason = "BUNDLE_OR_RUG_RISK"
        conviction = 0.0
    elif avg_late_fail > 0.5:
        veto_reason = "COPY_SIGNAL_TOO_LATE"
        conviction = 0.0
    
    latency_risk = "LOW" if avg_late_fail < 0.2 else "MEDIUM" if avg_late_fail < 0.5 else "HIGH"
    
    sig = EntryLikelihoodSignal(
        token_mint=token_mint,
        token_symbol=token_symbol,
        signal_time=now,
        matched_wallet_count=len(matched),
        elite_wallet_count=len(matched),
        wallet_entry_likelihood=entry_likelihood,
        copy_conviction_score=conviction,
        median_safe_x=median_safe_x,
        hit_rate_2x=hit_2x,
        hit_rate_3x=hit_3x,
        hit_rate_5x=hit_5x,
        copy_latency_risk=latency_risk,
        veto_reason=veto_reason,
        mode=mode
    )
    
    _persist_signal(sig, db_path)
    
    if conviction >= 0.7:
        emit_smart_wallet_event(
            "COPY_CONVICTION_SIGNAL_CREATED",
            f"{token_symbol or token_mint[:8]} — conviction {conviction:.2f}",
            token_mint=token_mint,
            db_path=db_path
        )
    
    return sig


def _persist_signal(sig: EntryLikelihoodSignal, db_path: Path | str = None) -> None:
    """Internal: persist signal to DB."""
    with closing(_connect(db_path)) as conn:
        sig_dict = asdict(sig)
        cols = ', '.join(sig_dict.keys())
        vals = ', '.join('?' * len(sig_dict))
        conn.execute(f"INSERT OR REPLACE INTO wallet_entry_likelihood_signals ({cols}) VALUES ({vals})",
                    tuple(sig_dict.values()))
        conn.commit()


def confidence_bonus(conviction_score: float, veto_reason: str, mode: str) -> float:
    """
    Bounded confidence modifier for execution engine.
    
    OBSERVE: 0.0 (no influence)
    PAPER: 0.0 (annotate only)
    PAPER_MODIFIER: max +0.08
    
    Strict whitelist: only PAPER_MODIFIER can return positive value.
    """
    mode = str(mode or "OBSERVE").upper()
    if mode != "PAPER_MODIFIER":
        return 0.0
    if veto_reason:
        return 0.0
    if conviction_score >= 0.90:
        return 0.08
    elif conviction_score >= 0.80:
        return 0.05
    elif conviction_score >= 0.70:
        return 0.03
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# EVENT EMISSION
# ══════════════════════════════════════════════════════════════════════════════

VALID_EVENT_TYPES = {
    "WALLET_ENTRY_FINGERPRINT_BUILT",
    "COPY_CONVICTION_SIGNAL_CREATED",
    "COPY_SIGNAL_VETOED_LATE",
    "COPY_SIGNAL_REDUCED_CONFIDENCE",
}


def emit_smart_wallet_event(
    event_type: str,
    message: str = "",
    token_mint: str = "",
    db_path: Path | str = None,
) -> None:
    """Emit event for world commentary (real DB-backed events only)."""
    if event_type not in VALID_EVENT_TYPES:
        return
    ensure_smart_wallet_schema(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute("""
            INSERT INTO smart_wallet_events(event_time, event_type, token_mint, message)
            VALUES(?,?,?,?)
        """, (time.time(), event_type, token_mint or "", message or ""))
        conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

def run_conviction_scan(db_path: str = "sentinuity_matrix.db") -> None:
    """CLI: Build fingerprints for all wallets with ≥5 trades."""
    ensure_smart_wallet_schema(db_path)
    with closing(_connect(db_path)) as conn:
        wallets = [r['wallet_address'] for r in conn.execute("""
            SELECT wallet_address, COUNT(*) as n
            FROM smart_wallet_trades
            GROUP BY wallet_address
            HAVING n >= 5
        """)]
    
    built = 0
    for w in wallets:
        if build_fingerprint_for_wallet(w, db_path):
            built += 1
    
    print(f"[OK] Built {built} fingerprints from {len(wallets)} wallets")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        db = sys.argv[2] if len(sys.argv) > 2 else "sentinuity_matrix.db"
        
        if cmd == "migrate":
            ensure_smart_wallet_schema(db)
            print(f"[OK] Migrated smart wallet tables in {db}")
        elif cmd == "scan":
            run_conviction_scan(db)
        elif cmd == "import-profiles":
            if len(sys.argv) < 3:
                print("Usage: python smart_wallet_conviction.py import-profiles <json_path> [db_path]")
                sys.exit(1)
            seen, ins = import_wallet_profiles_json(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else db)
            print(f"[OK] Profiles: seen={seen}, inserted={ins}")
        elif cmd == "import-trades":
            if len(sys.argv) < 3:
                print("Usage: python smart_wallet_conviction.py import-trades <csv_path> [db_path]")
                sys.exit(1)
            seen, ins = import_wallet_trades_csv(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else db)
            print(f"[OK] Trades: seen={seen}, inserted={ins}")
        else:
            print("Unknown command. Available: migrate, scan, import-profiles, import-trades")
    else:
        print("Smart Wallet Conviction Engine")
        print("Usage: python smart_wallet_conviction.py <command> [args]")
        print("Commands: migrate, scan, import-profiles, import-trades")
