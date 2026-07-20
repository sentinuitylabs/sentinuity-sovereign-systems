"""
services/runner_likelihood_detector.py — v2.0 escalation-merged
================================================================
Post-entry runner likelihood detector + pre-entry escalation scoring.

v2.0 additions (merged from exceptional_live_escalation.py):
  - grade_maturity()            Evidence-based maturity stage grader
  - score_for_escalation()      Exceptional live runner escalation score
  - evaluate_for_escalation()   Full evaluation + ledger write entry point
  - strict/legacy/relaxed gate  Three-way gate comparison
  - ensure_escalation_tables()  live_escalation_ledger + legacy_cluster_candidates
  - log_escalation()            Write one escalation ledger row
  - update_escalation_outcome() Update ledger with post-trade result

Approved sign-off scope:
  - Paper/open-position scoring (MONSTER/STRONG/NEUTRAL/DUD tiers)
  - Pre-entry escalation scoring for LIVE_ESCALATION_ENABLED lane
  - DB logging to runner_likelihood_scores + live_escalation_ledger
  - Maturity grading that replaces blunt flat-age gating

Not approved:
  - Automatic live scale-up
  - Automatic live execution changes
  - Wallet / private key / order routing logic
  - Changing default live position size or hard caps
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sentinuity_matrix.db"

# ── Escalation state constants ────────────────────────────────────────────────
ESC_WATCH    = "LIVE_ESCALATION_WATCH"
ESC_ARMED_50 = "LIVE_ESCALATION_ARMED_50"
ESC_KO_75    = "LIVE_ESCALATION_KNOCKOUT_75"
ESC_BLOCKED  = "LIVE_ESCALATION_BLOCKED"
ESC_EXECUTED = "LIVE_ESCALATION_EXECUTED"
ESC_ABORTED  = "LIVE_ESCALATION_ABORTED"

# ── Maturity stage constants ──────────────────────────────────────────────────
MATURITY_STAGES = [
    "DISCOVERED", "RESOLVED", "PRICED", "ROUTE_CONFIRMED",
    "LIQUIDITY_CONFIRMED", "QUALIFIED", "LATCHABLE",
    "PAPER_ENTRY_READY", "RUNNER_CONFIRMED",
    "LIVE_ESCALATION_READY", "GRADUATED",
]


def connect() -> sqlite3.Connection:
    try:
        from core.schema import get_connection  # type: ignore
        return get_connection()
    except Exception:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return default


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def get_config(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def ensure_score_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runner_likelihood_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            mint_address TEXT,
            token_name TEXT,
            scored_at REAL,
            age_sec REAL,
            entry_price REAL,
            peak_price REAL,
            peak_mult REAL,
            velocity_per_min REAL,
            likelihood REAL,
            tier TEXT,
            recommend TEXT,
            reason TEXT,
            mode TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runner_scores_pos ON runner_likelihood_scores(position_id, scored_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runner_scores_tier ON runner_likelihood_scores(tier, scored_at DESC)")


def ensure_escalation_tables(conn: sqlite3.Connection) -> None:
    """Create live_escalation_ledger and legacy_cluster_candidates if absent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_escalation_ledger (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at                  REAL,
            mint_address                TEXT,
            token_name                  TEXT,
            token_symbol                TEXT,
            source_snapshot_id          INTEGER,
            paper_position_id           INTEGER,
            strict_gate_result          TEXT,
            legacy_gate_result          TEXT,
            relaxed_gate_result         TEXT,
            runner_score                REAL,
            runner_score_pct            REAL,
            confidence                  REAL,
            raw_confidence              REAL,
            calibrated_confidence       REAL,
            freshness_score             REAL,
            price_freshness_seconds     REAL,
            token_age_seconds           REAL,
            signal_age_seconds          REAL,
            curve_progress_pct          REAL,
            liquidity_usd               REAL,
            wallet_convergence_score    REAL,
            smart_wallet_count          INTEGER,
            elite_wallet_count          INTEGER,
            first_tick_delay_sec        REAL,
            entry_latency_sec           REAL,
            live_escalation_state       TEXT,
            escalation_reason           TEXT,
            veto_reason                 TEXT,
            executed_live               INTEGER DEFAULT 0,
            live_position_id            INTEGER,
            live_entry_price            REAL,
            live_exit_price             REAL,
            live_realized_pnl_usd       REAL,
            live_realized_pnl_pct       REAL,
            max_favorable_excursion_pct REAL,
            max_adverse_excursion_pct   REAL,
            exit_reason                 TEXT,
            reviewed_at                 REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lel_mint ON live_escalation_ledger(mint_address, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lel_state ON live_escalation_ledger(live_escalation_state, created_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS legacy_cluster_candidates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      REAL,
            mint_address    TEXT,
            token_name      TEXT,
            token_symbol    TEXT,
            snapshot_id     INTEGER,
            strict_gate     TEXT,
            legacy_gate     TEXT,
            relaxed_gate    TEXT,
            confidence      REAL,
            liquidity_usd   REAL,
            volume_5m_usd   REAL,
            runner_tier     TEXT,
            runner_score    REAL,
            maturity_stage  TEXT,
            did_run         INTEGER DEFAULT 0,
            peak_pct        REAL,
            reject_reason   TEXT,
            notes           TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lcc_mint ON legacy_cluster_candidates(mint_address, created_at DESC)")
    try:
        conn.commit()
    except Exception:
        pass


def log_cognition(conn: sqlite3.Connection, stage: str, message: str) -> None:
    if not table_exists(conn, "cognition_log"):
        return
    cols = columns(conn, "cognition_log")
    now = time.time()
    data: dict = {}
    for cand in ("timestamp", "logged_at", "created_at"):
        if cand in cols:
            data[cand] = now
            break
    if "source" in cols:
        data["source"] = "runner_likelihood_detector"
    if "service_name" in cols:
        data["service_name"] = "runner_likelihood_detector"
    if "stage" in cols:
        data["stage"] = stage
    if "message" in cols:
        data["message"] = message
    elif "content" in cols:
        data["content"] = message
    if not data:
        return
    keys = list(data)
    conn.execute(f"INSERT INTO cognition_log({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [data[k] for k in keys])


def latest_market_price(conn: sqlite3.Connection, mint: str) -> float | None:
    if not mint or not table_exists(conn, "market_snapshots"):
        return None
    cols = columns(conn, "market_snapshots")
    price_col = "price" if "price" in cols else None
    ts_col = "price_updated_at" if "price_updated_at" in cols else ("timestamp" if "timestamp" in cols else "id")
    if not price_col:
        return None
    row = conn.execute(
        f"SELECT {price_col} AS price FROM market_snapshots WHERE mint_address=? AND {price_col} IS NOT NULL AND {price_col}>0 ORDER BY {ts_col} DESC LIMIT 1",
        (mint,),
    ).fetchone()
    if not row:
        return None
    try:
        return float(row["price"])
    except Exception:
        return None


# ── Post-entry runner scorer (original v1.1 logic) ────────────────────────────

def score_open_position(position_id: int, conn: sqlite3.Connection | None = None, mode: str | None = None, write_score: bool = True) -> dict[str, Any]:
    close_after = conn is None
    if conn is None:
        conn = connect()
    conn.row_factory = sqlite3.Row

    if not table_exists(conn, "paper_positions"):
        if close_after:
            conn.close()
        return {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": "paper_positions table missing"}

    cols = columns(conn, "paper_positions")
    required = {"id", "entry_price", "opened_at", "status"}
    missing = required - cols
    if missing:
        if close_after:
            conn.close()
        return {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": f"paper_positions missing columns: {sorted(missing)}"}

    select_cols = ["id", "entry_price", "opened_at", "status"]
    for c in ["mint_address", "token_name", "highest_price_seen", "current_price", "last_price", "position_size_usd"]:
        if c in cols:
            select_cols.append(c)
    pos = conn.execute(
        f"SELECT {','.join(select_cols)} FROM paper_positions WHERE id=? AND status='OPEN'",
        (position_id,),
    ).fetchone()

    if not pos:
        if close_after:
            conn.close()
        return {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": "position not found/open"}

    ensure_score_table(conn)
    now = time.time()
    mode = mode or str(get_config(conn, "RUNNER_DETECTOR_MODE", "paper") or "paper")
    live_scale_enabled = str(get_config(conn, "RUNNER_LIVE_SCALE_ENABLED", "0")) == "1"

    mint = row_get(pos, "mint_address", None)
    token_name = row_get(pos, "token_name", None)
    opened_at = float(row_get(pos, "opened_at", now) or now)
    age_sec = max(0.0, now - opened_at)
    entry = float(row_get(pos, "entry_price", 0) or 0)
    peak = float(row_get(pos, "highest_price_seen", 0) or 0)
    cur = row_get(pos, "current_price", None) or row_get(pos, "last_price", None)
    try:
        cur = float(cur) if cur is not None else None
    except Exception:
        cur = None
    market_cur = latest_market_price(conn, mint) if mint else None
    peak = max([v for v in [entry, peak, cur, market_cur] if v is not None and v > 0] or [entry])

    if entry <= 0:
        result = {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": "no entry price"}
        if close_after:
            conn.close()
        return result

    peak_mult = peak / entry if entry else 1.0
    velocity_per_min = (peak_mult - 1.0) / (age_sec / 60.0) if age_sec > 0 else 0.0

    monster_vel = float(get_config(conn, "RUNNER_MONSTER_VELOCITY_PER_MIN", "5.0") or 5.0)
    strong_vel = float(get_config(conn, "RUNNER_STRONG_VELOCITY_PER_MIN", "1.0") or 1.0)
    dud_min_age = float(get_config(conn, "RUNNER_DUD_MIN_AGE_SEC", "45") or 45)
    dud_min_peak = float(get_config(conn, "RUNNER_DUD_MIN_PEAK_MULT", "1.05") or 1.05)

    if age_sec < 15:
        tier, likelihood, recommend = "WAIT", 0.50, "WAIT"
        reason = f"Only {age_sec:.0f}s old — wait until 15s for signal"
    elif velocity_per_min >= monster_vel:
        tier, likelihood = "MONSTER", 0.95
        recommend = "PAPER_SCALE_SIGNAL" if mode == "paper" else "LIVE_REVIEW"
        reason = f"Monster velocity {velocity_per_min:.2f}x/min — hold aggressive; live scale remains gated"
    elif velocity_per_min >= strong_vel:
        tier, likelihood, recommend = "STRONG", 0.80, "HOLD"
        reason = f"Strong velocity {velocity_per_min:.2f}x/min — likely 2-5x runner"
    elif velocity_per_min >= 0.3 and age_sec < 60:
        tier, likelihood, recommend = "NEUTRAL", 0.55, "HOLD"
        reason = f"Moderate velocity {velocity_per_min:.2f}x/min — possible small runner"
    elif peak_mult >= 1.10 and age_sec < dud_min_age:
        tier, likelihood, recommend = "NEUTRAL", 0.40, "HOLD"
        reason = f"Mild peak {(peak_mult-1)*100:.1f}% in {age_sec:.0f}s — watch"
    elif age_sec >= dud_min_age and peak_mult < dud_min_peak:
        tier, likelihood, recommend = "DUD", 0.15, "CUT_RECOMMENDATION"
        reason = f"No move ({(peak_mult-1)*100:+.1f}% peak) after {age_sec:.0f}s — paper cut candidate"
    else:
        tier, likelihood, recommend = "NEUTRAL", 0.35, "HOLD"
        reason = f"velocity={velocity_per_min:.2f}/min peak={(peak_mult-1)*100:+.1f}% age={age_sec:.0f}s"

    result = {
        "likelihood": likelihood,
        "tier": tier,
        "velocity_per_min": velocity_per_min,
        "peak_mult": peak_mult,
        "peak_price": peak,
        "age_sec": age_sec,
        "recommend": recommend,
        "reason": reason,
        "mode": mode,
    }

    if write_score:
        conn.execute("""
            INSERT INTO runner_likelihood_scores(
                position_id, mint_address, token_name, scored_at, age_sec,
                entry_price, peak_price, peak_mult, velocity_per_min,
                likelihood, tier, recommend, reason, mode
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (position_id, mint, token_name, now, age_sec, entry, peak, peak_mult, velocity_per_min, likelihood, tier, recommend, reason, mode))
        if tier in {"MONSTER", "STRONG", "DUD"}:
            log_cognition(conn, f"RUNNER_{tier}", f"pos={position_id} {token_name or mint} tier={tier} vel={velocity_per_min:.2f}/min rec={recommend} {reason}")
        try:
            conn.commit()
        except Exception:
            pass

    if close_after:
        conn.close()
    return result


def score_all_open(conn: sqlite3.Connection | None = None, mode: str = "paper") -> list[dict[str, Any]]:
    close_after = conn is None
    if conn is None:
        conn = connect()
    conn.row_factory = sqlite3.Row
    if not table_exists(conn, "paper_positions"):
        return []
    rows = conn.execute("SELECT id FROM paper_positions WHERE status='OPEN'").fetchall()
    out = []
    for r in rows:
        out.append(score_open_position(int(r["id"]), conn, mode=mode, write_score=True))
    if close_after:
        conn.close()
    return out


# ── Pre-entry maturity grader ─────────────────────────────────────────────────

@dataclass
class MaturityResult:
    stage: str = "DISCOVERED"
    stage_index: int = 0
    ready_for_paper: bool = False
    ready_for_live_escalation: bool = False
    blocking: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def grade_maturity(snap: dict) -> MaturityResult:
    """
    Evidence-based maturity grader. Replaces blunt flat-age gating.
    Advances stages based on evidence present, not seconds elapsed.
    A token at 45s with clean data reaches PAPER_ENTRY_READY.
    A token at 600s with stale price stays at PRICED.
    """
    r = MaturityResult()
    blocking: list[str] = []
    evidence: list[str] = []

    mint = str(snap.get("mint_address") or "")
    price = float(snap.get("price_usd") or 0)
    price_age = float(snap.get("price_age_seconds") or 9999)
    liq = float(snap.get("liquidity_usd") or 0)
    conf = float(snap.get("confidence") or snap.get("mint_confidence") or 0)
    tok_age = float(snap.get("token_age_seconds") or 0)
    sig_age = float(snap.get("signal_age_seconds") or 9999)
    is_tradeable = bool(snap.get("is_tradeable") or snap.get("route_confirmed"))
    liq_ok = bool(snap.get("liquidity_integrity_ok") or (liq > 2000))
    runner_tier = str(snap.get("runner_tier") or "")
    runner_score = float(snap.get("runner_score") or 0)
    resolved = bool(mint and len(mint) >= 32)
    rug = bool(snap.get("known_rug_pattern") or snap.get("is_rug_risk") or snap.get("honeypot_risk"))
    post_pump = bool(snap.get("post_pump_exhaustion") or snap.get("price_exhaustion"))
    quality = str(snap.get("quality_status") or snap.get("candidate_state") or "")

    stage_idx = 0
    evidence.append("DISCOVERED")

    if resolved:
        stage_idx = max(stage_idx, 1)
        evidence.append("RESOLVED: mint valid")
    else:
        blocking.append("NO_MINT_IDENTITY")

    if price > 0 and price_age < 300:
        stage_idx = max(stage_idx, 2)
        evidence.append(f"PRICED: ${price:.6g} age={price_age:.0f}s")
    elif price <= 0:
        blocking.append("NO_PRICE")
    else:
        blocking.append(f"PRICE_STALE_{price_age:.0f}s")

    if is_tradeable:
        stage_idx = max(stage_idx, 3)
        evidence.append("ROUTE_CONFIRMED")
    else:
        blocking.append("ROUTE_UNCONFIRMED")

    if liq_ok and liq > 2000:
        stage_idx = max(stage_idx, 4)
        evidence.append(f"LIQUIDITY_CONFIRMED: ${liq:,.0f}")
    else:
        blocking.append(f"LIQUIDITY_LOW: ${liq:,.0f}")

    if conf >= 0.45 and quality in ("qualified", "latched", "QUALIFIED", "LATCHED"):
        stage_idx = max(stage_idx, 5)
        evidence.append(f"QUALIFIED: conf={conf:.2f}")
    elif conf < 0.45:
        blocking.append(f"CONF_LOW_{conf:.2f}")

    if sig_age < 600 and not rug and not post_pump:
        stage_idx = max(stage_idx, 6)
        evidence.append(f"LATCHABLE: sig_age={sig_age:.0f}s")
    else:
        if rug:
            blocking.append("RUG_PATTERN")
        if post_pump:
            blocking.append("POST_PUMP_EXHAUSTION")
        if sig_age >= 600:
            blocking.append(f"SIGNAL_STALE_{sig_age:.0f}s")

    if stage_idx >= 6 and price_age < 120 and not blocking:
        stage_idx = max(stage_idx, 7)
        evidence.append("PAPER_ENTRY_READY")
        r.ready_for_paper = True
    elif stage_idx >= 6 and price_age >= 120:
        blocking.append(f"ENTRY_PRICE_STALE_{price_age:.0f}s")

    if runner_tier in ("STRONG", "MONSTER") or runner_score >= 0.50:
        stage_idx = max(stage_idx, 8)
        evidence.append(f"RUNNER_CONFIRMED: tier={runner_tier} score={runner_score:.2f}")

    if stage_idx >= 8 and runner_score >= 0.60 and not blocking:
        stage_idx = max(stage_idx, 9)
        evidence.append("LIVE_ESCALATION_READY")
        r.ready_for_live_escalation = True

    if quality in ("executed", "graduated", "EXECUTED"):
        stage_idx = 10
        evidence.append("GRADUATED")

    r.stage = MATURITY_STAGES[min(stage_idx, len(MATURITY_STAGES) - 1)]
    r.stage_index = stage_idx
    r.blocking = blocking
    r.evidence = evidence
    return r


# ── Pre-entry escalation scorer ───────────────────────────────────────────────

@dataclass
class EscalationScore:
    score: float = 0.0
    score_pct: float = 0.0
    state: str = ESC_WATCH
    armed: bool = False
    knockout: bool = False
    blocking: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    maturity: MaturityResult = field(default_factory=MaturityResult)


def score_for_escalation(snap: dict, paper_pos: dict | None = None) -> EscalationScore:
    """
    Compute exceptional live runner escalation score.
    No DB writes. No live execution. Returns state for execution_engine to act on.
    """
    import math as _math
    es = EscalationScore()
    es.maturity = grade_maturity(snap)
    blocking: list[str] = []
    evidence: list[str] = []

    conf          = float(snap.get("confidence") or snap.get("mint_confidence") or 0)
    raw_conf      = float(snap.get("raw_confidence") or conf)
    calib_conf    = float(snap.get("calibrated_confidence") or conf)
    runner_score  = float(snap.get("runner_score") or 0)
    runner_tier   = str(snap.get("runner_tier") or "")
    cluster_sim   = float(snap.get("entry_cluster_similarity_score") or snap.get("cluster_similarity") or 0)
    freshness     = float(snap.get("freshness_score") or 0)
    price_age     = float(snap.get("price_age_seconds") or 9999)
    sig_age       = float(snap.get("signal_age_seconds") or 9999)
    liq           = float(snap.get("liquidity_usd") or 0)
    liq_integrity = float(snap.get("liquidity_integrity_score") or (1.0 if liq > 3000 else 0.0))
    wallet_conv   = float(snap.get("wallet_convergence_score") or 0)
    smart_wallets = int(snap.get("smart_wallet_count") or 0)
    elite_wallets = int(snap.get("elite_wallet_count") or 0)
    p5m           = float(snap.get("price_change_5m_pct") or 0)
    paid_boost    = bool(snap.get("paid_boost_detected"))
    top_holder_risk = bool(snap.get("top_holder_risk"))
    dev_risk      = bool(snap.get("dev_risk") or float(snap.get("dev_holding_pct") or 0) > 0.15)
    is_rug        = bool(snap.get("known_rug_pattern") or snap.get("is_rug_risk") or snap.get("honeypot_risk"))
    peak_pnl_pct  = float(paper_pos.get("peak_pnl_pct") or 0) if paper_pos else 0.0

    # Hard vetoes
    if is_rug:       blocking.append("RUG_OR_HONEYPOT")
    if paid_boost:   blocking.append("PAID_BOOST_DETECTED")
    if top_holder_risk: blocking.append("TOP_HOLDER_RISK")
    if dev_risk:     blocking.append("DEV_RISK")
    if price_age > 30:  blocking.append(f"PRICE_STALE_{price_age:.0f}s")
    if sig_age > 180:   blocking.append(f"SIGNAL_STALE_{sig_age:.0f}s")
    if liq < 3000:      blocking.append(f"LIQUIDITY_LOW_${liq:.0f}")
    if not es.maturity.ready_for_live_escalation and runner_score < 0.50:
        blocking.append(f"MATURITY_{es.maturity.stage}")

    # Scoring components
    if runner_tier == "MONSTER":
        rc = 1.0; evidence.append(f"MONSTER_RUNNER")
    elif runner_tier == "STRONG":
        rc = 0.80; evidence.append(f"STRONG_RUNNER")
    elif runner_score > 0:
        rc = min(1.0, runner_score); evidence.append(f"runner_score={runner_score:.2f}")
    else:
        rc = 0.0

    conf_composite = calib_conf * 0.5 + raw_conf * 0.3 + conf * 0.2
    if conf_composite >= 0.70:
        evidence.append(f"conf_composite={conf_composite:.2f}")

    if p5m >= 75: mom = 1.0; evidence.append(f"p5m=+{p5m:.1f}%")
    elif p5m >= 30: mom = 0.75; evidence.append(f"p5m=+{p5m:.1f}%")
    elif p5m >= 10: mom = 0.50
    elif p5m < 0:   mom = 0.10; blocking.append(f"MOMENTUM_NEGATIVE")
    else:           mom = 0.30

    wallet_component = min(1.0, wallet_conv + smart_wallets * 0.05 + elite_wallets * 0.10)
    if elite_wallets > 0:
        evidence.append(f"elite_wallets={elite_wallets}")

    if cluster_sim >= 0.60:
        evidence.append(f"cluster_sim={cluster_sim:.2f}")

    fresh_score = freshness if freshness > 0 else (1.0 if price_age < 15 else max(0, 1 - price_age / 60))

    raw_score = (
        rc               * 0.35 +
        min(1.0, conf_composite) * 0.20 +
        mom              * 0.15 +
        min(1.0, cluster_sim)    * 0.10 +
        wallet_component * 0.10 +
        min(1.0, liq_integrity)  * 0.05 +
        fresh_score      * 0.05 +
        (0.05 if peak_pnl_pct >= 50 else 0)
    )

    es.score = round(min(1.0, max(0.0, raw_score)), 4)
    es.score_pct = round(es.score * 100, 1)
    es.blocking = blocking
    es.evidence = evidence

    if any(b in ("RUG_OR_HONEYPOT", "PAID_BOOST_DETECTED", "TOP_HOLDER_RISK", "DEV_RISK") for b in blocking):
        es.state = ESC_BLOCKED
    elif blocking:
        es.state = ESC_BLOCKED
    elif es.score_pct >= 75:
        es.state = ESC_KO_75; es.armed = True; es.knockout = True
    elif es.score_pct >= 50:
        es.state = ESC_ARMED_50; es.armed = True
    else:
        es.state = ESC_WATCH

    return es


# ── Gate comparisons ──────────────────────────────────────────────────────────

def strict_cluster_gate(snap: dict) -> tuple[str, str]:
    conf = float(snap.get("confidence") or 0)
    liq = float(snap.get("liquidity_usd") or 0)
    price_age = float(snap.get("price_age_seconds") or 9999)
    sig_age = float(snap.get("signal_age_seconds") or 9999)
    is_rug = bool(snap.get("known_rug_pattern") or snap.get("is_rug_risk") or snap.get("honeypot_risk"))
    if is_rug: return "FAIL", "RUG"
    if price_age > 60: return "FAIL", f"PRICE_STALE_{price_age:.0f}s"
    if sig_age > 300: return "FAIL", f"SIG_STALE_{sig_age:.0f}s"
    if conf < 0.55: return "FAIL", f"CONF_{conf:.2f}<0.55"
    if liq < 3000: return "FAIL", f"LIQ_${liq:.0f}<3000"
    return "PASS", f"strict_ok conf={conf:.2f} liq=${liq:.0f}"


def legacy_cluster_gate(snap: dict) -> tuple[str, str]:
    """Legacy permissive gate — paper/shadow only, never live."""
    conf = float(snap.get("confidence") or snap.get("mint_confidence") or 0)
    liq = float(snap.get("liquidity_usd") or 0)
    vol5m = float(snap.get("volume_5m_usd") or 0)
    sig_age = float(snap.get("signal_age_seconds") or 9999)
    price = float(snap.get("price_usd") or 0)
    is_rug = bool(snap.get("known_rug_pattern") or snap.get("is_rug_risk") or snap.get("honeypot_risk"))
    cluster_sim = float(snap.get("entry_cluster_similarity_score") or snap.get("cluster_similarity") or 0)
    if is_rug: return "FAIL", "RUG_OR_HONEYPOT"
    if price <= 0: return "FAIL", "NO_PRICE"
    if sig_age > 900: return "FAIL", f"SIGNAL_STALE_{sig_age:.0f}s"
    reasons = []
    if conf < 0.35: reasons.append(f"conf={conf:.2f}<0.35")
    if liq < 1500: reasons.append(f"liq=${liq:.0f}<1500")
    if vol5m < 500 and cluster_sim < 0.40: reasons.append(f"vol5m=${vol5m:.0f}<500 and cluster_sim={cluster_sim:.2f}<0.40")
    if reasons: return "FAIL", " | ".join(reasons)
    return "PASS", f"legacy_ok conf={conf:.2f} liq=${liq:.0f}"


def relaxed_gate(snap: dict) -> tuple[str, str]:
    """Diagnostic relaxed gate — comparison only."""
    conf = float(snap.get("confidence") or 0)
    liq = float(snap.get("liquidity_usd") or 0)
    price = float(snap.get("price_usd") or 0)
    is_rug = bool(snap.get("known_rug_pattern") or snap.get("is_rug_risk") or snap.get("honeypot_risk"))
    if is_rug or price <= 0: return "FAIL", "RUG_OR_NO_PRICE"
    if conf < 0.20 and liq < 500: return "FAIL", f"BOTH_TOO_LOW conf={conf:.2f} liq=${liq:.0f}"
    return "PASS", f"relaxed_ok conf={conf:.2f} liq=${liq:.0f}"


# ── Ledger writes ─────────────────────────────────────────────────────────────

def log_escalation(
    conn: sqlite3.Connection,
    mint: str,
    snap: dict,
    es: EscalationScore,
    *,
    paper_pos_id: int | None = None,
    snap_id: int | None = None,
    strict_gate: str = "",
    legacy_gate: str = "",
    relaxed_gate_result: str = "",
    executed_live: bool = False,
    live_position_id: int | None = None,
    live_entry_price: float | None = None,
    veto_reason: str = "",
) -> int:
    ensure_escalation_tables(conn)
    now = time.time()
    cur = conn.execute("""
        INSERT INTO live_escalation_ledger(
            created_at, mint_address, token_name, token_symbol,
            source_snapshot_id, paper_position_id,
            strict_gate_result, legacy_gate_result, relaxed_gate_result,
            runner_score, runner_score_pct,
            confidence, raw_confidence, calibrated_confidence,
            freshness_score, price_freshness_seconds,
            token_age_seconds, signal_age_seconds,
            curve_progress_pct, liquidity_usd,
            wallet_convergence_score, smart_wallet_count, elite_wallet_count,
            first_tick_delay_sec, entry_latency_sec,
            live_escalation_state, escalation_reason, veto_reason,
            executed_live, live_position_id, live_entry_price
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        now, mint,
        str(snap.get("token_name") or ""),
        str(snap.get("token_symbol") or ""),
        snap_id, paper_pos_id,
        strict_gate, legacy_gate, relaxed_gate_result,
        es.score, es.score_pct,
        float(snap.get("confidence") or 0),
        float(snap.get("raw_confidence") or 0),
        float(snap.get("calibrated_confidence") or 0),
        float(snap.get("freshness_score") or 0),
        float(snap.get("price_age_seconds") or 0),
        float(snap.get("token_age_seconds") or 0),
        float(snap.get("signal_age_seconds") or 0),
        float(snap.get("curve_progress_pct") or 0),
        float(snap.get("liquidity_usd") or 0),
        float(snap.get("wallet_convergence_score") or 0),
        int(snap.get("smart_wallet_count") or 0),
        int(snap.get("elite_wallet_count") or 0),
        float(snap.get("first_tick_delay_sec") or 0),
        float(snap.get("signal_age_seconds") or 0),
        es.state,
        " | ".join(es.evidence[:5]),
        veto_reason or " | ".join(es.blocking[:3]),
        int(executed_live),
        live_position_id,
        live_entry_price,
    ))
    try:
        conn.commit()
    except Exception:
        pass
    return cur.lastrowid or 0


def update_escalation_outcome(
    conn: sqlite3.Connection,
    ledger_id: int,
    *,
    state: str | None = None,
    live_exit_price: float | None = None,
    live_realized_pnl_usd: float | None = None,
    live_realized_pnl_pct: float | None = None,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
    exit_reason: str | None = None,
) -> None:
    parts, vals = [], []
    if state:              parts.append("live_escalation_state=?"); vals.append(state)
    if live_exit_price is not None: parts.append("live_exit_price=?"); vals.append(live_exit_price)
    if live_realized_pnl_usd is not None: parts.append("live_realized_pnl_usd=?"); vals.append(live_realized_pnl_usd)
    if live_realized_pnl_pct is not None: parts.append("live_realized_pnl_pct=?"); vals.append(live_realized_pnl_pct)
    if mfe_pct is not None: parts.append("max_favorable_excursion_pct=?"); vals.append(mfe_pct)
    if mae_pct is not None: parts.append("max_adverse_excursion_pct=?"); vals.append(mae_pct)
    if exit_reason:        parts.append("exit_reason=?"); vals.append(exit_reason)
    if not parts:
        return
    parts.append("reviewed_at=?"); vals.append(time.time())
    vals.append(ledger_id)
    try:
        conn.execute(f"UPDATE live_escalation_ledger SET {', '.join(parts)} WHERE id=?", vals)
        conn.commit()
    except Exception:
        pass


def evaluate_for_escalation(
    snap: dict,
    paper_pos: dict | None = None,
    conn: sqlite3.Connection | None = None,
    write_ledger: bool = True,
) -> EscalationScore:
    """
    Full pre-entry evaluation: maturity + escalation score + optional ledger write.
    Called by execution_engine when RUNNER_LIVE_ESCALATION_ENABLED=1.
    """
    es = score_for_escalation(snap, paper_pos)
    sg, sr = strict_cluster_gate(snap)
    lg, lr = legacy_cluster_gate(snap)
    rg, rr = relaxed_gate(snap)
    if write_ledger and conn is not None:
        try:
            ensure_escalation_tables(conn)
            log_escalation(
                conn,
                mint=str(snap.get("mint_address") or ""),
                snap=snap,
                es=es,
                snap_id=snap.get("id"),
                strict_gate=f"{sg}: {sr}",
                legacy_gate=f"{lg}: {lr}",
                relaxed_gate_result=f"{rg}: {rr}",
            )
        except Exception:
            pass
    return es


def is_escalation_enabled(conn: sqlite3.Connection) -> bool:
    return str(get_config(conn, "RUNNER_LIVE_ESCALATION_ENABLED", "0")) == "1"


if __name__ == "__main__":
    db = connect()
    db.row_factory = sqlite3.Row
    if not table_exists(db, "paper_positions"):
        print("paper_positions table missing")
    else:
        open_pos = db.execute("SELECT id, token_name FROM paper_positions WHERE status='OPEN'").fetchall()
        print(f"Scoring {len(open_pos)} open paper positions:\n")
        for p in open_pos:
            s = score_open_position(int(p["id"]), db, mode="paper", write_score=True)
            print(f"  pos {p['id']:>5} {(p['token_name'] or '')[:14]:<14} tier={s['tier']:<8} vel={s.get('velocity_per_min',0):.2f}/min -> {s['recommend']:<18} {s['reason']}")
    db.close()



def connect() -> sqlite3.Connection:
    try:
        from core.schema import get_connection  # type: ignore
        return get_connection()
    except Exception:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return default


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def get_config(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def ensure_score_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runner_likelihood_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            mint_address TEXT,
            token_name TEXT,
            scored_at REAL,
            age_sec REAL,
            entry_price REAL,
            peak_price REAL,
            peak_mult REAL,
            velocity_per_min REAL,
            likelihood REAL,
            tier TEXT,
            recommend TEXT,
            reason TEXT,
            mode TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runner_scores_pos ON runner_likelihood_scores(position_id, scored_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runner_scores_tier ON runner_likelihood_scores(tier, scored_at DESC)")


def log_cognition(conn: sqlite3.Connection, stage: str, message: str) -> None:
    if not table_exists(conn, "cognition_log"):
        return
    cols = columns(conn, "cognition_log")
    now = time.time()
    data = {}
    for cand in ("timestamp", "logged_at", "created_at"):
        if cand in cols:
            data[cand] = now
            break
    if "source" in cols:
        data["source"] = "runner_likelihood_detector"
    if "service_name" in cols:
        data["service_name"] = "runner_likelihood_detector"
    if "stage" in cols:
        data["stage"] = stage
    if "message" in cols:
        data["message"] = message
    elif "content" in cols:
        data["content"] = message
    if not data:
        return
    keys = list(data)
    conn.execute(f"INSERT INTO cognition_log({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [data[k] for k in keys])


def latest_market_price(conn: sqlite3.Connection, mint: str) -> float | None:
    if not mint or not table_exists(conn, "market_snapshots"):
        return None
    cols = columns(conn, "market_snapshots")
    price_col = "price" if "price" in cols else None
    ts_col = "price_updated_at" if "price_updated_at" in cols else ("timestamp" if "timestamp" in cols else "id")
    if not price_col:
        return None
    row = conn.execute(
        f"SELECT {price_col} AS price FROM market_snapshots WHERE mint_address=? AND {price_col} IS NOT NULL AND {price_col}>0 ORDER BY {ts_col} DESC LIMIT 1",
        (mint,),
    ).fetchone()
    if not row:
        return None
    try:
        return float(row["price"])
    except Exception:
        return None


def score_open_position(position_id: int, conn: sqlite3.Connection | None = None, mode: str | None = None, write_score: bool = True) -> dict[str, Any]:
    close_after = conn is None
    if conn is None:
        conn = connect()
    conn.row_factory = sqlite3.Row

    if not table_exists(conn, "paper_positions"):
        if close_after:
            conn.close()
        return {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": "paper_positions table missing"}

    cols = columns(conn, "paper_positions")
    required = {"id", "entry_price", "opened_at", "status"}
    missing = required - cols
    if missing:
        if close_after:
            conn.close()
        return {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": f"paper_positions missing columns: {sorted(missing)}"}

    select_cols = ["id", "entry_price", "opened_at", "status"]
    for c in ["mint_address", "token_name", "highest_price_seen", "current_price", "last_price", "position_size_usd"]:
        if c in cols:
            select_cols.append(c)
    pos = conn.execute(
        f"SELECT {','.join(select_cols)} FROM paper_positions WHERE id=? AND status='OPEN'",
        (position_id,),
    ).fetchone()

    if not pos:
        if close_after:
            conn.close()
        return {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": "position not found/open"}

    ensure_score_table(conn)
    now = time.time()
    mode = mode or str(get_config(conn, "RUNNER_DETECTOR_MODE", "paper") or "paper")
    live_scale_enabled = str(get_config(conn, "RUNNER_LIVE_SCALE_ENABLED", "0")) == "1"

    mint = row_get(pos, "mint_address", None)
    token_name = row_get(pos, "token_name", None)
    opened_at = float(row_get(pos, "opened_at", now) or now)
    age_sec = max(0.0, now - opened_at)
    entry = float(row_get(pos, "entry_price", 0) or 0)
    peak = float(row_get(pos, "highest_price_seen", 0) or 0)
    cur = row_get(pos, "current_price", None) or row_get(pos, "last_price", None)
    try:
        cur = float(cur) if cur is not None else None
    except Exception:
        cur = None
    market_cur = latest_market_price(conn, mint) if mint else None
    peak = max([v for v in [entry, peak, cur, market_cur] if v is not None and v > 0] or [entry])

    if entry <= 0:
        result = {"likelihood": 0.0, "tier": "UNKNOWN", "recommend": "WAIT", "reason": "no entry price"}
        if close_after:
            conn.close()
        return result

    peak_mult = peak / entry if entry else 1.0
    velocity_per_min = (peak_mult - 1.0) / (age_sec / 60.0) if age_sec > 0 else 0.0

    monster_vel = float(get_config(conn, "RUNNER_MONSTER_VELOCITY_PER_MIN", "5.0") or 5.0)
    strong_vel = float(get_config(conn, "RUNNER_STRONG_VELOCITY_PER_MIN", "1.0") or 1.0)
    dud_min_age = float(get_config(conn, "RUNNER_DUD_MIN_AGE_SEC", "45") or 45)
    dud_min_peak = float(get_config(conn, "RUNNER_DUD_MIN_PEAK_MULT", "1.05") or 1.05)

    if age_sec < 15:
        tier, likelihood, recommend = "WAIT", 0.50, "WAIT"
        reason = f"Only {age_sec:.0f}s old — wait until 15s for signal"
    elif velocity_per_min >= monster_vel:
        tier, likelihood = "MONSTER", 0.95
        recommend = "PAPER_SCALE_SIGNAL" if mode == "paper" else ("LIVE_REVIEW" if not live_scale_enabled else "LIVE_REVIEW")
        reason = f"Monster velocity {velocity_per_min:.2f}x/min — hold aggressive; live scale remains gated"
    elif velocity_per_min >= strong_vel:
        tier, likelihood, recommend = "STRONG", 0.80, "HOLD"
        reason = f"Strong velocity {velocity_per_min:.2f}x/min — likely 2-5x runner"
    elif velocity_per_min >= 0.3 and age_sec < 60:
        tier, likelihood, recommend = "NEUTRAL", 0.55, "HOLD"
        reason = f"Moderate velocity {velocity_per_min:.2f}x/min — possible small runner"
    elif peak_mult >= 1.10 and age_sec < dud_min_age:
        tier, likelihood, recommend = "NEUTRAL", 0.40, "HOLD"
        reason = f"Mild peak {(peak_mult-1)*100:.1f}% in {age_sec:.0f}s — watch"
    elif age_sec >= dud_min_age and peak_mult < dud_min_peak:
        tier, likelihood, recommend = "DUD", 0.15, "CUT_RECOMMENDATION"
        reason = f"No move ({(peak_mult-1)*100:+.1f}% peak) after {age_sec:.0f}s — paper cut candidate"
    else:
        tier, likelihood, recommend = "NEUTRAL", 0.35, "HOLD"
        reason = f"velocity={velocity_per_min:.2f}/min peak={(peak_mult-1)*100:+.1f}% age={age_sec:.0f}s"

    result = {
        "likelihood": likelihood,
        "tier": tier,
        "velocity_per_min": velocity_per_min,
        "peak_mult": peak_mult,
        "peak_price": peak,
        "age_sec": age_sec,
        "recommend": recommend,
        "reason": reason,
        "mode": mode,
    }

    if write_score:
        conn.execute("""
            INSERT INTO runner_likelihood_scores(
                position_id, mint_address, token_name, scored_at, age_sec,
                entry_price, peak_price, peak_mult, velocity_per_min,
                likelihood, tier, recommend, reason, mode
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (position_id, mint, token_name, now, age_sec, entry, peak, peak_mult, velocity_per_min, likelihood, tier, recommend, reason, mode))
        if tier in {"MONSTER", "STRONG", "DUD"}:
            log_cognition(conn, f"RUNNER_{tier}", f"pos={position_id} {token_name or mint} tier={tier} vel={velocity_per_min:.2f}/min rec={recommend} {reason}")
        try:
            conn.commit()
        except Exception:
            pass

    if close_after:
        conn.close()
    return result


def score_all_open(conn: sqlite3.Connection | None = None, mode: str = "paper") -> list[dict[str, Any]]:
    close_after = conn is None
    if conn is None:
        conn = connect()
    conn.row_factory = sqlite3.Row
    if not table_exists(conn, "paper_positions"):
        return []
    rows = conn.execute("SELECT id FROM paper_positions WHERE status='OPEN'").fetchall()
    out = []
    for r in rows:
        out.append(score_open_position(int(r["id"]), conn, mode=mode, write_score=True))
    if close_after:
        conn.close()
    return out


if __name__ == "__main__":
    db = connect()
    db.row_factory = sqlite3.Row
    if not table_exists(db, "paper_positions"):
        print("paper_positions table missing")
    else:
        open_pos = db.execute("SELECT id, token_name FROM paper_positions WHERE status='OPEN'").fetchall()
        print(f"Scoring {len(open_pos)} open paper positions:\n")
        for p in open_pos:
            s = score_open_position(int(p["id"]), db, mode="paper", write_score=True)
            print(f"  pos {p['id']:>5} {(p['token_name'] or '')[:14]:<14} tier={s['tier']:<8} vel={s.get('velocity_per_min',0):.2f}/min -> {s['recommend']:<18} {s['reason']}")
    db.close()
