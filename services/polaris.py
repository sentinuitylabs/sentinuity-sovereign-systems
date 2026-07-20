
# SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
try:
    from birdeye_quota_guard import install_birdeye_requests_guard as _install_birdeye_guard
    _install_birdeye_guard()
except Exception:
    pass
# /SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
import io, json, sys, time, logging, os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except Exception:
    pass

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
BRAVE_KEY      = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
NIM_API_KEY    = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
# NUGGET routes through NIM/Kimi — no Gemini dependency
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_POLARIS_TOKEN", "").strip()  # POLARIS bot — briefs to Pop+Mum
TELEGRAM_OWNER_ID  = os.getenv("TELEGRAM_OWNER_ID", "").strip()
OPENAI_MODEL   = "gpt-5.4-nano"   # default budget tier; router escalates per-call

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

try:
    from services.polaris_notify import send_stage_notification as _notify
except ImportError:
    def _notify(stage, data, **kw): pass  # graceful fallback

from core.schema import (
    get_connection,
    init_db,
    update_heartbeat,
    queue_improvement,
    get_config_value,
    insert_polaris_proposal
)

try:
    from services.cognition_logger import log_cognition
except Exception:
    def log_cognition(*args, **kwargs):
        return None

# --- encoding fix ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [POLARIS] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()]
)

SERVICE_NAME = "polaris"

# ── SOVEREIGN IDENTITY ────────────────────────────────────────────────────────
try:
    from core.sovereign_identity import (
        POLARIS_IDENTITY, IVARIS_IDENTITY,
        get_polaris_prompt, log_identity_boot,
    )
    log_identity_boot("polaris")
    logging.info(
        "IDENTITY: %s | Partner: %s | Both powered by single Anthropic key",
        POLARIS_IDENTITY["name"], IVARIS_IDENTITY["name"],
    )
except ImportError:
    pass  # Identity manifest not yet deployed — safe to continue


# ── PATTERN MEMORY ────────────────────────────────────────────────────────────
try:
    from services.polaris_pattern_memory import run_pattern_analysis
    _PATTERN_MEMORY_AVAILABLE = True
    logging.info("DOCTRINE: pattern memory module loaded")
except ImportError:
    _PATTERN_MEMORY_AVAILABLE = False
    logging.warning("DOCTRINE: polaris_pattern_memory not found")


def _ensure_standing_tasks_compat_schema() -> None:
    """Idempotently extend the current standing_tasks table for Polaris leases."""
    additions = {
        "task_type": "TEXT",
        "task_name": "TEXT",
        "claimed_by": "TEXT",
        "claimed_at": "TEXT",
        "last_evidence_json": "TEXT",
        "reportable_update": "INTEGER DEFAULT 0",
        "last_reported_at": "TEXT",
    }
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='standing_tasks'"
        ).fetchone()
        if not exists:
            return
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(standing_tasks)")}
        for name, ddl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE standing_tasks ADD COLUMN {name} {ddl}")
        conn.execute("""
            UPDATE standing_tasks
            SET task_name = COALESCE(NULLIF(task_name,''), title, task_key, 'TASK'),
                task_type = COALESCE(NULLIF(task_type,''), domain, task_key, 'TASK')
        """)
        conn.commit()


