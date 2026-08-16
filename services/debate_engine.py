"""
services/debate_engine.py

SENTINUITY SOVEREIGN DEBATE ENGINE
=====================================

Orchestrates the 3-node consensus protocol:

  Node 1 — POLARIS (Proposer)
    Already running via polaris.py + polaris_calibrator.py.
    Generates proposals to polaris_proposals table.

  Node 2 — IVARIS (Adversarial Critic)
    Stress-tests every proposal. Raises objections.
    POLARIS must rebut. Up to MAX_ROUNDS of debate.
    Consensus requires IVARIS confidence >= 0.75.

  Node 3 — Brave Search (External Reality Check)
    When POLARIS and IVARIS agree, Brave searches for
    external evidence that either supports or contradicts
    the proposed change. Neither AI can argue with it.

  Final Step — HITL Push
    Packages the debate transcript, evidence, and exact
    code change. Pushes to Telegram for operator approval.
    If HITL_REQUIRED=false in config, auto-applies.

Runs every 60 seconds. Picks up 'open' proposals from
polaris_proposals table.

.env provider shape:
    NVIDIA_NIM_API_KEY=your_nim_key       (primary)
    OPENAI_API_KEY=your_openai_key         (fallback)
    BRAVE_SEARCH_API_KEY=your_brave_key    (optional but recommended)
    TELEGRAM_BOT_TOKEN=your_bot_token
    TELEGRAM_OWNER_ID=your_numeric_id

File location: trading-bot/services/debate_engine.py
"""
from __future__ import annotations


import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value
from services.ivaris import IvarisClient, get_polaris_rebuttal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [DEBATE] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("debate_engine")

SERVICE_NAME    = "debate_engine"
MAX_ROUNDS      = 5       # max back-and-forth rounds before forcing a decision
POLL_INTERVAL   = 60      # seconds between cycles
CONSENSUS_FLOOR = 0.75    # IVARIS confidence needed to reach consensus


