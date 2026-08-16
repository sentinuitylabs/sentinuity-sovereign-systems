"""
services/organism_causal_brief.py — ORGANISM CAUSAL BRIEF
=========================================================
The durable, machine-readable representation of what the Council currently
believes about one organism problem, and — just as importantly — what it does
NOT know.

DOCTRINE
--------
This module exists because boundary failures can turn unavailable evidence into
false certainty. A representative defect was
not a missing algorithm. It was a boundary that converted "unavailable" into
the number 0.0, after which every downstream consumer reasoned confidently on a
value that had never been observed.

The same failure mode destroys autonomous research: an agent that treats
"no evidence found" as "no problem exists", or "I could not read the file" as
"the file is fine", will produce confident nonsense. So the central object here
is not the Brief — it is UNKNOWN.

UNKNOWN is a singleton sentinel that:
  * is not None, not 0, not False, not ""
  * raises TypeError on arithmetic and on ordering comparisons
  * survives JSON round-trip as {"__unknown__": true, "reason": ...}
  * is falsy ONLY in the sense of `is_known()`, never via truthiness accident

If a field is UNKNOWN it stays UNKNOWN until evidence arrives. Nothing in this
module may downgrade UNKNOWN to a default.

AUTHORITY
---------
This module creates NO new source of truth about the organism. It records
references and provenance to evidence that already lives in the runtime DB,
the log files, or the source tree. Every Evidence carries where it came from
and when it was observed, so a consumer can always go back to the original.

RUNTIME SAFETY
--------------
Nothing here is on the trading hot path. All DB access is read-only for
evidence gathering and bounded-write for the brief itself, on its own
connection with a generous busy timeout so it yields to the trading writer.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

__all__ = [
    "UNKNOWN", "Unknown", "is_known", "unwrap_or",
    "Evidence", "CausalBrief", "CapabilityVerdict",
    "ensure_schema", "save_brief", "load_brief", "list_briefs",
]


# ─────────────────────────────────────────────────────────────────────────────
# UNKNOWN — the whole point of this module
# ─────────────────────────────────────────────────────────────────────────────

class Unknown:
    """Absence of evidence. Not zero, not healthy, not broken, not confirmed.

    Deliberately hostile to accidental coercion. If a caller tries to do
    arithmetic or ordering on an UNKNOWN, that is a bug in the caller and it
    should fail loudly here rather than silently produce a number — which is
    exactly how a zero-substitution cache defect can propagate.
    """

    _instance: Optional["Unknown"] = None
    __slots__ = ("reason",)

    def __new__(cls, reason: str = "no_evidence"):
        # Singleton for the default reason so `x is UNKNOWN` works.
        if reason == "no_evidence":
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                object.__setattr__(cls._instance, "reason", reason)
            return cls._instance
        obj = super().__new__(cls)
        object.__setattr__(obj, "reason", reason)
        return obj

    def __repr__(self) -> str:
        return f"UNKNOWN({self.reason})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Unknown)

    def __hash__(self) -> int:
        return hash("__UNKNOWN__")

    def _refuse(self, *_a, **_k):
        raise TypeError(
            f"Refusing to operate on UNKNOWN ({self.reason}). Absence of "
            f"evidence must not become a value. Check is_known() first."
        )

    # Arithmetic and ordering are hard errors, by design.
    __add__ = __radd__ = __sub__ = __rsub__ = _refuse
    __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _refuse
    __lt__ = __le__ = __gt__ = __ge__ = _refuse
    __float__ = __int__ = _refuse

    def to_json(self) -> dict:
        return {"__unknown__": True, "reason": self.reason}


UNKNOWN = Unknown()


def is_known(value: Any) -> bool:
    """True only when the value is real evidence."""
    return not isinstance(value, Unknown) and value is not None


def unwrap_or(value: Any, default: Any) -> Any:
    """Explicit, auditable downgrade. Use ONLY where a default is defensible
    and say so at the call site. Never use this to make a report look tidy."""
    return value if is_known(value) else default


def _encode(obj: Any) -> Any:
    if isinstance(obj, Unknown):
        return obj.to_json()
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if obj.get("__unknown__") is True:
            return Unknown(str(obj.get("reason") or "no_evidence"))
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE — nothing enters a brief without provenance
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Evidence:
    """One observation. Where it came from, when, and how much to trust it.

    `origin` is mandatory and free-form but must identify a re-checkable
    source: a table name, a log path, a file:line, a snapshot id. A claim
    without an origin cannot be added to a brief.
    """
    claim: str
    origin: str
    observed_at: float = field(default_factory=time.time)
    confidence: str = "MEDIUM"          # HIGH | MEDIUM | LOW
    external: bool = False              # True = came from outside Sentinuity
    detail: Any = field(default_factory=dict)

    def __post_init__(self):
        if not str(self.origin or "").strip():
            raise ValueError("Evidence requires an origin — provenance is not optional")
        if self.confidence not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError(f"confidence must be HIGH/MEDIUM/LOW, got {self.confidence!r}")


class CapabilityVerdict:
    """Classification of a candidate mechanism against what we already are."""
    ALREADY_HAVE = "ALREADY_HAVE"
    PARTIAL = "PARTIAL"
    COMPLEMENTS_EXISTING = "COMPLEMENTS_EXISTING"
    CONTRADICTS_EXISTING = "CONTRADICTS_EXISTING"
    OBSOLETE = "OBSOLETE"
    NEW = "NEW"
    UNSUPPORTED = "UNSUPPORTED"
    ALL = (ALREADY_HAVE, PARTIAL, COMPLEMENTS_EXISTING,
           CONTRADICTS_EXISTING, OBSOLETE, NEW, UNSUPPORTED)


# ─────────────────────────────────────────────────────────────────────────────
# THE BRIEF
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CausalBrief:
    """Everything the Council currently knows about ONE organism problem.

    Every analytical field defaults to UNKNOWN, not to an empty container that
    could be mistaken for "we looked and found nothing".
    """
    brief_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    capability: str = ""                       # domain under investigation
    symptom: str = ""                          # current runtime symptom
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    state: str = "OPEN"                        # OPEN|INTERNAL_DONE|EXTERNAL_REQUESTED|SYNTHESISED|CLOSED

    telemetry: Any = UNKNOWN                   # supporting metrics
    owning_code: Any = UNKNOWN                 # files/functions that own it
    upstream: Any = UNKNOWN                    # dependencies
    downstream: Any = UNKNOWN                  # blast radius
    recent_changes: Any = UNKNOWN              # changes in the window
    healthy_reference: Any = UNKNOWN           # historical better state
    current_implementation: Any = UNKNOWN
    existing_alternatives: Any = UNKNOWN       # other Sentinuity impls
    invariants: List[str] = field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    internal_verdict: Any = UNKNOWN            # CapabilityVerdict
    external_question: Any = UNKNOWN           # only when justified
    confidence: str = "LOW"

    # ── evidence handling ────────────────────────────────────────────────
    def add_evidence(self, ev: Evidence) -> "CausalBrief":
        if not isinstance(ev, Evidence):
            raise TypeError("only Evidence may be added to a brief")
        self.evidence.append(ev)
        self.updated_at = time.time()
        return self

    def evidence_quality(self) -> str:
        """Derived, never asserted. No evidence means UNSUPPORTED, not LOW."""
        if not self.evidence:
            return "UNSUPPORTED"
        internal = [e for e in self.evidence if not e.external]
        highs = [e for e in internal if e.confidence == "HIGH"]
        if len(highs) >= 2:
            return "HIGH"
        if internal:
            return "MEDIUM"
        return "LOW"   # external-only evidence never exceeds LOW on its own

    def add_hypothesis(self, statement: str, supporting: List[str],
                       competing: List[str], falsification: str,
                       rollback: str = "") -> "CausalBrief":
        """A hypothesis without a falsification condition is an opinion."""
        if not str(falsification or "").strip():
            raise ValueError("a hypothesis requires a falsification condition")
        self.hypotheses.append({
            "id": uuid.uuid4().hex[:8],
            "statement": statement,
            "supporting": list(supporting),
            "competing": list(competing),
            "falsification": falsification,
            "rollback": rollback,
            "status": "OPEN",           # OPEN|SUPPORTED|FAILED
            "created_at": time.time(),
        })
        self.updated_at = time.time()
        return self

    def fail_hypothesis(self, hyp_id: str, reason: str) -> bool:
        """The system must be able to say HYPOTHESIS FAILED and mean it."""
        for h in self.hypotheses:
            if h["id"] == hyp_id:
                h["status"] = "FAILED"
                h["failed_reason"] = reason
                h["failed_at"] = time.time()
                self.updated_at = time.time()
                return True
        return False

    # ── the external gate ────────────────────────────────────────────────
    def internal_work_complete(self) -> bool:
        """External reconnaissance may not begin until the organism has been
        examined. This is the doctrine, expressed as a predicate."""
        return (
            is_known(self.owning_code)
            and is_known(self.current_implementation)
            and is_known(self.internal_verdict)
            and bool(self.evidence)
        )

    def may_search_externally(self) -> tuple:
        """Returns (allowed, reason). Refusal is the default."""
        if not self.internal_work_complete():
            missing = [n for n, v in (("owning_code", self.owning_code),
                                      ("current_implementation", self.current_implementation),
                                      ("internal_verdict", self.internal_verdict))
                       if not is_known(v)]
            if not self.evidence:
                missing.append("evidence")
            return False, f"INTERNAL_ANALYSIS_INCOMPLETE:missing={','.join(missing)}"
        verdict = self.internal_verdict
        if verdict == CapabilityVerdict.ALREADY_HAVE:
            return False, "ALREADY_HAVE:internal capability exists; repair or reconnect it"
        if verdict == CapabilityVerdict.OBSOLETE:
            return False, "OBSOLETE:capability was deliberately retired"
        if verdict == CapabilityVerdict.CONTRADICTS_EXISTING:
            return False, "CONTRADICTS_EXISTING:would violate an existing invariant"
        if self.evidence_quality() == "UNSUPPORTED":
            return False, "UNSUPPORTED:no internal evidence to anchor a search"
        return True, f"GAP_CONFIRMED:{verdict}"

    def derive_external_question(self) -> Any:
        """Build the gap-driven question, or refuse. Never a generic query."""
        allowed, reason = self.may_search_externally()
        if not allowed:
            self.external_question = Unknown(reason)
            return self.external_question
        impl = unwrap_or(self.current_implementation, "unspecified")
        healthy = ("; historical healthier state: " + str(self.healthy_reference)
                   if is_known(self.healthy_reference) else "")
        inv = ("; invariants that must survive: " + ", ".join(self.invariants)
               if self.invariants else "")
        self.external_question = (
            f"Sentinuity capability '{self.capability}' currently behaves as: "
            f"{self.symptom}. Current implementation: {impl}. "
            f"Internal classification: {self.internal_verdict}{healthy}{inv}. "
            f"Determine whether an external mechanism addresses this measured "
            f"gap without duplicating an existing Sentinuity capability."
        )
        self.updated_at = time.time()
        return self.external_question

    # ── serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [asdict(e) for e in self.evidence]
        d["evidence_quality"] = self.evidence_quality()
        return _encode(d)

    @classmethod
    def from_dict(cls, raw: dict) -> "CausalBrief":
        raw = dict(_decode(raw))
        raw.pop("evidence_quality", None)
        evs = [Evidence(**e) for e in (raw.pop("evidence", None) or [])]
        allowed = {f for f in cls.__dataclass_fields__}
        brief = cls(**{k: v for k, v in raw.items() if k in allowed})
        brief.evidence = evs
        return brief


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE — additive table, own connection, yields to the trading writer
# ─────────────────────────────────────────────────────────────────────────────

def _db_path() -> str:
    for env in ("SENTINUITY_DB", "MATRIX_DB", "DB_PATH"):
        v = os.getenv(env, "").strip()
        if v:
            return v
    try:
        from core.schema import DB_PATH  # type: ignore
        return str(DB_PATH)
    except Exception:
        return os.path.join(os.getcwd(), "sentinuity_matrix.db")


def connect_brief_db(path: Optional[str] = None) -> sqlite3.Connection:
    # Council persistence is non-runtime work. Use a short acquisition window so
    # Council work yields quickly when the trading writer owns SQLite.
    conn = sqlite3.connect(path or _db_path(), timeout=0.25)
    conn.row_factory = sqlite3.Row
    # Council work must never make trading wait. A long busy timeout means we
    # queue behind the trading writer rather than competing with it.
    conn.execute("PRAGMA busy_timeout=250")
    return conn


# Backward-compatible private alias for any older caller; new code uses the public accessor.
_connect = connect_brief_db


_SCHEMA = """
CREATE TABLE IF NOT EXISTS organism_causal_briefs (
    brief_id      TEXT PRIMARY KEY,
    capability    TEXT NOT NULL,
    symptom       TEXT,
    state         TEXT NOT NULL DEFAULT 'OPEN',
    confidence    TEXT,
    created_at    REAL,
    updated_at    REAL,
    payload_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ocb_state ON organism_causal_briefs(state, updated_at);
CREATE INDEX IF NOT EXISTS idx_ocb_cap   ON organism_causal_briefs(capability, updated_at);
"""


def ensure_schema(path: Optional[str] = None) -> None:
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def save_brief(brief: CausalBrief, path: Optional[str] = None) -> str:
    brief.updated_at = time.time()
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO organism_causal_briefs "
            "(brief_id, capability, symptom, state, confidence, created_at, updated_at, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(brief_id) DO UPDATE SET capability=excluded.capability, "
            "symptom=excluded.symptom, state=excluded.state, confidence=excluded.confidence, "
            "updated_at=excluded.updated_at, payload_json=excluded.payload_json",
            (brief.brief_id, brief.capability, brief.symptom, brief.state,
             brief.evidence_quality(), brief.created_at, brief.updated_at,
             json.dumps(brief.to_dict(), default=str)))
        conn.commit()
        return brief.brief_id
    finally:
        conn.close()


def load_brief(brief_id: str, path: Optional[str] = None) -> Optional[CausalBrief]:
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT payload_json FROM organism_causal_briefs WHERE brief_id=?",
            (brief_id,)).fetchone()
        if not row:
            return None
        return CausalBrief.from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


def list_briefs(state: Optional[str] = None, limit: int = 50,
                path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        if state:
            rows = conn.execute(
                "SELECT brief_id, capability, symptom, state, confidence, updated_at "
                "FROM organism_causal_briefs WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                (state, int(limit))).fetchall()
        else:
            rows = conn.execute(
                "SELECT brief_id, capability, symptom, state, confidence, updated_at "
                "FROM organism_causal_briefs ORDER BY updated_at DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
