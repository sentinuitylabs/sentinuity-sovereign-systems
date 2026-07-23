# coding: utf-8
"""
services/world_narrative_engine.py — SOVEREIGN_WORLD_UPGRADE_20260723

Translates REAL backend state into world meaning. Three jobs:

1. evaluate_chapter(): probes actual evidence (files, tables, heartbeats,
   council rows, verifier results) and advances the Intelligence Institute
   AT MOST ONE stage per pass — only when the next stage's evidence exists.
   Time alone never advances anything. Stages 8-10 (TESTING/VERIFIED/
   PAPER_READY) require explicit test/review/audit rows and therefore
   cannot be fabricated by this engine.

2. derive_agent_states(): maps active council_world_tasks rows to canonical
   agent action states and buildings. Idle agents return home. Collaboration
   groups exist only while a shared gather-phase task is in progress.

3. ambient_signals(): maps ambient NPCs to real maintenance signals,
   maximum three active at once.

Write discipline: this module writes ONLY to world_* tables (via
world_build_state). Reads of trading/telemetry tables are read-only.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.world_build_state import (
    ROOT, DB_PATH, STAGES, STAGE_INDEX, CHAPTER, AGENT_HOMES, GATHER_PHASES,
    canonical_location, ensure_schema, get_building, advance_stage,
    record_event, upsert_agent, _connect,
)

INSTITUTE = "intelligence_institute"

PHASE_TO_ACTION = {
    "DISCOVER": "DISCOVER", "RESEARCH": "RESEARCH", "DEBATE": "DEBATE",
    "PLAN": "PLAN", "ACQUIRE_TOOL": "ACQUIRE_TOOL", "BUILD": "BUILD",
    "TEST": "TEST", "REVIEW": "REVIEW", "APPLY": "APPLY", "VERIFY": "VERIFY",
    "RELEASE": "RELEASE", "BLOCKED": "BLOCKED",
}


def _has_table(c: sqlite3.Connection, t: str) -> bool:
    try:
        c.execute(f"SELECT 1 FROM {t} LIMIT 1")
        return True
    except Exception:
        return False


def _one(c, sql, *p):
    try:
        r = c.execute(sql, p).fetchone()
        return r[0] if r else None
    except Exception:
        return None


# ── Chapter evidence chain ──────────────────────────────────────────────────
def _institute_evidence(c: sqlite3.Connection) -> List[Dict[str, str]]:
    """Ordered evidence checks; index i proves stage i+1 is earned.
    Each entry: {ok, ref, note}. Later stages need explicit human/verifier
    rows — deliberately unfakeable here."""
    ev: List[Dict[str, Any]] = []

    # → 1 PLANNED: a council task for the institute exists
    n = _one(c, "SELECT COUNT(*) FROM council_world_tasks WHERE "
                "building_id='intelligence_institute'") if _has_table(c, "council_world_tasks") else 0
    ev.append({"ok": bool(n), "ref": f"council_world_tasks:institute:{n or 0}",
               "note": "chapter task exists"})

    # → 2 RESEARCHED: inspiration/source research rows exist
    n = _one(c, "SELECT COUNT(*) FROM inspiration_intake_ledger") \
        if _has_table(c, "inspiration_intake_ledger") else 0
    ev.append({"ok": bool(n), "ref": f"inspiration_intake_ledger:rows={n or 0}",
               "note": "legal source research recorded"})

    # → 3 SPECIFIED: an approved plan/proposal referencing intelligence exists
    n = 0
    if _has_table(c, "polaris_standing_tasks"):
        n = _one(c, "SELECT COUNT(*) FROM polaris_standing_tasks WHERE "
                    "lower(task_name) LIKE '%intel%' OR lower(task_name) LIKE '%event%alpha%'") or 0
    ev.append({"ok": bool(n), "ref": f"polaris_standing_tasks:intel_plan={n}",
               "note": "specification/plan row"})

    # → 4 FOUNDATION: the backend module exists on disk
    p = ROOT / "services" / "intelligence_orchestrator.py"
    ev.append({"ok": p.exists(), "ref": "file:services/intelligence_orchestrator.py",
               "note": "backend exists"})

    # → 5 FRAME: persistence/schema for intelligence exists
    frame_tables = [t for t in ("intelligence_sources", "intel_findings",
                                "alpha_radar", "market_snapshots")
                    if _has_table(c, t)]
    ev.append({"ok": bool(frame_tables), "ref": "tables:" + ",".join(frame_tables[:3]),
               "note": "schema/persistence exists"})

    # → 6 SYSTEMS: the legal UI is implemented (LEGAL EVENT-ALPHA)
    ui = ROOT / "ui" / "intelligence_tab.py"
    ok = False
    if ui.exists():
        try:
            ok = "_render_legal_event_alpha_tab" in ui.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            ok = False
    ev.append({"ok": ok, "ref": "file:ui/intelligence_tab.py::_render_legal_event_alpha_tab",
               "note": "LEGAL EVENT-ALPHA UI implemented"})

    # → 7 INTEGRATION: orchestrator heartbeat live within 10 minutes
    age = None
    if _has_table(c, "system_heartbeat"):
        ts = _one(c, "SELECT last_pulse FROM system_heartbeat WHERE "
                     "service_name IN ('intelligence_orchestrator','intel_orchestrator')"
                     " ORDER BY last_pulse DESC LIMIT 1")
        if ts:
            age = time.time() - float(ts)
    ev.append({"ok": bool(age is not None and age < 600),
               "ref": f"system_heartbeat:intelligence_orchestrator:age={None if age is None else round(age)}",
               "note": "integration heartbeat"})

    # → 8 TESTING: verifier recorded a TEST_PASS world event for the institute
    n = _one(c, "SELECT COUNT(*) FROM world_events WHERE "
                "building_id=? AND event_type='TEST_PASS'", INSTITUTE) or 0
    ev.append({"ok": bool(n), "ref": f"world_events:TEST_PASS:{n}",
               "note": "tests passed (verifier-written)"})

    # → 9 VERIFIED: council review pass row
    n = _one(c, "SELECT COUNT(*) FROM world_events WHERE "
                "building_id=? AND event_type='COUNCIL_REVIEW_PASS'", INSTITUTE) or 0
    ev.append({"ok": bool(n), "ref": f"world_events:COUNCIL_REVIEW_PASS:{n}",
               "note": "council review passed"})

    # → 10 PAPER_READY: independent audit pass row
    n = _one(c, "SELECT COUNT(*) FROM world_events WHERE "
                "building_id=? AND event_type='INDEPENDENT_AUDIT_PASS'", INSTITUTE) or 0
    ev.append({"ok": bool(n), "ref": f"world_events:INDEPENDENT_AUDIT_PASS:{n}",
               "note": "independent audit passed"})

    # → 11 RELEASED: operator approval row
    n = _one(c, "SELECT COUNT(*) FROM world_events WHERE "
                "building_id=? AND event_type='OPERATOR_RELEASE'", INSTITUTE) or 0
    ev.append({"ok": bool(n), "ref": f"world_events:OPERATOR_RELEASE:{n}",
               "note": "operator released"})
    return ev


def evaluate_chapter(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Advance the Institute at most ONE earned stage. Returns status."""
    ensure_schema(db_path)
    b = get_building(INSTITUTE, db_path)
    if not b:
        return {"ok": False, "reason": "NO_BUILDING"}
    con = _connect(db_path)
    try:
        chain = _institute_evidence(con)
    finally:
        con.close()
    cur = int(b["stage"])
    nxt = cur + 1
    if nxt >= len(STAGES):
        return {"ok": True, "reason": "COMPLETE", "stage": cur, "chain": chain}
    proof = chain[cur] if cur < len(chain) else {"ok": False, "ref": "", "note": ""}
    if proof["ok"]:
        res = advance_stage(INSTITUTE, nxt, proof["ref"],
                            f"Institute earned {STAGES[nxt]}: {proof['note']}",
                            db_path=db_path)
        return {"ok": res["ok"], "reason": res["reason"], "stage": res.get("stage", cur),
                "chain": chain}
    return {"ok": True, "reason": f"HOLDING:{STAGES[cur]} awaiting {proof['ref'] or STAGES[nxt]}",
            "stage": cur, "chain": chain}


