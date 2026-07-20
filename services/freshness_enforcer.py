"""
services/freshness_enforcer.py
================================
Continuous Freshness Enforcer - runs every 8 seconds.

Signal age doctrine (LOCKED):
  Signal age = time since first_seen/created/qualified - NOT price oracle ticks.
  Price freshness and signal freshness are separate concepts.
  A stale signal with a fresh oracle price tick is still STALE.

Freshness model:
  HOT:   0-45s    (fresh off the pump monitor)
  WARM:  45-120s  (still viable)
  COOL:  120-300s (borderline)
  STALE: 300-600s (veto pending)
  DEAD:  600s+    (expire everything including latched)

Time rule (LOCKED):
  Always compute time.time() in Python.
  Pass as ? parameter into SQL.
  Never use ? in SQL.
"""
from __future__ import annotations
import sys, time, logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [freshness] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("freshness_enforcer")

SERVICE_NAME  = "freshness_enforcer"
CYCLE_SECONDS = 8

# Age thresholds (seconds)
_HOT_SEC   = 45
_WARM_SEC  = 120
_COOL_SEC  = 300
_STALE_SEC = 600   # veto at this age
_DEAD_SEC  = 600   # expire latched at this age

# SIGNAL birth timestamp - uses ONLY first_seen_at / created_at / qualified_at.
# Deliberately EXCLUDES price_updated_at so oracle price refreshes don't
# mask a stale signal and let it survive longer than it should.
_BIRTH_TS = """MAX(
    COALESCE(CAST(first_seen_at  AS REAL), 0),
    COALESCE(CAST(qualified_at   AS REAL), 0),
    COALESCE(CAST(created_at     AS REAL), 0)
)"""


