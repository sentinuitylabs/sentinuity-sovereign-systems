"""
ui/world_tasks.py — WORLD_OS_20260612 persistence + real backend probes.

Writes ONLY to council_world_tasks + world_command_log (ops work board —
never trading tables). Every probe is read-only against the same sources the
panels use. Task completion is probe-gated: nothing fakes done.
"""
from __future__ import annotations
try:
    from ui.substrate_wallet_panel import render_substrate_wallet_panel
except Exception:
    try:
        from services.substrate_wallet_panel import render_substrate_wallet_panel
    except Exception:
        try:
            from substrate_wallet_panel import render_substrate_wallet_panel
        except Exception:
            render_substrate_wallet_panel = None
import json, sqlite3, time
from pathlib import Path

DB = Path("sentinuity_matrix.db")
TIERS = {0:"OBSERVE",1:"SUGGEST",2:"SIMULATE",3:"PATCH-QUEUE",4:"APPROVED-APPLY",5:"LIVE-GATED"}

# SOVEREIGN_WORLD_UPGRADE_20260723 — canonical registry replaces loose keys.
# Legacy keys remain accepted on input and are migrated deterministically.
try:
    from services.world_build_state import (
        WORLD_LOCATIONS, LEGACY_LOCATION_MAP, canonical_location)
except Exception:                                  # standalone fallback
    WORLD_LOCATIONS = {k: {} for k in (
        "council_lodge","research_conservatory","archive_grove",
        "oracle_observatory","forge_workshop","tool_mart","substrate_node",
        "intelligence_institute","release_gate")}
    LEGACY_LOCATION_MAP = {
        "council_grove":"council_lodge","copytrade_observatory":"substrate_node",
        "executor_vault":"release_gate","oracle_bridge":"oracle_observatory",
        "market_intel_lab":"intelligence_institute","substrate_core":"substrate_node",
        "build_foundry":"forge_workshop"}
    def canonical_location(k):
        k = str(k or "").strip()
        return k if k in WORLD_LOCATIONS else LEGACY_LOCATION_MAP.get(k, "council_lodge")

LOCATIONS = list(WORLD_LOCATIONS.keys())

def _con():
    c = sqlite3.connect(str(DB), timeout=10); c.row_factory = sqlite3.Row; return c

