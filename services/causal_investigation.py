"""
services/causal_investigation.py — INTERNAL-FIRST COUNCIL ORCHESTRATION
=======================================================================
Sequences one organism investigation:

    UNDERSTAND SELF -> COMPARE SELF -> LOCALISE GAP -> SEARCH SELF
      -> RESEARCH EXTERNALLY IF NEEDED -> SYNTHESISE -> ADVERSARIAL REVIEW
      -> TEST -> APPROVAL -> APPLY -> VERIFY

This module ORCHESTRATES. It owns no facts. Source history belongs to the
snapshot tree, telemetry belongs to the runtime DB and logs, Council state
belongs to the existing Council tables, and approval belongs to the existing
Golden Lattice / operator machinery. The only thing stored here is the
investigation's own progress, so it can survive a restart.

That restraint is deliberate. The defect we just repaired was caused by one
boundary quietly substituting its own value for an upstream authority's. A
research layer that mirrors telemetry, source history and approval state into
its own tables would reproduce that failure at a larger scale.

RUNTIME ISOLATION
-----------------
Nothing here may be called from pricing, qualification, MTM, execution, exit
evaluation or wallet reconciliation. Every entry point is safe to fail: if the
Council layer raises, the caller receives a refusal, not an exception, and the
runtime continues on existing doctrine. Historical scans are bounded by file
count and cached by signature so an investigation cannot become a scan storm.
"""

from __future__ import annotations

import difflib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.organism_causal_brief import (
    UNKNOWN, Unknown, Evidence, CausalBrief, CapabilityVerdict,
    is_known, ensure_schema as _ensure_brief_schema, save_brief, load_brief,
    connect_brief_db,
)
from services.internal_capability_index import (
    InternalCapabilityIndex, classify_mechanism,
)

__all__ = [
    "InvestigationState", "Investigation", "HistoricalDifferential",
    "open_investigation", "run_internal_first", "request_external_research",
    "adversarial_review", "record_external_finding", "council_telemetry",
    "ensure_schema",
]


