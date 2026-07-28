# coding: utf-8
"""
services/debate_quorum.py — COUNCIL_AUTOBUILD_20260723

Degraded-quorum debate doctrine. One unavailable model must never freeze the
Council. Route order per role: primary provider → alternate provider →
DETERMINISTIC STRUCTURAL CRITIC (always available, rule-based).

Quorum: Tier A (UI/charts/analytics/research) needs 3 functioning roles.
Tier B+ needs 4 AND operator approval downstream regardless of verdict.
DEGRADED_QUORUM verdicts carry transparently reduced confidence (×0.8/sub).

Repeated zero-round verdicts are suppressed: every attempt has a unique
identity and a cooldown; the same proposal is not re-debated inside the window.
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

ROLES = ["POLARIS", "IVARIS", "NUGGET", "ORACLE", "RHIZA"]
RETRY_COOLDOWN_SEC = 600.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS debate_attempts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    quorum TEXT,
    consensus INTEGER,
    confidence REAL,
    degraded INTEGER DEFAULT 0,
    substitutions TEXT,
    verdict TEXT,
    cooldown_until REAL,
    UNIQUE(proposal_id, attempt_no)
);
"""


def _con(db_path: Optional[Path] = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
    return c


def ensure_schema(db_path: Optional[Path] = None) -> None:
    c = _con(db_path)
    try:
        c.executescript(SCHEMA); c.commit()
    finally:
        c.close()


def cooldown_active(proposal_id: int, db_path: Optional[Path] = None) -> bool:
    c = _con(db_path)
    try:
        r = c.execute("SELECT MAX(cooldown_until) FROM debate_attempts"
                      " WHERE proposal_id=?", (proposal_id,)).fetchone()
        return bool(r and r[0] and float(r[0]) > time.time())
    finally:
        c.close()


# ── Structural critic: deterministic, always available ──────────────────────
def structural_critic(role: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Rule-based review — mechanical truth checks, no model required.
    Verdict is deliberately conservative: any structural failure vetoes."""
    checks: List[Tuple[str, bool]] = []
    paths = proposal.get("files", []) or []
    checks.append(("has_target_files", bool(paths)))
    try:
        from services.apply_policy import classify
        tier, why = classify(paths)
    except Exception:
        tier, why = "B", "policy_unavailable"
    checks.append(("tier_not_C", tier != "C"))
    diff_chars = int(proposal.get("diff_chars") or 0)
    checks.append(("diff_bounded_200k", 0 < diff_chars <= 200_000))
    checks.append(("compile_pass", bool(proposal.get("compile_ok"))))
    checks.append(("has_test", bool(proposal.get("test_cmd"))))
    checks.append(("has_backup_plan", bool(proposal.get("backup_planned", True))))
    ok = all(v for _, v in checks)
    failed = [k for k, v in checks if not v]
    return {"role": role, "provider": "structural_critic", "approve": ok,
            "confidence": 0.72 if ok else 0.15,
            "notes": ("structural checks pass"
                      if ok else f"failed: {', '.join(failed)}"),
            "tier": tier, "tier_reason": why}


def _default_model_router(role: str, proposal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Primary → alternate provider via existing llm_client if configured.
    Returns None when unavailable (missing keys / errors) so the caller
    substitutes the structural critic instead of dying."""
    try:
        from services.llm_client import council_role_review  # optional richer path
        return council_role_review(role, proposal)           # may raise / None
    except Exception:
        return None


def run_debate(proposal: Dict[str, Any], risk_tier: str = "A", *,
               model_router: Optional[Callable] = None,
               db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Returns {consensus, confidence, quorum, degraded, substitutions,
    verdict, attempt_id}. NEVER emits rounds=0 death loops: unavailable roles
    are substituted; duplicate attempts inside cooldown are refused."""
    ensure_schema(db_path)
    pid = int(proposal.get("proposal_id") or 0)
    if pid and cooldown_active(pid, db_path):
        return {"consensus": False, "confidence": 0.0, "quorum": "COOLDOWN",
                "degraded": False, "verdict": "DUPLICATE_SUPPRESSED",
                "substitutions": [], "attempt_id": None}
    router = model_router or _default_model_router
    reviews: List[Dict[str, Any]] = []
    subs: List[str] = []
    for role in ROLES:
        r = None
        for attempt in ("primary", "alternate"):
            try:
                r = router(role, proposal)
            except Exception:
                r = None
            if r:
                break
        if not r:
            r = structural_critic(role, proposal)
            subs.append(f"{role}->structural_critic")
        reviews.append(r)
    functioning = len(reviews)                      # substitution keeps all 5
    need = 3 if risk_tier == "A" else 4
    degraded = bool(subs)
    approvals = [r for r in reviews if r.get("approve")]
    consensus = len(approvals) >= need
    base_conf = (sum(float(r.get("confidence") or 0) for r in approvals)
                 / max(1, len(approvals)))
    confidence = round(base_conf * (0.8 ** min(len(subs), 3)), 3)
    quorum = "DEGRADED_QUORUM" if degraded else "FULL_QUORUM"
    verdict = ("APPROVED" if consensus else "REJECTED") \
        + ("_DEGRADED" if degraded else "")
    now = time.time()
    attempt_id = None
    c = _con(db_path)
    try:
        prev = c.execute("SELECT COALESCE(MAX(attempt_no),0) FROM debate_attempts"
                         " WHERE proposal_id=?", (pid,)).fetchone()[0]
        cur = c.execute(
            "INSERT INTO debate_attempts(proposal_id, attempt_no, started_at,"
            " finished_at, quorum, consensus, confidence, degraded,"
            " substitutions, verdict, cooldown_until)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (pid, int(prev) + 1, now, now, quorum, int(consensus), confidence,
             int(degraded), json.dumps(subs), verdict,
             now + (RETRY_COOLDOWN_SEC if not consensus else 0)))
        attempt_id = cur.lastrowid
        c.commit()
    finally:
        c.close()
    return {"consensus": consensus, "confidence": confidence, "quorum": quorum,
            "degraded": degraded, "substitutions": subs, "verdict": verdict,
            "reviews": reviews, "attempt_id": attempt_id,
            "functioning_roles": functioning, "needed": need}