def _run_cycle() -> dict:
    stats = {}
    now = time.time()   # Python-computed, passed as parameter

    # Cutoff timestamps - all Python-computed
    hot_cutoff   = now - _HOT_SEC
    warm_cutoff  = now - _WARM_SEC
    cool_cutoff  = now - _COOL_SEC
    stale_cutoff = now - _STALE_SEC
    dead_cutoff  = now - _DEAD_SEC

    with get_connection() as conn:

        # 1. Reclassify tiers using signal birth time (NOT price_updated_at)
        n1 = conn.execute(f"""
            UPDATE market_snapshots
            SET tier = CASE
                WHEN {_BIRTH_TS} >= ?  THEN 'HOT'
                WHEN {_BIRTH_TS} >= ?  THEN 'WARM'
                WHEN {_BIRTH_TS} >= ?  THEN 'COOL'
                ELSE 'STALE'
            END,
            freshness_score = MAX(0.0, ({_BIRTH_TS} - ?) / {float(_STALE_SEC)})
            WHERE candidate_state NOT IN
                ('vetoed','exited','executed','expired_stale','EXECUTOR_STALE_GATE')
              AND latched = 0
              AND {_BIRTH_TS} > 0
        """, (hot_cutoff, warm_cutoff, cool_cutoff, stale_cutoff)).rowcount
        stats["tiers_updated"] = n1

        # 2. Veto un-latched qualified signals older than STALE_SEC (600s)
        # Uses birth timestamp - price oracle ticks do NOT extend signal life
        n2 = conn.execute(f"""
            UPDATE market_snapshots
            SET candidate_state = 'vetoed',
                execution_ready = 0,
                quality_reason = CASE
                    WHEN quality_reason IS NULL OR quality_reason = ''
                    THEN 'SIGNAL_BIRTH_AGE_EXPIRED_600S'
                    ELSE quality_reason || '|SIGNAL_BIRTH_AGE_EXPIRED_600S'
                END
            WHERE candidate_state NOT IN
                ('vetoed','exited','executed','expired_stale','EXECUTOR_STALE_GATE','mtm')
              AND latched = 0
              AND execution_ready = 0
              AND {_BIRTH_TS} > 0
              AND {_BIRTH_TS} < ?
        """, (stale_cutoff,)).rowcount
        stats["signal_age_vetoed"] = n2

        # 3. Evict stale pending rows older than DEAD_SEC (600s)
        n3 = conn.execute(f"""
            UPDATE market_snapshots
            SET candidate_state = 'expired_stale',
                quality_reason = CASE
                    WHEN quality_reason IS NULL OR quality_reason = ''
                    THEN 'FRESHNESS_ENFORCER_STALE'
                    ELSE quality_reason || '|FRESHNESS_ENFORCER_STALE'
                END
            WHERE candidate_state = 'pending'
              AND quality_status NOT IN ('qualified','rejected','error')
              AND latched = 0
              AND {_BIRTH_TS} > 0
              AND {_BIRTH_TS} < ?
        """, (dead_cutoff,)).rowcount
        stats["stale_evicted"] = n3

        # 4. Clear stale execution_ready flags (unlatch if COOL_SEC passed)
        n4 = conn.execute(f"""
            UPDATE market_snapshots
            SET execution_ready = 0,
                latched = 0,
                candidate_state = 'vetoed',
                quality_reason = CASE
                    WHEN quality_reason IS NULL OR quality_reason = ''
                    THEN 'EXECUTION_READY_STALE'
                    ELSE quality_reason || '|EXECUTION_READY_STALE'
                END
            WHERE COALESCE(execution_ready,0) IN (1,2)
              AND latched = 0
              AND {_BIRTH_TS} > 0
              AND {_BIRTH_TS} < ?
        """, (cool_cutoff,)).rowcount
        stats["exec_ready_cleared"] = n4

        # 5. EXPIRE STALE LATCHED ROWS - directive requirement.
        # A latched signal that was never executed and is now >600s old
        # must be expired. This prevents zombie latches blocking the queue.
        # NEVER touches executed rows or open positions.
        n5 = conn.execute(f"""
            UPDATE market_snapshots
            SET candidate_state = 'expired_stale',
                latched = 0,
                execution_ready = 0,
                latch_claimed_until = NULL,
                quality_reason = CASE
                    WHEN quality_reason IS NULL OR quality_reason = ''
                    THEN 'LATCHED_SIGNAL_EXPIRED_600S'
                    ELSE quality_reason || '|LATCHED_SIGNAL_EXPIRED_600S'
                END
            WHERE latched = 1
              AND candidate_state NOT IN ('executed', 'exited')
              AND {_BIRTH_TS} > 0
              AND {_BIRTH_TS} < ?
        """, (dead_cutoff,)).rowcount
        stats["latched_expired"] = n5

        # 6. Release expired claim locks (Python time as parameter)
        n6 = conn.execute("""
            UPDATE market_snapshots
            SET qualify_claimed_until = NULL
            WHERE qualify_claimed_until IS NOT NULL
              AND CAST(qualify_claimed_until AS REAL) < ?
        """, (now,)).rowcount
        stats["claims_released"] = n6

        conn.commit()

    return stats