def _ensure_polaris_proposals_compat_schema() -> None:
    """Make legacy proposal tables compatible without deleting or rewriting data."""
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS polaris_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_hash TEXT UNIQUE,
                    proposal_type TEXT,
                    proposal_text TEXT,
                    suggested_action TEXT,
                    confidence REAL DEFAULT 0.0,
                    metrics_json TEXT,
                    status TEXT DEFAULT 'open',
                    created_at REAL,
                    last_seen_at REAL,
                    seen_count INTEGER DEFAULT 1
                )
            """)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(polaris_proposals)")}
            additions = {
                "proposal_hash": "TEXT",
                "proposal_type": "TEXT",
                "proposal_text": "TEXT",
                "suggested_action": "TEXT",
                "confidence": "REAL DEFAULT 0.0",
                "metrics_json": "TEXT",
                "status": "TEXT DEFAULT 'open'",
                "created_at": "REAL",
                "last_seen_at": "REAL",
                "seen_count": "INTEGER DEFAULT 1",
            }
            for column, ddl in additions.items():
                if column not in existing:
                    conn.execute(f'ALTER TABLE polaris_proposals ADD COLUMN "{column}" {ddl}')
            now = time.time()
            conn.execute(
                "UPDATE polaris_proposals SET created_at=COALESCE(created_at,last_seen_at,?) "
                "WHERE created_at IS NULL",
                (now,),
            )
            conn.execute(
                "UPDATE polaris_proposals SET last_seen_at=COALESCE(last_seen_at,created_at,?) "
                "WHERE last_seen_at IS NULL",
                (now,),
            )
            conn.commit()
    except Exception as exc:
        logging.warning("POLARIS proposal schema compatibility check failed: %s", exc)


class Polaris:

    def __init__(self):
        self.last_analysis           = 0.0
        self.last_pattern_run        = 0.0
        self.reviews_at_last_pattern = 0
        self.PATTERN_INTERVAL        = 10   # run after every 10 new reviews

    # -----------------------------
    #  MISSION LOCK: PRICE STALENESS GATE
    # -----------------------------

    # PATCH A: Threshold in seconds — if freshest MTM price is older than this
    # and there is an open position, treat as stale and suppress non-repair proposals.
    STALENESS_THRESHOLD_SEC = 300.0  # PATCH 4: extended — prevents premature Mission Lock while feed stabilises

    def _is_price_stale(self) -> bool:
        """
        Returns True if there is at least one OPEN position AND the freshest
        observed_price update in market_snapshots is older than STALENESS_THRESHOLD_SEC.

        Fail-safe: returns False on any DB error so normal operation is never
        blocked by a diagnostic check failure. Logs the error for visibility.
        """
        try:
            with get_connection() as conn:
                open_count = conn.execute(
                    "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
                ).fetchone()[0]

                if open_count == 0:
                    return False   # no positions — staleness irrelevant

                freshest_row = conn.execute(
                    """SELECT MAX(price_updated_at) AS freshest
                       FROM market_snapshots
                       WHERE observed_price IS NOT NULL AND observed_price > 0"""
                ).fetchone()

                freshest = float(freshest_row["freshest"] or 0) if freshest_row else 0.0
                age_s = time.time() - freshest

                if age_s > self.STALENESS_THRESHOLD_SEC:
                    logging.warning(
                        "POLARIS MISSION-LOCK: price stale %.0fs (threshold=%ds) "
                        "with %d open position(s) — suppressing non-repair proposals",
                        age_s, self.STALENESS_THRESHOLD_SEC, open_count,
                    )
                    return True

                return False

        except Exception as e:
            logging.error("POLARIS _is_price_stale: DB check failed — defaulting to stale=False: %s", e)
            return False   # fail-open: never silently block the organism on a check error

    def _repair_already_queued(self) -> bool:
        """
        Returns True if a SYSTEM_REPAIR proposal targeting price staleness
        is already in the 'open' state. Prevents duplicate repair spam.
        """
        try:
            with get_connection() as conn:
                row = conn.execute(
                    """SELECT id FROM polaris_proposals
                       WHERE proposal_type = 'SYSTEM_REPAIR'
                         AND status = 'open'
                         AND (proposal_text LIKE '%stale%' OR proposal_text LIKE '%price%')
                       LIMIT 1"""
                ).fetchone()
                return row is not None
        except Exception:
            return False


    def _get_recent_sensory_signals(self, conn, limit: int = 10) -> list[dict]:
        """
        Read the freshest Sensory Scout events from cognition_log.
        This is advisory context only — never blocks the main trading loop.
        """
        try:
            rows = conn.execute(
                """
                SELECT timestamp, token, message, confidence
                FROM cognition_log
                WHERE stage IN ('SENSORY_SCOUT','X_SCOUT')
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [
                {
                    "timestamp": float(r["timestamp"] or 0.0),
                    "token": str(r["token"] or ""),
                    "message": str(r["message"] or ""),
                    "confidence": float(r["confidence"] or 0.0),
                }
                for r in rows
            ]
        except Exception as e:
            logging.debug("POLARIS sensory context unavailable: %s", e)
            return []


    # -----------------------------
    #  TRADING INTELLIGENCE LOOP
    # -----------------------------
    def analyze_trades(self, conn, counters: List[int]):

        # PATCH B: Hard gate — suppress all strategy proposals while price is stale.
        # A stale-price organism must not be tuned; it must be repaired first.
        if self._is_price_stale():
            # PATCH C: Only queue one SYSTEM_REPAIR, never duplicate it.
            if self._repair_already_queued():
                logging.info("POLARIS MISSION-LOCK: repair already queued — skipping analyze_trades")
                return {}
            self.add_proposal(
                "SYSTEM_REPAIR",
                "Price staleness detected with open position(s). "
                "market_snapshots.observed_price has not been updated within the expected window. "
                "The oracle or MTM pipeline requires investigation before any strategy changes.",
                "Investigate ws_price_oracle and market_intelligence MTM pipeline. "
                "Confirm price feed is live before resuming normal governance.",
                0.92,
                {"staleness_detected": True},
                counters,
            )
            logging.warning("POLARIS MISSION-LOCK: SYSTEM_REPAIR queued — analyze_trades suppressed")
            return {}

        # GHOST TRADE FILTER: exclude trades where oracle was blind (no coverage)
        # or where guardian force-closed without execution engine decision.
        # These trades have zero post-entry ticks — learning from them would
        # train Polaris on infrastructure failures, not market behaviour.
        # Falls back to unfiltered if coverage columns don't exist yet (older DB).
        try:
            _cols = {r[0] for r in conn.execute(
                "PRAGMA table_info(polaris_trade_reviews)"
            ).fetchall()}
            _has_coverage = "coverage_score" in _cols and "exit_validity" in _cols
        except Exception:
            _has_coverage = False

        if _has_coverage:
            rows = conn.execute("""
                SELECT win_loss, exit_category, realized_pnl_usd,
                       coverage_score, exit_validity
                FROM polaris_trade_reviews
                WHERE (
                    coverage_score > 0.3
                    AND exit_validity NOT IN ('NO_COVERAGE', 'GUARDIAN')
                )
                OR exit_validity IS NULL
                OR exit_validity = 'UNKNOWN'
                ORDER BY reviewed_at DESC
                LIMIT 50
            """).fetchall()
            # If coverage filter is too aggressive (new columns, all NULL),
            # fall back to unfiltered so Polaris keeps running
            if len(rows) < 10:
                rows = conn.execute("""
                    SELECT win_loss, exit_category, realized_pnl_usd
                    FROM polaris_trade_reviews
                    ORDER BY reviewed_at DESC
                    LIMIT 30
                """).fetchall()
        else:
            rows = conn.execute("""
                SELECT win_loss, exit_category, realized_pnl_usd
                FROM polaris_trade_reviews
                ORDER BY reviewed_at DESC
                LIMIT 30
            """).fetchall()

        if not rows or len(rows) < 5:
            return {}

        total     = len(rows)
        wins      = sum(1 for r in rows if r["win_loss"] == "WIN")
        sl_losses = sum(1 for r in rows if r["exit_category"] == "SL")
        avg_pnl   = sum(float(r["realized_pnl_usd"] or 0) for r in rows) / total
        win_rate  = (wins / total) * 100

        stats = {
            "trades":   total,
            "win_rate": round(win_rate, 2),
            "avg_pnl":  round(avg_pnl, 4),
            "sl_rate":  round(sl_losses / total, 2),
        }

        # ── READ PROPOSAL OUTCOME HISTORY ────────────────────────────
        # POLARIS reads whether her LAST proposal helped or hurt.
        # This closes the feedback loop — she does not propose blind.
        feedback = self.read_proposal_outcomes(conn)
        last_outcome          = feedback.get("last_outcome", "NO_HISTORY")
        last_proposal_helped  = feedback.get("last_proposal_helped")
        rolled_back_count     = feedback.get("rolled_back_count", 0)

        # ── PROPOSALS (CAUSE → EFFECT + FEEDBACK) ────────────────────

        # If last proposal was rolled back — POLARIS is more cautious this cycle
        # Lower confidence on new proposals, let IVARIS scrutinise harder
        caution_modifier = -0.05 if rolled_back_count > 0 else 0.0

        # Only propose again if last proposal has been evaluated
        # Prevents POLARIS spamming proposals without waiting for outcomes
        if last_outcome == "NO_HISTORY":
            # First cycle — propose normally
            pass
        elif last_outcome == "IMPROVED":
            # Last proposal worked — POLARIS can propose follow-on changes
            # but only if there is still a clear signal to act on
            pass
        elif last_outcome == "ROLLED_BACK":
            # Last proposal was auto-reverted — POLARIS pauses aggressive changes
            caution_modifier = -0.10
            logging.info("POLARIS: last proposal was rolled back — applying caution modifier")
        elif last_outcome == "DEGRADED":
            # Performance got worse after proposal — POLARIS is more conservative
            caution_modifier = -0.08
            logging.info("POLARIS: last proposal degraded performance — applying caution modifier")

        # Build feedback context for proposal text
        feedback_context = ""
        if last_outcome != "NO_HISTORY" and feedback.get("recent_outcomes"):
            last = feedback["recent_outcomes"][0]
            delta = last.get("delta") or {}
            feedback_context = (
                f" Note: my last proposal ({last.get('action','')}) "
                f"resulted in outcome={last_outcome}, "
                f"win_rate_delta={delta.get('win_rate_delta','?')}, "
                f"pnl_delta={delta.get('pnl_delta','?')}."
            )

        if stats["sl_rate"] > 0.6:
            self.add_proposal(
                "EARLY_STOP_CLUSTER",
                f"High SL rate of {stats['sl_rate']:.0%} detected across last {total} trades.{feedback_context}",
                "Increase entry threshold or delay entry",
                round(0.88 + caution_modifier, 3),
                {**stats, "feedback": feedback},
                counters,
            )

        if stats["win_rate"] < 30:
            self.add_proposal(
                "LOW_WIN_RATE",
                f"Win rate {stats['win_rate']:.1f}% below threshold across {total} trades.{feedback_context}",
                "Raise minimum confidence floor",
                round(0.86 + caution_modifier, 3),
                {**stats, "feedback": feedback},
                counters,
            )

        if stats["win_rate"] > 40 and stats["avg_pnl"] < 0:
            self.add_proposal(
                "NEGATIVE_EDGE",
                f"Win rate {stats['win_rate']:.1f}% is positive but avg PnL ${stats['avg_pnl']:.4f} is negative.{feedback_context}",
                "Expand take profit or reduce SL sensitivity",
                round(0.84 + caution_modifier, 3),
                {**stats, "feedback": feedback},
                counters,
            )

        return stats


    # -----------------------------
    #  PROPOSAL OUTCOME FEEDBACK — THE CLOSED LOOP
    # -----------------------------
    def read_proposal_outcomes(self, conn) -> dict:
        """
        Read POLARIS's own proposal history and whether each proposal
        improved or degraded performance after it was applied.

        This closes the loop: POLARIS sees not just current performance
        but whether her LAST intervention helped or hurt.

        Without this she is proposing blind — she sees symptoms but not
        whether her previous medicine worked.
        """
        try:
            # Get last 5 applied proposals
            applied = conn.execute("""
                SELECT
                    pp.id,
                    pp.proposal_type,
                    pp.proposal_text,
                    pp.suggested_action,
                    pp.confidence,
                    pp.created_at,
                    ps.status as snapshot_status,
                    ps.applied_at,
                    ps.evaluation_due
                FROM polaris_proposals pp
                LEFT JOIN parameter_snapshots ps
                    ON ps.proposal_id = pp.proposal_hash
                WHERE pp.status IN ('applied', 'validated', 'rolled_back', 'sent')
                ORDER BY pp.created_at DESC
                LIMIT 5
            """).fetchall()

            outcomes = []
            for row in applied:
                applied_at = row["applied_at"] if row["applied_at"] else row["created_at"]

                # Get performance BEFORE the proposal (30 trades before applied_at)
                before_trades = conn.execute("""
                    SELECT win_loss, realized_pnl_usd
                    FROM polaris_trade_reviews
                    WHERE reviewed_at < ?
                    ORDER BY reviewed_at DESC
                    LIMIT 30
                """, (applied_at,)).fetchall()

                # Get performance AFTER the proposal
                after_trades = conn.execute("""
                    SELECT win_loss, realized_pnl_usd
                    FROM polaris_trade_reviews
                    WHERE reviewed_at > ?
                    ORDER BY reviewed_at ASC
                    LIMIT 30
                """, (applied_at,)).fetchall()

                def calc_stats(trades):
                    if not trades:
                        return None
                    total = len(trades)
                    wins  = sum(1 for t in trades if t["win_loss"] == "WIN")
                    pnl   = sum(float(t["realized_pnl_usd"] or 0) for t in trades)
                    return {
                        "trades":   total,
                        "win_rate": round((wins / total) * 100, 1),
                        "avg_pnl":  round(pnl / total, 4),
                    }

                before_stats = calc_stats(before_trades)
                after_stats  = calc_stats(after_trades)

                # Determine outcome
                outcome = "UNKNOWN"
                delta   = None
                if before_stats and after_stats:
                    wr_delta  = after_stats["win_rate"] - before_stats["win_rate"]
                    pnl_delta = after_stats["avg_pnl"]  - before_stats["avg_pnl"]
                    delta = {
                        "win_rate_delta": round(wr_delta, 1),
                        "pnl_delta":      round(pnl_delta, 4),
                    }
                    if row["snapshot_status"] == "rolled_back":
                        outcome = "ROLLED_BACK"
                    elif wr_delta > 2 and pnl_delta > 0:
                        outcome = "IMPROVED"
                    elif wr_delta < -2 or pnl_delta < -0.005:
                        outcome = "DEGRADED"
                    else:
                        outcome = "NEUTRAL"

                outcomes.append({
                    "proposal_type":  row["proposal_type"],
                    "action":         row["suggested_action"],
                    "outcome":        outcome,
                    "delta":          delta,
                    "before":         before_stats,
                    "after":          after_stats,
                    "snapshot_status": row["snapshot_status"],
                })

            return {
                "recent_outcomes":      outcomes,
                "last_proposal_helped": outcomes[0]["outcome"] == "IMPROVED" if outcomes else None,
                "last_outcome":         outcomes[0]["outcome"] if outcomes else "NO_HISTORY",
                "rolled_back_count":    sum(1 for o in outcomes if o["outcome"] == "ROLLED_BACK"),
                "improved_count":       sum(1 for o in outcomes if o["outcome"] == "IMPROVED"),
            }

        except Exception as e:
            logging.warning("read_proposal_outcomes failed: %s", e)
            return {
                "recent_outcomes": [],
                "last_outcome":    "NO_HISTORY",
                "last_proposal_helped": None,
            }

    # -----------------------------
    # ⚙️ SYSTEM MONITOR LOOP (existing)
    # -----------------------------
    def analyze_system(self, conn, counters):

        total = conn.execute("SELECT COUNT(*) FROM raw_dna").fetchone()[0]

        if total > 50:
            errors = conn.execute(
                "SELECT COUNT(*) FROM raw_dna WHERE processed_state < 0"
            ).fetchone()[0]

            error_rate = (errors / total) * 100

            if error_rate > 20:
                self.add_proposal(
                    "RAW_DNA_ERROR",
                    f"{error_rate:.1f}% error rate",
                    "Audit ingest pipeline",
                    0.85,
                    {"error_rate": error_rate},
                    counters
                )

    # -----------------------------
    # 🌱 ECOSYSTEM EXPANSION LOOP
    # -----------------------------

    # ─────────────────────────────────────────────────────────────────────────
    # EXTERNAL INTELLIGENCE LAYER
    # ─────────────────────────────────────────────────────────────────────────

    def brave_search(self, query: str, count: int = 3) -> list[str]:
        """Search the web via Brave for grounding proposals in reality."""
        if not BRAVE_KEY:
            return []
        try:
            import requests as _req
            resp = _req.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_KEY},
                params={"q": query, "count": count},
                timeout=8,
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("web", {}).get("results", [])
            return [f"{r.get('title','')}: {r.get('description','')}" for r in results[:count]]
        except Exception:
            return []

    def gpt_think(self, system_prompt: str, user_message: str, max_tokens: int = 600,
                  *, task_type: str = "routine_summary", risk_level: str = "low",
                  live_trade: bool = False, code_touch: bool = False,
                  code_touch_file: Optional[str] = None,
                  stalemate: bool = False, confidence_gap: float = 0.0) -> Optional[str]:
        """Ask GPT to generate narrative proposal text grounded in evidence.
        Routes via services.model_router — nano by default, mini on escalation."""
        if not OPENAI_API_KEY:
            return None
        # Route via centralised LLM client (ensures budget/signoff/critical tier).
        try:
            from services.llm_client import polaris_complete
            result = polaris_complete(
                system_prompt, user_message,
                task_type=task_type, risk_level=risk_level,
                live_trade=live_trade, code_touch=code_touch,
                code_touch_file=code_touch_file, stalemate=stalemate,
                confidence_gap=confidence_gap,
                max_tokens=max_tokens, temperature=1,
            )
            if result and result.get("text"):
                logging.info("POLARIS routed via model_router: tier=%s model=%s reason=%s",
                             result.get("tier"), result.get("model"), result.get("reason"))
                return result["text"]
        except ImportError:
            pass  # fall through to legacy path
        # Legacy fallback if router unavailable
        try:
            import requests as _req
            resp = _req.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": OPENAI_MODEL,
                    "max_completion_tokens": max_tokens,
                    "temperature": 1,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_message},
                    ],
                },
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logging.warning("POLARIS GPT call failed: %s", e)
            return None

    def read_standing_tasks(self, conn) -> list[dict]:
        """Read the current tasklist without assuming one specific status vocabulary."""
        try:
            rows = conn.execute("""
                SELECT id,
                       COALESCE(task_type, task_name, 'TASK') AS task_type,
                       COALESCE(description, task_name, '') AS description,
                       COALESCE(priority, 0) AS priority,
                       COALESCE(status, 'UNKNOWN') AS status,
                       task_name
                FROM standing_tasks
                WHERE UPPER(COALESCE(status, '')) IN ('PENDING', 'IN_PROGRESS', 'ACTIVE')
                ORDER BY priority DESC, id ASC
                LIMIT 5
            """).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _now_text(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _task_next_run_after(self, hours: int = 4) -> str:
        return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    def _record_task_run(
        self,
        conn,
        task_id: Optional[int],
        task_name: str,
        run_type: str,
        status: str,
        started_at: str,
        summary: str,
        evidence: Optional[dict] = None,
        operator_visible: bool = False,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO task_runs (
                task_id, task_name, run_type, status, started_at, finished_at,
                summary, evidence_json, operator_visible, telegram_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                task_id,
                task_name,
                run_type,
                status,
                started_at,
                self._now_text(),
                (summary or "")[:800],
                json.dumps(evidence or {}, ensure_ascii=False),
                1 if operator_visible else 0,
            ),
        )
        return int(cur.lastrowid)

    def _claim_due_task(self, conn) -> Optional[dict]:
        row = conn.execute(
            """
            SELECT *
            FROM standing_tasks
            WHERE UPPER(COALESCE(status, 'ACTIVE')) = 'ACTIVE'
              AND COALESCE(claimed_by, '') = ''
              AND (
                    next_run_after IS NULL OR next_run_after = ''
                    OR (typeof(next_run_after) IN ('integer','real')
                        AND CAST(next_run_after AS REAL) <= CAST(strftime('%s','now') AS REAL))
                    OR (typeof(next_run_after) = 'text'
                        AND next_run_after <= datetime('now'))
                  )
            ORDER BY priority DESC, COALESCE(last_run, '1970-01-01 00:00:00') ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None

        task = dict(row)
        res = conn.execute(
            """
            UPDATE standing_tasks
            SET claimed_by = ?, claimed_at = ?, status = 'IN_PROGRESS'
            WHERE id = ? AND COALESCE(claimed_by, '') = ''
            """,
            (SERVICE_NAME, self._now_text(), task["id"]),
        )
        if res.rowcount != 1:
            return None
        task["status"] = "IN_PROGRESS"
        return task

    def _complete_task(
        self,
        conn,
        task: dict,
        status: str,
        summary: str,
        evidence: Optional[dict] = None,
        operator_visible: bool = False,
        run_type: str = "task",
        cooldown_hours: int = 4,
    ) -> int:
        started_at = str(task.get("claimed_at") or self._now_text())
        task_id = int(task.get("id") or 0) if task.get("id") else None
        task_name = str(task.get("task_name") or task.get("task_type") or "TASK")
        run_id = self._record_task_run(
            conn,
            task_id,
            task_name,
            run_type,
            status,
            started_at,
            summary,
            evidence=evidence,
            operator_visible=operator_visible,
        )
        if task_id:
            conn.execute(
                """
                UPDATE standing_tasks
                SET status = 'ACTIVE',
                    claimed_by = NULL,
                    claimed_at = NULL,
                    last_run = ?,
                    next_run_after = ?,
                    run_count = COALESCE(run_count, 0) + 1,
                    last_outcome = ?,
                    last_evidence_json = ?,
                    reportable_update = ?,
                    last_reported_at = CASE WHEN ? THEN ? ELSE last_reported_at END
                WHERE id = ?
                """,
                (
                    self._now_text(),
                    self._task_next_run_after(cooldown_hours),
                    status,
                    json.dumps(evidence or {}, ensure_ascii=False),
                    1 if operator_visible else 0,
                    1 if operator_visible else 0,
                    self._now_text(),
                    task_id,
                ),
            )
        # SIGNOFF_AUDIT_MEMORY_20260719: every completed/deferred standing-task
        # run gets one durable, sanitised report under audits/. SQLite remains
        # the task-state truth and stores the report path for later Polaris/Council use.
        try:
            try:
                from services.audit_artifact_store import persist_report
            except ImportError:
                from audit_artifact_store import persist_report
            artifact = persist_report(
                conn, source="POLARIS", report_type=run_type,
                title=f"{task_name} — {status}", task_id=task_id,
                task_name=task_name, status=status, summary=summary,
                evidence=evidence or {}, tags=["polaris", "standing-task"],
                metadata={"task_run_id": run_id, "operator_visible": bool(operator_visible)},
            )
            # Older task_runs schemas may not have artifact_path; migrate safely.
            cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(task_runs)").fetchall()}
            if "artifact_path" not in cols:
                conn.execute("ALTER TABLE task_runs ADD COLUMN artifact_path TEXT")
            conn.execute("UPDATE task_runs SET artifact_path=? WHERE id=?",
                         (artifact.get("canonical_path"), run_id))
        except Exception as artifact_error:
            logging.warning("POLARIS audit artifact persistence failed: %s", artifact_error)

        log_cognition(
            "POLARIS",
            f"TASK {task_name} | {summary}",
            token="SYSTEM",
            confidence=0.85 if status.upper() in {"OK", "COMPLETED"} else 0.55,
            meta={"task_id": task_id, "status": status, "evidence": evidence or {}},
        )
        return run_id

    def _check_ivaris_status(self) -> dict:
        """
        Lightweight diagnostic ping — matches the live provider-aware routing
        in sovereign_governor.py. Reads IVARIS_PROVIDER and IVARIS_MODEL from
        system_config so the morning brief reflects the actual live provider.
        Does NOT affect debate routing. Diagnostic only.
        """
        provider = str(get_config_value("IVARIS_PROVIDER", "anthropic")).strip().lower()
        model    = str(get_config_value("IVARIS_MODEL",    "claude-haiku-4-5-20251001")).strip()

        if provider == "anthropic":
            _key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not _key:
                return {
                    "configured": False, "status": "missing_key",
                    "detail": "ANTHROPIC_API_KEY not configured",
                    "provider": "anthropic", "model": model,
                }
            try:
                import requests as _req
                resp = _req.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key":         _key,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":      model,
                        "max_completion_tokens": 10,
                        "messages":   [{"role": "user", "content": "Reply with OK."}],
                    },
                    timeout=12,
                )
                if resp.status_code == 200:
                    return {"configured": True, "status": "ok",
                            "detail": "Anthropic API reachable",
                            "provider": "anthropic", "model": model}
                return {"configured": True, "status": "error",
                        "detail": f"Anthropic HTTP {resp.status_code}",
                        "provider": "anthropic", "model": model}
            except Exception as e:
                return {"configured": True, "status": "error",
                        "detail": str(e)[:160],
                        "provider": "anthropic", "model": model}

        else:
            # NUGGET routes through NIM (Kimi K2) — no gemini_client dependency
            try:
                import json as _jn, urllib.request as _ur
                _nk = os.getenv("NVIDIA_NIM_API_KEY","").strip()
                if not _nk:
                    return {"configured": False, "status": "missing_key",
                            "detail": "NVIDIA_NIM_API_KEY not set", "provider": "nim", "model": "meta/llama-3.3-70b-instruct"}
                _pl = _jn.dumps({"model": _nim_assignment("NUGGET", "nvidia/nemotron-3-super-120b-a12b"),
                    "messages":[{"role":"user","content":"Reply OK."}],"max_tokens":5}).encode()
                _rq = _ur.Request("https://integrate.api.nvidia.com/v1/chat/completions",
                    data=_pl, method="POST",
                    headers={"Authorization":f"Bearer {_nk}","Content-Type":"application/json"})
                with _ur.urlopen(_rq, timeout=10) as _r:
                    _txt = _jn.loads(_r.read())["choices"][0]["message"]["content"].strip()
                return {"configured": True, "status": "ok" if _txt else "error",
                        "detail": f"NUGGET→NIM/Llama3.3: {_txt[:40]}",
                        "provider": "nim", "model": _nim_assignment("NUGGET", "nvidia/nemotron-3-super-120b-a12b")}
            except Exception as e:
                return {"configured": False, "status": "error",
                        "detail": str(e)[:160], "provider": "nim", "model": _nim_assignment("NUGGET", "nvidia/nemotron-3-super-120b-a12b")}

    def _run_deterministic_task(self, conn, task: dict) -> dict:
        task_name = str(task.get("task_name") or "TASK")

        if task_name == "SIGNAL_EXHAUSTION_HANDLING":
            cutoff = time.time() - 1800
            latched = conn.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE candidate_state='latched' AND COALESCE(timestamp, 0) >= ?",
                (cutoff,),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT COALESCE(quality_reason, resolution_method, candidate_state, 'unknown') AS reason
                FROM market_snapshots
                WHERE COALESCE(timestamp, 0) >= ?
                ORDER BY id DESC
                LIMIT 80
                """,
                (cutoff,),
            ).fetchall()
            reasons = Counter(str(r["reason"] or "unknown")[:60] for r in rows)
            top = reasons.most_common(3)
            evidence = {"latched_last_30m": int(latched), "top_reasons": top}
            if latched == 0:
                return {
                    "status": "BLOCKED",
                    "summary": f"No latched signals in the last 30m. Dominant blockers: {top[:2] if top else 'none logged' }.",
                    "evidence": evidence,
                    "operator_visible": True,
                }
            return {
                "status": "OK",
                "summary": f"Signal flow alive — {latched} latched signal(s) in the last 30m.",
                "evidence": evidence,
                "operator_visible": False,
            }

        if task_name == "ANOMALY_QUEUE_HEALTH":
            pending = old = 0
            try:
                pending = conn.execute("SELECT COUNT(*) FROM anomaly_queue WHERE status='pending'").fetchone()[0]
                old = conn.execute(
                    "SELECT COUNT(*) FROM anomaly_queue WHERE status='pending' AND COALESCE(detected_at, 0) < ?",
                    (time.time() - 86400,),
                ).fetchone()[0]
            except Exception:
                pass
            evidence = {"pending_anomalies": int(pending), "older_than_24h": int(old)}
            return {
                "status": "BLOCKED" if old > 0 else "OK",
                "summary": f"Anomaly queue pending={pending}, older_than_24h={old}.",
                "evidence": evidence,
                "operator_visible": old > 0,
            }

        if task_name == "AUDIT_STOP_LOSS_EFFECTIVENESS":
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN exit_category='SL' THEN 1 ELSE 0 END) AS sl_count,
                       AVG(realized_pnl_usd) AS avg_pnl,
                       AVG(CASE WHEN exit_category='SL' THEN entry_mint_confidence END) AS sl_conf
                FROM (
                    SELECT exit_category, realized_pnl_usd, entry_mint_confidence
                    FROM polaris_trade_reviews
                    ORDER BY id DESC
                    LIMIT 50
                )
                """
            ).fetchone()
            total = int(row["total"] or 0)
            sl_count = int(row["sl_count"] or 0)
            sl_rate = (sl_count / max(total, 1)) if total else 0.0
            evidence = {
                "sample": total,
                "sl_count": sl_count,
                "sl_rate": round(sl_rate, 3),
                "avg_pnl": float(row["avg_pnl"] or 0.0),
                "avg_sl_entry_confidence": float(row["sl_conf"] or 0.0),
            }
            return {
                "status": "OK",
                "summary": f"SL audit complete — sample={total}, SL rate={sl_rate:.0%}, avg pnl={float(row['avg_pnl'] or 0.0):+.4f}.",
                "evidence": evidence,
                "operator_visible": sl_rate >= 0.55 and total >= 10,
            }

        if task_name == "OPTIMIZE_ENTRY_CONFIDENCE":
            row = conn.execute(
                """
                SELECT
                    AVG(CASE WHEN win_loss='WIN' THEN entry_mint_confidence END) AS win_conf,
                    AVG(CASE WHEN win_loss='LOSS' THEN entry_mint_confidence END) AS loss_conf,
                    COUNT(*) AS total
                FROM (
                    SELECT win_loss, entry_mint_confidence
                    FROM polaris_trade_reviews
                    ORDER BY id DESC
                    LIMIT 60
                )
                """
            ).fetchone()
            evidence = {
                "sample": int(row["total"] or 0),
                "avg_win_conf": float(row["win_conf"] or 0.0),
                "avg_loss_conf": float(row["loss_conf"] or 0.0),
            }
            delta = evidence["avg_win_conf"] - evidence["avg_loss_conf"]
            return {
                "status": "OK",
                "summary": f"Confidence audit complete — win/loss entry confidence delta={delta:+.3f} over {evidence['sample']} reviews.",
                "evidence": evidence,
                "operator_visible": False,
            }

        return {
            "status": "DEFERRED",
            "summary": "Task remains on agenda, but no deterministic executor is wired for it yet.",
            "evidence": {"reason": "manual_or_ai_research_lane_required"},
            "operator_visible": False,
        }

    def run_one_standing_task(self, conn) -> Optional[dict]:
        task = self._claim_due_task(conn)
        if not task:
            return None
        result = self._run_deterministic_task(conn, task)
        self._complete_task(
            conn,
            task,
            status=str(result.get("status") or "OK"),
            summary=str(result.get("summary") or "Task processed."),
            evidence=result.get("evidence") or {},
            operator_visible=bool(result.get("operator_visible")),
            cooldown_hours=4,
        )
        return {"task_name": task.get("task_name"), **result}

    def ensure_morning_brief(self, conn, task_result: Optional[dict]) -> bool:
        # Hard rate limit: one brief per calendar day UTC, enforced by TWO checks
        # 1. DB check (task_runs table)
        # 2. system_config key with last-sent timestamp
        # Both must agree before sending — prevents UTC/local mismatch spam
        today_utc = time.strftime("%Y-%m-%d", time.gmtime())

        # Check 1: task_runs table.
        # SIGN-OFF 2026-07-15: current task_runs schemas use started_at /
        # finished_at; older code queried created_at unconditionally and
        # crashed Polaris every cycle. Resolve the available timestamp column.
        already = None
        try:
            task_run_cols = {
                str(r["name"]) for r in conn.execute(
                    "PRAGMA table_info(task_runs)"
                ).fetchall()
            }
            brief_time_col = next(
                (
                    c for c in (
                        "started_at",
                        "finished_at",
                        "created_at",
                        "updated_at",
                        "ts",
                    )
                    if c in task_run_cols
                ),
                None,
            )
            if brief_time_col and "run_type" in task_run_cols:
                already = conn.execute(
                    f"SELECT 1 FROM task_runs "
                    f"WHERE run_type='morning_brief' "
                    f"AND substr(CAST({brief_time_col} AS TEXT),1,10)=? LIMIT 1",
                    (today_utc,),
                ).fetchone()
        except sqlite3.Error as exc:
            log.warning(
                "Morning-brief duplicate check degraded safely: %s", exc
            )
        if already:
            return False

        # Check 2: system_config last-sent key (survives task_runs wipes)
        last_sent_row = conn.execute(
            "SELECT value FROM system_config WHERE key='POLARIS_BRIEF_LAST_SENT_UTC'"
        ).fetchone()
        if last_sent_row and str(last_sent_row["value"]).strip() == today_utc:
            return False

        # Check 3: config kill switch
        enabled = str(get_config_value("POLARIS_MORNING_BRIEF_ENABLED", "1")).strip()
        if enabled == "0":
            return False

        heartbeat_rows = conn.execute(
            "SELECT service_name, last_pulse, status FROM system_heartbeat"
        ).fetchall()
        now_ts = time.time()
        stale = []
        for row in heartbeat_rows:
            age = now_ts - float(row["last_pulse"] or 0)
            if age > 120:
                stale.append({"service_name": row["service_name"], "age_s": int(age), "status": row["status"]})

        open_props = conn.execute(
            "SELECT COUNT(*) FROM polaris_proposals WHERE status IN ('open','debating','pushed','pending_replay')"
        ).fetchone()[0]

        anomaly_pending = 0
        try:
            anomaly_pending = conn.execute("SELECT COUNT(*) FROM anomaly_queue WHERE status='pending'").fetchone()[0]
        except Exception:
            anomaly_pending = 0

        # IVARIS status — no Gemini reference
        ivaris = self._check_ivaris_status()
        ivaris_line = f"{ivaris.get('status')} — {ivaris.get('detail','')}"
        if "gemini" in ivaris_line.lower():
            ivaris_line = ivaris_line.replace("gemini_client", "NIM/Mistral").replace("gemini", "NIM")

        task_line = "No standing task advanced yet today."
        if task_result:
            task_line = f"{task_result.get('task_name')}: {task_result.get('summary')}"

        summary = (
            "POLARIS MORNING BRIEF\n\n"
            f"Heartbeat sweep: {len(heartbeat_rows)} service(s) checked, {len(stale)} stale.\n"
            f"IVARIS: {ivaris_line}\n"
            f"Open proposals: {open_props}\n"
            f"Pending anomalies: {anomaly_pending}\n"
            f"Task update: {task_line}"
        )
        evidence = {
            "services_checked": len(heartbeat_rows),
            "stale_services": stale[:10],
            "ivaris": ivaris,
            "open_proposals": int(open_props),
            "pending_anomalies": int(anomaly_pending),
            "task_result": task_result or {},
        }
        self._record_task_run(
            conn,
            None,
            "MORNING_BRIEF",
            "morning_brief",
            "READY" if not stale else "ATTENTION",
            self._now_text(),
            summary,
            evidence=evidence,
            operator_visible=True,
        )

        # Write guard key BEFORE sending — prevents retry on Telegram failure
        conn.execute(
            "INSERT OR REPLACE INTO system_config(key,value) VALUES('POLARIS_BRIEF_LAST_SENT_UTC',?)",
            (today_utc,)
        )
        conn.commit()

        _notify("morning_brief", {"summary": summary, "task_name": "MORNING_BRIEF"})
        log_cognition("POLARIS", "Morning brief queued for operator delivery.", token="SYSTEM", confidence=0.9, meta=evidence)
        return True

    def analyze_expansion(self, conn, counters):
        """
        UPGRADED: Polaris now thinks with GPT, searches with Brave, reads the tasklist.
        Instead of hardcoded proposals, she synthesizes evidence into narrative proposals
        that explain the ecosystem impact — not just parameter changes.
        """
        # PATCH B: Hard gate — suppress expansion proposals while price is stale.
        # Expansion logic burns API credits (GPT + Brave). Do not run while in repair mode.
        if self._is_price_stale():
            if not self._repair_already_queued():
                logging.warning("POLARIS MISSION-LOCK: price stale — analyze_expansion suppressed (repair not yet queued)")
            else:
                logging.info("POLARIS MISSION-LOCK: price stale — analyze_expansion suppressed (repair already queued)")
            return

        trades = conn.execute("SELECT COUNT(*) FROM polaris_trade_reviews").fetchone()[0]
        if trades < 20:
            return

        # ── READ STANDING TASKS ───────────────────────────────────────────────
        tasks = self.read_standing_tasks(conn)
        task_context = ""
        if tasks:
            task_context = "\n".join([
                f"- [{t['task_type']}] {t['description']} (priority={t['priority']})"
                for t in tasks
            ])
            logging.info("POLARIS: %d standing task(s) in context", len(tasks))

        # ── READ RECENT SENSORY SCOUT CONTEXT ─────────────────────────────────
        sensory_signals = self._get_recent_sensory_signals(conn, limit=8)
        sensory_context = ""
        if sensory_signals:
            sensory_context = "\n".join([
                f"- [{s.get('token','?')}] {s.get('message','')} (conf={float(s.get('confidence',0.0)):.2f})"
                for s in sensory_signals
            ])
            logging.info("POLARIS: %d sensory scout event(s) in context", len(sensory_signals))

        # ── READ RECENT ORGANISM STATE ────────────────────────────────────────
        wallet = conn.execute("SELECT wallet_balance, initial_capital FROM system_state WHERE id=1").fetchone()
        bal   = float(wallet["wallet_balance"] if wallet else 0)
        init  = float(wallet["initial_capital"] if wallet else 1000)
        roi   = ((bal - init) / max(init, 1)) * 100

        win_rate_row = conn.execute("""
            SELECT AVG(CASE WHEN win_loss='WIN' THEN 1.0 ELSE 0.0 END) as wr,
                   COUNT(*) as n, AVG(realized_pnl_usd) as avg_pnl
            FROM polaris_trade_reviews ORDER BY id DESC LIMIT 50
        """).fetchone()
        win_rate = float(win_rate_row["wr"] or 0) * 100
        trade_count = int(win_rate_row["n"] or 0)
        avg_pnl = float(win_rate_row["avg_pnl"] or 0)

        # Only proceed if we have meaningful data
        if trade_count < 20:
            return

        # ── BRAVE SEARCH: GROUND IN EXTERNAL REALITY ─────────────────────────
        search_results = []
        if BRAVE_KEY:
            query = f"pump.fun token trading strategy {time.strftime('%Y')}"
            search_results = self.brave_search(query, count=3)
            logging.info("POLARIS: Brave search returned %d results", len(search_results))

        # ── GPT: SYNTHESIZE A NARRATIVE PROPOSAL ─────────────────────────────
        if not OPENAI_API_KEY:
            # Fallback to simple proposal if no GPT
            self.add_proposal(
                "ECOSYSTEM_EXPANSION",
                f"System stable at {win_rate:.1f}% win rate over {trade_count} trades — expansion opportunity identified. Sensory Scout context reviewed.",
                "Review tasklist and prioritize next ecosystem improvement",
                0.80,
                {"trades": trade_count, "win_rate": win_rate, "roi": roi},
                counters
            )
            return

        # Load Zaak soul from DB — POLARIS governs by this doctrine
        # Falls back to clean functional prompt if soul not set
        _zaak_soul = str(get_config_value("POLARIS_SOUL", "")).strip()

        system_prompt = (_zaak_soul + "\n\n") if _zaak_soul else ""
        system_prompt += """You are POLARIS — the Autonomous Architect of the Sentinuity sovereign trading organism.

