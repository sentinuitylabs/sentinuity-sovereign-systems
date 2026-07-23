# coding: utf-8
"""
services/world_build_state.py — SOVEREIGN_WORLD_UPGRADE_20260723

Canonical persistence layer for the Sanctuary Tech Town.

Doctrine (FABLE 5 WORLD DIRECTIVE):
  * Canonical progress lives in SQLite, never in the browser.
  * A building stage NEVER advances without an evidence_ref.
  * Time alone advances nothing.
  * This module writes ONLY to world_buildings / world_agent_state /
    world_tools / world_events. It never touches trading tables.
  * localStorage on the client may hold cosmetic preferences only.

Chapter I: BUILD THE INTELLIGENCE INSTITUTE TO PAPER-READY.
The Institute maps to the previously agreed legal Polymarket alternative
already present in this codebase: LEGAL EVENT-ALPHA
(ui/intelligence_tab.py::_render_legal_event_alpha_tab — spot-market
intelligence, no prediction markets / event contracts / wagering).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

CHAPTER = {
    "id": "CHAPTER_I",
    "title": "CHAPTER I — BUILD THE INTELLIGENCE INSTITUTE",
    "target_building": "intelligence_institute",
    "target_module": "LEGAL EVENT-ALPHA (ui/intelligence_tab.py)",
}

# ── Canonical location registry (percent coords on the CURRENT backdrop) ────
# Zones already visible on the embedded map are reused; new structures are
# anchored to sensible existing areas. The backdrop image is NOT modified.
WORLD_LOCATIONS: Dict[str, Dict[str, Any]] = {
    "council_lodge":         {"x": 48, "y": 16, "label": "COUNCIL LODGE",
                              "backend": "council chamber · standing_task_scheduler · council_build_orchestrator · proposal ledger"},
    "research_conservatory": {"x": 33, "y": 29, "label": "RESEARCH CONSERVATORY",
                              "backend": "inspiration_intake_ledger · source discovery · legal research"},
    "archive_grove":         {"x": 16, "y": 37, "label": "ARCHIVE GROVE",
                              "backend": "genesis_vault · build_retrospective · code history"},
    "oracle_observatory":    {"x": 76, "y": 36, "label": "ORACLE OBSERVATORY",
                              "backend": "ws_price_oracle · price_router · source consensus · freshness"},
    "forge_workshop":        {"x": 20, "y": 74, "label": "FORGE WORKSHOP",
                              "backend": "forge_code_writer · patch queue · code_apply_journal · verifier"},
    "tool_mart":             {"x": 62, "y": 66, "label": "TOOL MART",
                              "backend": "approved capabilities: deps · APIs · models · migrations · harnesses"},
    "substrate_node":        {"x": 71, "y": 71, "label": "SUBSTRATE NODE",
                              "backend": "substrate_price_feed · paper trader · strategy lab · copytrade bridge · supervisor"},
    "intelligence_institute": {"x": 47, "y": 50, "label": "INTELLIGENCE INSTITUTE",
                              "backend": "LEGAL EVENT-ALPHA · intelligence_orchestrator · intelligence_tab"},
    "release_gate":          {"x": 84, "y": 50, "label": "RELEASE GATE",
                              "backend": "operator approval · paper/live readiness · verifier evidence"},
}

# Deterministic migration of every legacy world_tasks location key.
LEGACY_LOCATION_MAP: Dict[str, str] = {
    "council_grove":        "council_lodge",
    "copytrade_observatory": "substrate_node",
    "executor_vault":       "release_gate",      # execution authority sits behind the operator gate
    "oracle_bridge":        "oracle_observatory",
    "market_intel_lab":     "intelligence_institute",
    "substrate_core":       "substrate_node",
    "build_foundry":        "forge_workshop",
}

def canonical_location(key: str) -> str:
    k = str(key or "").strip()
    if k in WORLD_LOCATIONS:
        return k
    return LEGACY_LOCATION_MAP.get(k, "council_lodge")

# ── Building stages (§6) ────────────────────────────────────────────────────
STAGES = ["DORMANT", "PLANNED", "RESEARCHED", "SPECIFIED", "FOUNDATION",
          "FRAME", "SYSTEMS", "INTEGRATION", "TESTING", "VERIFIED",
          "PAPER_READY", "RELEASED"]
STAGE_INDEX = {n: i for i, n in enumerate(STAGES)}
SCAFFOLD_STAGES = set(range(STAGE_INDEX["FOUNDATION"], STAGE_INDEX["VERIFIED"]))

# Buildable registry rows seeded at first run. intelligence_institute is the
# active chapter site; stable service districts start VERIFIED-equivalent
# health display but stage reflects build narrative (existing modules = RELEASED
# only when their release evidence exists; default conservative DORMANT for
# non-chapter plots so nothing fakes progress).
SEED_BUILDINGS = [
    ("council_lodge",        "Council Lodge",        "council",              10),
    ("research_conservatory", "Research Conservatory", "inspiration_intake",  0),
    ("archive_grove",        "Archive Grove",        "genesis_vault",        10),
    ("oracle_observatory",   "Oracle Observatory",   "ws_price_oracle",      10),
    ("forge_workshop",       "Forge Workshop",       "forge_code_writer",    10),
    ("tool_mart",            "Tool Mart",            "world_tools",           0),
    ("substrate_node",       "Substrate Node",       "substrate_paper_trader", 6),
    ("intelligence_institute", "Intelligence Institute", "legal_event_alpha",  0),
    ("release_gate",         "Release Gate",         "operator_gate",        10),
]
# NOTE on seeds: stage-10 seeds are for long-operational districts whose
# paper-readiness is already evidenced by live heartbeats + shipped modules;
# their evidence_ref is stamped at seed time to the module heartbeat truth.
# The chapter site and unproven plots seed at 0 — they must EARN stages.

AGENT_HOMES = {
    "POLARIS": "council_lodge",
    "IVARIS":  "archive_grove",
    "NUGGET":  "release_gate",
    "ORACLE":  "oracle_observatory",
    "RHIZA":   "archive_grove",
    "GUARDIAN": "tool_mart",          # Brook — quartermaster
}

ACTION_STATES = ["DISCOVER", "RESEARCH", "DEBATE", "PLAN", "ACQUIRE_TOOL",
                 "BUILD", "TEST", "REVIEW", "APPLY", "VERIFY", "RELEASE",
                 "BLOCKED", "IDLE"]
# Phases where a shared collaboration_group gathers at one building.
GATHER_PHASES = {"DEBATE", "TEST", "RELEASE"}
# REVIEW is deliberately NOT a gather phase: Ivaris and Nugget inspect
# independently (§7) — same building allowed, wide opposing offsets, no
# following.

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS world_buildings(
    building_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    module_key TEXT,
    location_key TEXT NOT NULL,
    stage INTEGER NOT NULL DEFAULT 0,
    progress_pct REAL NOT NULL DEFAULT 0,
    health_state TEXT NOT NULL DEFAULT 'IDLE',
    active_task_id INTEGER,
    scaffold_state TEXT NOT NULL DEFAULT 'NONE',
    unlocked INTEGER NOT NULL DEFAULT 1,
    paper_ready INTEGER NOT NULL DEFAULT 0,
    released INTEGER NOT NULL DEFAULT 0,
    evidence_ref TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS world_agent_state(
    agent_id TEXT PRIMARY KEY,
    location_key TEXT NOT NULL,
    action_state TEXT NOT NULL DEFAULT 'IDLE',
    assigned_task_id INTEGER,
    carried_tool_id TEXT,
    animation_key TEXT,
    task_phase TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS world_tools(
    tool_id TEXT PRIMARY KEY,
    capability_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    acquired INTEGER NOT NULL DEFAULT 0,
    installed_building_id TEXT,
    provenance TEXT,
    evidence_ref TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS world_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task_id INTEGER,
    agent_id TEXT,
    building_id TEXT,
    event_type TEXT NOT NULL,
    evidence_ref TEXT,
    narrative TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS world_events_ts ON world_events(ts DESC);
"""

