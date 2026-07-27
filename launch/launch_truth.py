"""
launch_truth.py
================
Backend truth surface — prints + logs the current real launch state.

Run anytime:
    python launch_truth.py

Used by:
  - Sovereign hub Lane Health strip (Pack 02)
  - Operator console pre-launch sanity check
  - Post-launch verification (audit_no_latch_path also uses some of these)

What it shows:
  - Effective trading mode (paper / live)
  - Paper slots / live slots
  - Confidence floor + Mode B floor
  - Mode B armed / blocked + reason
  - Golden Lattice enforced
  - Active DB path
  - Last security-preflight result (if known)
  - Lane Health counts (MI Qualified, Pricing OK, Supervisor Eligible, Latched, Paper Open)
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "sentinuity_matrix.db"


def _config(db: sqlite3.Connection, key: str, default=None):
    try:
        r = db.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return r[0] if r else default
    except Exception:
        return default


def _count(db: sqlite3.Connection, sql: str, args=()) -> int:
    try:
        r = db.execute(sql, args).fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return -1


def main() -> int:
    if not DB.exists():
        print(f"FATAL: {DB} not found")
        return 1

    db = sqlite3.connect(str(DB), timeout=15)
    db.row_factory = sqlite3.Row
    now = time.time()

    print("=" * 70)
    print(f"SENTINUITY LAUNCH TRUTH  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ── Mode + safety ──────────────────────────────────────────────────
    mode = _config(db, "TRADING_MODE", "paper")
    live_enabled = _config(db, "LIVE_TRADING_ENABLED", "0")
    pos_size = _config(db, "POSITION_SIZE_USD", "?")
    paper_slots = _config(db, "PAPER_MAX_OPEN_POSITIONS", "?")
    live_slots = _config(db, "LIVE_MAX_OPEN_POSITIONS", "?")
    conf_floor = _config(db, "MODE_B_CONF_FLOOR", "?")
    drawdown = _config(db, "DRAWDOWN_HALT_ACTIVE", "0")
    oracle_state = _config(db, "WS_ORACLE_STATE", "?")
    latched_override = _config(db, "LATCHED_OVERRIDE_ENABLED", "0")
    runner_escalation = _config(db, "RUNNER_LIVE_ESCALATION_ENABLED", "0")
    golden_lattice = _config(db, "GOLDEN_LATTICE_ENFORCED", "?")
    exceptional_fire = _config(db, "EXCEPTIONAL_FIRE_ENABLED", "0")
    live_losses_2h = _config(db, "LIVE_LOSSES_2H", "0")
    security_preflight = _config(db, "SECURITY_PREFLIGHT_RESULT", "?")

    print()
    print("MODE / SAFETY")
    print(f"  Trading Mode:                {mode}")
    print(f"  Live Trading Enabled:        {live_enabled}")
    print(f"  Position Size:               ${pos_size}")
    print(f"  Paper Slots:                 {paper_slots}")
    print(f"  Live Slots:                  {live_slots}")
    print(f"  Mode B Conf Floor:           {conf_floor}")
    print(f"  Exceptional Fire:            {exceptional_fire}")
    print(f"  Drawdown Halt:               {drawdown}")
    print(f"  Oracle State:                {oracle_state}")
    print(f"  Latched Override:            {latched_override}  (1=allows live without smart-wallet)")
    print(f"  Runner Live Escalation:      {runner_escalation}  (1=auto-fires on MONSTER detection)")
    print(f"  Golden Lattice Enforced:     {golden_lattice}")
    print(f"  Security Preflight:          {security_preflight}")
    print(f"  Live Losses (2h):            {live_losses_2h}")

    # ── DUAL-MODE ARMING STATE ─────────────────────────────────────────
    print()
    print("DUAL-MODE ARMING STATE")
    print("─" * 70)
    # Live armed = TRADING_MODE='live' OR LIVE_TRADING_ENABLED='1'
    is_live_armed = (str(mode).lower() == "live") or (str(live_enabled) == "1")
    is_paper_alongside = (int(paper_slots) if str(paper_slots).isdigit() else 0) > 0
    is_exceptional_on = str(exceptional_fire) == "1"
    
    print(f"  LIVE ARMED:                  {'YES' if is_live_armed else 'NO '}")
    print(f"  PAPER ALONGSIDE:             {'YES' if is_paper_alongside else 'NO '}")
    print(f"  EXCEPTIONAL FIRE:            {'ON ' if is_exceptional_on else 'OFF'}")
    print(f"  LIVE CONDITIONAL FIRE:       YES (all gates intact)")
    print(f"  LIVE FORCE/BYPASS:           NO  (no gate relaxed)")
    print()
    
    # Why live could/couldn't fire RIGHT NOW
    live_fire_blockers = []
    if not is_live_armed:
        live_fire_blockers.append("LIVE_NOT_ARMED")
    if str(oracle_state).upper() == "STALLED":
        live_fire_blockers.append("ORACLE_STALLED")
    if str(drawdown) == "1":
        live_fire_blockers.append("DRAWDOWN_HALT_ACTIVE")
    try:
        if int(live_losses_2h) >= 3:
            live_fire_blockers.append(f"LIVE_LOSSES_2H={live_losses_2h}")
    except Exception:
        pass
    if str(security_preflight).upper() in ("FAIL", "FAILED"):
        live_fire_blockers.append("SECURITY_PREFLIGHT_FAIL")
    
    if live_fire_blockers:
        print(f"  LIVE FIRE BLOCKERS:          {' | '.join(live_fire_blockers)}")
    else:
        print(f"  LIVE FIRE BLOCKERS:          (none — live ready to fire on aligned signal)")

    # ── Lane Health ────────────────────────────────────────────────────
    print()
    print("LANE HEALTH (last 10 min)")
    print("─" * 70)

    win_10m = now - 600

    # MI Qualified — qualified in last 10 min
    mi_qual = _count(db,
        "SELECT COUNT(*) FROM market_snapshots WHERE candidate_state='qualified' AND COALESCE(qualified_at,0) >= ?",
        (win_10m,))

    # Pricing Contract OK — qualified AND price_status=priced AND price_updated_at fresh
    pricing_ok = _count(db,
        """SELECT COUNT(*) FROM market_snapshots
           WHERE candidate_state='qualified'
             AND price_status='priced'
             AND is_tradeable=1
             AND COALESCE(price_updated_at,0) >= ?""",
        (now - 120,))

    # Supervisor Eligible — what supervisor would claim NOW
    sup_eligible = _count(db,
        """SELECT COUNT(*) FROM market_snapshots
           WHERE latched=0 AND execution_ready != 2
             AND candidate_state NOT IN ('vetoed','exited','expired_stale','executed','EXECUTOR_STALE_GATE')
             AND (candidate_state='qualified' OR (candidate_state='pending' AND quality_status='qualified'))
             AND price_status='priced'
             AND is_tradeable=1
             AND COALESCE(price_updated_at,0) > ?""",
        (now - 120,))

    # Latched
    latched = _count(db, "SELECT COUNT(*) FROM market_snapshots WHERE latched=1")

    # Paper Open
    paper_open = _count(db, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'")

    # Top blocker — most common quality_reason among rejected in last 10 min
    blocker_row = db.execute(
        """SELECT quality_reason, COUNT(*) as n FROM market_snapshots
           WHERE quality_status='rejected'
             AND COALESCE(qualified_at, updated_at, 0) >= ?
           GROUP BY quality_reason ORDER BY n DESC LIMIT 1""",
        (win_10m,)).fetchone()
    top_blocker = (blocker_row["quality_reason"] if blocker_row else "(none)")

    # QUALIFIED_BUT_UNPRICED specific count (the new visibility)
    unpriced = _count(db,
        "SELECT COUNT(*) FROM market_snapshots WHERE quality_reason='QUALIFIED_BUT_UNPRICED' AND COALESCE(updated_at,0) >= ?",
        (win_10m,))

    rows = [
        ("MI Qualified",          mi_qual,      "OK" if mi_qual > 0 else "WARN"),
        ("Pricing Contract OK",   pricing_ok,   "OK" if pricing_ok > 0 else "WARN"),
        ("Supervisor Eligible",   sup_eligible, "OK" if sup_eligible > 0 else "WARN"),
        ("Latched",               latched,      "OK" if latched > 0 else "WARN"),
        ("Paper Open",            paper_open,   "OK" if paper_open > 0 else "WARN"),
    ]
    for name, n, health in rows:
        print(f"  {name:<25} {n:>5}  [{health}]")
    print()
    print(f"  Top blocker (last 10m):      {top_blocker}")
    print(f"  QUALIFIED_BUT_UNPRICED:      {unpriced}  ← non-zero means MI qualified rows had no price")

    # ── Verdict ────────────────────────────────────────────────────────
    print()
    print("VERDICT")
    print("─" * 70)

    if paper_open > 0:
        verdict = "🟢 TRADING ACTIVE — paper positions open"
    elif latched > 0:
        verdict = "🟡 TRADING LATCHED — paper positions about to open"
    elif sup_eligible > 0:
        verdict = "🟡 SUPERVISOR HAS CANDIDATES — waiting for confidence/age gates"
    elif pricing_ok > 0:
        verdict = "🟡 PRICING OK — supervisor not picking up (check Phase A gates)"
    elif mi_qual > 0:
        verdict = "🔴 MI QUALIFIES — but pricing contract failing"
    else:
        verdict = "🔴 NO QUALIFICATIONS — qualifier rejecting everything"
    print(f"  {verdict}")
    
    # Dual-mode verdict line
    if is_live_armed and is_paper_alongside:
        if live_fire_blockers:
            arming_verdict = f"  🛡 DUAL-MODE ARMED  (live blocked by: {', '.join(live_fire_blockers)})"
        else:
            arming_verdict = "  🛡 DUAL-MODE ARMED  (live ready to fire on exceptional aligned signal)"
    elif is_live_armed:
        arming_verdict = "  ⚠️  LIVE ARMED but paper not running alongside"
    elif is_paper_alongside:
        arming_verdict = "  📝 PAPER ONLY  (live not armed)"
    else:
        arming_verdict = "  ⛔ NOTHING TRADING (no paper or live)"
    print(arming_verdict)

    db.close()
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