def ensure_schema() -> None:
    c = _con()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS council_world_tasks(
      task_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, risk_tier INT,
      world_location TEXT, agent_owner TEXT, backend_files TEXT, commands TEXT,
      requires_user_approval INT, status TEXT, result_summary TEXT,
      created_at REAL, updated_at REAL);
    CREATE TABLE IF NOT EXISTS world_command_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, source TEXT, command TEXT,
      outcome TEXT);
    """)
    # SOVEREIGN_WORLD_UPGRADE_20260723 — additive columns + deterministic
    # legacy-location migration (idempotent).
    cols = {r[1] for r in c.execute("PRAGMA table_info(council_world_tasks)")}
    for name, decl in (("building_id","TEXT"),("task_phase","TEXT"),
                       ("evidence_ref","TEXT"),("progress_weight","REAL DEFAULT 1.0"),
                       ("blocker_reason","TEXT"),("collaboration_group","TEXT")):
        if name not in cols:
            c.execute(f"ALTER TABLE council_world_tasks ADD COLUMN {name} {decl}")
    for legacy, canon in LEGACY_LOCATION_MAP.items():
        c.execute("UPDATE council_world_tasks SET building_id=?, world_location=? "
                  "WHERE world_location=? AND (building_id IS NULL OR building_id='')",
                  (canon, canon, legacy))
    for r in c.execute("SELECT task_id, world_location FROM council_world_tasks "
                       "WHERE building_id IS NULL OR building_id=''").fetchall():
        c.execute("UPDATE council_world_tasks SET building_id=? WHERE task_id=?",
                  (canonical_location(r[1]), r[0]))
    c.commit(); c.close()

STANDING = [
 ("Price integrity restoration", 1, "oracle_bridge", "ORACLE", ["services/ws_price_oracle.py","services/price_integrity_contract.py"], 0),
 ("Modest banking restoration", 1, "executor_vault", "AXON", ["services/execution_engine.py"], 0),
 ("Monster runner spine audit", 1, "council_grove", "POLARIS", ["services/execution_engine.py"], 1),
 ("Final overnight pre-live validation", 0, "market_intel_lab", "POLARIS", ["logs/execution_engine.log","logs/ws_price_oracle.log"], 0),
 ("Debate Chamber continuity", 1, "build_foundry", "FORGE", ["services/council_chamber_bridge.py","services/sovereign_hub.py","launch/Launch_Sentinuity.bat"], 1),
 ("Executor freshness watch: stale latch / price age / paper opens", 0, "executor_vault", "AXON", ["services/execution_engine.py"], 0),
 ("Council schedule optimizer: leaner launch/runtime/shutdown plan", 1, "council_lodge", "RHIZA", ["launch/Launch_Sentinuity.bat","launch/prelaunch.py"], 1),
 # SOVEREIGN_WORLD_UPGRADE_20260723 — Chapter I + world meaning audit (§17)
 ("CHAPTER I: Build the Intelligence Institute to paper-ready (LEGAL EVENT-ALPHA)",
  1, "intelligence_institute", "POLARIS",
  ["ui/intelligence_tab.py","services/intelligence_orchestrator.py",
   "services/world_narrative_engine.py"], 1),
 ("Sovereign World Meaning Audit: assess movement/construction/tool/narrative "
  "truth; propose one evidence-grounded improvement; never fabricate progress "
  "or alter trading authority",
  1, "council_lodge", "RHIZA",
  ["services/world_build_state.py","services/world_narrative_engine.py",
   "ui/sovereign_world.html"], 1),
]

_WORLD_TASK_PHASES = {
 "CHAPTER I: Build the Intelligence Institute to paper-ready (LEGAL EVENT-ALPHA)":
     ("intelligence_institute", "BUILD", "chapter_i"),
 "Sovereign World Meaning Audit: assess movement/construction/tool/narrative "
 "truth; propose one evidence-grounded improvement; never fabricate progress "
 "or alter trading authority":
     ("council_lodge", "REVIEW", ""),
}

def seed_standing() -> int:
    ensure_schema(); c = _con(); n = 0
    for title, tier, loc, owner, files, appr in STANDING:
        if not c.execute("SELECT 1 FROM council_world_tasks WHERE title=?",(title,)).fetchone():
            c.execute("INSERT INTO council_world_tasks(title,risk_tier,world_location,"
                      "agent_owner,backend_files,commands,requires_user_approval,status,"
                      "result_summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (title,tier,canonical_location(loc),owner,json.dumps(files),"[]",appr,"queued","",
                       time.time(),time.time())); n += 1
    # stamp canonical building/phase/collab metadata on the world tasks
    for title,(bld,phase,grp) in _WORLD_TASK_PHASES.items():
        c.execute("UPDATE council_world_tasks SET building_id=?, task_phase=?, "
                  "collaboration_group=? WHERE title=? AND "
                  "(task_phase IS NULL OR task_phase='')", (bld, phase, grp, title))
    c.commit(); c.close(); return n

def add_task(title:str, tier:int=1, loc:str="council_lodge", owner:str="Polaris") -> int:
    loc = canonical_location(loc)
    ensure_schema(); c=_con()
    cur = c.execute("INSERT INTO council_world_tasks(title,risk_tier,world_location,"
        "agent_owner,backend_files,commands,requires_user_approval,status,result_summary,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (title,tier,loc,owner,"[]","[]",1 if tier>=3 else 0,"queued","",time.time(),time.time()))
    tid = cur.lastrowid; c.commit(); c.close(); return tid

def set_status(task_id:int, status:str, summary:str="") -> None:
    c=_con(); c.execute("UPDATE council_world_tasks SET status=?,result_summary=?,"
        "updated_at=? WHERE task_id=?",(status,summary,time.time(),task_id))
    c.commit(); c.close()

def tasks(limit:int=20) -> list[dict]:
    ensure_schema(); c=_con()
    rows=[dict(r) for r in c.execute("SELECT * FROM council_world_tasks "
        "ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'voting' THEN 1 "
        "WHEN 'queued' THEN 2 ELSE 3 END, updated_at DESC LIMIT ?",(limit,))]
    c.close(); return rows

def log_command(source:str, command:str, outcome:str) -> None:
    ensure_schema(); c=_con()
    c.execute("INSERT INTO world_command_log(ts,source,command,outcome) VALUES(?,?,?,?)",
              (time.time(),source,command,outcome)); c.commit(); c.close()

# ── REAL BACKEND PROBES (read-only, schema-tolerant) ────────────────────────
def probes() -> dict:
    out = {}
    try:
        try:
            from ui import data_sources as D
        except Exception:
            import data_sources as D
        g = D.gate_counters(); l = D.latch_state(); ct = D.copytrade_state(); hb = D.heartbeats()
        c = g.get("counts", {})
        out["paper_opens_blocked"] = c.get("phase_a_pass",0) > 0 and c.get("paper_opened",0) == 0
        out["stale_latch"] = (l.get("vetoed_still_visible") or 0) > 0
        out["copytrade_unwired"] = not ct.get("influences_entries", False)
        oracle = next((r for r in hb.get("rows",[]) if "oracle" in str(r["service_name"]).lower()), None)
        out["oracle_stale"] = bool(oracle and oracle["age_s"] > 120)
        out["paper_opened_10m"] = c.get("paper_opened",0)
        out["latched_visible"] = l.get("executor_visible")
    except Exception as e:
        out["probe_error"] = str(e)
    try:
        out["db_mb"] = round(DB.stat().st_size/1024/1024,1) if DB.exists() else 0
    except Exception:
        out["db_mb"] = 0
    return out


# --- SUBSTRATE WALLET STANDALONE PANEL ---
def render_substrate_wallet_panel_safe() -> None:
    """Render from a UI page only. No import-time side effects."""
    if render_substrate_wallet_panel is None:
        try:
            import streamlit as st
            st.info("Substrate Wallet panel is not available in this runtime.")
        except Exception:
            pass
        return
    try:
        render_substrate_wallet_panel()
    except Exception as exc:
        try:
            import streamlit as st
            st.error(f"Substrate Wallet panel failed: {exc}")
        except Exception:
            print(f"Substrate Wallet panel failed: {exc}")
# --- END SUBSTRATE WALLET STANDALONE PANEL ---