class InvestigationState:
    OPEN = "OPEN"
    INTERNAL_ANALYSIS = "INTERNAL_ANALYSIS"
    INTERNAL_DONE = "INTERNAL_DONE"
    EXTERNAL_REQUESTED = "EXTERNAL_REQUESTED"
    SYNTHESISED = "SYNTHESISED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    CLOSED_HYPOTHESIS_FAILED = "CLOSED_HYPOTHESIS_FAILED"
    CLOSED_REPAIRED_INTERNALLY = "CLOSED_REPAIRED_INTERNALLY"
    ALL = (OPEN, INTERNAL_ANALYSIS, INTERNAL_DONE, EXTERNAL_REQUESTED,
           SYNTHESISED, REVIEW_REJECTED, NEEDS_APPROVAL,
           CLOSED_HYPOTHESIS_FAILED, CLOSED_REPAIRED_INTERNALLY)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS council_investigations (
    investigation_id TEXT PRIMARY KEY,
    brief_id         TEXT NOT NULL,
    capability       TEXT NOT NULL,
    state            TEXT NOT NULL,
    created_at       REAL,
    updated_at       REAL,
    last_note        TEXT,
    payload_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ci_state ON council_investigations(state, updated_at);

CREATE TABLE IF NOT EXISTS council_external_findings (
    finding_id       TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL,
    brief_id         TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    commit_sha       TEXT,
    licence          TEXT,
    mechanism        TEXT NOT NULL,
    addresses_gap    TEXT,
    classification   TEXT NOT NULL,
    conflicts        TEXT,
    assumptions      TEXT,
    risks            TEXT,
    evidence_quality TEXT,
    is_native_truth  INTEGER NOT NULL DEFAULT 0,
    created_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_cef_inv ON council_external_findings(investigation_id);
"""


def ensure_schema(path: Optional[str] = None) -> None:
    _ensure_brief_schema(path)
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# HISTORICAL DIFFERENTIAL — healthy state -> breakpoint -> changed code
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HistoricalDifferential:
    """Reasons over dated source snapshots to localise a regression.

    Answers: when did behaviour change, what source changed in that interval,
    which changed components can causally REACH the symptom, and which cannot.

    Correlation is never promoted to causation here. A file that changed in the
    breakpoint interval is a SUSPECT. It becomes a candidate cause only if a
    reachability argument connects it to the symptom, and even then the output
    is a hypothesis with a falsification condition attached.
    """
    snapshot_root: str
    ordered_snapshots: List[str] = field(default_factory=list)
    max_files_per_pair: int = 60

    def _snap_dir(self, name: str) -> str:
        return os.path.join(self.snapshot_root, name)

    def discover_snapshots(self) -> List[str]:
        if self.ordered_snapshots:
            return self.ordered_snapshots
        try:
            names = sorted(d for d in os.listdir(self.snapshot_root)
                           if os.path.isdir(os.path.join(self.snapshot_root, d)))
        except OSError:
            return []
        self.ordered_snapshots = names
        return names

    def changed_files(self, older: str, newer: str) -> Any:
        """Files differing between two snapshots, or UNKNOWN if unreadable."""
        a, b = self._snap_dir(older), self._snap_dir(newer)
        if not (os.path.isdir(a) and os.path.isdir(b)):
            return Unknown(f"snapshot_missing:{older if not os.path.isdir(a) else newer}")
        changed, seen = [], 0
        for dirpath, _dn, filenames in os.walk(b):
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                seen += 1
                if seen > self.max_files_per_pair:
                    break
                nb = os.path.join(dirpath, fn)
                rel = os.path.relpath(nb, b)
                na = os.path.join(a, rel)
                if not os.path.exists(na):
                    changed.append({"path": rel.replace("\\", "/"), "kind": "ADDED"})
                    continue
                try:
                    ta = open(na, "r", encoding="utf-8", errors="replace").read()
                    tb = open(nb, "r", encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if ta != tb:
                    added = sum(1 for ln in difflib.unified_diff(
                        ta.splitlines(), tb.splitlines(), n=0) if ln.startswith("+")
                        and not ln.startswith("+++"))
                    removed = sum(1 for ln in difflib.unified_diff(
                        ta.splitlines(), tb.splitlines(), n=0) if ln.startswith("-")
                        and not ln.startswith("---"))
                    changed.append({"path": rel.replace("\\", "/"), "kind": "MODIFIED",
                                    "added": added, "removed": removed})
        return changed

    def find_breakpoint(self, health_by_snapshot: Dict[str, Any]) -> Any:
        """Locate the first snapshot pair where a health metric deteriorates.

        health_by_snapshot maps snapshot name -> numeric health (higher is
        better) or UNKNOWN. Snapshots with UNKNOWN health are skipped rather
        than assumed healthy — a gap in observation is not a clean bill.
        """
        snaps = self.discover_snapshots()
        usable = [s for s in snaps if is_known(health_by_snapshot.get(s, UNKNOWN))]
        if len(usable) < 2:
            return Unknown("insufficient_health_observations")
        for older, newer in zip(usable, usable[1:]):
            if float(health_by_snapshot[newer]) < float(health_by_snapshot[older]):
                return {"older": older, "newer": newer,
                        "health_before": float(health_by_snapshot[older]),
                        "health_after": float(health_by_snapshot[newer]),
                        "skipped_unobserved": [s for s in snaps if s not in usable]}
        return Unknown("no_deterioration_observed")

    def localise(self, breakpoint: Dict[str, str],
                 symptom_tokens: Sequence[str]) -> Dict[str, Any]:
        """Split changed files into can-reach-symptom and cannot-reach.

        Reachability here is lexical: does the changed file mention the
        vocabulary of the symptom. That is a weak proxy and is labelled as
        such — a REACHABLE verdict is a reason to look, never a conclusion.
        """
        changed = self.changed_files(breakpoint["older"], breakpoint["newer"])
        if not is_known(changed):
            return {"reachable": UNKNOWN, "unreachable": UNKNOWN, "changed": changed}
        toks = {t.lower() for t in symptom_tokens if len(t) > 2}
        reach, no_reach = [], []
        newer_dir = self._snap_dir(breakpoint["newer"])
        for entry in changed:
            p = os.path.join(newer_dir, entry["path"])
            try:
                text = open(p, "r", encoding="utf-8", errors="replace").read().lower()
            except OSError:
                entry["reachability"] = "UNKNOWN"
                no_reach.append(entry)
                continue
            hits = sorted(t for t in toks if t in text)
            if hits:
                entry["reachability"] = "REACHABLE"
                entry["symptom_tokens_present"] = hits
                reach.append(entry)
            else:
                entry["reachability"] = "NO_LEXICAL_PATH"
                no_reach.append(entry)
        reach.sort(key=lambda e: -len(e.get("symptom_tokens_present", [])))
        return {"reachable": reach, "unreachable": no_reach, "changed": changed,
                "method": "lexical_reachability_proxy",
                "caveat": ("lexical reachability is a search heuristic, not a "
                           "causal proof; a REACHABLE file is a suspect only")}


# ─────────────────────────────────────────────────────────────────────────────
# INVESTIGATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Investigation:
    investigation_id: str
    brief: CausalBrief
    state: str = InvestigationState.OPEN
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_note: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)

    def transition(self, new_state: str, note: str = "") -> "Investigation":
        if new_state not in InvestigationState.ALL:
            raise ValueError(f"unknown investigation state {new_state!r}")
        self.history.append({"from": self.state, "to": new_state,
                             "at": time.time(), "note": note})
        self.state = new_state
        self.last_note = note
        self.updated_at = time.time()
        return self

    def to_payload(self) -> str:
        return json.dumps({"history": self.history}, default=str)


def _save(inv: Investigation, path: Optional[str] = None) -> None:
    save_brief(inv.brief, path)
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO council_investigations "
            "(investigation_id, brief_id, capability, state, created_at, updated_at, last_note, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(investigation_id) DO UPDATE SET state=excluded.state, "
            "updated_at=excluded.updated_at, last_note=excluded.last_note, "
            "payload_json=excluded.payload_json",
            (inv.investigation_id, inv.brief.brief_id, inv.brief.capability,
             inv.state, inv.created_at, inv.updated_at, inv.last_note,
             inv.to_payload()))
        conn.commit()
    finally:
        conn.close()


def open_investigation(capability: str, symptom: str,
                       telemetry: Any = UNKNOWN,
                       invariants: Optional[Sequence[str]] = None,
                       path: Optional[str] = None) -> Investigation:
    ensure_schema(path)
    brief = CausalBrief(capability=capability, symptom=symptom)
    brief.telemetry = telemetry
    brief.invariants = list(invariants or [])
    if is_known(telemetry):
        brief.add_evidence(Evidence(
            claim=f"runtime symptom observed: {symptom}",
            origin="runtime_telemetry", confidence="HIGH",
            detail=telemetry if isinstance(telemetry, dict) else {"value": telemetry}))
    inv = Investigation(investigation_id=uuid.uuid4().hex[:16], brief=brief)
    inv.transition(InvestigationState.OPEN, f"opened for {capability}")
    _save(inv, path)
    return inv


def resume_investigation(investigation_id: str,
                         path: Optional[str] = None) -> Optional[Investigation]:
    """Durable state: an investigation survives restart and continues."""
    ensure_schema(path)
    conn = connect_brief_db(path)
    try:
        row = conn.execute(
            "SELECT * FROM council_investigations WHERE investigation_id=?",
            (investigation_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    brief = load_brief(row["brief_id"], path)
    if brief is None:
        return None
    inv = Investigation(investigation_id=row["investigation_id"], brief=brief,
                        state=row["state"], created_at=row["created_at"] or 0.0,
                        updated_at=row["updated_at"] or 0.0,
                        last_note=row["last_note"] or "")
    try:
        inv.history = (json.loads(row["payload_json"] or "{}") or {}).get("history", [])
    except Exception:
        inv.history = []
    return inv


def run_internal_first(inv: Investigation,
                       mechanism_description: str,
                       index: Optional[InternalCapabilityIndex] = None,
                       differential: Optional[HistoricalDifferential] = None,
                       health_by_snapshot: Optional[Dict[str, Any]] = None,
                       symptom_tokens: Optional[Sequence[str]] = None,
                       path: Optional[str] = None) -> Investigation:
    """UNDERSTAND SELF -> COMPARE SELF -> LOCALISE GAP -> SEARCH SELF."""
    inv.transition(InvestigationState.INTERNAL_ANALYSIS, "examining the organism")
    b = inv.brief

    # SEARCH SELF
    verdict, evidence, detail = classify_mechanism(
        mechanism_description, index=index, invariants=b.invariants)
    for e in evidence:
        b.add_evidence(e)
    b.internal_verdict = verdict
    b.current_implementation = detail if is_known(detail) else detail

    if is_known(detail) and isinstance(detail, dict) and detail.get("symbol"):
        b.owning_code = [detail["symbol"]]
    elif not is_known(b.owning_code):
        b.owning_code = Unknown("no_owning_symbol_identified")

    # COMPARE SELF — historical differential, only if snapshots were supplied
    if differential is not None and health_by_snapshot:
        bp = differential.find_breakpoint(health_by_snapshot)
        if is_known(bp):
            b.healthy_reference = {"snapshot": bp["older"],
                                   "health": bp["health_before"]}
            b.add_evidence(Evidence(
                claim=f"health deteriorates between {bp['older']} and {bp['newer']}",
                origin="historical_differential.find_breakpoint",
                confidence="HIGH", detail=bp))
            loc = differential.localise(bp, symptom_tokens or [])
            if is_known(loc.get("reachable")):
                b.recent_changes = loc
                top = loc["reachable"][:3]
                if top:
                    b.owning_code = [t["path"] for t in top]
                    b.add_evidence(Evidence(
                        claim=("changed files in the breakpoint interval that can "
                               "lexically reach the symptom: "
                               + ", ".join(t["path"] for t in top)),
                        origin="historical_differential.localise",
                        confidence="MEDIUM", detail=loc["caveat"]))
                    b.add_hypothesis(
                        statement=(f"a change to {top[0]['path']} in the "
                                   f"{bp['older']}->{bp['newer']} interval causes: {b.symptom}"),
                        supporting=[f"file changed in breakpoint interval",
                                    f"symptom vocabulary present in file"],
                        competing=["market regime change",
                                   "infrastructure/provider degradation",
                                   "population quality shift",
                                   "a change in an unobserved component"],
                        falsification=("symptom persists at the same rate after "
                                       "reverting or repairing this change, in a "
                                       "window where the component is verified healthy"),
                        rollback="revert the single changed function and re-measure")
        else:
            b.recent_changes = bp   # UNKNOWN with a reason

    b.confidence = b.evidence_quality()
    inv.transition(InvestigationState.INTERNAL_DONE,
                   f"internal verdict={verdict} evidence={b.confidence}")
    _save(inv, path)
    return inv


def request_external_research(inv: Investigation,
                              path: Optional[str] = None) -> Tuple[bool, str, Any]:
    """The gate. External reconnaissance must answer a measured internal
    question, and refusal is the default."""
    b = inv.brief
    allowed, reason = b.may_search_externally()
    question = b.derive_external_question()
    if not allowed:
        if b.internal_verdict == CapabilityVerdict.ALREADY_HAVE:
            inv.transition(InvestigationState.CLOSED_REPAIRED_INTERNALLY,
                           f"no expedition: {reason}")
        else:
            inv.transition(InvestigationState.INTERNAL_DONE,
                           f"expedition refused: {reason}")
        _save(inv, path)
        return False, reason, question
    b.state = "EXTERNAL_REQUESTED"
    inv.transition(InvestigationState.EXTERNAL_REQUESTED, reason)
    _save(inv, path)
    return True, reason, question


def record_external_finding(inv: Investigation, *, source_url: str,
                            mechanism: str, commit_sha: str = "",
                            licence: str = "", addresses_gap: str = "",
                            assumptions: str = "", risks: str = "",
                            index: Optional[InternalCapabilityIndex] = None,
                            path: Optional[str] = None) -> Dict[str, Any]:
    """External findings retain provenance and can never become native truth.

    is_native_truth is hard-wired to 0. There is no code path in this module
    that sets it to 1. A foreign mechanism is evidence about the world, not a
    fact about Sentinuity, and the column exists so that any future attempt to
    blur that is visible in the schema.
    """
    if not str(source_url or "").strip():
        raise ValueError("an external finding requires a source URL — provenance is mandatory")
    verdict, evidence, detail = classify_mechanism(
        mechanism, index=index, invariants=inv.brief.invariants)
    for e in evidence:
        e.external = False          # the classification is OUR reasoning
        inv.brief.add_evidence(e)
    inv.brief.add_evidence(Evidence(
        claim=f"external mechanism reported: {mechanism[:200]}",
        origin=f"github:{source_url}@{commit_sha or 'HEAD'}",
        confidence="LOW", external=True,
        detail={"licence": licence, "assumptions": assumptions, "risks": risks}))

    finding = {
        "finding_id": uuid.uuid4().hex[:16],
        "investigation_id": inv.investigation_id,
        "brief_id": inv.brief.brief_id,
        "source_url": source_url, "commit_sha": commit_sha, "licence": licence,
        "mechanism": mechanism, "addresses_gap": addresses_gap,
        "classification": verdict,
        "conflicts": json.dumps(detail, default=str) if is_known(detail) else "",
        "assumptions": assumptions, "risks": risks,
        "evidence_quality": inv.brief.evidence_quality(),
        "is_native_truth": 0,
        "created_at": time.time(),
    }
    conn = connect_brief_db(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO council_external_findings "
            "(finding_id, investigation_id, brief_id, source_url, commit_sha, licence, "
            " mechanism, addresses_gap, classification, conflicts, assumptions, risks, "
            " evidence_quality, is_native_truth, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(finding[k] for k in (
                "finding_id", "investigation_id", "brief_id", "source_url",
                "commit_sha", "licence", "mechanism", "addresses_gap",
                "classification", "conflicts", "assumptions", "risks",
                "evidence_quality", "is_native_truth", "created_at")))
        conn.commit()
    finally:
        conn.close()
    inv.transition(InvestigationState.SYNTHESISED,
                   f"external finding classified {verdict}")
    _save(inv, path)
    return finding


def adversarial_review(inv: Investigation,
                       proposal: Dict[str, Any],
                       path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Ivaris. Actively tries to stop the proposal.

    A proposal survives only if it clears every objection. This is not a vote
    and it is not a score — any single upheld objection is a veto, because the
    failure mode we are guarding against is six agents agreeing with Polaris.
    """
    objections: List[str] = []
    b = inv.brief

    if b.evidence_quality() == "UNSUPPORTED":
        objections.append("UNSUPPORTED_EVIDENCE: no internal evidence anchors this proposal")

    if not b.hypotheses:
        objections.append("NO_CAUSAL_HYPOTHESIS: proposal has no stated cause to falsify")
    else:
        live = [h for h in b.hypotheses if h["status"] != "FAILED"]
        if not live:
            objections.append("ALL_HYPOTHESES_FAILED: every stated cause has been refuted")
        for h in live:
            if not h.get("competing"):
                objections.append(
                    f"NO_COMPETING_EXPLANATION: hypothesis {h['id']} considers no alternative")

    if b.internal_verdict == CapabilityVerdict.ALREADY_HAVE:
        objections.append("DUPLICATE_AUTHORITY: Sentinuity already implements this; "
                          "repair or reconnect rather than add")
    if b.internal_verdict == CapabilityVerdict.CONTRADICTS_EXISTING:
        objections.append("INVARIANT_CONFLICT: proposal contradicts a stated invariant")
    if b.internal_verdict == CapabilityVerdict.UNSUPPORTED:
        objections.append("UNCLASSIFIED: internal capability search could not "
                          "establish whether this is novel")

    # An internally-observed SYMPTOM does not license an externally-sourced
    # CURE. This checks for internal evidence about the MECHANISM specifically:
    # a code reference, or output from the internal capability search. Without
    # that, the proposal is a foreign idea wearing our telemetry as a costume.
    def _is_mechanism_evidence(e) -> bool:
        if e.external:
            return False
        origin = str(e.origin or "")
        return ("internal_capability_index" in origin
                or ".py:" in origin
                or origin.startswith("historical_differential"))

    mechanism_evidence = [e for e in b.evidence if _is_mechanism_evidence(e)]
    if b.evidence and not mechanism_evidence:
        objections.append(
            "EXTERNAL_ONLY_MECHANISM: the symptom is observed internally but "
            "nothing internal supports this particular remedy")

    if not str(proposal.get("falsification") or "").strip():
        objections.append("NO_FALSIFICATION: proposal cannot be shown to have failed")
    if not str(proposal.get("rollback") or "").strip():
        objections.append("NO_ROLLBACK: proposal has no defined reversal")

    touches = [str(t) for t in (proposal.get("touches") or [])]
    hot = [t for t in touches if any(k in t.lower() for k in
           ("execution_engine", "live_trading", "wallet", "price_truth", "oracle"))]
    if hot and not proposal.get("operator_approval_required"):
        objections.append(
            f"AUTHORITY_ESCALATION: touches consequential runtime ({', '.join(hot)}) "
            f"without requiring operator approval")

    passed = not objections
    inv.transition(
        InvestigationState.NEEDS_APPROVAL if passed else InvestigationState.REVIEW_REJECTED,
        "adversarial review passed" if passed else f"{len(objections)} objection(s) upheld")
    _save(inv, path)
    return passed, objections


# ─────────────────────────────────────────────────────────────────────────────
# OBSERVABILITY — counts of work, never implied intelligence
# ─────────────────────────────────────────────────────────────────────────────

def council_telemetry(path: Optional[str] = None, stale_after_sec: float = 86400.0) -> dict:
    """Structured counts. Deliberately contains no metric that could be read as
    'the Council is working well' merely because agents were active."""
    ensure_schema(path)
    conn = connect_brief_db(path)
    now = time.time()
    try:
        states = {r["state"]: r["n"] for r in conn.execute(
            "SELECT state, COUNT(*) n FROM council_investigations GROUP BY state")}
        cls = {r["classification"]: r["n"] for r in conn.execute(
            "SELECT classification, COUNT(*) n FROM council_external_findings "
            "GROUP BY classification")}
        native = conn.execute(
            "SELECT COUNT(*) n FROM council_external_findings WHERE is_native_truth=1"
        ).fetchone()["n"]
        stale = conn.execute(
            "SELECT COUNT(*) n FROM council_investigations WHERE updated_at < ? "
            "AND state NOT LIKE 'CLOSED%'", (now - stale_after_sec,)).fetchone()["n"]
        briefs = conn.execute(
            "SELECT COUNT(*) n FROM organism_causal_briefs").fetchone()["n"]
        failed = 0
        for r in conn.execute("SELECT payload_json FROM organism_causal_briefs"):
            try:
                d = json.loads(r["payload_json"] or "{}")
                failed += sum(1 for h in (d.get("hypotheses") or [])
                              if h.get("status") == "FAILED")
            except Exception:
                continue
    finally:
        conn.close()
    return {
        "causal_briefs_created": briefs,
        "investigations_by_state": states,
        "internal_first_completed": sum(
            states.get(s, 0) for s in (
                InvestigationState.INTERNAL_DONE,
                InvestigationState.EXTERNAL_REQUESTED,
                InvestigationState.SYNTHESISED,
                InvestigationState.NEEDS_APPROVAL,
                InvestigationState.REVIEW_REJECTED,
                InvestigationState.CLOSED_REPAIRED_INTERNALLY)),
        "progressed_to_external_research": sum(
            states.get(s, 0) for s in (InvestigationState.EXTERNAL_REQUESTED,
                                       InvestigationState.SYNTHESISED)),
        "closed_without_external_research": states.get(
            InvestigationState.CLOSED_REPAIRED_INTERNALLY, 0),
        "external_findings_by_classification": cls,
        "external_findings_promoted_to_native_truth": native,   # must stay 0
        "rejected_by_adversarial_review": states.get(InvestigationState.REVIEW_REJECTED, 0),
        "awaiting_operator_approval": states.get(InvestigationState.NEEDS_APPROVAL, 0),
        "failed_hypotheses": failed,
        "stale_investigations": stale,
        "note": ("counts of work performed; none of these fields indicates "
                 "profitability or that any improvement has been verified"),
    }