# ── ENV ───────────────────────────────────────────────────────────────────────
# Council provider doctrine: NVIDIA NIM primary, OpenAI fallback.
# IVARIS uses the same provider pool through services.ivaris; Gemini is retired.
def _approved_provider_available() -> bool:
    return bool(
        os.getenv("NVIDIA_NIM_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )

# ── STARTUP VALIDATION ────────────────────────────────────────────────────────
def _validate_keys():
    if not _approved_provider_available():
        raise RuntimeError(
            "No approved Council provider key: NVIDIA_NIM_API_KEY primary or OPENAI_API_KEY fallback"
        )
BRAVE_KEY     = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID      = int(os.getenv("TELEGRAM_OWNER_ID", "0"))


# ── DB HELPERS ────────────────────────────────────────────────────────────────
# Cooldown per proposal_type after a CONCLUDED debate (pushed/approved/rejected)
# Research proposals (RESEARCH_NOTE, DOCTRINE_UPDATE) have no cooldown — they
# accumulate evidence continuously and never block the research loop.
PROPOSAL_COOLDOWN_HOURS = 6   # same concluded proposal_type blocked for 6h
RESEARCH_TYPES = {            # these types bypass cooldown — research runs always
    "RESEARCH_NOTE",
    "DOCTRINE_UPDATE",
    "PATTERN_OBSERVATION",
    "WALLET_INTEL",
}

def get_open_proposals() -> list[dict]:
    """
    Fetch proposals ready for debate.

    Cooldown logic:
    - RESEARCH_TYPES bypass cooldown entirely — continuous background work
    - Other types have a 6h cooldown after a concluded debate (same type)
    - deferred proposals become eligible again only when retry_after expires
    - This means Polaris and IVARIS can always be working on SOMETHING
    """
    try:
        cooldown_cutoff = time.time() - (PROPOSAL_COOLDOWN_HOURS * 3600)
        with get_connection() as conn:
            # Find non-research proposal_types concluded recently
            recent = conn.execute("""
                SELECT DISTINCT proposal_type FROM polaris_proposals
                WHERE status IN ('rejected_by_ivaris', 'pushed', 'approved', 'rejected')
                AND created_at > ?
            """, (cooldown_cutoff,)).fetchall()
            blocked_types = {
                r["proposal_type"] for r in recent
                if r["proposal_type"] not in RESEARCH_TYPES
            }

            # COUNCIL_SPINE_20260815 — retry_after is now honoured.
            #
            # The previous selector took status IN ('open','critic_unavailable',
            # 'debate_retryable') and ORDERED critic_unavailable FIRST, on the
            # reasoning "IVARIS may be back". While IVARIS was down that made
            # the same handful of proposals the highest-priority work on every
            # single cycle, each one re-marked critic_unavailable at the end of
            # it. That is the 4,440-event loop: a permanent status treated as a
            # priority retry, with no cooldown between attempts.
            #
            # sovereign_governor already writes a retry_after timestamp for
            # exactly this reason (sovereign_governor.py:3576). This selector
            # ignored the column. It no longer does.
            _ensure_retry_after(conn)
            _migrate_legacy_critic_unavailable(conn)
            _now = time.time()
            rows = conn.execute("""
                SELECT id, proposal_type, proposal_text,
                       suggested_action, confidence, metrics_json,
                       status, created_at
                FROM polaris_proposals
                WHERE status IN ('open', 'debate_retryable',
                                 'insufficient_quorum', 'needs_specification')
                  AND COALESCE(retry_after, 0) <= ?
                ORDER BY
                    CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                    CASE WHEN proposal_type IN ('RESEARCH_NOTE','DOCTRINE_UPDATE',
                         'PATTERN_OBSERVATION','WALLET_INTEL') THEN 0 ELSE 1 END,
                    id ASC
                LIMIT 5
            """, (_now,)).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            # Hydrate engine-grounded payload persisted by council_autobuilder.
            # Only the reserved engine_measurements key is accepted; it is
            # written by the local candidate measurement path.
            try:
                _meta = json.loads(d.get("metrics_json") or "{}")
                if isinstance(_meta, dict):
                    if isinstance(_meta.get("files"), list):
                        d["files"] = [str(x) for x in _meta["files"] if str(x).strip()]
                    _em = _meta.get("engine_measurements")
                    if isinstance(_em, dict):
                        d["_engine_measurements"] = _em
                    if _meta.get("test_cmd"):
                        d["test_cmd"] = str(_meta.get("test_cmd"))
            except Exception:
                pass
            ptype = d.get("proposal_type", "")

            # COUNCIL_RETRY_BACKOFF_20260809: the same malformed/provider
            # failures were re-running every cycle and adding avoidable DB/API
            # pressure. Keep the proposal retryable, but exponentially cool the
            # generic error lane. critic_unavailable keeps its provider-recovery
            # semantics.
            if d.get("status") == "debate_retryable":
                try:
                    meta = json.loads(d.get("metrics_json") or "{}")
                except Exception:
                    meta = {}
                retry_n = max(1, int(meta.get("debate_retry_count") or 1))
                last_err = float(meta.get("last_debate_error_at") or 0.0)
                cooldown = min(1800.0, 60.0 * (2 ** min(retry_n - 1, 5)))
                if last_err > 0 and (time.time() - last_err) < cooldown:
                    log.info(
                        "Proposal #%d debate retry cooling down %.0fs (retry=%d)",
                        d.get("id"), cooldown - (time.time() - last_err), retry_n,
                    )
                    continue

            # Always allow critic_unavailable and research work once any
            # debate-error backoff above has elapsed.
            if d["status"] in ("debate_retryable", "insufficient_quorum",
                               "needs_specification") or ptype in RESEARCH_TYPES:
                result.append(d)
            elif ptype not in blocked_types:
                result.append(d)
            else:
                log.info(
                    "Proposal #%d type=%s skipped — in %dh cooldown. "
                    "Research types still running.",
                    d["id"], ptype, PROPOSAL_COOLDOWN_HOURS
                )
        return result
    except Exception as e:
        log.error("get_open_proposals failed: %s", e)
        return []


def get_trade_context() -> dict:
    """Pull recent trade stats for AI context."""
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN win_loss='WIN' THEN 1 ELSE 0 END) as wins,
                    AVG(realized_pnl_usd) as avg_pnl,
                    AVG(CASE WHEN exit_category='SL' THEN 1.0 ELSE 0.0 END) as sl_rate
                FROM polaris_trade_reviews
                ORDER BY id DESC
                LIMIT 1
            """).fetchone()
        if row and row["total"]:
            total = row["total"] or 1
            return {
                "total_trades": total,
                "sample_size":  total,
                "win_rate":     ((row["wins"] or 0) / total) * 100,
                "avg_pnl":      float(row["avg_pnl"] or 0),
                "sl_rate":      float(row["sl_rate"] or 0),
            }
    except Exception as e:
        log.warning("get_trade_context failed: %s", e)
    return {"total_trades": 0, "sample_size": 0, "win_rate": 0, "avg_pnl": 0, "sl_rate": 0}


def write_cognition_event(stage: str, token: str, message: str, confidence: float = 0.0) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO cognition_log (timestamp, stage, token, message, confidence) VALUES (?, ?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), stage, token, message[:500], float(confidence or 0.0))
            )
            conn.commit()
    except Exception as e:
        log.debug("write_cognition_event skipped: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
# COUNCIL_SPINE_20260815 — ONE AUTHORISING QUORUM
# ═══════════════════════════════════════════════════════════════════════════
#
# WHAT WAS WRONG. Three modules independently authorised proposals:
# this file, sovereign_governor.run_debate(), and services.debate_quorum.
# Only the last one carries the hardened contract — abstention on missing
# evidence, structural critic for IVARIS ONLY, engine-measured verification,
# Tier C refused. It was also the only one nothing called: its sole importer
# is council_autobuilder, which claimed zero tasks. The hardened quorum was
# dead code while the unhardened path ran.
#
# WHAT THIS FILE IS NOW. A presenter and a router. It still produces the
# POLARIS/IVARIS transcript the chamber displays, because that is genuinely
# useful and costs nothing. It no longer decides anything. Every authorising
# verdict comes from services.debate_quorum.run_debate(), and its attempt_id
# is recorded so the conversation the operator reads carries the identity of
# the decision that was actually made.
#
# WHY "ROUTE EVERYTHING TO THE QUORUM" WOULD HAVE MADE THIS WORSE. The
# hardened quorum requires engine-measured facts, and its Tier C precondition
# calls golden_latch_gate.classify(files). A Polaris config/strategy proposal
# declares no files at all, and classify([]) returns ("C", "no_target_paths").
# Routing those straight through would have converted every one of them into
# TIER_C_REFUSED — a permanent block dressed as a constitutional decision.
#
# So proposals are triaged first. A proposal that names no artifact and
# carries no measurement has not failed critique; it has not yet been
# specified. It goes back for specification with a cooldown, which is what
# the directive asks for, rather than being told a critic rejected it.

#: how long a proposal waits before another attempt, by outcome
SPEC_COOLDOWN_SEC = float(os.getenv("COUNCIL_SPEC_COOLDOWN_SEC", "1800"))      # 30 min
QUORUM_COOLDOWN_SEC = float(os.getenv("COUNCIL_QUORUM_COOLDOWN_SEC", "900"))   # 15 min
NEEDS_YOU_COOLDOWN_SEC = float(os.getenv("COUNCIL_NEEDS_YOU_COOLDOWN_SEC", "86400"))

#: proposal types that describe a code artifact and can therefore be measured
BUILDABLE_TYPES = {
    "INTELLIGENCE_BUILD", "UI_BUILD", "CODE_PATCH", "SYSTEM_REPAIR",
    "ANALYTICS_BUILD", "DOCS_BUILD", "CAPABILITY_GAP",
}


def _ensure_retry_after(conn) -> None:
    """retry_after already exists in sovereign_governor's lineage; make sure."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(polaris_proposals)")}
        if "retry_after" not in cols:
            conn.execute("ALTER TABLE polaris_proposals ADD COLUMN retry_after REAL")
        if "blocker_reason" not in cols:
            conn.execute("ALTER TABLE polaris_proposals ADD COLUMN blocker_reason TEXT")
        if "last_attempt_id" not in cols:
            conn.execute("ALTER TABLE polaris_proposals ADD COLUMN last_attempt_id INTEGER")
    except Exception as exc:
        log.debug("retry_after ensure failed: %s", exc)


def _migrate_legacy_critic_unavailable(conn) -> None:
    """One-time compatibility migration for proposals parked by the old loop.

    Without this, removing critic_unavailable from the new selector strands all
    existing rows forever.  Convert them to the explicit retryable vocabulary
    and preserve any existing retry_after written by sovereign_governor.
    """
    try:
        _ensure_retry_after(conn)
        now = time.time()
        conn.execute(
            "UPDATE polaris_proposals "
            "SET status='debate_retryable', "
            "retry_after=CASE WHEN COALESCE(retry_after,0)>? THEN retry_after ELSE ? END, "
            "blocker_reason=COALESCE(NULLIF(blocker_reason,''), "
            "'LEGACY_CRITIC_UNAVAILABLE_MIGRATED') "
            "WHERE status='critic_unavailable'",
            (now, now),
        )
        conn.commit()
    except Exception as exc:
        log.warning("legacy critic_unavailable migration failed: %s", exc)


def defer_proposal(proposal_id: int, status: str, cooldown_sec: float,
                   reason: str, attempt_id=None) -> None:
    """Park a proposal with an explicit reason and a real cooldown.

    This replaces the permanent 'critic_unavailable' stamp. A proposal parked
    here is not lost and is not retried on the next cycle; it becomes eligible
    again when its cooldown expires, and the operator can read why it stopped.
    """
    try:
        with get_connection() as conn:
            _ensure_retry_after(conn)
            conn.execute(
                "UPDATE polaris_proposals SET status=?, retry_after=?, "
                "blocker_reason=?, last_attempt_id=? WHERE id=?",
                (status, time.time() + max(0.0, float(cooldown_sec)),
                 str(reason)[:500], attempt_id, proposal_id),
            )
            conn.commit()
    except Exception as exc:
        log.warning("defer_proposal failed id=%s: %s", proposal_id, exc)
    log.info("PROPOSAL_DEFERRED #%s status=%s retry_in=%.0fs reason=%s",
             proposal_id, status, cooldown_sec, str(reason)[:180])


def _proposal_target_files(proposal: dict) -> list:
    """Target artifacts a proposal names, if any. Never inferred."""
    for key in ("files", "target_files", "target_file"):
        v = proposal.get(key)
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
    meta = proposal.get("metrics_json")
    if isinstance(meta, str) and meta.strip():
        try:
            d = json.loads(meta)
            v = d.get("files") or d.get("target_files")
            if isinstance(v, (list, tuple)):
                return [str(x) for x in v if str(x).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
        except Exception:
            pass
    return []


def triage_proposal(proposal: dict) -> dict:
    """Decide whether a proposal is ready for the authorising quorum.

    Returns {"route", "reason", "files", "tier"}.
      SPECIFICATION_REQUIRED — names no artifact, or names one with no
                               engine measurement. Not a critique failure.
      QUORUM                 — has an artifact and measured facts.
      NEEDS_YOU              — names Tier C territory. A debate cannot
                               authorise it and never could.
    """
    files = _proposal_target_files(proposal)
    ptype = str(proposal.get("proposal_type") or "").upper()

    if not files:
        return {"route": "SPECIFICATION_REQUIRED", "files": [], "tier": None,
                "reason": (f"proposal type {ptype or 'UNKNOWN'} names no target "
                           f"artifact; specification/evidence required before "
                           f"any critique can be measured")}

    try:
        from services.golden_latch_gate import classify
        tier, why = classify(files)
    except Exception as exc:
        tier, why = "C", f"gate_unavailable:{exc}"
    if tier == "C":
        return {"route": "NEEDS_YOU", "files": files, "tier": tier,
                "reason": f"Tier C territory is never autonomous ({why})"}

    m = proposal.get("_engine_measurements")
    if not isinstance(m, dict) or m.get("compile_ok") is None:
        return {"route": "SPECIFICATION_REQUIRED", "files": files, "tier": tier,
                "reason": (f"target {files} declared but no engine measurement "
                           f"exists; a critic cannot verify what has not been "
                           f"built. Return to specification/build.")}

    return {"route": "QUORUM", "files": files, "tier": tier,
            "reason": f"tier {tier}, {len(files)} artifact(s), measured"}


def authorise(proposal: dict, transcript_log: dict) -> dict:
    """THE single authorising call. services.debate_quorum decides."""
    from services import debate_quorum
    q_proposal = dict(proposal)
    q_proposal["proposal_id"] = proposal.get("id")
    q_proposal.setdefault("files", _proposal_target_files(proposal))
    if transcript_log:
        q_proposal["_presenter_transcript_rounds"] = transcript_log.get("rounds", 0)
    tier = str(triage_proposal(proposal).get("tier") or "A")
    return debate_quorum.run_debate(q_proposal, risk_tier=tier)


def mark_proposal_status(proposal_id: int, status: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE polaris_proposals SET status=? WHERE id=?",
                (status, proposal_id),
            )
            conn.commit()
    except Exception as e:
        log.warning("mark_proposal_status failed: %s", e)


def apply_proposal_to_config(proposal: dict) -> bool:
    """
    Parse the suggested_action and apply the config change directly.
    Format: 'Change PARAM from X to Y'
    """
    import re
    action = proposal.get("suggested_action", "")
    m = re.search(
        r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)",
        action, re.IGNORECASE,
    )
    if not m:
        log.warning("Could not parse action for auto-apply: %s", action)
        return False
    param, old_val, new_val = m.group(1), m.group(2), m.group(3)
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO system_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
            """, (param, new_val, time.time()))
            conn.commit()
        log.info("AUTO-APPLIED: %s %s → %s", param, old_val, new_val)
        return True
    except Exception as e:
        log.error("apply_proposal_to_config failed: %s", e)
        return False


def log_debate_to_db(proposal_id: int, debate_log: dict) -> None:
    """
    Store full debate transcript in debate_log table for live dashboard display.
    Each round stored as a separate row so dashboard can stream it in real time.
    Also writes summary to cognition_log for the brain feed.
    """
    import json
    try:
        with get_connection() as conn:
            # Ensure debate_log table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS debate_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id     INTEGER NOT NULL,
                    logged_at       REAL NOT NULL,
                    round_num       INTEGER DEFAULT 0,
                    speaker         TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    content_json    TEXT,
                    consensus       INTEGER DEFAULT 0,
                    confidence      REAL DEFAULT 0.0,
                    is_final        INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_debate_log_proposal
                ON debate_log(proposal_id, logged_at DESC)
            """)

            now = time.time()
            transcript = debate_log.get("transcript", [])

            # Store each round as a row
            for entry in transcript:
                conn.execute("""
                    INSERT INTO debate_log
                        (proposal_id, logged_at, round_num, speaker, action,
                         content_json, consensus, confidence, is_final)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    proposal_id,
                    now,
                    entry.get("round", 0),
                    entry.get("speaker", "UNKNOWN"),
                    entry.get("action", ""),
                    json.dumps(entry.get("result", {}), default=str),
                    1 if debate_log.get("consensus") else 0,
                    float(debate_log.get("final_confidence", 0.0)),
                ))

            # Store final verdict row
            conn.execute("""
                INSERT INTO debate_log
                    (proposal_id, logged_at, round_num, speaker, action,
                     content_json, consensus, confidence, is_final)
                VALUES (?, ?, ?, 'VERDICT', 'final_verdict', ?, ?, ?, 1)
            """, (
                proposal_id,
                now,
                len(transcript),
                json.dumps({
                    "consensus":       debate_log.get("consensus", False),
                    "rounds":          debate_log.get("rounds", 0),
                    "final_confidence": debate_log.get("final_confidence", 0.0),
                    "final_objections": debate_log.get("final_objections", []),
                    "brave_confirmed":  debate_log.get("brave_confirmed", False),
                    "brave_evidence":   debate_log.get("brave_evidence", []),
                }, default=str),
                1 if debate_log.get("consensus") else 0,
                float(debate_log.get("final_confidence", 0.0)),
            ))

            conn.commit()

        # Also write summary to cognition_log for brain feed
        summary = (
            f"DEBATE | rounds={debate_log.get('rounds',0)} "
            f"consensus={debate_log.get('consensus',False)} "
            f"confidence={debate_log.get('final_confidence',0.0):.2f} "
            f"brave={debate_log.get('brave_confirmed',False)}"
        )
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO cognition_log
                    (timestamp, stage, token, message, confidence)
                VALUES (?, 'DEBATE', ?, ?, ?)
            """, (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                f"proposal_{proposal_id}",
                summary,
                debate_log.get("final_confidence", 0.0),
            ))
            conn.commit()

    except Exception as e:
        log.warning("log_debate_to_db failed: %s", e)



