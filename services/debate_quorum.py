# coding: utf-8
"""
services/debate_quorum.py — SENTINUITY_PACK_P0_20260814

One authorising debate record. Missing evidence ABSTAINS.
The deterministic structural critic may stand in for IVARIS only.
Verification facts are engine-measured, never proposer-declared.
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"
ROLES = ["NUGGET", "AXON", "RHIZA", "IVARIS", "SUBSTRATE", "FORGE", "POLARIS"]
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
    reviews_json TEXT,
    abstentions_json TEXT,
    independent_sources INTEGER,
    measured_compile_ok INTEGER,
    measured_diff_chars INTEGER,
    risk_tier TEXT,
    UNIQUE(proposal_id, attempt_no)
);
"""

def _con(db_path: Optional[Path] = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
    return c

def _ensure_columns(c, table, specs):
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, decl in specs.items():
        if name not in cols:
            c.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {decl}')

def ensure_schema(db_path: Optional[Path] = None) -> None:
    c = _con(db_path)
    try:
        c.executescript(SCHEMA)
        _ensure_columns(c, "debate_attempts", {
            "reviews_json": "TEXT", "abstentions_json": "TEXT",
            "independent_sources": "INTEGER",
            "measured_compile_ok": "INTEGER",
            "measured_diff_chars": "INTEGER",
            "risk_tier": "TEXT",
        })
        c.commit()
    finally:
        c.close()

def cooldown_active(proposal_id: int, db_path: Optional[Path] = None) -> bool:
    c = _con(db_path)
    try:
        r = c.execute("SELECT MAX(cooldown_until) FROM debate_attempts WHERE proposal_id=?",
                      (proposal_id,)).fetchone()
        return bool(r and r[0] and float(r[0]) > time.time())
    finally:
        c.close()

def _measurements(proposal: Dict[str, Any]) -> Dict[str, Any]:
    m = proposal.get("_engine_measurements")
    return m if isinstance(m, dict) else {}

def structural_critic(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic IVARIS fallback only."""
    m = _measurements(proposal)
    paths = proposal.get("files", []) or []
    checks: List[Tuple[str, bool]] = [("has_target_files", bool(paths))]
    try:
        from services.golden_latch_gate import classify
        tier, why = classify(paths)
    except Exception:
        tier, why = "C", "policy_unavailable"
    checks.append(("territory_not_C", tier != "C"))
    measured_diff = m.get("diff_chars")
    measured_compile = m.get("compile_ok")
    checks.append(("measured_diff_present",
                   isinstance(measured_diff, int) and 0 < measured_diff <= 200_000))
    checks.append(("measured_compile_pass", measured_compile is True))
    checks.append(("has_test", bool(proposal.get("test_cmd"))))
    ok = all(v for _, v in checks)
    failed = [k for k, v in checks if not v]
    return {
        "role": "IVARIS",
        "provider": "structural_critic",
        "model": "deterministic_v1",
        "evidence_source": "engine_measurements+golden_latch",
        "approve": ok,
        "confidence": 0.72 if ok else 0.10,
        "notes": "structural checks pass" if ok else f"failed: {', '.join(failed)}",
        "tier": tier,
        "tier_reason": why,
    }

def _default_model_router(role: str, proposal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from services.llm_client import council_role_review
        return council_role_review(role, proposal)
    except Exception:
        return None

def _source_id(review: Dict[str, Any]) -> str:
    explicit = str(review.get("evidence_source") or "").strip()
    provider = str(review.get("provider") or "").strip()
    model = str(review.get("model") or review.get("model_id") or "").strip()
    if explicit:
        return f"evidence:{explicit}"
    if provider or model:
        return f"model:{provider}:{model}"
    return ""

def run_debate(proposal: Dict[str, Any], risk_tier: str = "A", *,
               model_router: Optional[Callable] = None,
               db_path: Optional[Path] = None) -> Dict[str, Any]:
    ensure_schema(db_path)
    pid = int(proposal.get("proposal_id") or 0)
    if pid and cooldown_active(pid, db_path):
        return {"consensus": False, "confidence": 0.0, "quorum": "COOLDOWN",
                "degraded": False, "verdict": "DUPLICATE_SUPPRESSED",
                "substitutions": [], "attempt_id": None}

    # Constitutional precondition: a debate cannot authorise Tier C.
    try:
        from services.golden_latch_gate import classify
        territory, territory_reason = classify(proposal.get("files", []) or [])
    except Exception:
        territory, territory_reason = "C", "gate_unavailable"
    if territory == "C":
        reviews = []
        abstentions = [{"role": r, "reason": "tier_c_precondition"} for r in ROLES]
        return _persist(pid, risk_tier, reviews, abstentions, False, 0.0,
                        "INSUFFICIENT_QUORUM", "TIER_C_REFUSED",
                        [], proposal, db_path)

    router = model_router or _default_model_router
    reviews: List[Dict[str, Any]] = []
    abstentions: List[Dict[str, str]] = []
    substitutions: List[str] = []

    used_sources = set()
    for role in ROLES:
        review = None
        for _attempt in ("primary", "alternate"):
            try:
                candidate = router(role, proposal)
            except Exception:
                candidate = None
            if candidate:
                review = dict(candidate)
                break

        if review is None:
            if role == "IVARIS":
                review = structural_critic(proposal)
                substitutions.append("IVARIS->structural_critic")
            else:
                abstentions.append({"role": role, "reason": "evidence_or_model_unavailable"})
                continue

        review.setdefault("role", role)
        sid = _source_id(review)
        if not sid:
            abstentions.append({"role": role, "reason": "unidentified_evidence_source"})
            continue
        if sid in used_sources:
            abstentions.append({"role": role, "reason": f"duplicate_source:{sid}"})
            continue
        used_sources.add(sid)
        review["_source_id"] = sid
        reviews.append(review)

    independent = len(reviews)
    ivaris_present = any(r.get("role") == "IVARIS" for r in reviews)
    need = 3

    if independent < need or not ivaris_present:
        quorum = "INSUFFICIENT_QUORUM"
    elif abstentions:
        quorum = "DEGRADED_QUORUM"
    else:
        quorum = "FULL_QUORUM"

    approvals = [r for r in reviews if bool(r.get("approve"))]
    ivaris_veto = any(r.get("role") == "IVARIS" and not bool(r.get("approve"))
                      for r in reviews)
    consensus = quorum != "INSUFFICIENT_QUORUM" and len(approvals) >= need and not ivaris_veto
    base_conf = (sum(float(r.get("confidence") or 0.0) for r in approvals)
                 / len(approvals)) if approvals else 0.0
    confidence = round(base_conf * (0.8 ** min(len(abstentions), 3)), 3)
    verdict = "APPROVED" if consensus else "REJECTED"
    if quorum == "DEGRADED_QUORUM":
        verdict += "_DEGRADED"
    elif quorum == "INSUFFICIENT_QUORUM":
        verdict = "INSUFFICIENT_QUORUM"

    return _persist(pid, risk_tier, reviews, abstentions, consensus, confidence,
                    quorum, verdict, substitutions, proposal, db_path)

def _persist(pid, risk_tier, reviews, abstentions, consensus, confidence,
             quorum, verdict, substitutions, proposal, db_path):
    now = time.time()
    m = _measurements(proposal)
    c = _con(db_path)
    try:
        prev = c.execute("SELECT COALESCE(MAX(attempt_no),0) FROM debate_attempts "
                         "WHERE proposal_id=?", (pid,)).fetchone()[0]
        cur = c.execute(
            "INSERT INTO debate_attempts(proposal_id,attempt_no,started_at,finished_at,"
            "quorum,consensus,confidence,degraded,substitutions,verdict,cooldown_until,"
            "reviews_json,abstentions_json,independent_sources,measured_compile_ok,"
            "measured_diff_chars,risk_tier) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, int(prev)+1, now, now, quorum, int(consensus), confidence,
             int(quorum != "FULL_QUORUM"), json.dumps(substitutions),
             verdict, now + (RETRY_COOLDOWN_SEC if not consensus else 0),
             json.dumps(reviews, default=str), json.dumps(abstentions),
             len(reviews), int(bool(m.get("compile_ok"))) if "compile_ok" in m else None,
             m.get("diff_chars"), str(risk_tier)))
        attempt_id = cur.lastrowid
        c.commit()
    finally:
        c.close()
    return {"consensus": consensus, "confidence": confidence, "quorum": quorum,
            "degraded": quorum != "FULL_QUORUM", "substitutions": substitutions,
            "verdict": verdict, "reviews": reviews, "abstentions": abstentions,
            "attempt_id": attempt_id, "functioning_roles": len(reviews),
            "independent_sources": len(reviews), "needed": 3}
