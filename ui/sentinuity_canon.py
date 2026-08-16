"""
ui/sentinuity_canon.py
======================
SENTINUITY_CANONICAL_EVENT_MODEL_20260817

ONE read-only projection of runtime truth. Every UI surface — World, Expedition,
Debate Chamber, Forge/Polaris, Evidence, Chronicle — derives from THIS payload
and nothing else. That is what removes the repetition: the same event is
rendered four different ways, never described four different times.

HARD RULES (enforced by construction, not by convention):

  1. STRICT read-only. `mode=ro` + `PRAGMA query_only=ON`. No writes, ever.
  2. NO FABRICATION. If a table/column is absent the block is marked
     available=False with a reason and the UI must render it as unknown.
  3. TRI-STATE TRUTH. compile/smoke/verify/polaris are PASS | FAIL | NOT_RUN.
     `None` NEVER becomes PASS and NEVER becomes "done".
  4. PATCH WRITTEN != APPLIED != ABSORBED. Distinct stages, distinct evidence.
  5. This module NEVER touches trading, pricing, execution, governance or
     acceptance logic. It only reads and projects.

Canonical journey (doctrine):
  EXPLORE -> DISCOVER -> EXTRACT -> DEBATE -> FORGE -> VERIFY -> POLARIS
          -> ABSORB/REJECT -> MEASURE
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CANON_VERSION = 1

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "sentinuity_matrix.db"

# ── tri-state ──────────────────────────────────────────────────────────────
PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT_RUN"

_PASS_WORDS = {"pass", "passed", "ok", "success", "succeeded", "true", "1", "green"}
_FAIL_WORDS = {"fail", "failed", "error", "false", "0", "red", "blocked", "reject",
               "rejected"}


def tri(value: Any) -> str:
    """
    The single most important function in this module.

    NULL / '' / 'none' / 'pending' -> NOT_RUN. A check that has not run is not
    a check that passed. The UI must be able to look unfinished.
    """
    if value is None:
        return NOT_RUN
    s = str(value).strip().lower()
    if s in ("", "none", "null", "pending", "not_run", "n/a", "unknown", "awaiting"):
        return NOT_RUN
    if s in _PASS_WORDS:
        return PASS
    if s in _FAIL_WORDS:
        return FAIL
    # structured payloads: {"ok": true} / "compile ok" / "3 failed"
    if "fail" in s or "error" in s or "trace" in s:
        return FAIL
    if "ok" in s or "pass" in s:
        return PASS
    return NOT_RUN


# ── journey stages ─────────────────────────────────────────────────────────
JOURNEY = ["EXPLORE", "DISCOVER", "EXTRACT", "DEBATE", "FORGE",
           "VERIFY", "POLARIS", "ABSORB", "MEASURE"]

# faculty registry — geography in the world maps 1:1 onto these keys
FACULTIES = {
    "PRICE_TRUTH":  {"label": "Price Truth",  "site": "price_truth",
                     "services": ["ws_price_oracle", "price_enricher", "macro_price_feed"]},
    "EXECUTION":    {"label": "Execution",    "site": "execution",
                     "services": ["execution_engine", "execution_engine_exit",
                                  "execution_engine_real_exit"]},
    "INTELLIGENCE": {"label": "Intelligence", "site": "intelligence",
                     "services": ["intelligence_orchestrator", "market_intelligence",
                                  "neural_supervisor"]},
    "SMART_MONEY":  {"label": "Smart Money",  "site": "smart_money",
                     "services": ["smart_wallet_trade_ingester", "copytrade_shadow_scanner",
                                  "wallet_scout"]},
    "SUBSTRATE":    {"label": "Substrate",    "site": "substrate",
                     "services": ["substrate_paper_trader", "substrate_portfolio_supervisor",
                                  "substrate_opportunity_scanner"]},
    "COUNCIL":      {"label": "Council",      "site": "council",
                     "services": ["council_chamber_bridge", "council_build_orchestrator",
                                  "debate_engine", "polaris"]},
}

# council agents — canonical six (services/council_build_orchestrator.CANONICAL_COUNCIL)
COUNCIL_AGENTS = {
    "NUGGET":  {"home": "council", "service": "reconnaissance_engine",
                "role": "Field scout · auditor"},
    "POLARIS": {"home": "council", "service": "polaris",
                "role": "Coordinator · safety gate"},
    "IVARIS":  {"home": "council", "service": "polaris_auxiliary",
                "role": "Adversarial critic"},
    "ORACLE":  {"home": "price_truth", "service": "ws_price_oracle",
                "role": "Senses · price & market truth"},
    "AXON":    {"home": "execution", "service": "execution_engine",
                "role": "Implementation · execution validator"},
    "RHIZA":   {"home": "intelligence", "service": "symbiotic_router",
                "role": "Synthesis · memory"},
}

NPC_AGENTS = {
    "COURIER":  {"home": "council",      "service": "council_chamber_bridge"},
    "GUARDIAN": {"home": "smart_money",  "service": "system_guardian"},
    "KEEPER":   {"home": "substrate",    "service": "substrate_paper_trader"},
    "WARDEN":   {"home": "intelligence", "service": "code_vault"},
}

HEARTBEAT_ALIVE_SEC = 90
_SECRET_RX = re.compile(r"(?i)(api[-_ ]?key|private[-_ ]?key|secret|token)\s*[:=]\s*\S+")
_ADDR_RX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def scrub(text: Any, limit: int = 240) -> str:
    s = str(text or "")
    s = _SECRET_RX.sub(r"\1=[redacted]", s)
    s = _ADDR_RX.sub("[address]", s)
    return s[:limit]


# ── connection + guards ────────────────────────────────────────────────────
class Reader:
    """Every read is individually guarded. One bad table never kills a payload."""

    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self.notes: List[str] = []
        self.missing: List[str] = []
        self.conn: Optional[sqlite3.Connection] = None
        try:
            self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True,
                                        timeout=2.0)
            self.conn.execute("PRAGMA query_only=ON")
            self.conn.execute("PRAGMA busy_timeout=1200")
            self.conn.row_factory = sqlite3.Row
        except Exception as exc:
            self.notes.append(f"db_open_failed:{type(exc).__name__}")
            self.conn = None

    @property
    def ok(self) -> bool:
        return self.conn is not None

    def guard(self, label: str, fn: Callable, default=None):
        if self.conn is None:
            return default
        try:
            return fn()
        except Exception as exc:
            self.notes.append(f"{label}:{type(exc).__name__}")
            return default

    def has(self, table: str) -> bool:
        if self.conn is None:
            return False
        got = self.guard(f"has:{table}", lambda: self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone())
        if not got and table not in self.missing:
            self.missing.append(table)
        return bool(got)

    def cols(self, table: str) -> set:
        return self.guard(f"cols:{table}", lambda: {
            r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}, set()) or set()

    def rows(self, table: str, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        if not self.has(table):
            return []
        return self.guard(f"q:{table}", lambda: self.conn.execute(sql, params).fetchall(),
                          []) or []

    def one(self, table: str, sql: str, params: tuple = (), default=None):
        if not self.has(table):
            return default
        row = self.guard(f"q1:{table}", lambda: self.conn.execute(sql, params).fetchone())
        return row if row is not None else default

    def count(self, table: str, where: str = "1=1", params: tuple = ()) -> int:
        if not self.has(table):
            return 0
        r = self.guard(f"cnt:{table}", lambda: self.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone())
        return int(r[0]) if r else 0

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass


def _jload(raw: Any) -> dict:
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {"value": v}
    except Exception:
        return {}


# ── heartbeats ─────────────────────────────────────────────────────────────
def read_heartbeats(r: Reader, now: float) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not r.has("system_heartbeat"):
        return out
    cols = r.cols("system_heartbeat")
    tcol = "last_pulse" if "last_pulse" in cols else (
        "last_seen" if "last_seen" in cols else None)
    for row in r.rows("system_heartbeat", "SELECT * FROM system_heartbeat"):
        d = dict(row)
        pulse = d.get(tcol) if tcol else None
        age = (now - float(pulse)) if pulse else None
        status = str(d.get("status") or "").upper()
        out[str(d.get("service_name"))] = {
            "status": status or "UNKNOWN",
            "age": round(age, 1) if age is not None else None,
            # a RESTARTING claim with no pulse is NOT alive
            "alive": bool(age is not None and age < HEARTBEAT_ALIVE_SEC
                          and status not in ("RESTARTING", "ERROR", "DEAD")),
            "note": scrub(d.get("note"), 200),
        }
    return out


# ── HUD / trade truth ──────────────────────────────────────────────────────
def build_trade_truth(r: Reader, now: float) -> dict:
    """
    The profitability block. Deliberately blunt: this is the measure the whole
    organism exists to move, and the UI must never soften it.
    """
    out = {"available": False, "source": None, "mode": "UNKNOWN",
           "equity": None, "cash": None, "realized": None, "unrealized": None,
           "open_positions": 0, "closed_sample": 0, "wins": 0, "losses": 0,
           "flat": 0, "win_rate": None, "pnl_sum": None, "reason": None,
           "exit_reasons": [], "verdict": "UNKNOWN"}

    if not r.has("paper_positions"):
        out["reason"] = "paper_positions table absent"
        return out

    pc = r.cols("paper_positions")
    out["available"] = True
    out["source"] = "paper_positions"
    out["open_positions"] = r.count("paper_positions", "UPPER(status)='OPEN'")

    pnl_col = next((c for c in ("realized_pnl_usd", "realized_pnl", "pnl_usd", "pnl")
                    if c in pc), None)
    if pnl_col:
        closed = r.rows("paper_positions",
                        f"SELECT {pnl_col} AS p FROM paper_positions "
                        f"WHERE UPPER(status)!='OPEN' AND {pnl_col} IS NOT NULL "
                        f"ORDER BY rowid DESC LIMIT 50")
        vals = [float(x["p"]) for x in closed]
        out["closed_sample"] = len(vals)
        out["wins"] = sum(1 for v in vals if v > 0)
        out["losses"] = sum(1 for v in vals if v < 0)
        out["flat"] = sum(1 for v in vals if v == 0)
        out["pnl_sum"] = round(sum(vals), 4) if vals else None
        if vals:
            out["win_rate"] = round(out["wins"] / len(vals) * 100, 1)
    else:
        out["reason"] = "no realized-pnl column on paper_positions"

    if r.has("paper_wallet"):
        wc = r.cols("paper_wallet")
        w = r.one("paper_wallet", "SELECT * FROM paper_wallet LIMIT 1")
        if w:
            d = dict(w)
            if "equity" in wc:
                out["equity"] = round(float(d.get("equity") or 0), 2)
            if "cash_balance" in wc:
                out["cash"] = round(float(d.get("cash_balance") or 0), 2)
            for k in ("realized_pnl", "realized"):
                if k in wc and d.get(k) is not None:
                    out["realized"] = round(float(d[k]), 2)
                    break

    # why positions actually die — the loss funnel, unedited
    if r.has("trade_autopsies"):
        ac = r.cols("trade_autopsies")
        rc = next((c for c in ("exit_reason", "reason", "close_reason", "outcome")
                   if c in ac), None)
        if rc:
            rows = r.rows("trade_autopsies",
                          f"SELECT {rc} AS reason, COUNT(*) AS n FROM trade_autopsies "
                          f"WHERE {rc} IS NOT NULL GROUP BY {rc} ORDER BY n DESC LIMIT 8")
            out["exit_reasons"] = [{"reason": scrub(x["reason"], 48), "count": int(x["n"])}
                                   for x in rows]

    if r.has("system_config"):
        row = r.one("system_config",
                    "SELECT value FROM system_config WHERE key='TRADING_MODE'")
        out["mode"] = str(row[0]).upper() if row else "PAPER"

    wr = out["win_rate"]
    if wr is None:
        out["verdict"] = "UNKNOWN"
    elif wr < 20:
        out["verdict"] = "NOT PROFITABLE"
    elif wr < 45:
        out["verdict"] = "UNPROVEN"
    else:
        out["verdict"] = "IMPROVING"
    return out


# ── discoveries / relics ───────────────────────────────────────────────────
# disposition -> relic lifecycle state (visual grammar)
_DISPOSITION_STATE = {
    "COUNCIL_REVIEW": "inspecting",
    "PENDING_COUNCIL": "inspecting",
    "PENDING": "unidentified",
    "NEW": "unidentified",
    "PROMISING": "promising",
    "CONTESTED": "contested",
    "ACCEPTED": "accepted",
    "COUNCIL_ACCEPTED": "accepted",
    "REJECTED": "dud",
    "DISCARDED": "dud",
    "NOISE": "dud",
    "ABSORBED": "absorbed",
    "APPLIED": "testing",
}


def relic_state(disposition: str, safety: str, score: Any) -> str:
    """
    GOLD ('accepted') means: survived Council scrutiny.
    GOLD DOES NOT MEAN "an AI liked it" and it is NOT absorption.
    """
    d = str(disposition or "").upper().strip()
    if str(safety or "").upper() in ("UNSAFE", "BLOCKED", "FAIL", "REJECTED"):
        return "rejected"
    if d in _DISPOSITION_STATE:
        return _DISPOSITION_STATE[d]
    return "unidentified"


def build_discoveries(r: Reader, now: float) -> dict:
    out = {"available": False, "total": 0, "recent": [], "by_state": {},
           "scout": {"status": "UNKNOWN", "cycle": None, "repos": None,
                     "discoveries": None, "mode": None, "note": None},
           "reason": None}
    if not r.has("github_discovery_ledger"):
        out["reason"] = "github_discovery_ledger absent"
        return out
    out["available"] = True
    out["total"] = r.count("github_discovery_ledger")
    rows = r.rows("github_discovery_ledger",
                  "SELECT * FROM github_discovery_ledger "
                  "ORDER BY created_at DESC LIMIT 40")
    for row in rows:
        d = dict(row)
        st = relic_state(d.get("disposition"), d.get("safety_status"), d.get("score"))
        out["by_state"][st] = out["by_state"].get(st, 0) + 1
        out["recent"].append({
            "id": d.get("discovery_id"),
            "created_at": d.get("created_at"),
            "age_sec": round(now - float(d.get("created_at") or now), 1),
            "state": st,
            "repository": scrub(d.get("repository"), 90),
            "repository_url": scrub(d.get("repository_url"), 200),
            "commit_sha": scrub(d.get("commit_sha"), 42),
            "licence": scrub(d.get("licence"), 32),
            "language": scrub(d.get("language"), 24),
            "stars": d.get("stars"),
            "topic": scrub(d.get("topic"), 90),
            "project_key": scrub(d.get("project_key"), 60),
            "found_by": scrub(d.get("found_by"), 32) or "NUGGET",
            "files_examined": scrub(d.get("files_examined"), 200),
            "principle": scrub(d.get("extracted_principle"), 400),
            "relevance": scrub(d.get("relevance_reason"), 300),
            "safety_status": scrub(d.get("safety_status"), 32),
            "safety_findings": scrub(d.get("safety_findings"), 200),
            "score": d.get("score"),
            "value_label": scrub(d.get("value_label"), 40),
            "disposition": scrub(d.get("disposition"), 40),
        })

    if r.has("github_expedition_state"):
        st = r.one("github_expedition_state",
                   "SELECT * FROM github_expedition_state WHERE singleton=1")
        if st:
            d = dict(st)
            out["scout"] = {
                "status": scrub(d.get("status"), 40) or "UNKNOWN",
                "cycle": d.get("total_cycles"),
                "repos": d.get("repositories_seen"),
                "discoveries": d.get("discoveries_recorded"),
                "mode": scrub(d.get("mode_label") or d.get("current_mode"), 40),
                "active_query": scrub(d.get("active_query"), 120),
                "current_project": scrub(d.get("current_project"), 60),
                "note": scrub(d.get("last_note"), 160),
                "updated_at": d.get("updated_at"),
            }
    return out


# ── forge / patches ────────────────────────────────────────────────────────
def build_forge(r: Reader, now: float) -> dict:
    """
    The stage where PATCH WRITTEN is kept rigorously distinct from APPLIED.
    compile = axon_dry_run/axon_passed, smoke = test_result, verify = verify_result.
    """
    out = {"available": False, "projects": [], "patches": [], "counts": {},
           "reason": None}

    if r.has("forge_projects"):
        out["available"] = True
        for row in r.rows("forge_projects",
                          "SELECT * FROM forge_projects ORDER BY priority DESC, "
                          "updated_at DESC LIMIT 12"):
            d = dict(row)
            out["projects"].append({
                "key": scrub(d.get("project_key"), 60),
                "title": scrub(d.get("title"), 90),
                "stage": scrub(d.get("current_stage"), 32) or "RESEARCH",
                "status": scrub(d.get("status"), 24),
                "updated_at": d.get("updated_at"),
            })

    if not r.has("code_patches"):
        out["reason"] = "code_patches absent — no implementation has been written"
        return out

    out["available"] = True
    pc = r.cols("code_patches")
    for row in r.rows("code_patches",
                      "SELECT * FROM code_patches ORDER BY created_at DESC LIMIT 25"):
        d = dict(row)
        status = str(d.get("status") or "").lower()
        applied_at = d.get("applied_at")
        compile_v = d.get("axon_passed") if "axon_passed" in pc else None
        if compile_v in (None, "") and "axon_dry_run" in pc:
            compile_v = d.get("axon_dry_run")
        rec = {
            "id": d.get("id"),
            "created_at": d.get("created_at"),
            "target_file": scrub(d.get("target_file") or d.get("file_path"), 140),
            "description": scrub(d.get("description"), 240),
            "author": scrub(d.get("author_agent"), 32) or "AXON",
            "project_key": scrub(d.get("project_key"), 60),
            "proposal_id": d.get("proposal_id"),
            "tier": scrub(d.get("tier"), 12),
            "diff_chars": d.get("diff_chars"),
            "status": status or "unknown",
            # ── the tri-states ──
            "compile": tri(compile_v),
            "smoke": tri(d.get("test_result") if "test_result" in pc else None),
            "verify": tri(d.get("verify_result") if "verify_result" in pc else None),
            "applied": bool(applied_at) and status in ("applied", "active", "live"),
            "applied_at": applied_at,
            "rolled_back_at": d.get("rolled_back_at"),
        }
        # ABSORBED is the strictest possible reading of the evidence
        rec["absorbed"] = bool(
            rec["applied"] and rec["compile"] == PASS
            and rec["smoke"] == PASS and rec["verify"] == PASS
            and not rec["rolled_back_at"])
        rec["stage_label"] = (
            "ROLLED BACK" if rec["rolled_back_at"] else
            "ABSORBED" if rec["absorbed"] else
            "APPLIED — UNVERIFIED" if rec["applied"] else
            "PATCH WRITTEN")
        out["patches"].append(rec)

    out["counts"] = {
        "written": len(out["patches"]),
        "applied": sum(1 for p in out["patches"] if p["applied"]),
        "absorbed": sum(1 for p in out["patches"] if p["absorbed"]),
        "unverified": sum(1 for p in out["patches"]
                          if p["applied"] and not p["absorbed"]),
        "compile_not_run": sum(1 for p in out["patches"] if p["compile"] == NOT_RUN),
        "smoke_not_run": sum(1 for p in out["patches"] if p["smoke"] == NOT_RUN),
        "verify_not_run": sum(1 for p in out["patches"] if p["verify"] == NOT_RUN),
    }
    return out


# ── polaris ────────────────────────────────────────────────────────────────
def build_polaris(r: Reader, hb: Dict[str, dict]) -> dict:
    out = {"available": False, "open": 0, "recent": [], "gate": "UNKNOWN",
           "heartbeat": hb.get("polaris", {}), "trades": None, "win_rate": None,
           "reason": None}
    note = str(out["heartbeat"].get("note") or "")
    payload = _jload(note) if note.strip().startswith("{") else {}
    if payload:
        out["trades"] = payload.get("trades")
        out["win_rate"] = payload.get("win_rate")
        out["last_outcome"] = scrub(payload.get("last_outcome"), 60)
        out["mission_lock"] = payload.get("mission_lock")

    if not r.has("polaris_proposals"):
        out["reason"] = "polaris_proposals absent"
        return out
    out["available"] = True
    out["open"] = r.count("polaris_proposals",
                          "LOWER(status) IN ('open','debating','approved')")
    for row in r.rows("polaris_proposals",
                      "SELECT * FROM polaris_proposals ORDER BY COALESCE(last_seen_at,"
                      "created_at) DESC LIMIT 12"):
        d = dict(row)
        out["recent"].append({
            "id": d.get("id"),
            "type": scrub(d.get("proposal_type"), 48),
            "text": scrub(d.get("proposal_text"), 300),
            "action": scrub(d.get("suggested_action"), 160),
            "confidence": d.get("confidence"),
            "status": scrub(d.get("status"), 24),
            "domain": scrub(d.get("proposal_domain"), 24),
            "stage": scrub(d.get("stage"), 32),
            "created_at": d.get("created_at"),
            "seen_count": d.get("seen_count"),
        })
    # Polaris is a gate, not a cheerleader: it only reads PASS on real evidence.
    out["gate"] = "AWAITING IMPLEMENTATION EVIDENCE" if out["open"] == 0 else "REVIEWING"
    return out


# ── debate (deduplicated) ──────────────────────────────────────────────────
def build_debate(r: Reader, now: float) -> dict:
    """
    §9: collapse near-duplicate agent speech into canonical events with an
    observation count. Raw utterances survive underneath — deduplicated in
    PRESENTATION only, never deleted.
    """
    out = {"available": False, "total_rows": 0, "canonical": [], "positions": [],
           "question": None, "reason": None}
    if not r.has("debate_log"):
        out["reason"] = "debate_log absent"
        return out
    out["available"] = True
    out["total_rows"] = r.count("debate_log")
    dc = r.cols("debate_log")
    tcol = "logged_at" if "logged_at" in dc else "id"

    rows = r.rows("debate_log",
                  f"SELECT * FROM debate_log ORDER BY {tcol} DESC LIMIT 400")

    groups: Dict[tuple, dict] = {}
    for row in rows:
        d = dict(row)
        speaker = str(d.get("speaker") or "SYSTEM").upper()
        action = str(d.get("action") or "").upper()
        payload = _jload(d.get("content_json"))
        summary = scrub(payload.get("summary") or d.get("message") or action, 200)
        # collapse key: same agent making the same kind of statement
        key = (speaker, action, summary[:60].lower())
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "agent": speaker, "action": action or "STATEMENT",
                "summary": summary, "count": 0,
                "first_at": d.get(tcol), "last_at": d.get(tcol),
                "confidence": d.get("confidence"),
                "consensus": d.get("consensus"),
                "is_final": d.get("is_final"),
                "raw": [],
            }
        g["count"] += 1
        ts = d.get(tcol)
        if ts and g["last_at"] and ts > g["last_at"]:
            g["last_at"] = ts
        if ts and g["first_at"] and ts < g["first_at"]:
            g["first_at"] = ts
        if len(g["raw"]) < 8:
            g["raw"].append({
                "at": ts, "round": d.get("round_num"),
                "detail": scrub(payload.get("details") or d.get("content_json"), 300),
                "confidence": d.get("confidence"),
            })

    canonical = sorted(groups.values(), key=lambda g: (g["last_at"] or 0), reverse=True)
    out["canonical"] = canonical[:24]

    # current decisive positions: newest statement per agent
    seen = set()
    for g in canonical:
        if g["agent"] in seen or g["agent"] == "SYSTEM":
            continue
        seen.add(g["agent"])
        act = g["action"]
        stance = ("support" if "APPROV" in act or "SUPPORT" in act or "ACCEPT" in act
                  else "challenge" if "REJECT" in act or "CHALLENG" in act or "BLOCK" in act
                  else "observation")
        out["positions"].append({
            "agent": g["agent"], "stance": stance, "action": act,
            "confidence": g["confidence"], "summary": g["summary"],
            "observations": g["count"],
        })
        if len(out["positions"]) >= 6:
            break
    return out


# ── council quests ─────────────────────────────────────────────────────────
def build_quests(r: Reader, now: float) -> dict:
    out = {"available": False, "active": None, "all": [], "events": [],
           "chamber": {}, "reason": None}
    if not r.has("council_quests"):
        out["reason"] = "council_quests absent"
        return out
    out["available"] = True
    for row in r.rows("council_quests",
                      "SELECT * FROM council_quests ORDER BY updated_at DESC LIMIT 8"):
        d = dict(row)
        q = {
            "id": d.get("id"),
            "faculty": scrub(d.get("faculty"), 32),
            "question": scrub(d.get("question"), 400),
            "status": scrub(d.get("status"), 24),
            "stage": scrub(d.get("current_stage"), 32),
            "success_condition": scrub(d.get("success_condition"), 300),
            "kill_condition": scrub(d.get("kill_condition"), 300),
            "opened_at": d.get("opened_at"),
            "updated_at": d.get("updated_at"),
        }
        out["all"].append(q)
        if str(q["status"]).upper() == "ACTIVE" and out["active"] is None:
            out["active"] = q

    if out["active"] and r.has("council_quest_events"):
        for row in r.rows("council_quest_events",
                          "SELECT * FROM council_quest_events WHERE quest_id=? "
                          "ORDER BY created_at DESC LIMIT 40",
                          (out["active"]["id"],)):
            d = dict(row)
            out["events"].append({
                "id": d.get("id"), "stage": scrub(d.get("stage"), 32),
                "actor": scrub(d.get("actor_role"), 32),
                "type": scrub(d.get("event_type"), 40),
                "summary": scrub(d.get("summary"), 300),
                "next_action": scrub(d.get("next_action"), 200),
                "source_table": scrub(d.get("source_table"), 60),
                "source_row_id": scrub(d.get("source_row_id"), 40),
                "created_at": d.get("created_at"),
            })
    return out


# ── build tasks (council ledger) ───────────────────────────────────────────
def build_tasks(r: Reader) -> dict:
    out = {"available": False, "tasks": [], "blocked": [], "reason": None}
    table = "council_task_ledger"
    if not r.has(table):
        out["reason"] = f"{table} absent"
        return out
    out["available"] = True
    for row in r.rows(table, f"SELECT * FROM {table} ORDER BY updated_at DESC LIMIT 20"):
        d = dict(row)
        t = {
            "id": d.get("canonical_id"),
            "title": scrub(d.get("title"), 160),
            "phase": scrub(d.get("phase"), 32),
            "owner": scrub(d.get("owner"), 24),
            "progress": d.get("progress_pct"),
            "next_action": scrub(d.get("next_action"), 200),
            "blocker": scrub(d.get("blocker_code"), 120),
            "verification": tri(d.get("verification_result")),
            "updated_at": d.get("updated_at"),
        }
        out["tasks"].append(t)
        if t["blocker"]:
            out["blocked"].append(t)
    return out


# ── faculties ──────────────────────────────────────────────────────────────
def build_faculties(r: Reader, hb: Dict[str, dict], forge: dict,
                    discoveries: dict) -> dict:
    """
    Faculty health is derived from real service heartbeats. A faculty whose
    services are down/restarting looks STRESSED in the world. Capability
    markers are granted ONLY by absorbed patches — never by a written patch.
    """
    out = {}
    absorbed_by_project: Dict[str, int] = {}
    for p in forge.get("patches", []):
        if p.get("absorbed"):
            k = (p.get("project_key") or "").upper()
            absorbed_by_project[k] = absorbed_by_project.get(k, 0) + 1

    for key, meta in FACULTIES.items():
        svcs = meta["services"]
        states = [hb.get(s) for s in svcs if s in hb]
        known = [s for s in states if s]
        alive = sum(1 for s in known if s["alive"])
        restarting = sum(1 for s in known if s["status"] == "RESTARTING")
        degraded = sum(1 for s in known if s["status"] in ("DEGRADED", "WARN", "ALERT"))
        errored = sum(1 for s in known if s["status"] in ("ERROR", "DEAD"))

        if not known:
            health = "UNKNOWN"
        elif errored or (restarting and alive == 0):
            health = "STRESSED"
        elif degraded or restarting:
            health = "DEGRADED"
        elif alive == len(known):
            health = "HEALTHY"
        elif alive:
            health = "PARTIAL"
        else:
            health = "STRESSED"

        caps = absorbed_by_project.get(key, 0)
        out[key] = {
            "label": meta["label"], "site": meta["site"], "health": health,
            "services_alive": alive, "services_known": len(known),
            "services_total": len(svcs),
            "restarting": restarting, "degraded": degraded, "errored": errored,
            "capabilities": caps,
            "detail": [{"service": s, "status": (hb.get(s) or {}).get("status", "ABSENT"),
                        "age": (hb.get(s) or {}).get("age"),
                        "note": (hb.get(s) or {}).get("note", "")} for s in svcs],
        }
    return out


# ── expedition state machine ───────────────────────────────────────────────
def build_expedition(quests: dict, discoveries: dict, debate: dict, forge: dict,
                     polaris: dict, tasks: dict, hb: Dict[str, dict]) -> dict:
    """
    ONE dominant current story. Derived, never invented. If nothing is in
    flight the expedition is explicitly DORMANT — the world must be allowed to
    look quiet.
    """
    exp = {
        "id": None, "title": None, "faculty": None, "found": None, "why": None,
        "stage": "DORMANT", "stage_index": 0, "relic_state": "unidentified",
        "current_action": None, "council": {"support": 0, "challenge": 0},
        "discovery": None, "quest": None, "patch": None,
        "journey": [], "blocked_reason": None, "evidence_refs": [],
    }

    scout = discoveries.get("scout", {})
    scout_hb = hb.get("github_scout", {})
    scout_idle = str(scout_hb.get("status", "")).lower() in ("idle", "")

    # the relic under consideration: newest non-dud discovery, else newest
    recent = discoveries.get("recent", [])
    live = [d for d in recent if d["state"] not in ("dud", "rejected")]
    disc = (live or recent or [None])[0]

    quest = quests.get("active")
    if quest:
        exp["quest"] = quest
        exp["faculty"] = quest.get("faculty")
        exp["why"] = quest.get("question")
        exp["evidence_refs"].append(f"council_quests:{quest.get('id')}")

    if disc:
        exp["discovery"] = disc
        exp["id"] = disc["id"]
        exp["title"] = disc.get("topic") or disc.get("repository") or "Untitled find"
        exp["found"] = disc.get("principle") or disc.get("relevance") or None
        exp["relic_state"] = disc["state"]
        exp["faculty"] = exp["faculty"] or (disc.get("project_key") or "").upper() or None
        exp["evidence_refs"].append(f"github_discovery_ledger:{disc['id']}")

    # patch tied to this story, if any
    patch = None
    for p in forge.get("patches", []):
        if disc and p.get("project_key") and disc.get("project_key") \
                and p["project_key"] == disc["project_key"]:
            patch = p
            break
    if patch is None and forge.get("patches"):
        patch = forge["patches"][0]
    if patch:
        exp["patch"] = patch
        exp["evidence_refs"].append(f"code_patches:{patch['id']}")

    for p in debate.get("positions", []):
        if p["stance"] == "support":
            exp["council"]["support"] += 1
        elif p["stance"] == "challenge":
            exp["council"]["challenge"] += 1

    # ── stage resolution: strictly evidence-ordered ──
    stage = "EXPLORE"
    if scout_idle and not disc:
        stage = "EXPLORE"
    if disc:
        stage = "DISCOVER"
        if disc.get("principle"):
            stage = "EXTRACT"
    if debate.get("canonical"):
        stage = "DEBATE" if stage in ("DISCOVER", "EXTRACT") else stage
    if disc and disc["state"] == "accepted":
        stage = "FORGE"
    if patch:
        stage = "FORGE"
        if patch["compile"] != NOT_RUN or patch["smoke"] != NOT_RUN:
            stage = "VERIFY"
        if patch["verify"] == PASS:
            stage = "POLARIS"
        if patch["absorbed"]:
            stage = "ABSORB"
    if disc and disc["state"] in ("dud", "rejected"):
        stage = "ABSORB"          # terminal: rejection is an outcome, not a failure state

    exp["stage"] = stage
    exp["stage_index"] = JOURNEY.index(stage) if stage in JOURNEY else 0

    # ── per-stage status for the journey rail ──
    def st(name: str, done: bool, active: bool, detail: str, evidence: str = "",
           failed: bool = False) -> dict:
        # the stage the organism is standing on reads ACTIVE even when its
        # evidence already exists — the rail shows position, not just history
        return {"stage": name,
                "status": ("FAILED" if failed else "ACTIVE" if active
                           else "DONE" if done else "PENDING"),
                "detail": detail, "evidence": evidence}

    idx = exp["stage_index"]
    dstate = disc["state"] if disc else None
    exp["journey"] = [
        st("EXPLORE", bool(disc) or bool(scout.get("repos")), idx == 0,
           f"scout {scout_hb.get('status','UNKNOWN')} · cycle {scout.get('cycle')} · "
           f"repos {scout.get('repos')}", "github_expedition_state"),
        st("DISCOVER", bool(disc), idx == 1,
           disc["repository"] if disc else "no discovery recorded",
           f"github_discovery_ledger:{disc['id']}" if disc else ""),
        st("EXTRACT", bool(disc and disc.get("principle")), idx == 2,
           (disc.get("principle")[:90] if disc and disc.get("principle")
            else "no principle extracted"),
           f"github_discovery_ledger:{disc['id']}" if disc else ""),
        st("DEBATE", bool(debate.get("canonical")), idx == 3,
           f"{len(debate.get('canonical', []))} canonical events from "
           f"{debate.get('total_rows', 0)} raw rows", "debate_log"),
        st("FORGE", bool(patch), idx == 4,
           patch["stage_label"] if patch else "no patch written", "code_patches"),
        st("VERIFY", bool(patch and patch["compile"] == PASS and patch["smoke"] == PASS),
           idx == 5,
           (f"compile {patch['compile']} · smoke {patch['smoke']} · "
            f"verify {patch['verify']}" if patch else "nothing to verify"),
           "code_patches",
           failed=bool(patch and (patch["compile"] == FAIL or patch["smoke"] == FAIL))),
        st("POLARIS", bool(patch and patch["verify"] == PASS), idx == 6,
           polaris.get("gate", "UNKNOWN"), "polaris_proposals"),
        st("ABSORB", bool(patch and patch["absorbed"]), idx == 7,
           ("absorbed into organism" if patch and patch["absorbed"]
            else "discovery discarded" if dstate in ("dud", "rejected")
            else "nothing absorbed"), "code_patches",
           failed=bool(dstate in ("dud", "rejected"))),
        st("MEASURE", False, idx == 8,
           "awaiting executable outcome evidence", "paper_positions"),
    ]

    # blockers surface as blockers, never as progress
    blocked = tasks.get("blocked") or []
    orch = hb.get("council_build_orchestrator", {})
    if blocked:
        exp["blocked_reason"] = blocked[0].get("blocker")
    elif "BLOCKED" in str(orch.get("note", "")).upper():
        m = re.search(r"(GATE_BLOCKED[^\"'}]*)", str(orch.get("note")))
        exp["blocked_reason"] = scrub(m.group(1) if m else orch.get("note"), 160)

    if exp["blocked_reason"]:
        exp["current_action"] = f"BLOCKED · {exp['blocked_reason']}"
    elif scout_idle and not disc:
        exp["current_action"] = "Scout is resting at the settlement — no expedition underway"
    elif stage == "DEBATE":
        exp["current_action"] = "Council is inspecting the find"
    elif stage == "FORGE":
        exp["current_action"] = "Axon is preparing an isolated implementation"
    elif stage == "VERIFY":
        exp["current_action"] = "Awaiting compile / smoke evidence"
    elif stage == "POLARIS":
        exp["current_action"] = "Polaris is reviewing implementation evidence"
    else:
        exp["current_action"] = "Observing"

    if not exp["title"]:
        exp["title"] = (quest.get("question")[:70] if quest else "No active expedition")
    return exp


# ── world projection (semantic sprite placement) ───────────────────────────
def build_world(exp: dict, faculties: dict, hb: Dict[str, dict],
                discoveries: dict) -> dict:
    """
    §4: every actor position derives from observable runtime state. There is no
    wander timer. An idle scout SITS. A blocked build LOOKS blocked.
    """
    stage = exp["stage"]
    actors: Dict[str, dict] = {}

    def place(aid: str, site: str, action: str, note: str = "", carrying: bool = False):
        actors[aid] = {"site": site, "action": action, "note": scrub(note, 120),
                       "carrying": carrying}

    for aid, meta in COUNCIL_AGENTS.items():
        svc = hb.get(meta["service"], {})
        alive = svc.get("alive")
        status = svc.get("status", "ABSENT")
        if status == "RESTARTING":
            place(aid, meta["home"], "RECOVERING", f"{meta['service']} restarting")
        elif alive is False or status in ("ERROR", "DEAD"):
            place(aid, meta["home"], "OFFLINE", f"{meta['service']} {status.lower()}")
        else:
            place(aid, meta["home"], "IDLE", meta["role"])

    # ── expedition choreography, one stage at a time ──
    if stage == "EXPLORE":
        scout_hb = hb.get("github_scout", {})
        if str(scout_hb.get("status", "")).lower() == "idle":
            place("NUGGET", "council", "RESTING", "Scout idle — no repositories examined")
        else:
            place("NUGGET", "trailhead", "DEPARTING", "Leaving for the wild forest")
    elif stage == "DISCOVER":
        place("NUGGET", "inspection", "RETURNING",
              exp["discovery"]["repository"] if exp["discovery"] else "", carrying=True)
    elif stage == "EXTRACT":
        place("NUGGET", "inspection", "PRESENTING", "Setting down the find", carrying=True)
        place("RHIZA", "inspection", "EXTRACTING", "Reading the principle")
    elif stage == "DEBATE":
        for aid in ("NUGGET", "IVARIS", "ORACLE", "RHIZA", "POLARIS"):
            place(aid, "inspection", "DEBATING", "Council inspection")
        place("IVARIS", "inspection", "CHALLENGING", "Adversarial review")
    elif stage == "FORGE":
        place("AXON", "execution", "FORGING", "Preparing isolated implementation")
        place("POLARIS", "execution", "OBSERVING", "Watching the build")
    elif stage == "VERIFY":
        place("AXON", "execution", "TESTING", "Compile / smoke in progress")
    elif stage == "POLARIS":
        place("POLARIS", "execution", "VERIFYING", "Reviewing implementation evidence")
        place("AXON", "execution", "WAITING", "Awaiting gate")
    elif stage == "ABSORB":
        target = FACULTIES.get(str(exp.get("faculty") or "").upper(), {}).get("site")
        if exp.get("patch") and exp["patch"].get("absorbed"):
            place("POLARIS", target or "heart", "ABSORBING", "Capability absorbed")
        else:
            place("NUGGET", "inspection", "DISCARDING", "Discovery discarded")

    if exp.get("blocked_reason"):
        place("AXON", "execution", "BLOCKED", exp["blocked_reason"])

    for aid, meta in NPC_AGENTS.items():
        svc = hb.get(meta["service"], {})
        actors[aid] = {"site": meta["home"],
                       "action": "IDLE" if svc.get("alive") else "OFFLINE",
                       "note": scrub(svc.get("note", ""), 100), "carrying": False}

    relic = None
    if exp.get("discovery"):
        relic_site = {
            "EXPLORE": None, "DISCOVER": "trailhead", "EXTRACT": "inspection",
            "DEBATE": "inspection", "FORGE": "execution", "VERIFY": "execution",
            "POLARIS": "execution",
        }.get(stage, "inspection")
        if stage == "ABSORB":
            p = exp.get("patch")
            relic_site = (FACULTIES.get(str(exp.get("faculty") or "").upper(), {})
                          .get("site", "heart")) if (p and p.get("absorbed")) else "inspection"
        if relic_site:
            relic = {"site": relic_site, "state": exp["relic_state"],
                     "label": exp["title"]}

    return {"actors": actors, "relic": relic, "stage": stage,
            "faculties": {k: {"health": v["health"], "site": v["site"],
                              "capabilities": v["capabilities"],
                              "label": v["label"]}
                          for k, v in faculties.items()}}


# ── canonical event stream ─────────────────────────────────────────────────
def build_events(quests: dict, discoveries: dict, debate: dict, forge: dict,
                 polaris: dict, now: float) -> List[dict]:
    """
    §11: ONE normalized event list. World renders movement from it, Chamber
    renders argument from it, Chronicle renders history from it, Evidence
    renders provenance from it. Nobody re-describes anything.
    """
    ev: List[dict] = []

    def add(**kw):
        kw.setdefault("confidence", None)
        kw.setdefault("faculty", None)
        kw.setdefault("compile", None)
        kw.setdefault("smoke", None)
        kw.setdefault("verify", None)
        kw.setdefault("polaris", None)
        kw.setdefault("result", None)
        ev.append(kw)

    for d in discoveries.get("recent", [])[:24]:
        add(event_id=f"discovery:{d['id']}", timestamp=d.get("created_at"),
            quest_id=None, agent=d.get("found_by") or "NUGGET",
            event_type="DISCOVERY", faculty=(d.get("project_key") or "").upper(),
            source=f"github_discovery_ledger:{d['id']}",
            summary=f"{d['repository']} — {d.get('topic') or 'find'}",
            detail=d.get("principle") or d.get("relevance") or "",
            state=d["state"], confidence=d.get("score"),
            repo=d.get("repository"), repo_url=d.get("repository_url"),
            file=d.get("files_examined"), commit=d.get("commit_sha"),
            result=d.get("disposition"))

    for g in debate.get("canonical", [])[:24]:
        add(event_id=f"debate:{g['agent']}:{abs(hash(g['summary'])) % 10**8}",
            timestamp=g.get("last_at"), quest_id=None, agent=g["agent"],
            event_type=f"COUNCIL · {g['action']}", source="debate_log",
            summary=g["summary"], detail=f"{g['count']} related observations",
            state="DEBATE", confidence=g.get("confidence"),
            observations=g["count"], raw=g.get("raw", []))

    for p in forge.get("patches", [])[:16]:
        add(event_id=f"patch:{p['id']}", timestamp=p.get("created_at"), quest_id=None,
            agent=p.get("author") or "AXON", event_type="FORGE",
            source=f"code_patches:{p['id']}",
            summary=f"{p['stage_label']} — {p.get('target_file') or 'unknown target'}",
            detail=p.get("description") or "", state=p["stage_label"],
            compile=p["compile"], smoke=p["smoke"], verify=p["verify"],
            build_id=p["id"], file=p.get("target_file"),
            result="ABSORBED" if p["absorbed"] else
                   ("APPLIED_UNVERIFIED" if p["applied"] else "WRITTEN"))

    for q in quests.get("events", [])[:24]:
        add(event_id=f"quest:{q['id']}", timestamp=q.get("created_at"),
            quest_id=None, agent=q.get("actor") or "COUNCIL",
            event_type=f"QUEST · {q['type']}", source=f"council_quest_events:{q['id']}",
            summary=q.get("summary") or "", detail=q.get("next_action") or "",
            state=q.get("stage"))

    for p in polaris.get("recent", [])[:12]:
        add(event_id=f"proposal:{p['id']}", timestamp=p.get("created_at"), quest_id=None,
            agent="POLARIS", event_type="PROPOSAL",
            source=f"polaris_proposals:{p['id']}",
            summary=p.get("text") or p.get("type") or "", detail=p.get("action") or "",
            state=p.get("status"), confidence=p.get("confidence"),
            polaris=p.get("status"))

    ev.sort(key=lambda e: (e.get("timestamp") or 0), reverse=True)
    for e in ev:
        ts = e.get("timestamp")
        e["age_sec"] = round(now - float(ts), 1) if ts else None
    return ev


# ── top level ──────────────────────────────────────────────────────────────
def load_canonical_state(db_path: Path | str = DEFAULT_DB) -> dict:
    now = time.time()
    r = Reader(db_path)

    if not r.ok:
        return {
            "canon_version": CANON_VERSION, "generated_at": now, "db_ok": False,
            "reason": "database unavailable — world renders as unknown, not as healthy",
            "notes": r.notes, "missing_tables": [],
            "hud": {}, "world": {"actors": {}, "relic": None, "faculties": {}},
            "expedition": {"stage": "UNKNOWN", "journey": []},
            "debate": {"available": False}, "forge": {"available": False},
            "polaris": {"available": False}, "trade_truth": {"available": False},
            "discoveries": {"available": False}, "quests": {"available": False},
            "faculties": {}, "events": [], "chronicle": [],
        }

    hb = read_heartbeats(r, now)
    trade = build_trade_truth(r, now)
    discoveries = build_discoveries(r, now)
    forge = build_forge(r, now)
    polaris = build_polaris(r, hb)
    debate = build_debate(r, now)
    quests = build_quests(r, now)
    tasks = build_tasks(r)
    faculties = build_faculties(r, hb, forge, discoveries)
    exp = build_expedition(quests, discoveries, debate, forge, polaris, tasks, hb)
    world = build_world(exp, faculties, hb, discoveries)
    events = build_events(quests, discoveries, debate, forge, polaris, now)

    alive = sum(1 for v in hb.values() if v["alive"])
    restarting = sum(1 for v in hb.values() if v["status"] == "RESTARTING")

    oracle = hb.get("ws_price_oracle", {})
    hud = {
        "mode": trade.get("mode", "UNKNOWN"),
        "equity": trade.get("equity"),
        "cash": trade.get("cash"),
        "realized": trade.get("realized"),
        "open_positions": trade.get("open_positions"),
        "win_rate": trade.get("win_rate"),
        "closed_sample": trade.get("closed_sample"),
        "verdict": trade.get("verdict"),
        "oracle_state": oracle.get("status", "UNKNOWN"),
        "oracle_note": oracle.get("note", ""),
        "services_alive": alive,
        "services_total": len(hb),
        "services_restarting": restarting,
        "risk": ("PRICE COVERAGE DEGRADED"
                 if oracle.get("status") in ("DEGRADED", "RESTARTING", "ERROR")
                 else "NOMINAL"),
    }

    state = {
        "canon_version": CANON_VERSION,
        "generated_at": now,
        "db_ok": True,
        "hud": hud,
        "world": world,
        "expedition": exp,
        "discoveries": discoveries,
        "quests": quests,
        "debate": debate,
        "forge": forge,
        "polaris": polaris,
        "tasks": tasks,
        "faculties": faculties,
        "trade_truth": trade,
        "events": events,
        "chronicle": events[:60],
        "heartbeats": hb,
        "notes": r.notes,
        "missing_tables": r.missing,
    }
    r.close()
    return state


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_DB)
    s = load_canonical_state(db)
    print(json.dumps({k: v for k, v in s.items()
                      if k not in ("events", "chronicle", "heartbeats")},
                     indent=2, default=str)[:6000])