def _write_patch_history(
    proposal:      dict,
    debate_result: dict,
    brave_result:  dict,
    hitl_approved: bool,
    outcome:       str,
) -> None:
    """
    Write one row to patch_history after a proposal is applied or pushed.
    This is what populates the SOVEREIGN PATCHES panel in the dashboard.
    Table is created here if it doesn't exist (safe to call any time).
    """
    import re as _re
    try:
        action   = proposal.get("suggested_action", "")
        ptype    = proposal.get("proposal_type", "")
        conf     = float(proposal.get("confidence", 0.0) or 0.0)
        rounds   = int(debate_result.get("rounds", 0))
        brave_ok = 1 if brave_result.get("confirmed") else 0

        # Parse param/old/new from action string
        m = _re.search(
            r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)",
            action, _re.IGNORECASE,
        )
        param_key = m.group(1) if m else ""
        old_value = m.group(2) if m else ""
        new_value = m.group(3) if m else ""

        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS patch_history (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    applied_at     REAL NOT NULL,
                    proposal_type  TEXT,
                    action         TEXT,
                    param_key      TEXT,
                    old_value      TEXT,
                    new_value      TEXT,
                    confidence     REAL DEFAULT 0.0,
                    rounds         INTEGER DEFAULT 0,
                    brave_confirmed INTEGER DEFAULT 0,
                    hitl_approved  INTEGER DEFAULT 0,
                    outcome        TEXT DEFAULT 'applied'
                )
            """)
            conn.execute("""
                INSERT INTO patch_history
                    (applied_at, proposal_type, action, param_key, old_value,
                     new_value, confidence, rounds, brave_confirmed,
                     hitl_approved, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(), ptype, action, param_key, old_value,
                new_value, conf, rounds, brave_ok,
                1 if hitl_approved else 0, outcome,
            ))
            conn.commit()
    except Exception as e:
        log.warning("_write_patch_history failed: %s", e)