# ── Purposeful agent derivation (§7/§8/§10) ─────────────────────────────────
def derive_agent_states(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    ensure_schema(db_path)
    con = _connect(db_path)
    now = time.time()
    assigned: Dict[str, Dict[str, Any]] = {}
    try:
        if _has_table(con, "council_world_tasks"):
            cols = {r[1] for r in con.execute("PRAGMA table_info(council_world_tasks)")}
            phase_col = "task_phase" if "task_phase" in cols else None
            bld_col = "building_id" if "building_id" in cols else None
            grp_col = "collaboration_group" if "collaboration_group" in cols else None
            blk_col = "blocker_reason" if "blocker_reason" in cols else None
            for r in con.execute(
                    "SELECT * FROM council_world_tasks WHERE status IN "
                    "('in_progress','active','assigned') ORDER BY task_id DESC LIMIT 24"):
                owner = str(r["agent_owner"] or "").strip().upper()
                if owner not in AGENT_HOMES:
                    owner = {"BROOK": "GUARDIAN"}.get(owner, "")
                if not owner or owner in assigned:
                    continue
                loc = canonical_location(
                    (r[bld_col] if bld_col else None) or r["world_location"])
                phase = str((r[phase_col] if phase_col else "") or "BUILD").upper()
                blocked = bool(blk_col and r[blk_col])
                action = "BLOCKED" if blocked else PHASE_TO_ACTION.get(phase, "BUILD")
                assigned[owner] = {
                    "location_key": loc, "action_state": action,
                    "task_id": int(r["task_id"]), "task_phase": phase,
                    "group": (str(r[grp_col]) if grp_col and r[grp_col] else ""),
                    "gather": (phase in GATHER_PHASES) and not blocked,
                    "blocker": (str(r[blk_col])[:80] if blocked else ""),
                }
        # persist canonical state; idle agents go home (never random roam)
        for aid, home in AGENT_HOMES.items():
            st = assigned.get(aid, {"location_key": home, "action_state": "IDLE",
                                    "task_id": None, "task_phase": "",
                                    "group": "", "gather": False, "blocker": ""})
            upsert_agent(aid, st["location_key"], st["action_state"],
                         task_id=st["task_id"], task_phase=st["task_phase"],
                         con=con)
        con.commit()
    finally:
        con.close()
    out = {}
    for aid, home in AGENT_HOMES.items():
        st = assigned.get(aid)
        out[aid] = st or {"location_key": home, "action_state": "IDLE",
                          "task_id": None, "task_phase": "", "group": "",
                          "gather": False, "blocker": ""}
    return out


# ── Ambient NPC truth signals (§13) — max three moving ──────────────────────
def ambient_signals(db_path: Optional[Path] = None) -> List[str]:
    con = _connect(db_path)
    active: List[str] = []
    now = time.time()
    try:
        def hb_age(svc):
            ts = _one(con, "SELECT last_pulse FROM system_heartbeat WHERE service_name=?", svc)
            return (now - float(ts)) if ts else 1e9

        if _has_table(con, "system_heartbeat"):
            degraded = any(hb_age(s) > 300 for s in
                           ("ws_price_oracle", "execution_engine", "neural_supervisor"))
            if degraded:
                active.append("MECH")            # Mechanic: a core service degraded
        if _has_table(con, "code_apply_journal"):
            n = _one(con, "SELECT COUNT(*) FROM code_apply_journal WHERE applied_at > ?",
                     now - 900) or 0
            if n:
                active.append("COURIER")          # Courier: patch transferred
        if _has_table(con, "world_events"):
            n = _one(con, "SELECT COUNT(*) FROM world_events WHERE ts > ?", now - 600) or 0
            if n:
                active.append("ARCH")             # Archivist: evidence written
        if len(active) < 3 and _has_table(con, "substrate_trade_log"):
            n = _one(con, "SELECT COUNT(*) FROM substrate_trade_log WHERE ts > ?",
                     now - 600) or 0
            if n:
                active.append("DOCK")             # Dockhand: substrate data moved
        if len(active) < 3 and _has_table(con, "system_heartbeat"):
            if hb_age("db_retention_trim") < 3600:
                active.append("GARDEN")           # Gardener: retention/cleanup ran
    except Exception:
        pass
    finally:
        con.close()
    return active[:3]


# ── Chronicle translation (§12) ─────────────────────────────────────────────
_MILESTONE_TEMPLATES = {
    "STAGE_ADVANCE": "{narrative}",
    "PAPER_READY": "{narrative}",
    "TOOL_APPROVED": "{narrative}",
    "WORLD_RESUME": "{narrative}",
    "TEST_PASS": "NUGGET verified: {narrative}",
    "COUNCIL_REVIEW_PASS": "COUNCIL review passed: {narrative}",
    "INDEPENDENT_AUDIT_PASS": "Independent audit passed: {narrative}",
    "OPERATOR_RELEASE": "OPERATOR released: {narrative}",
    "APPLY": "POLARIS carried {narrative} from Forge to site.",
    "BLOCKED": "Blocked: {narrative}",
}


def chronicle(db_path: Optional[Path] = None, limit: int = 18) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    out = []
    try:
        if _has_table(con, "world_events"):
            for r in con.execute("SELECT * FROM world_events ORDER BY ts DESC LIMIT ?",
                                 (limit,)):
                d = dict(r)
                tpl = _MILESTONE_TEMPLATES.get(d["event_type"], "{narrative}")
                out.append({"ts": d["ts"], "agent": d["agent_id"],
                            "building": d["building_id"],
                            "message": tpl.format(narrative=d["narrative"]),
                            "evidence": d["evidence_ref"] or ""})
    except Exception:
        pass
    finally:
        con.close()
    return out


def run_world_pass(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """One narrative pass: evaluate chapter, refresh agents, ambient signals."""
    chapter = evaluate_chapter(db_path)
    agents = derive_agent_states(db_path)
    ambient = ambient_signals(db_path)
    return {"chapter": chapter, "agents": agents, "ambient": ambient}


if __name__ == "__main__":
    import json
    print(json.dumps(run_world_pass(), indent=2, default=str)[:4000])