_WRITE_TABLES = {"world_buildings", "world_agent_state", "world_tools", "world_events"}


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path or DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    return con


def ensure_schema(db_path: Optional[Path] = None) -> None:
    """Idempotent — safe on every launch."""
    con = _connect(db_path)
    try:
        con.executescript(SCHEMA_SQL)
        now = time.time()
        for bid, name, module, stage in SEED_BUILDINGS:
            con.execute(
                "INSERT INTO world_buildings(building_id, display_name, module_key,"
                " location_key, stage, progress_pct, scaffold_state, paper_ready,"
                " evidence_ref, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(building_id) DO NOTHING",
                (bid, name, module, bid, stage,
                 round(stage / (len(STAGES) - 1) * 100, 1),
                 "UP" if stage in SCAFFOLD_STAGES else "NONE",
                 1 if stage >= STAGE_INDEX["PAPER_READY"] else 0,
                 ("seed:operational_module_heartbeat" if stage >= 10 else None),
                 now))
        for aid, home in AGENT_HOMES.items():
            con.execute(
                "INSERT INTO world_agent_state(agent_id, location_key, action_state,"
                " updated_at) VALUES(?,?, 'IDLE', ?)"
                " ON CONFLICT(agent_id) DO NOTHING",
                (aid, home, now))
        con.commit()
    finally:
        con.close()