# ── BRAVE SEARCH ──────────────────────────────────────────────────────────────
def brave_verify(proposal: dict, ivaris_verdict: dict) -> dict:
    """
    Run a Brave search to get external real-world evidence.
    Returns dict with confirmed, evidence_snippets, search_query.
    """
    if not BRAVE_KEY:
        return {
            "confirmed":        None,
            "evidence_snippets": ["Brave Search not configured — skipping external verification"],
            "search_query":     "",
            "skipped":          True,
        }

    ptype  = proposal.get("proposal_type", "")
    action = proposal.get("suggested_action", "")

    # Build a targeted search query based on the proposal type
    import re
    m = re.search(r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)", action, re.IGNORECASE)
    if m:
        param, old_val, new_val = m.group(1), m.group(2), m.group(3)
        query = f"pump.fun solana meme token trading {param.lower().replace('_',' ')} optimal 2025"
    else:
        query = f"pump.fun solana meme token trading strategy improvement {ptype.lower()} 2025"

    log.info("Brave search: %s", query)

    try:
        import requests
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept":                "application/json",
                "Accept-Encoding":       "gzip",
                "X-Subscription-Token":  BRAVE_KEY,
            },
            params={"q": query, "count": 5},
            timeout=10,
        )
        resp.raise_for_status()
        data    = resp.json()
        results = data.get("web", {}).get("results", [])

        snippets = []
        for r in results[:4]:
            title = r.get("title", "")
            desc  = r.get("description", "")
            if title or desc:
                snippets.append(f"{title}: {desc[:150]}")

        # Simple heuristic — does the search support the proposal direction?
        combined = " ".join(snippets).lower()
        if m:
            # If raising a threshold, check if sources mention it being beneficial
            direction_words = ["increase", "higher", "raise", "longer", "more"] if float(new_val) > float(old_val) else ["decrease", "lower", "reduce", "shorter", "less"]
            support_count = sum(1 for w in direction_words if w in combined)
            confirmed = support_count >= 1
        else:
            confirmed = None  # Can't determine without param context

        return {
            "confirmed":         confirmed,
            "evidence_snippets": snippets,
            "search_query":      query,
            "skipped":           False,
        }

    except Exception as e:
        log.warning("Brave search failed: %s", e)
        return {
            "confirmed":         None,
            "evidence_snippets": [f"Brave search failed: {e}"],
            "search_query":      query,
            "skipped":           True,
        }


