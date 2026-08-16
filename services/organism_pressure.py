# coding: utf-8
"""
services/organism_pressure.py — ORGANISM_PRESSURE_20260813

Read-only pressure model. Derives each faculty's state — and specifically its
MISSING SENSES — from telemetry the organism already emits.

WHY MISSING SENSES ARE THE PRIMARY OUTPUT
=========================================
`smart_money_coverage` has been recording, on every evaluation, exactly which
evidence components were observed and which were missing. In the latest runtime
79/79 evaluations observed only `wallet_cluster` — 20 of 100 weight — leaving
coverage below the 0.35 floor so `compute_metrics()` returned None and every
candidate was admitted with `sm=NOT_MEASURED`.

That sensor was working perfectly and no surface displayed it. A faculty that
cannot perceive is not a warning to be dismissed; it is the shape of the
organism's blindness, and it should be the most visible thing on screen.

So this module reports, per faculty:
    senses_present / senses_absent  — named, with the reason each is absent
    perception                      — fraction of intended evidence observable
    pressure                        — importance x blindness x uncertainty
    evidence                        — the table/rows the state was read from

CONTRACT
========
  * Read-only. No writes, no DDL, no schema creation. Ever.
  * Tier-3 cognition: bounded LIMITs, 1.5s busy timeout, never blocks Tier-0.
  * Every public function fails soft — a DB fault yields UNKNOWN, not an
    exception into the render thread.
  * Contains NO thresholds of its own. Floors are imported from the owning
    service so this module can never drift from the gate it describes.
  * Absence of data is reported as ABSENT, never as zero. A faculty with no
    telemetry is blind, not healthy.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

try:
    from core.schema import get_connection
except Exception:                                    # pragma: no cover
    get_connection = None                            # type: ignore

# Floors are owned elsewhere. Import, never restate.
try:
    from services.smart_money_metrics import (
        _COMPONENT_WEIGHTS as SM_WEIGHTS,
        MIN_COVERAGE_FRACTION as SM_MIN_COVERAGE,
    )
except Exception:                                    # pragma: no cover
    SM_WEIGHTS, SM_MIN_COVERAGE = {}, 0.35

_LIMIT = 400
_WINDOW_S = 10800.0          # 3h — matches the operator's audit window

# Importance is a constitutional judgement about how much trading truth depends
# on a faculty. It is deliberately hard-coded and deliberately small: it is the
# only non-measured input in this module, and it never changes at runtime.
FACULTIES = {
    "EDGE":          {"order": 0, "importance": 1.00, "label": "EDGE"},
    "PRICE_TRUTH":   {"order": 1, "importance": 0.95, "label": "PRICE TRUTH"},
    "SMART_MONEY":   {"order": 2, "importance": 0.85, "label": "SMART MONEY"},
    "EXECUTION":     {"order": 3, "importance": 0.90, "label": "EXECUTION"},
    "COPYTRADE":     {"order": 4, "importance": 0.55, "label": "COPYTRADE"},
    "SUBSTRATE":     {"order": 5, "importance": 0.40, "label": "SUBSTRATE"},
    "INTELLIGENCE":  {"order": 6, "importance": 0.50, "label": "INTELLIGENCE"},
    "COUNCIL":       {"order": 7, "importance": 0.30, "label": "COUNCIL"},
    "OBSERVABILITY": {"order": 8, "importance": 0.45, "label": "OBSERVABILITY"},
}

STATE_ALIVE, STATE_PARTIAL = "ALIVE", "PARTIAL"
STATE_BLIND, STATE_UNKNOWN = "BLIND", "UNKNOWN"


def _conn() -> Optional[sqlite3.Connection]:
    if get_connection is None:
        return None
    try:
        c = get_connection()
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=1500")
        return c
    except Exception:
        return None


def _rows(sql: str, args: tuple = ()) -> List[sqlite3.Row]:
    c = _conn()
    if c is None:
        return []
    try:
        return list(c.execute(sql, args).fetchall())
    except Exception:
        return []


def _faculty(key: str, present: List[Dict], absent: List[Dict],
             evidence: str, note: str = "") -> Dict[str, Any]:
    """Assemble a faculty. Perception is weighted when weights are known."""
    meta = FACULTIES[key]
    tw = sum(s.get("weight", 1) for s in present + absent) or 1
    ow = sum(s.get("weight", 1) for s in present)
    perception = ow / float(tw)

    if not present and not absent:
        state = STATE_UNKNOWN
    elif not present:
        state = STATE_BLIND
    elif absent:
        state = STATE_PARTIAL
    else:
        state = STATE_ALIVE

    # Pressure = importance x blindness. A faculty nothing depends on can be
    # fully dark without being urgent; EDGE cannot be dim without being urgent.
    blindness = 1.0 - perception
    pressure = meta["importance"] * blindness
    if state == STATE_UNKNOWN:
        # Unknown is not healthy. We cannot see the sensor at all, which is
        # strictly worse than knowing a sense is missing.
        pressure = meta["importance"] * 0.75

    return {
        "key": key, "label": meta["label"], "order": meta["order"],
        "importance": meta["importance"], "state": state,
        "perception": round(perception, 4),
        "pressure": round(pressure, 4),
        "senses_present": present, "senses_absent": absent,
        "evidence": evidence, "note": note,
    }


# ── SMART MONEY ─────────────────────────────────────────────────────────────
def _smart_money(now: float) -> Dict[str, Any]:
    """The reference implementation: the sensor already names its own gaps."""
    rows = _rows(
        "SELECT components_observed, components_missing, coverage_pct, measured,"
        " reason FROM smart_money_coverage WHERE ts > ? ORDER BY ts DESC LIMIT ?",
        (now - _WINDOW_S, _LIMIT))
    if not rows:
        return _faculty("SMART_MONEY", [], [], "smart_money_coverage",
                        "No coverage rows in window — the sensor itself is silent.")

    seen: Dict[str, int] = {}
    for r in rows:
        for comp in str(r["components_observed"] or "").split(","):
            comp = comp.strip()
            if comp:
                seen[comp] = seen.get(comp, 0) + 1

    n = len(rows)
    measured = sum(1 for r in rows if int(r["measured"] or 0) == 1)
    reasons: Dict[str, int] = {}
    for r in rows:
        rs = str(r["reason"] or "").strip()
        if rs:
            reasons[rs] = reasons.get(rs, 0) + 1
    top_reason = max(reasons.items(), key=lambda x: x[1])[0] if reasons else ""

    present, absent = [], []
    for comp, weight in (SM_WEIGHTS or {}).items():
        hits = seen.get(comp, 0)
        entry = {"name": comp, "weight": weight,
                 "observed_in": hits, "of": n}
        if hits == 0:
            entry["reason"] = "no producer wrote this evidence in the window"
            absent.append(entry)
        elif hits < n:
            entry["reason"] = f"intermittent — present in {hits}/{n}"
            present.append(entry)
        else:
            present.append(entry)

    note = (f"{measured}/{n} evaluations cleared the "
            f"{SM_MIN_COVERAGE:.0%} coverage floor.")
    if top_reason:
        note += f" Most common abort: {top_reason}."
    return _faculty("SMART_MONEY", present, absent,
                    f"smart_money_coverage ({n} rows)", note)


# ── PRICE TRUTH ─────────────────────────────────────────────────────────────
def _price_truth(now: float) -> Dict[str, Any]:
    present, absent = [], []
    rows = _rows(
        "SELECT COALESCE(price_updated_at, timestamp, created_at, 0) AS pts"
        " FROM market_snapshots"
        " WHERE COALESCE(price_updated_at, timestamp, created_at, 0) > ?"
        " ORDER BY pts DESC LIMIT ?", (now - _WINDOW_S, _LIMIT))
    if not rows:
        return _faculty("PRICE_TRUTH", [], [], "market_snapshots",
                        "No timestamped snapshot rows in window.")

    ages = sorted(max(0.0, now - float(r["pts"] or 0)) for r in rows)
    med = ages[len(ages) // 2]
    p90 = ages[min(len(ages) - 1, int(len(ages) * 0.9))]

    def sense(name, ok, weight, why):
        e = {"name": name, "weight": weight}
        (present if ok else absent).append(e)
        if not ok:
            e["reason"] = why
        return e

    sense("recent_marks", True, 25, "")
    sense("median_freshness", med <= 30.0, 25,
          f"median mark age {med:.0f}s exceeds the 30s live gate")
    sense("tail_freshness", p90 <= 60.0, 25,
          f"p90 mark age {p90:.0f}s — long tail of stale observations")
    # Executable route coverage is a distinct sense from mark freshness: a
    # fresh reference price with no executable route is still blindness.
    cov = _rows("SELECT COUNT(*) AS n FROM paper_positions"
                " WHERE status='CLOSED' AND exit_reason LIKE 'NO_COVERAGE%'"
                " AND COALESCE(closed_at,0) > ?", (now - _WINDOW_S,))
    nocov = int(cov[0]["n"]) if cov else 0
    sense("executable_route", nocov == 0, 25,
          f"{nocov} position(s) exited with no executable route")

    return _faculty("PRICE_TRUTH", present, absent,
                    f"market_snapshots ({len(rows)} rows), paper_positions",
                    f"median {med:.0f}s / p90 {p90:.0f}s.")


# ── EDGE ────────────────────────────────────────────────────────────────────
def _edge(now: float) -> Dict[str, Any]:
    rows = _rows(
        "SELECT pnl_pct, peak_pnl_pct, exit_reason FROM paper_positions"
        " WHERE status='CLOSED' AND COALESCE(closed_at,0) > ? LIMIT ?",
        (now - _WINDOW_S, _LIMIT))
    if not rows:
        return _faculty("EDGE", [], [], "paper_positions",
                        "No closes in window.")

    n = len(rows)
    runners = sum(1 for r in rows if float(r["peak_pnl_pct"] or 0) >= 24.0)
    # A NEGATIVE peak means the position never traded above entry, not once.
    # That is an entry-basis signal, not a harvesting signal, and it is the
    # single most diagnostic number available about population quality.
    neg_peak = sum(1 for r in rows if float(r["peak_pnl_pct"] or 0) < 0)
    stagnant = sum(1 for r in rows
                   if "TIME_CUT_STAGNANT" in str(r["exit_reason"] or ""))

    present, absent = [], []
    for name, ok, w, why in (
        ("runner_incidence", runners > 0, 30,
         "no position reached a 24% peak in the window"),
        ("positive_excursion", neg_peak < n * 0.5, 40,
         f"{neg_peak}/{n} positions never traded above entry"),
        ("movement", stagnant < n * 0.5, 30,
         f"{stagnant}/{n} exited stagnant — the population is not moving"),
    ):
        e = {"name": name, "weight": w}
        if ok:
            present.append(e)
        else:
            e["reason"] = why
            absent.append(e)

    return _faculty("EDGE", present, absent, f"paper_positions ({n} closes)",
                    f"{runners} runner(s), {stagnant} stagnant of {n}.")


# ── remaining faculties ─────────────────────────────────────────────────────
def _table_backed(key: str, now: float, probes) -> Dict[str, Any]:
    """Generic: each probe is (sense_name, weight, sql, args, predicate, why)."""
    present, absent = [], []
    ev = []
    for name, weight, sql, args, pred, why in probes:
        rows = _rows(sql, args)
        ev.append(sql.split("FROM")[-1].strip().split()[0] if "FROM" in sql else "")
        e = {"name": name, "weight": weight}
        try:
            ok = pred(rows)
        except Exception:
            ok = False
        if ok:
            present.append(e)
        else:
            e["reason"] = why
            absent.append(e)
    return _faculty(key, present, absent, ", ".join(sorted({x for x in ev if x})))


def _nonempty(rows) -> bool:
    return bool(rows) and int(rows[0][0] or 0) > 0


def snapshot(now: Optional[float] = None) -> Dict[str, Any]:
    """Full organism state. Safe to call when every table is empty."""
    now = now or time.time()
    w = now - _WINDOW_S
    facs = [_edge(now), _price_truth(now), _smart_money(now)]

    facs.append(_table_backed("EXECUTION", now, [
        ("opens", 40, "SELECT COUNT(*) FROM paper_positions WHERE COALESCE(opened_at,0)>?",
         (w,), _nonempty, "no positions opened in the window"),
        ("live_path", 30, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'",
         (), lambda r: True, ""),
        ("closes", 30, "SELECT COUNT(*) FROM paper_positions WHERE COALESCE(closed_at,0)>?",
         (w,), _nonempty, "no closes recorded in the window"),
    ]))
    facs.append(_table_backed("COPYTRADE", now, [
        ("wallet_observations", 50,
         "SELECT COUNT(*) FROM wallet_pattern_observations WHERE observed_at>?",
         (w,), _nonempty, "no wallet observations written in the window"),
        ("actor_independence", 50, "SELECT 0", (), lambda r: False,
         "convergence counts DISTINCT addresses; funding ancestry is not modelled, "
         "so four addresses cannot be distinguished from four actors"),
    ]))
    facs.append(_table_backed("SUBSTRATE", now, [
        ("experiments", 100, "SELECT COUNT(*) FROM substrate_positions", (),
         _nonempty, "no substrate experiments present"),
    ]))
    facs.append(_table_backed("INTELLIGENCE", now, [
        ("synthesis", 100, "SELECT COUNT(*) FROM inspiration_intake_ledger", (),
         _nonempty, "no inspirations captured"),
    ]))
    facs.append(_table_backed("COUNCIL", now, [
        ("discovery", 34, "SELECT COUNT(*) FROM github_discovery_ledger", (),
         _nonempty, "no discoveries recorded"),
        ("judgement", 33, "SELECT COUNT(*) FROM polaris_proposals WHERE status='APPROVED'",
         (), _nonempty, "no Polaris judgement has completed"),
        ("retrospective", 33, "SELECT COUNT(*) FROM build_retrospective", (),
         _nonempty, "no runtime retrospective has closed the loop"),
    ]))
    facs.append(_table_backed("OBSERVABILITY", now, [
        ("coverage_sensor", 50, "SELECT COUNT(*) FROM smart_money_coverage WHERE ts>?",
         (w,), _nonempty, "coverage sensor silent"),
        ("candidate_funnel", 50,
         "SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(price_updated_at,timestamp,created_at,0)>?",
         (w,), _nonempty, "candidate funnel has no timestamped rows in the window"),
    ]))

    facs.sort(key=lambda f: f["order"])
    ranked = sorted(facs, key=lambda f: -f["pressure"])
    blind = [f for f in facs if f["state"] in (STATE_BLIND, STATE_UNKNOWN)]
    return {
        "at": now,
        "faculties": facs,
        "ranked": ranked,
        "top_pressure": ranked[0] if ranked else None,
        "blind_count": len(blind),
        "window_s": _WINDOW_S,
        "any_evidence": any(f["state"] != STATE_UNKNOWN for f in facs),
    }


def leading_question(snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Turn the highest pressure into a Quest Contract.

    A quest originates from a QUESTION about a measured weakness — never from
    curiosity. The destination is derived from the evidence needed, so the
    highest-pressure question today resolves to RUNTIME/CODE, not GitHub.
    """
    snap = snap or snapshot()
    top = snap.get("top_pressure")
    if not top:
        return {}
    absent = top["senses_absent"]
    names = ", ".join(s["name"] for s in absent[:4]) or "—"

    if top["state"] == STATE_UNKNOWN:
        dest = ["RUNTIME", "CODE"]
    elif absent and any("producer" in (s.get("reason") or "") for s in absent):
        dest = ["CODE", "RUNTIME"]
    else:
        dest = ["RUNTIME", "HISTORY"]

    return {
        "faculty": top["key"], "label": top["label"],
        "pressure": {
            "text": (f"{top['label']} perceives {top['perception']:.0%} of its "
                     f"intended evidence."),
            "measured_from": top["evidence"],
        },
        "question": (f"Why is {top['label']} unable to observe {names}?"
                     if absent else
                     f"What is limiting {top['label']}?"),
        "current_evidence": top["note"] or top["evidence"],
        "knowledge_required": [
            f"which producer is expected to write {s['name']}" for s in absent[:4]
        ] or ["a measurement that separates cause from symptom"],
        "destinations": dest,
        "success_condition": (
            "the absent evidence is either produced, or proven unproducible "
            "with a named reason"),
        "kill_condition": (
            "the cause is found to lie outside this faculty, or two "
            "expeditions return without new evidence"),
        "territory": "observability / producer services — no trading policy",
    }