You analyze the organism's performance data, standing tasks, and external market intelligence.
Your role is to generate ONE precise, evidence-based proposal that will improve the organism.

Proposals can be:
1. PARAMETER_CHANGE — adjust a trading parameter with clear evidence
2. CODE_UPGRADE — suggest a specific code improvement with ecosystem narrative  
3. ECOSYSTEM_EXPANSION — propose a new capability or integration
4. STRATEGY_SHIFT — propose a change in trading approach

Output JSON only:
{
  "proposal_type": "PARAMETER_CHANGE|CODE_UPGRADE|ECOSYSTEM_EXPANSION|STRATEGY_SHIFT",
  "proposal_text": "2-3 sentence evidence-based reasoning",
  "suggested_action": "Precise, actionable instruction",
  "confidence": 0.0-1.0,
  "ecosystem_narrative": "How this ties into the organism's broader evolution",
  "falsification_condition": "What would prove this wrong after 50 trades"
}"""

        user_message = f"""ORGANISM STATE:
- Wallet: ${bal:.2f} (ROI: {roi:+.1f}%)
- Win rate: {win_rate:.1f}% over {trade_count} trades
- Avg PnL per trade: ${avg_pnl:+.4f}

STANDING TASKS:
{task_context if task_context else "No pending tasks"}