# ── TELEGRAM PUSH ─────────────────────────────────────────────────────────────
async def push_to_telegram(proposal: dict, debate_log: dict, brave_result: dict) -> bool:
    """Push the final packaged proposal to the operator via Telegram."""
    if not BOT_TOKEN or not OWNER_ID:
        log.warning("Telegram not configured — cannot push proposal")
        return False

    try:
        import requests

        pid    = proposal.get("id", "?")
        ptype  = proposal.get("proposal_type", "?")
        ptext  = proposal.get("proposal_text", "")
        action = proposal.get("suggested_action", "")
        rounds = debate_log.get("rounds", 0)
        final_conf = debate_log.get("final_confidence", 0.0)
        brave_conf = " CONFIRMED" if brave_result.get("confirmed") else (
                     " NOT CONFIRMED" if brave_result.get("confirmed") is False else
                     "— NOT RUN" if brave_result.get("skipped") else "— INCONCLUSIVE")

        # Build objection summary
        objections = debate_log.get("final_objections", [])
        obj_text = "\n".join(f"  • {o}" for o in objections[:3]) if objections else "  None remaining"

        # Evidence snippet
        evidence = brave_result.get("evidence_snippets", [])
        ev_text  = evidence[0][:120] if evidence else "No evidence retrieved"

        msg = (
            f" *SOVEREIGN PATCH PROPOSAL #{pid}*\n\n"
            f"*POLARIS proposed · IVARIS challenged · {rounds} rounds*\n"
            f"External: Brave Search {brave_conf}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"*TYPE:* `{ptype}`\n"
            f"*CONFIDENCE:* `{final_conf:.2f}`\n\n"
            f"*FINDING:*\n{ptext[:300]}\n\n"
            f"*PROPOSED CHANGE:*\n`{action}`\n\n"
            f"*REMAINING OBJECTIONS:*\n{obj_text}\n\n"
            f"*BRAVE EVIDENCE:*\n_{ev_text}_\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"️ _Patch is HALTED pending your approval_\n"
            f"Proposal ID: `{pid}`"
        )

        keyboard = {
            "inline_keyboard": [[
                {"text": "  APPROVE",  "callback_data": f"approve:{pid}"},
                {"text": "  REJECT",   "callback_data": f"reject:{pid}"},
            ]]
        }

        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":      OWNER_ID,
                "text":         msg,
                "parse_mode":   "Markdown",
                "reply_markup": keyboard,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Proposal #%s pushed to Telegram", pid)
        return True

    except Exception as e:
        log.error("push_to_telegram failed: %s", e)
        return False


def push_to_telegram_sync(proposal: dict, debate_log: dict, brave_result: dict) -> bool:
    """Synchronous wrapper for push_to_telegram."""
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            push_to_telegram(proposal, debate_log, brave_result)
        )
        loop.close()
        return result
    except Exception as e:
        log.error("push_to_telegram_sync failed: %s", e)
        return False



def get_proposal_feedback() -> dict:
    """
    Build POLARIS's proposal track record for IVARIS to weigh.
    Reads recent applied/validated/rolled_back proposals and their outcomes.
    Returns a summary dict + history list so IVARIS can calibrate scepticism.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT pp.proposal_type, pp.suggested_action, pp.confidence,
                       pp.status, pp.created_at,
                       ps.status AS snapshot_status
                FROM polaris_proposals pp
                LEFT JOIN parameter_snapshots ps
                    ON ps.proposal_id = pp.proposal_hash
                WHERE pp.status IN ('applied', 'validated', 'rolled_back',
                                    'auto_applied', 'pushed', 'rejected_by_ivaris')
                ORDER BY pp.created_at DESC
                LIMIT 10
            """).fetchall()

        if not rows:
            return {"summary": "No proposal history yet.", "history": []}

        total      = len(rows)
        rolled     = sum(1 for r in rows if r["snapshot_status"] == "rolled_back"
                         or r["status"] == "rolled_back")
        approved   = sum(1 for r in rows if r["status"] in ("applied", "auto_applied",
                                                              "validated"))
        rejected   = sum(1 for r in rows if r["status"] == "rejected_by_ivaris")
        rollback_rate = rolled / total if total else 0.0

        summary = (
            f"{total} recent proposals: {approved} approved, "
            f"{rejected} rejected by IVARIS, {rolled} rolled back "
            f"(rollback rate {rollback_rate:.0%})."
        )

        history = [
            {
                "type":    r["proposal_type"],
                "action":  (r["suggested_action"] or "")[:80],
                "status":  r["status"],
                "outcome": r["snapshot_status"] or r["status"],
            }
            for r in rows
        ]

        return {"summary": summary, "history": history}

    except Exception as e:
        log.warning("get_proposal_feedback failed: %s", e)
        return {"summary": "History unavailable.", "history": []}