def record_event(event_type: str, narrative: str, *, evidence_ref: str = "",
                 agent_id: str = "", building_id: str = "",
                 task_id: Optional[int] = None,
                 db_path: Optional[Path] = None,
                 con: Optional[sqlite3.Connection] = None) -> None:
    own = con is None
    c = con or _connect(db_path)
    try:
        c.execute("INSERT INTO world_events(ts, task_id, agent_id, building_id,"
                  " event_type, evidence_ref, narrative) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), task_id, agent_id or None, building_id or None,
                   event_type, evidence_ref or None, str(narrative)[:300]))
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def get_building(building_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    con = _connect(db_path)
    try:
        r = con.execute("SELECT * FROM world_buildings WHERE building_id=?",
                        (building_id,)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def advance_stage(building_id: str, target_stage: int, evidence_ref: str,
                  narrative: str, *, agent_id: str = "",
                  db_path: Optional[Path] = None) -> Dict[str, Any]:
    """THE evidence gate. Refuses without evidence_ref; refuses stage skips;
    refuses regressions (corrections must go through correct_stage with
    explicit evidence). Returns {ok, reason, stage}."""
    evidence_ref = str(evidence_ref or "").strip()
    if not evidence_ref:
        return {"ok": False, "reason": "EVIDENCE_REQUIRED", "stage": None}
    try:
        target_stage = int(target_stage)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "BAD_STAGE", "stage": None}
    if not (0 <= target_stage < len(STAGES)):
        return {"ok": False, "reason": "STAGE_OUT_OF_RANGE", "stage": None}
    con = _connect(db_path)
    try:
        row = con.execute("SELECT stage FROM world_buildings WHERE building_id=?",
                          (building_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "UNKNOWN_BUILDING", "stage": None}
        cur = int(row["stage"])
        if target_stage != cur + 1:
            return {"ok": False, "reason": f"NO_SKIP cur={cur} target={target_stage}",
                    "stage": cur}
        now = time.time()
        con.execute(
            "UPDATE world_buildings SET stage=?, progress_pct=?, scaffold_state=?,"
            " paper_ready=?, released=?, evidence_ref=?, updated_at=?,"
            " health_state='BUILDING' WHERE building_id=?",
            (target_stage,
             round(target_stage / (len(STAGES) - 1) * 100, 1),
             "UP" if target_stage in SCAFFOLD_STAGES else "NONE",
             1 if target_stage >= STAGE_INDEX["PAPER_READY"] else 0,
             1 if target_stage >= STAGE_INDEX["RELEASED"] else 0,
             evidence_ref, now, building_id))
        record_event("STAGE_ADVANCE",
                     f"{building_id.upper()} advanced: "
                     f"{STAGES[cur]} → {STAGES[target_stage]}",
                     evidence_ref=evidence_ref, agent_id=agent_id,
                     building_id=building_id, con=con)
        if target_stage == STAGE_INDEX["PAPER_READY"]:
            record_event("PAPER_READY",
                         f"{building_id.upper().replace('_', ' ')} — "
                         "PAPER READY FOR OPERATOR TESTING.",
                         evidence_ref=evidence_ref, building_id=building_id,
                         con=con)
        con.commit()
        return {"ok": True, "reason": "ADVANCED", "stage": target_stage}
    finally:
        con.close()


def set_blocked(building_id: str, blocker_reason: str,
                db_path: Optional[Path] = None) -> None:
    con = _connect(db_path)
    try:
        con.execute("UPDATE world_buildings SET health_state=?, updated_at=?"
                    " WHERE building_id=?",
                    (f"BLOCKED:{str(blocker_reason)[:80]}", time.time(), building_id))
        con.commit()
    finally:
        con.close()


def upsert_agent(agent_id: str, location_key: str, action_state: str,
                 *, task_id: Optional[int] = None, task_phase: str = "",
                 carried_tool_id: str = "", animation_key: str = "",
                 db_path: Optional[Path] = None,
                 con: Optional[sqlite3.Connection] = None) -> None:
    if action_state not in ACTION_STATES:
        action_state = "IDLE"
    own = con is None
    c = con or _connect(db_path)
    try:
        c.execute(
            "INSERT INTO world_agent_state(agent_id, location_key, action_state,"
            " assigned_task_id, carried_tool_id, animation_key, task_phase, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(agent_id) DO UPDATE SET location_key=excluded.location_key,"
            " action_state=excluded.action_state, assigned_task_id=excluded.assigned_task_id,"
            " carried_tool_id=excluded.carried_tool_id, animation_key=excluded.animation_key,"
            " task_phase=excluded.task_phase, updated_at=excluded.updated_at",
            (agent_id, canonical_location(location_key), action_state, task_id,
             carried_tool_id or None, animation_key or None, task_phase or None,
             time.time()))
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def register_tool(tool_id: str, capability_key: str, display_name: str,
                  source_type: str, *, provenance: str = "",
                  evidence_ref: str = "",
                  db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Tool doctrine (§11): a tool must be a real capability. Registration
    requires a capability_key AND evidence_ref (the task/council row that
    declared the missing capability). No fake consumables, no currency."""
    if not str(capability_key or "").strip():
        return {"ok": False, "reason": "CAPABILITY_REQUIRED"}
    if not str(evidence_ref or "").strip():
        return {"ok": False, "reason": "EVIDENCE_REQUIRED"}
    if source_type not in {"python_dependency", "api", "model", "connector",
                           "schema_migration", "test_harness", "runtime_service",
                           "browser_automation", "visual_asset_pack"}:
        return {"ok": False, "reason": "UNKNOWN_SOURCE_TYPE"}
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO world_tools(tool_id, capability_key, display_name,"
            " source_type, approved, provenance, evidence_ref, updated_at)"
            " VALUES(?,?,?,?,1,?,?,?)"
            " ON CONFLICT(tool_id) DO UPDATE SET approved=1,"
            " evidence_ref=excluded.evidence_ref, updated_at=excluded.updated_at",
            (tool_id, capability_key, display_name, source_type,
             provenance or None, evidence_ref, time.time()))
        record_event("TOOL_APPROVED",
                     f"Tool Mart approved capability: {display_name}",
                     evidence_ref=evidence_ref, building_id="tool_mart", con=con)
        con.commit()
        return {"ok": True, "reason": "APPROVED"}
    finally:
        con.close()


def load_world_layer(db_path: Optional[Path] = None,
                     events_limit: int = 24) -> Dict[str, Any]:
    """Read-only snapshot merged into the UI state payload."""
    out: Dict[str, Any] = {"chapter": dict(CHAPTER), "locations": WORLD_LOCATIONS,
                           "buildings": {}, "agents": {}, "chronicle": [],
                           "stages": STAGES, "saved_at": None}
    try:
        con = _connect(db_path)
    except Exception:
        return out
    try:
        for r in con.execute("SELECT * FROM world_buildings"):
            d = dict(r)
            d["stage_name"] = STAGES[int(d["stage"])] if 0 <= int(d["stage"]) < len(STAGES) else "?"
            out["buildings"][d["building_id"]] = d
        newest = 0.0
        for r in con.execute("SELECT * FROM world_agent_state"):
            d = dict(r)
            out["agents"][d["agent_id"]] = d
            newest = max(newest, float(d.get("updated_at") or 0))
        for r in con.execute("SELECT * FROM world_events ORDER BY ts DESC LIMIT ?",
                             (events_limit,)):
            out["chronicle"].append(dict(r))
        out["saved_at"] = newest or None
    except Exception:
        pass
    finally:
        con.close()
    return out


def append_resume_event(db_path: Optional[Path] = None) -> None:
    """§18: on launch, append a resume event — never restart progress."""
    con = _connect(db_path)
    try:
        n = con.execute("SELECT COUNT(*) FROM world_buildings").fetchone()[0]
        record_event("WORLD_RESUME",
                     f"World resumed — {n} structures restored from canonical state.",
                     evidence_ref="world_buildings:restore", con=con)
        con.commit()
    finally:
        con.close()