def run() -> None:
    log.info("Freshness enforcer started - cycle=%ds", CYCLE_SECONDS)
    log.info(
        "Signal age uses birth timestamps only (first_seen_at/created_at/qualified_at). "
        "Price oracle ticks excluded from age calculation."
    )
    update_heartbeat(SERVICE_NAME, "starting", "freshness_enforcer online")
    _cycle = 0

    while True:
        try:
            stats = _run_cycle()
            _cycle += 1

            if any(v > 0 for v in stats.values()) or _cycle % 60 == 0:
                note = (
                    f"tiers={stats['tiers_updated']} "
                    f"vetoed={stats.get('signal_age_vetoed',0)} "
                    f"evicted={stats['stale_evicted']} "
                    f"latched_expired={stats.get('latched_expired',0)} "
                    f"exec_cleared={stats.get('exec_ready_cleared',0)} "
                    f"claims={stats['claims_released']}"
                )
                log.debug("[ENFORCER] %s", note)
                update_heartbeat(SERVICE_NAME, "alive", note,
                                 work_processed=stats.get("latched_expired", 0)
                                 + stats.get("signal_age_vetoed", 0))

        except Exception as exc:
            log.warning("[ENFORCER_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()

# ─────────────────────────────────────────────────────────────────────────────
# HOTFIX_20260707_PRELAUNCH_COMPAT
# Launcher/prelaunch expect these two one-shot helpers. They are intentionally
# DB-path based, schema-tolerant, and never touch paper_positions/open positions,
# balances, PnL, live flags, or strategy thresholds.
def ensure_freshness_config(db_path=None) -> dict:
    """Ensure non-strategy freshness config defaults exist. Idempotent."""
    import sqlite3, time
    from pathlib import Path
    db = Path(db_path or "sentinuity_matrix.db")
    out = {"db": str(db), "inserted": 0, "ok": False}
    if not db.exists():
        out["error"] = "db_missing"
        return out
    pairs = {
        "FRESHNESS_ENFORCER_CYCLE_SECONDS": "8",
        "FRESHNESS_SIGNAL_STALE_SEC": "600",
        "FRESHNESS_SIGNAL_DEAD_SEC": "600",
        "FRESHNESS_PRELAUNCH_MAX_EXEC_READY_AGE_SEC": "600",
        "FRESHNESS_PRELAUNCH_MAX_LATCH_AGE_SEC": "600",
    }
    con = sqlite3.connect(str(db), timeout=10)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT, description TEXT)")
        for k, v in pairs.items():
            before = con.total_changes
            con.execute(
                "INSERT OR IGNORE INTO system_config(key,value,description) VALUES(?,?,?)",
                (k, v, "freshness prelaunch compatibility default")
            )
            if con.total_changes > before:
                out["inserted"] += 1
        con.commit()
        out["ok"] = True
        return out
    finally:
        con.close()