# ── CORE DEBATE PROTOCOL ──────────────────────────────────────────────────────
def run_debate(proposal: dict, ivaris: IvarisClient) -> dict:
    """
    Run the full POLARIS vs IVARIS debate protocol.

    Returns debate_log dict with:
        consensus, rounds, final_confidence,
        final_objections, transcript
    """
    trade_context = get_trade_context()
    transcript    = []
    consensus     = False
    final_conf    = 0.0
    final_obj     = []

    log.info(
        "DEBATE START proposal_id=%s type=%s",
        proposal.get("id"), proposal.get("proposal_type"),
    )

    # Round 0 — initial critique
    feedback       = get_proposal_feedback()
    # IVARIS receives full context: current performance + POLARIS's track record
    trade_context_with_feedback = {**trade_context, "proposal_feedback": feedback}
    # SIGNOFF_20260810_DEBATE_CRITIC_BOUNDARY
    # A provider/model/nullable-payload failure is a critic outage, not a
    # proposal failure.  Preserve the proposal and fail the debate closed.
    try:
        ivaris_verdict = ivaris.critique(proposal, trade_context_with_feedback)
    except Exception as _critic_exc:
        log.exception("IVARIS critique boundary caught %s", type(_critic_exc).__name__)
        ivaris_verdict = {
            "verdict": "CRITIC_UNAVAILABLE",
            "consensus": False,
            "confidence": 0.0,
            "objections": [f"IVARIS critic boundary: {type(_critic_exc).__name__}"],
            "_ivaris_failed": True,
        }
    # MODE3_3H_EDGE_RECOVERY_20260807: provider outages and malformed model
    # responses may return None or a non-dict payload. Normalise that condition
    # into an explicit critic-unavailable verdict instead of raising
    # TypeError: 'NoneType' object is not subscriptable/gettable.
    if not isinstance(ivaris_verdict, dict):
        ivaris_verdict = {
            "verdict": "CRITIC_UNAVAILABLE",
            "consensus": False,
            "confidence": 0.0,
            "objections": ["IVARIS provider returned no structured verdict"],
            "provider_payload_type": type(ivaris_verdict).__name__,
        }
    # IVARIS_CONTRACT_COMPAT_20260713: normalize the current IvarisClient
    # verdict contract into the debate engine's historical consensus contract.
    if "consensus" not in ivaris_verdict:
        _v = str(ivaris_verdict.get("verdict", "DEBATE")).strip().upper()
        ivaris_verdict["consensus"] = (_v == "APPROVE")
    transcript.append({
        "round":   0,
        "speaker": "IVARIS",
        "action":  "initial_critique",
        "result":  ivaris_verdict,
    })

    # ── CRITIC UNAVAILABLE GUARD ──────────────────────────────────────────────
    # If Gemini API is down at runtime, IVARIS returns verdict with "API unavailable"
    # in its objections. We must NOT continue — a proposal cannot be self-approved
    # by Polaris talking to herself for 5 rounds. Mark as CRITIC_UNAVAILABLE and
    # halt immediately. Operator will see this in the debate chamber.
    verdict_text = str(ivaris_verdict.get("verdict", "")).lower()
    objections   = ivaris_verdict.get("objections", [])
    if not isinstance(objections, (list, tuple)):
        objections = [str(objections)] if objections not in (None, "") else []
    api_blocked  = (
        "api unavailable" in verdict_text
        or any("api unavailable" in str(o).lower() for o in objections)
        or any("unavailable" in str(o).lower() for o in objections)
    )
    if api_blocked:
        log.warning("DEBATE: IVARIS API unavailable — marking CRITIC_UNAVAILABLE, blocking proposal")
        return {
            "consensus":          False,
            "rounds":             0,
            "final_confidence":   0.0,
            "final_objections":   ["IVARIS model route unavailable — proposal cannot be critiqued"],
            "transcript":         transcript,
            "critic_unavailable": True,
        }
    # ── END CRITIC UNAVAILABLE GUARD ─────────────────────────────────────────

    final_conf = float(ivaris_verdict.get("confidence", 0.0))
    final_obj  = ivaris_verdict.get("objections", [])

    log.info(
        "Round 0 — IVARIS: consensus=%s confidence=%.2f objections=%d",
        ivaris_verdict.get("consensus"), final_conf, len(final_obj),
    )

    # Check for immediate consensus
    if ivaris_verdict.get("consensus") and final_conf >= CONSENSUS_FLOOR:
        consensus = True
        log.info("DEBATE: immediate consensus reached")
        return {
            "consensus":        consensus,
            "rounds":           0,
            "final_confidence": final_conf,
            "final_objections": final_obj,
            "transcript":       transcript,
        }

    # Debate rounds — POLARIS rebuts, IVARIS re-evaluates
    for round_num in range(1, MAX_ROUNDS + 1):
        log.info("DEBATE Round %d — getting POLARIS rebuttal", round_num)

        # POLARIS rebuts
        polaris_rebuttal = get_polaris_rebuttal(
            proposal, ivaris_verdict, round_num
        ) or {
            "summary": "POLARIS rebuttal unavailable",
            "proposal_adjusted": False,
            "addressed_objections": [],
            "remaining_concerns": ivaris_verdict.get("objections", []),
        }
        transcript.append({
            "round":   round_num,
            "speaker": "POLARIS",
            "action":  "rebuttal",
            "result":  polaris_rebuttal,
        })

        # Update proposal action if POLARIS adjusted it
        if polaris_rebuttal.get("proposal_adjusted"):
            new_action = polaris_rebuttal.get("adjusted_action", "")
            if new_action:
                proposal = dict(proposal)
                proposal["suggested_action"] = new_action
                log.info("POLARIS adjusted action: %s", new_action[:80])

        # IVARIS evaluates the rebuttal.  Keep the same fail-closed boundary
        # used for the opening critique so one provider exception cannot trap
        # a proposal in an endless retry loop.
        try:
            ivaris_verdict = ivaris.evaluate_rebuttal(
                proposal, polaris_rebuttal, ivaris_verdict
            )
        except Exception as _critic_exc:
            log.exception("IVARIS rebuttal boundary caught %s", type(_critic_exc).__name__)
            ivaris_verdict = {
                "verdict": "CRITIC_UNAVAILABLE",
                "consensus": False,
                "confidence": 0.0,
                "objections": [f"IVARIS rebuttal boundary: {type(_critic_exc).__name__}"],
                "_ivaris_failed": True,
            }
        if not isinstance(ivaris_verdict, dict):
            ivaris_verdict = {
                "verdict": "CRITIC_UNAVAILABLE",
                "consensus": False,
                "confidence": 0.0,
                "objections": ["IVARIS provider returned no structured rebuttal verdict"],
                "provider_payload_type": type(ivaris_verdict).__name__,
            }
        if "consensus" not in ivaris_verdict:
            _v = str(ivaris_verdict.get("verdict", "DEBATE")).strip().upper()
            ivaris_verdict["consensus"] = (_v == "APPROVE")
        transcript.append({
            "round":   round_num,
            "speaker": "IVARIS",
            "action":  "rebuttal_evaluation",
            "result":  ivaris_verdict,
        })

        final_conf = float(ivaris_verdict.get("confidence", 0.0))
        final_obj  = ivaris_verdict.get("objections", [])
        if not isinstance(final_obj, (list, tuple)):
            final_obj = [str(final_obj)] if final_obj not in (None, "") else []

        log.info(
            "Round %d — IVARIS: consensus=%s confidence=%.2f",
            round_num, ivaris_verdict.get("consensus"), final_conf,
        )

        if ivaris_verdict.get("consensus") and final_conf >= CONSENSUS_FLOOR:
            consensus = True
            log.info("DEBATE: consensus reached at round %d", round_num)
            break

        if round_num == MAX_ROUNDS:
            log.info("DEBATE: max rounds reached — no consensus")

    return {
        "consensus":        consensus,
        "rounds":           min(MAX_ROUNDS, round_num if "round_num" in dir() else 0),
        "final_confidence": final_conf,
        "final_objections": final_obj,
        "transcript":       transcript,
        "final_proposal":   proposal,
    }