EXTERNAL INTELLIGENCE (Brave Search):
{chr(10).join(search_results) if search_results else "No search results available"}

Based on this evidence, generate ONE proposal to improve the organism. 
Focus on the highest-leverage change. Be specific and evidence-based."""

        response = self.gpt_think(system_prompt, user_message, max_tokens=500)
        if not response:
            return

        # Parse GPT response
        try:
            # Strip markdown if present
            clean = response.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            proposal_data = json.loads(clean.strip())

            ptype     = proposal_data.get("proposal_type", "ECOSYSTEM_EXPANSION")
            ptext     = proposal_data.get("proposal_text", "")
            action    = proposal_data.get("suggested_action", "")
            conf      = float(proposal_data.get("confidence", 0.80))
            narrative = proposal_data.get("ecosystem_narrative", "")
            falsify   = proposal_data.get("falsification_condition", "")

            # Enrich proposal text with narrative
            full_text = ptext
            if narrative:
                full_text = f"{ptext} ECOSYSTEM: {narrative}"

            metrics = {
                "trades": trade_count,
                "win_rate": win_rate,
                "avg_pnl": avg_pnl,
                "roi": roi,
                "brave_grounded": len(search_results) > 0,
                "tasks_in_context": len(tasks),
                "falsification": falsify,
            }

            self.add_proposal(ptype, full_text, action, conf, metrics, counters)
            logging.info("POLARIS GPT proposal: %s (conf=%.2f)", ptype, conf)

        except (json.JSONDecodeError, KeyError) as e:
            logging.warning("POLARIS: Could not parse GPT proposal: %s", e)
            # Fallback simple proposal
            self.add_proposal(
                "ECOSYSTEM_EXPANSION",
                response[:300] if response else "GPT analysis complete — manual review recommended.",
                "Review POLARIS reasoning and propose next action",
                0.75,
                {"trades": trade_count, "win_rate": win_rate},
                counters
            )

    # -----------------------------
    # PROPOSAL HELPER
    # -----------------------------
    def add_proposal(self, ptype, text, action, confidence, metrics, counters):

        min_conf = float(get_config_value("POLARIS_PROPOSAL_MIN_CONFIDENCE", 0.80))

        if confidence < min_conf:
            return False

        # PATCH D: Suppress non-repair proposals when a staleness repair is
        # already queued. Prevents debate loops burning credits on strategy
        # changes while the organism cannot price positions.
        if ptype != "SYSTEM_REPAIR" and self._repair_already_queued():
            logging.info(
                "POLARIS: proposal suppressed [%s] — mission lock active: repair already queued",
                ptype,
            )
            return False

        try:
            ok = insert_polaris_proposal(ptype, text, action, confidence, metrics)

            if ok:
                counters[0] += 1
                logging.info("POLARIS: proposal stored [%s]", ptype)
            else:
                logging.warning("POLARIS: proposal skipped (duplicate or DB busy) [%s]", ptype)

            return ok

        except Exception as e:
            logging.warning("POLARIS: proposal failed: %s", e)
            return False

    # -----------------------------
    # MAIN ANALYSIS
    # -----------------------------
    def analyze(self):

        now = time.time()
        interval = int(get_config_value("POLARIS_ANALYSIS_INTERVAL_SECONDS", 45))

        if now - self.last_analysis < interval:
            return

        counters = [0]

        # ── FORGE FOCUS LOCK — if active, skip all trading analysis ──────────
        _forge_lock = str(get_config_value("POLARIS_FORGE_ONLY_MODE", "0")).strip()
        _forge_lock_active = _forge_lock in ("1", "true", "yes")

        with get_connection() as conn:

            task_result   = self.run_one_standing_task(conn)
            self.ensure_morning_brief(conn, task_result)

            if _forge_lock_active:
                # Focus lock engaged — skip trading proposals entirely
                logging.info("POLARIS FORGE LOCK: trading analysis suppressed — FORGE build mode active")
                trade_stats = {"trades": 0, "win_rate": 0}
                feedback    = self.read_proposal_outcomes(conn)
            else:
                trade_stats = self.analyze_trades(conn, counters)
                feedback    = self.read_proposal_outcomes(conn)
                self.analyze_system(conn, counters)
                self.analyze_expansion(conn, counters)

            summary = {
                "trades":           trade_stats.get("trades", 0),
                "win_rate":         trade_stats.get("win_rate", 0),
                "new_proposals":    counters[0],
                "last_outcome":     feedback.get("last_outcome", "NO_HISTORY"),
                "improved_count":   feedback.get("improved_count", 0),
                "rolled_back_count":feedback.get("rolled_back_count", 0),
                "task_run":         task_result.get("task_name") if task_result else None,
                "task_status":      task_result.get("status") if task_result else None,
            }

            # ── DOCTRINE MEMORY: run after every 10 new reviews ──────────
            if _PATTERN_MEMORY_AVAILABLE:
                try:
                    total_reviews = trade_stats.get("trades", 0)
                    new_reviews = total_reviews - self.reviews_at_last_pattern
                    if new_reviews >= self.PATTERN_INTERVAL or (
                        total_reviews > 0 and self.reviews_at_last_pattern == 0
                    ):
                        patterns_written = run_pattern_analysis()
                        if patterns_written > 0:
                            logging.info(
                                "DOCTRINE: %d pattern(s) written to polaris_learned_patterns",
                                patterns_written
                            )
                        self.reviews_at_last_pattern = total_reviews
                        self.last_pattern_run = now
                except Exception as pe:
                    logging.warning("DOCTRINE: pattern analysis failed: %s", pe)

            update_heartbeat(SERVICE_NAME, "ALIVE", json.dumps(summary))

            logging.info(f"analysis_complete {json.dumps(summary)}")

        self.last_analysis = now

    # -----------------------------
    # RUN LOOP
    # -----------------------------
    def run(self):

        init_db()
        _ensure_polaris_proposals_compat_schema()
        _ensure_standing_tasks_compat_schema()

        logging.info("POLARIS ONLINE -- SOVEREIGN SYSTEM")

        update_heartbeat(SERVICE_NAME, "BOOTING", "Initializing sovereign cortex")

        while True:
            try:
                self.analyze()
            except Exception as e:
                logging.exception("POLARIS ERROR")
                update_heartbeat(SERVICE_NAME, "ERROR", str(e))
                try:
                    queue_improvement("polaris", "ERROR", {"error": str(e)})
                except Exception as qe:
                    logging.warning("POLARIS: queue_improvement failed: %s", qe)

            time.sleep(10)


if __name__ == "__main__":
    Polaris().run()