def run_prelaunch_freshness_cleanup(db_path=None, dry_run=False) -> dict:
    """One-shot stale pre-entry cleanup. Never touches open/closed positions."""
    import sqlite3, time
    from pathlib import Path
    db = Path(db_path or "sentinuity_matrix.db")
    stats = {
        "expired_candidates": 0,
        "cleared_execution_ready": 0,
        "cleared_latches": 0,
        "stale_price_blocks": 0,
        "open_position_mints_excluded": 0,
        "dry_run": bool(dry_run),
        "ok": False,
    }
    if not db.exists():
        stats["error"] = "db_missing"
        return stats
    now = time.time()
    dead_cutoff = now - 600
    cool_cutoff = now - 300
    con = sqlite3.connect(str(db), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "market_snapshots" not in tables:
            stats["ok"] = True
            stats["note"] = "market_snapshots_missing_noop"
            return stats
        cols = {r[1] for r in con.execute("PRAGMA table_info(market_snapshots)")}
        def has(*names): return all(n in cols for n in names)
        birth_parts = []
        for c in ("first_seen_at", "qualified_at", "created_at", "timestamp"):
            if c in cols:
                birth_parts.append(f"COALESCE(CAST({c} AS REAL),0)")
        birth = "MAX(" + ",".join(birth_parts) + ")" if birth_parts else "0"
        # Exclude currently open mints if available.
        open_mints = set()
        if "paper_positions" in tables:
            pc = {r[1] for r in con.execute("PRAGMA table_info(paper_positions)")}
            mint_col = "mint_address" if "mint_address" in pc else ("mint" if "mint" in pc else None)
            status_col = "status" if "status" in pc else None
            if mint_col and status_col:
                for r in con.execute(f"SELECT DISTINCT {mint_col} m FROM paper_positions WHERE UPPER(COALESCE({status_col},'')) IN ('OPEN','LIVE','ACTIVE')"):
                    if r["m"]: open_mints.add(str(r["m"]))
        stats["open_position_mints_excluded"] = len(open_mints)
        mint_filter = ""
        params_common = []
        if open_mints and "mint_address" in cols:
            qs = ",".join("?" for _ in open_mints)
            mint_filter = f" AND COALESCE(mint_address,'') NOT IN ({qs})"
            params_common = list(open_mints)
        # Ensure optional columns used by updates exist before referencing them.
        # If the schema lacks a column, skip that specific cleanup rather than error.
        if has("execution_ready"):
            sql = f"""
                UPDATE market_snapshots
                SET execution_ready=0
                {", latched=0" if "latched" in cols else ""}
                {", candidate_state='vetoed'" if "candidate_state" in cols else ""}
                {", quality_reason=COALESCE(NULLIF(quality_reason,''),'EXECUTION_READY_STALE')" if "quality_reason" in cols else ""}
                WHERE COALESCE(execution_ready,0) IN (1,2) AND {birth} > 0 AND {birth} < ? {mint_filter}
            """
            if dry_run:
                q = "SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(execution_ready,0) IN (1,2) AND " + birth + " > 0 AND " + birth + " < ? " + mint_filter
                stats["cleared_execution_ready"] = con.execute(q, [cool_cutoff] + params_common).fetchone()[0]
            else:
                cur = con.execute(sql, [cool_cutoff] + params_common)
                stats["cleared_execution_ready"] = cur.rowcount if cur.rowcount >= 0 else 0
        if has("latched"):
            sql = f"""
                UPDATE market_snapshots
                SET latched=0
                {", execution_ready=0" if "execution_ready" in cols else ""}
                {", candidate_state='expired_stale'" if "candidate_state" in cols else ""}
                {", latch_claimed_until=NULL" if "latch_claimed_until" in cols else ""}
                {", quality_reason=COALESCE(NULLIF(quality_reason,''),'LATCHED_SIGNAL_EXPIRED_600S')" if "quality_reason" in cols else ""}
                WHERE latched=1
                  {"AND candidate_state NOT IN ('executed','exited')" if "candidate_state" in cols else ""}
                  AND {birth} > 0 AND {birth} < ? {mint_filter}
            """
            if dry_run:
                q = "SELECT COUNT(*) FROM market_snapshots WHERE latched=1 AND " + birth + " > 0 AND " + birth + " < ? " + mint_filter
                stats["cleared_latches"] = con.execute(q, [dead_cutoff] + params_common).fetchone()[0]
            else:
                cur = con.execute(sql, [dead_cutoff] + params_common)
                stats["cleared_latches"] = cur.rowcount if cur.rowcount >= 0 else 0
        if has("candidate_state"):
            sql = f"""
                UPDATE market_snapshots
                SET candidate_state='expired_stale'
                {", execution_ready=0" if "execution_ready" in cols else ""}
                {", latched=0" if "latched" in cols else ""}
                {", quality_reason=COALESCE(NULLIF(quality_reason,''),'FRESHNESS_PRELAUNCH_STALE')" if "quality_reason" in cols else ""}
                WHERE candidate_state IN ('pending','qualified')
                  AND {birth} > 0 AND {birth} < ? {mint_filter}
            """
            if dry_run:
                q = "SELECT COUNT(*) FROM market_snapshots WHERE candidate_state IN ('pending','qualified') AND " + birth + " > 0 AND " + birth + " < ? " + mint_filter
                stats["expired_candidates"] = con.execute(q, [dead_cutoff] + params_common).fetchone()[0]
            else:
                cur = con.execute(sql, [dead_cutoff] + params_common)
                stats["expired_candidates"] = cur.rowcount if cur.rowcount >= 0 else 0
        if not dry_run:
            con.commit()
        stats["ok"] = True
        return stats
    finally:
        con.close()
# END HOTFIX_20260707_PRELAUNCH_COMPAT