# ── MAIN CYCLE ────────────────────────────────────────────────────────────────
def run_cycle(ivaris: IvarisClient) -> None:
    proposals = get_open_proposals()

    if not proposals:
        update_heartbeat(SERVICE_NAME, "ALIVE", "Idle — no open proposals")
        return

    hitl_required = str(get_config_value("HITL_REQUIRED", "1")).strip() == "1"

    for proposal in proposals:
        pid   = proposal.get("id")
        ptype = proposal.get("proposal_type", "?")

        log.info("Processing proposal #%d type=%s", pid, ptype)

        # Mark as in-debate so it isn't picked up twice
        mark_proposal_status(pid, "debating")

        try:
            # ── STEP 0: TRIAGE ────────────────────────────────────────────
            # A proposal that names no artifact, or names one that has not
            # been built, has not failed critique. Saying "the critic is
            # unavailable" about a proposal nothing could have critiqued is
            # the loop this pack exists to end.
            _triage = triage_proposal(proposal)
            if _triage["route"] == "NEEDS_YOU":
                defer_proposal(pid, "needs_you", NEEDS_YOU_COOLDOWN_SEC,
                               _triage["reason"])
                write_cognition_event(
                    "DEBATE", f"proposal_{pid}",
                    f"NEEDS_YOU | {_triage['reason']}", 0.0)
                continue
            if _triage["route"] == "SPECIFICATION_REQUIRED":
                defer_proposal(pid, "needs_specification", SPEC_COOLDOWN_SEC,
                               _triage["reason"])
                write_cognition_event(
                    "DEBATE", f"proposal_{pid}",
                    f"NEEDS_SPECIFICATION | {_triage['reason']}", 0.0)
                continue

            # ── STEP 1: PRESENTER TRANSCRIPT (never authorises) ───────────
            # Kept because the chamber is more readable with it, and because
            # a live IVARIS turn is real evidence the quorum can use. If the
            # provider is down this simply produces a shorter transcript; it
            # no longer blocks anything.
            debate_log = run_debate(proposal, ivaris)
            log_debate_to_db(pid, debate_log)
            if debate_log.get("critic_unavailable"):
                log.info("Proposal #%d — IVARIS route unavailable; transcript "
                         "is degraded. Authority passes to debate_quorum, "
                         "which will abstain or substitute per contract.", pid)
            write_cognition_event("DEBATE", f"proposal_{pid}", f"DEBATE_CYCLE | rounds={debate_log.get('rounds', 0)} consensus={debate_log.get('consensus', False)} conf={debate_log.get('final_confidence', 0.0):.2f}", debate_log.get("final_confidence", 0.0))

            # ── STEP 2: THE AUTHORISING QUORUM ────────────────────────────
            _q = authorise(proposal, debate_log)
            _aid = _q.get("attempt_id")
            log.info("QUORUM #%d verdict=%s quorum=%s sources=%d/%d conf=%.2f "
                     "attempt=%s subs=%s",
                     pid, _q.get("verdict"), _q.get("quorum"),
                     int(_q.get("independent_sources") or 0),
                     int(_q.get("needed") or 3),
                     float(_q.get("confidence") or 0.0), _aid,
                     _q.get("substitutions"))
            write_cognition_event(
                "DEBATE", f"proposal_{pid}",
                f"QUORUM | {_q.get('verdict')} sources="
                f"{_q.get('independent_sources')}/{_q.get('needed')} "
                f"attempt={_aid}", float(_q.get("confidence") or 0.0))

            _verdict = str(_q.get("verdict") or "")
            if _verdict == "TIER_C_REFUSED":
                defer_proposal(pid, "needs_you", NEEDS_YOU_COOLDOWN_SEC,
                               "quorum refused: Tier C territory", _aid)
                continue
            if _verdict == "DUPLICATE_SUPPRESSED":
                defer_proposal(pid, "debate_retryable", QUORUM_COOLDOWN_SEC,
                               "duplicate attempt suppressed by quorum cooldown",
                               _aid)
                continue
            if _q.get("quorum") == "INSUFFICIENT_QUORUM":
                # Not a rejection — an inability to review. Retrying on the
                # next cycle cannot conjure an evidence source, so this waits.
                _abst = _q.get("abstentions") or []
                defer_proposal(
                    pid, "insufficient_quorum", QUORUM_COOLDOWN_SEC,
                    (f"only {_q.get('independent_sources')} independent "
                     f"evidence source(s), {_q.get('needed')} required; "
                     f"abstained={[a.get('role') for a in _abst]}"), _aid)
                continue

            if not _q.get("consensus"):
                log.info("Proposal #%d REJECTED by quorum (%s, conf=%.2f)",
                         pid, _q.get("quorum"), float(_q.get("confidence") or 0.0))
                mark_proposal_status(pid, "rejected_by_quorum")
                write_cognition_event(
                    "DEBATE", f"proposal_{pid}",
                    f"REJECTED | {_q.get('verdict')} attempt={_aid}",
                    float(_q.get("confidence") or 0.0))
                continue

            # Consensus reached. Re-shape the legacy verdict the downstream
            # approval/push path already understands, using quorum values.
            debate_log = dict(debate_log)
            debate_log["consensus"] = True
            debate_log["final_confidence"] = float(_q.get("confidence") or 0.0)
            debate_log["quorum_attempt_id"] = _aid
            debate_log["quorum_state"] = _q.get("quorum")


            # ── STEP 2: BRAVE EXTERNAL VERIFICATION ──────────────────────
            # Use the potentially-adjusted proposal from debate
            final_proposal = debate_log.get("final_proposal", proposal)
            brave_result   = brave_verify(final_proposal, debate_log)

            log.info(
                "Brave verification: confirmed=%s query='%s'",
                brave_result.get("confirmed"), brave_result.get("search_query", "")[:60],
            )

            # ── STEP 3: APPLY OR PUSH TO HITL ────────────────────────────
            if hitl_required:
                # Push to Telegram for human approval
                pushed = push_to_telegram_sync(final_proposal, debate_log, brave_result)
                if pushed:
                    mark_proposal_status(pid, "pushed")
                    write_cognition_event("POLARIS", f"proposal_{pid}", f"PUSHED | Consensus patch escalated to HITL approval. Action: {final_proposal.get('suggested_action', '')[:220]}", debate_log.get("final_confidence", 0.0))
                    log.info("Proposal #%d pushed to Telegram for HITL approval", pid)
                    # Write to patch_history as pending_hitl so dashboard shows it
                    _write_patch_history(
                        proposal      = final_proposal,
                        debate_result = debate_log,
                        brave_result  = brave_result,
                        hitl_approved = False,
                        outcome       = "pending_hitl",
                    )
                else:
                    mark_proposal_status(pid, "push_failed")
                    log.warning("Proposal #%d push failed", pid)
            else:
                # Auto-apply without human approval (HITL_REQUIRED=0 in config)
                # Route through SovereignParameterEngine for throttle + snapshot + rollback.
                # Falls back to direct SQL apply only if SPE raises an exception.
                applied      = False
                spe_result   = None
                apply_method = "direct"

                try:
                    from services.sovereign_parameter_engine import (
                        SovereignParameterEngine, Proposal as SPEProposal, ParameterChange
                    )
                    import re as _re
                    action = final_proposal.get("suggested_action", "")
                    m = _re.search(
                        r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)",
                        action, _re.IGNORECASE,
                    )
                    if m:
                        param, old_val, new_val = m.group(1), m.group(2), m.group(3)
                        spe_proposal = SPEProposal(
                            proposal_id           = str(pid),
                            hypothesis            = final_proposal.get("proposal_text", "")[:300],
                            expected_outcome      = f"Improve trading performance via {param}",
                            falsifiability_condition = "Win rate or PnL degrades within 1h",
                            risk_assessment       = f"Thermal-throttled change to {param}",
                            success_metric        = "Win rate >= previous baseline",
                            parameter_changes     = [
                                ParameterChange(param=param, old_value=old_val, new_value=new_val,
                                                proposal_id=str(pid))
                            ],
                        )
                        spe_result = SovereignParameterEngine().apply_proposal(spe_proposal)
                        applied    = spe_result.get("success", False)
                        apply_method = "spe"
                        if spe_result.get("blocked"):
                            log.warning("SPE blocked changes: %s", spe_result["blocked"])
                    else:
                        # No parseable param change — fall through to direct apply
                        applied      = apply_proposal_to_config(final_proposal)
                        apply_method = "direct"
                except Exception as spe_err:
                    log.warning("SPE apply failed (%s) — falling back to direct apply", spe_err)
                    applied      = apply_proposal_to_config(final_proposal)
                    apply_method = "direct_fallback"

                if applied:
                    mark_proposal_status(pid, "auto_applied")
                    write_cognition_event("POLARIS", f"proposal_{pid}", f"AUTO_APPLIED | Patch applied. Action: {final_proposal.get('suggested_action', '')[:220]}", debate_log.get("final_confidence", 0.0))
                    log.info("Proposal #%d AUTO-APPLIED via %s (HITL_REQUIRED=0)",
                             pid, apply_method)
                    # Write to patch_history so dashboard panel populates
                    _write_patch_history(
                        proposal      = final_proposal,
                        debate_result = debate_log,
                        brave_result  = brave_result,
                        hitl_approved = False,
                        outcome       = "applied",
                    )
                    if BOT_TOKEN and OWNER_ID:
                        try:
                            import requests
                            requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                json={
                                    "chat_id":    OWNER_ID,
                                    "text":       (
                                        f"*Proposal #{pid} AUTO-APPLIED ({apply_method})*\n\n"
                                        f"`{final_proposal.get('suggested_action', '')}`\n\n"
                                        f"_HITL_REQUIRED=0 — patch applied without approval._"
                                    ),
                                    "parse_mode": "Markdown",
                                },
                                timeout=10,
                            )
                        except Exception:
                            pass
                else:
                    mark_proposal_status(pid, "apply_failed")
                    write_cognition_event("DEBATE", f"proposal_{pid}", f"APPLY_FAILED | Consensus reached but apply path failed. Action: {final_proposal.get('suggested_action', '')[:220]}", debate_log.get("final_confidence", 0.0))

        except Exception as e:
            # SIGNOFF_COUNCIL_RETRY_EVIDENCE_20260801: a transient provider,
            # schema or parser exception must not terminally strand the task.
            # Preserve concise evidence and return it to the retry queue.
            import traceback as _traceback
            err = f"{type(e).__name__}: {e}"[:500]
            trace = _traceback.format_exc(limit=8)[-3000:]
            log.exception("Debate failed for proposal #%d: %s", pid, e)
            try:
                with get_connection() as _c:
                    _row = _c.execute(
                        "SELECT metrics_json FROM polaris_proposals WHERE id=?", (pid,)
                    ).fetchone()
                    try:
                        _meta = json.loads((_row[0] if _row else '') or '{}')
                    except Exception:
                        _meta = {}
                    _meta['last_debate_error'] = err
                    _meta['last_debate_trace'] = trace
                    _meta['last_debate_error_at'] = time.time()
                    _meta['debate_retry_count'] = int(_meta.get('debate_retry_count') or 0) + 1
                    _c.execute(
                        "UPDATE polaris_proposals SET status='debate_retryable', metrics_json=? WHERE id=?",
                        (json.dumps(_meta, sort_keys=True), pid),
                    )
                    _c.commit()
                write_cognition_event("DEBATE", f"proposal_{pid}",
                                      f"DEBATE_RETRYABLE | {err}", 0.0)
            except Exception:
                mark_proposal_status(pid, "debate_retryable")

        # Brief pause between proposals to avoid rate limiting
        time.sleep(5)

    update_heartbeat(SERVICE_NAME, "ALIVE",
        f"Processed {len(proposals)} proposal(s)")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
def run() -> None:
    if not _approved_provider_available():
        log.error("No approved Council provider key — NIM primary / OpenAI fallback unavailable")
        update_heartbeat(SERVICE_NAME, "ERROR", "NIM/OpenAI provider keys unavailable")
        return

    log.info("DEBATE ENGINE ONLINE")
    log.info("POLARIS vs IVARIS — %d max rounds | Consensus floor: %.2f", MAX_ROUNDS, CONSENSUS_FLOOR)
    log.info("Brave Search: %s", "ACTIVE" if BRAVE_KEY else "NOT CONFIGURED")
    log.info("HITL Required: %s", get_config_value("HITL_REQUIRED", "1"))

    # Ensure HITL_REQUIRED exists in config
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO system_config (key, value, description)
                VALUES ('HITL_REQUIRED', '1',
                        '1=require human approval before applying patches, 0=auto-apply after consensus')
            """)
            conn.commit()
    except Exception:
        pass

    ivaris = IvarisClient()

    update_heartbeat(SERVICE_NAME, "ALIVE", "Debate engine online")

    # Startup delay — let other services settle
    log.info("Waiting 30s for services to stabilise...")
    time.sleep(30)

    while True:
        try:
            run_cycle(ivaris)
        except Exception as e:
            log.exception("DEBATE ENGINE ERROR: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
