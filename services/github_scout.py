# coding: utf-8
"""
services/github_scout.py
========================
SENTINUITY_GITHUB_EXPEDITION_20260810

Read-only external-code reconnaissance for the Sentinuity Council.

This service deliberately does NOT clone, execute, install, compile, import or
copy repository code. GitHub contents are untrusted research evidence. The
Scout reads repository metadata, a commit-pinned source tree and a small set of
UTF-8 text/source files in memory, extracts structural signals, screens obvious
prompt-injection / execution-risk patterns, and records a provenance-rich
finding for the existing inspiration -> Council -> Forge pipeline.

Existing authority remains intact:
  github_scout -> inspiration_intake_ledger -> council_execution_spine
  -> proposal -> Golden Latch -> council_apply / operator gate

The scout cannot apply code and cannot change live trading authority.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat
from services.provider_firewall import check_provider, log_api_call

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=True)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [github_scout] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("github_scout")

SERVICE_NAME = "github_scout"
NORMAL_CYCLE_SECONDS = int(os.getenv("GITHUB_SCOUT_CYCLE_SECONDS", "2700"))  # 45m
BOOTSTRAP_CYCLE_SECONDS = int(os.getenv("GITHUB_SCOUT_BOOTSTRAP_SECONDS", "900"))  # 15m
BOOTSTRAP_CYCLES = int(os.getenv("GITHUB_SCOUT_BOOTSTRAP_CYCLES", "4"))
ENTRY_TTL_SEC = 86400
MAX_REPOS_PER_QUERY = 5
MAX_SOURCE_FILES = 7
MAX_SOURCE_BYTES = 140_000

# Default long-run allocation: 50% long-promised organism buildout,
# 35% trading-edge reconnaissance, 15% frontier architecture.
# This is represented as a 20-cycle deterministic trail so the Council cannot
# starve the Intel/Substrate buildout simply because trading repositories are
# more numerous or more exciting.
# Interleaved 20-cycle trail preserves the exact 50/35/15 allocation while
# ensuring a short operator session sees both organism buildout and edge hunting.
# The prior grouped 10/7/3 sequence could spend many hours entirely in BUILDOUT.
_MODE_TRAIL = [
    "BUILDOUT", "EDGE_HUNT", "BUILDOUT", "FRONTIER",
    "BUILDOUT", "EDGE_HUNT", "BUILDOUT", "EDGE_HUNT",
    "BUILDOUT", "FRONTIER", "EDGE_HUNT", "BUILDOUT",
    "EDGE_HUNT", "BUILDOUT", "FRONTIER", "EDGE_HUNT",
    "BUILDOUT", "EDGE_HUNT", "BUILDOUT", "BUILDOUT",
]

MODE_LABELS = {
    "BUILDOUT": "BUILDING THE ORGANISM",
    "EDGE_HUNT": "HUNTING FOR TRANSFERABLE EDGE",
    "FRONTIER": "SCOUTING THE FRONTIER",
}
# Discovery doctrine: GitHub code is evidence, never authority.  Architecture
# may become a Council build request; trading constants/thresholds are only
# hypotheses until Sentinuity's own paper/outcome history supports them.
# High-value findings flow through inspiration_intake_ledger and the existing
# Council execution spine; foreign repository code is never executed.

MODE_DESCRIPTIONS = {
    "BUILDOUT": "Intel, Substrate Node, Council continuity and the self-building organism take priority.",
    "EDGE_HUNT": "Copy trading, smart-money mapping, Solana entry selection, launch trading and sniper architecture are being inspected.",
    "FRONTIER": "The Council is looking beyond trading bots for better orchestration, observability, memory and self-repair patterns.",
}

EXPEDITIONS = {
    "BUILDOUT": [
        {
            "project_key": "intelligence_tab_evolution",
            "topic": "intelligence_observability",
            "queries": [
                "trading intelligence dashboard provenance research stream python",
                "multi agent observability dashboard debate research ledger python",
                "agent research discovery implementation pipeline evidence dashboard python",
            ],
            "keywords": ("provenance", "research", "event", "ledger", "dashboard", "observability", "agent"),
        },
        {
            "project_key": "substrate_node_evolution",
            "topic": "substrate_opportunity_architecture",
            "queries": [
                "event driven crypto opportunity scanner portfolio paper trading python",
                "multi exchange copy trading architecture portfolio supervisor python",
                "autonomous opportunity research node paper execution evidence ledger python",
            ],
            "keywords": ("opportunity", "portfolio", "scanner", "signal", "event", "paper", "copy"),
        },
        {
            "project_key": "autonomous_organism_buildout",
            "topic": "safe_autonomous_build",
            "queries": [
                "autonomous coding agent safe patch rollback planner critic tester python",
                "multi agent software engineering planner reviewer sandbox patch python",
            ],
            "keywords": ("patch", "rollback", "planner", "critic", "review", "sandbox", "test"),
        },
    ],
    "EDGE_HUNT": [
        # ENTRY EDGE ---------------------------------------------------------
        {
            "project_key": "solana_entry_pipeline_research",
            "topic": "pumpfun_entry_selection",
            "queries": [
                "pump fun trading bot solana bonding curve market cap liquidity filter",
                "solana memecoin early entry fresh pair momentum liquidity bot",
                "pumpfun smart money market cap entry regime wallet convergence solana",
                "solana token launch filter bonding curve graduation entry strategy",
            ],
            "keywords": ("pump", "bonding", "curve", "liquidity", "market", "momentum", "solana", "entry"),
        },

        # SMART CAPITAL / COPYTRADE -----------------------------------------
        {
            "project_key": "wallet_convergence",
            "topic": "smart_money_copytrade",
            "queries": [
                "solana smart money wallet tracking copy trading python",
                "solana whale wallet copytrade scoring wallet performance",
                "solana wallet funding ancestry cluster copytrade profitable wallets",
                "solana holder concentration wallet convergence smart capital bot",
            ],
            "keywords": ("wallet", "copy", "whale", "smart", "performance", "funding", "holder", "solana"),
        },

        # MANIPULATION / SAFETY ---------------------------------------------
        {
            "project_key": "solana_manipulation_research",
            "topic": "bundle_insider_rug_detection",
            "queries": [
                "solana bundled wallet detection pumpfun insider dev wallet bot",
                "pumpfun bundle detection coordinated wallets funding ancestry",
                "solana liquidity withdrawal rug detector deployer wallet cluster",
            ],
            "keywords": ("bundle", "bundled", "insider", "deployer", "rug", "wallet", "liquidity", "funding"),
        },

        # EXECUTION ----------------------------------------------------------
        {
            "project_key": "sniper_lane_research",
            "topic": "solana_launch_execution",
            "queries": [
                "pumpfun sniper bot jito solana python",
                "solana token launch sniper bonding curve transaction simulation",
                "solana trading bot rpc failover jupiter route priority fee latency",
                "jito priority fee solana trading execution route optimization",
            ],
            "keywords": ("sniper", "jito", "launch", "simulation", "transaction", "rpc", "jupiter", "latency"),
        },

        # HARVESTING ---------------------------------------------------------
        {
            "project_key": "runner_harvest_research",
            "topic": "dynamic_profit_capture",
            "queries": [
                "solana memecoin trailing take profit runner exit trading bot",
                "crypto dynamic profit lock peak capture algorithm bot",
                "solana momentum trailing stop runner harvesting strategy",
            ],
            "keywords": ("trailing", "profit", "runner", "exit", "peak", "take", "harvest", "momentum"),
        },

        # MARKET MAKING / ARBITRAGE / MEV ----------------------------------
        {
            "project_key": "market_structure_edge_research",
            "topic": "market_making_arbitrage_mev",
            "queries": [
                "solana market making bot inventory spread risk python",
                "solana arbitrage bot jupiter dex routing latency",
                "solana mev trading strategy bundle arbitrage open source",
            ],
            "keywords": ("market", "making", "arbitrage", "mev", "spread", "inventory", "jupiter", "dex"),
        },

        # PASSIVE / AUTOMATED CLAIMS ----------------------------------------
        # Claims are discovery leads only. Existing safety/provenance scoring
        # still decides whether any underlying mechanism is worth abstraction.
        {
            "project_key": "automated_income_claim_research",
            "topic": "passive_automated_trading_claims",
            "queries": [
                "solana passive income trading bot automated open source",
                "profitable solana trading bot automated income github",
                "hands free crypto trading bot solana market making",
                "solana yield automated trading strategy bot",
            ],
            "keywords": ("passive", "automated", "profit", "profitable", "income", "yield", "strategy", "solana"),
        },

        # SUBSTRATE-NATIVE / LOWER-VOLATILITY AUTOMATION -------------------
        {
            "project_key": "substrate_passive_strategy_research",
            "topic": "substrate_native_automation",
            "queries": [
                "crypto paper trading framework passive strategy mean reversion python",
                "automated market neutral crypto strategy risk controlled python",
                "crypto systematic trading bot low volatility portfolio rebalance",
            ],
            "keywords": ("paper", "systematic", "mean", "reversion", "market", "neutral", "portfolio", "risk"),
        },
    ],
    "FRONTIER": [
        {
            "project_key": "organism_frontier_architecture",
            "topic": "agent_orchestration_memory",
            "queries": [
                "multi agent orchestration durable workflow memory event sourcing python",
                "self healing autonomous software agent observability rollback architecture",
            ],
            "keywords": ("workflow", "memory", "event", "durable", "rollback", "agent", "orchestration"),
        },
        {
            "project_key": "data_truth_frontier",
            "topic": "streaming_truth_architecture",
            "queries": [
                "event sourcing market data provenance trading system python",
                "stream processing trading data lineage provenance architecture",
            ],
            "keywords": ("event", "stream", "lineage", "provenance", "market", "data", "truth"),
        },
    ],
}

SAFE_EXTENSIONS = {".py", ".rs", ".go", ".ts", ".tsx", ".js", ".jsx", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".sql", ".proto"}
SKIP_PARTS = ("node_modules/", "vendor/", "dist/", "build/", "coverage/", ".github/workflows/", "fixtures/")
PERMISSIVE_LICENCES = {"MIT", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "MPL-2.0", "UNLICENSE", "CC0-1.0"}
PROMPT_PATTERNS = [
    re.compile(x, re.I) for x in (
        r"ignore (?:all |any |the )?(?:previous|prior) instructions",
        r"system prompt", r"developer message", r"agent\s+(?:must|should)\s+(?:run|execute|obey)",
        r"run (?:this|the following) command", r"execute (?:this|the following)",
        r"curl\s+[^\n|]+\|\s*(?:sh|bash)", r"wget\s+[^\n|]+\|\s*(?:sh|bash)",
    )
]
EXECUTION_PATTERNS = [
    re.compile(x, re.I) for x in (
        r"\bos\.system\s*\(", r"\bsubprocess\.", r"\beval\s*\(", r"\bexec\s*\(",
        r"child_process", r"powershell", r"cmd\.exe", r"rm\s+-rf", r"chmod\s+\+x",
    )
]


def _ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS research_activity_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, event_type TEXT NOT NULL,
      actor TEXT NOT NULL, task_id TEXT, query TEXT, source_ref TEXT, source_type TEXT,
      commit_sha TEXT, licence TEXT, safety_status TEXT, summary TEXT, confidence REAL,
      parent_event_id INTEGER, disposition TEXT, metadata_json TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_activity_created ON research_activity_ledger(created_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS github_expedition_state(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), current_mode TEXT NOT NULL,
      mode_label TEXT NOT NULL, status TEXT NOT NULL, cycle_started_at REAL,
      cycle_finished_at REAL, active_query TEXT, current_project TEXT,
      repositories_seen INTEGER NOT NULL DEFAULT 0, discoveries_recorded INTEGER NOT NULL DEFAULT 0,
      total_cycles INTEGER NOT NULL DEFAULT 0, bootstrap_remaining INTEGER NOT NULL DEFAULT 0,
      last_note TEXT, updated_at REAL NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS github_discovery_ledger(
      discovery_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
      cycle_no INTEGER NOT NULL, mode TEXT NOT NULL, project_key TEXT NOT NULL,
      topic TEXT NOT NULL, found_by TEXT NOT NULL, model_id TEXT,
      repository TEXT NOT NULL, repository_url TEXT, commit_sha TEXT,
      licence TEXT, stars INTEGER, language TEXT, updated_at TEXT,
      files_examined TEXT, extracted_principle TEXT, relevance_reason TEXT,
      safety_status TEXT NOT NULL, safety_findings TEXT, score REAL NOT NULL,
      colour_band TEXT NOT NULL, value_label TEXT NOT NULL,
      disposition TEXT NOT NULL DEFAULT 'COUNCIL_REVIEW', inspiration_id INTEGER,
      metadata_json TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_github_discovery_score ON github_discovery_ledger(score DESC, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_github_discovery_mode ON github_discovery_ledger(mode, created_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS github_agent_field_score(
      agent_name TEXT NOT NULL, model_id TEXT NOT NULL, discoveries INTEGER NOT NULL DEFAULT 0,
      high_value INTEGER NOT NULL DEFAULT 0, noise_rejected INTEGER NOT NULL DEFAULT 0,
      cumulative_score REAL NOT NULL DEFAULT 0, last_score REAL, last_cycle INTEGER,
      updated_at REAL NOT NULL, PRIMARY KEY(agent_name, model_id))""")
    row = conn.execute("SELECT singleton FROM github_expedition_state WHERE singleton=1").fetchone()
    if not row:
        conn.execute("""INSERT INTO github_expedition_state(
          singleton,current_mode,mode_label,status,total_cycles,bootstrap_remaining,last_note,updated_at)
          VALUES(1,'BUILDOUT',?,'TRAILHEAD',0,?,'Awaiting first expedition',?)""",
          (MODE_LABELS["BUILDOUT"], BOOTSTRAP_CYCLES, time.time()))
    conn.commit()


def _research_event(conn, event_type: str, **fields) -> int:
    cols = ["created_at","event_type","actor","task_id","query","source_ref","source_type","commit_sha","licence","safety_status","summary","confidence","parent_event_id","disposition","metadata_json"]
    vals = [time.time(), event_type, fields.get("actor", "NUGGET"), fields.get("task_id"), fields.get("query"), fields.get("source_ref"), fields.get("source_type", "github"), fields.get("commit_sha"), fields.get("licence"), fields.get("safety_status"), fields.get("summary"), fields.get("confidence"), fields.get("parent_event_id"), fields.get("disposition"), json.dumps(fields.get("metadata", {}), sort_keys=True)]
    cur = conn.execute(f"INSERT INTO research_activity_ledger({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", vals)
    return int(cur.lastrowid or 0)


def _provider_headers(token: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class GitHubAccessUnavailable(RuntimeError):
    """Provider/rate/auth failure: not a legitimate zero-result search."""


def _effective_github_token(configured_token: str) -> tuple[str, str]:
    """Return the token that is safe to use for this request.

    A rejected GitHub token is a credential problem, not a provider outage.
    provider_firewall deliberately permits anonymous public-repository research
    during the short auth backoff and later permits an authenticated
    revalidation attempt so replacing GITHUB_TOKEN self-heals without a DB edit.
    """
    allowed, reason = check_provider("github", SERVICE_NAME)
    if not allowed:
        raise GitHubAccessUnavailable(f"provider firewall: {reason}")
    if reason == "GITHUB_AUTH_INVALID_ANONYMOUS":
        return "", reason
    return configured_token, reason


def _github_get(url: str, configured_token: str, *, params=None, timeout: int = 12, accept: str = "application/vnd.github+json"):
    """GET GitHub with one safe authenticated->anonymous fallback on HTTP 401."""
    import requests
    token, reason = _effective_github_token(configured_token)
    headers = _provider_headers(token)
    headers["Accept"] = accept
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    endpoint = url.split("api.github.com")[-1][:100]
    log_api_call("github", SERVICE_NAME, endpoint, r.status_code,
                 error_type=None if r.status_code == 200 else r.text[:120])

    if r.status_code == 401 and token:
        # The credential was rejected. log_api_call has now marked GitHub auth
        # invalid; ask the firewall again and continue only if it explicitly
        # grants anonymous public-read authority.
        anon_token, anon_reason = _effective_github_token(configured_token)
        if anon_token == "" and anon_reason == "GITHUB_AUTH_INVALID_ANONYMOUS":
            headers = _provider_headers("")
            headers["Accept"] = accept
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            log_api_call("github", SERVICE_NAME, endpoint, r.status_code,
                         error_type=None if r.status_code == 200 else r.text[:120])
            if r.status_code == 200:
                log.warning("GitHub token rejected; expedition continuing in anonymous public-read mode")
    return r


def _request_json(url: str, token: str, timeout: int = 12):
    r = _github_get(url, token, timeout=timeout)
    if r.status_code != 200:
        raise GitHubAccessUnavailable(f"GitHub HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


def _github_search(query: str, token: str, max_results: int = MAX_REPOS_PER_QUERY) -> list[dict]:
    try:
        r = _github_get(
            "https://api.github.com/search/repositories", token,
            params={"q": query, "sort": "stars", "order": "desc", "per_page": max_results},
            timeout=12,
        )
        if r.status_code in (401, 403, 429):
            detail = (r.headers.get("x-ratelimit-remaining") or "?")
            raise GitHubAccessUnavailable(f"GitHub HTTP {r.status_code}; remaining={detail}")
        if r.status_code != 200:
            raise GitHubAccessUnavailable(f"GitHub HTTP {r.status_code}: {r.text[:120]}")
        out = []
        for item in r.json().get("items", []):
            out.append({
                "name": item.get("full_name", ""), "description": item.get("description", "") or "",
                "stars": int(item.get("stargazers_count", 0) or 0), "language": item.get("language", "") or "",
                "url": item.get("html_url", ""), "updated": item.get("updated_at", ""),
                "topics": item.get("topics", []) or [], "owner": (item.get("owner") or {}).get("login", "") or "NOT_RESOLVED",
                "licence": ((item.get("license") or {}).get("spdx_id") or "NOT_RESOLVED"),
                "default_branch": item.get("default_branch", "main") or "main", "archived": bool(item.get("archived")),
                "fork": bool(item.get("fork")),
            })
        return out
    except GitHubAccessUnavailable:
        raise
    except Exception as exc:
        raise GitHubAccessUnavailable(f"GitHub search transport error: {type(exc).__name__}: {exc}") from exc


def _fetch_readme(repo: str, token: str, commit_sha: str = "") -> str:
    try:
        suffix = f"?ref={commit_sha}" if commit_sha else ""
        data = _request_json(f"https://api.github.com/repos/{repo}/readme{suffix}", token, timeout=10)
        raw = base64.b64decode(data.get("content", "")) if data.get("content") else b""
        return raw.decode("utf-8", errors="replace")[:8000]
    except Exception:
        return ""


def _commit_and_tree(repo: dict, token: str) -> tuple[str, list[dict]]:
    branch = repo.get("default_branch") or "main"
    commit = _request_json(f"https://api.github.com/repos/{repo['name']}/commits/{branch}", token)
    commit_sha = str(commit.get("sha") or "")
    if not commit_sha:
        return "", []
    tree = _request_json(f"https://api.github.com/repos/{repo['name']}/git/trees/{commit_sha}?recursive=1", token)
    return commit_sha, list(tree.get("tree", []) or [])


def _candidate_source_paths(tree: list[dict], keywords: tuple[str, ...]) -> list[str]:
    scored = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        low = path.lower()
        ext = Path(path).suffix.lower()
        size = int(item.get("size", 0) or 0)
        if ext not in SAFE_EXTENSIONS or size <= 0 or size > MAX_SOURCE_BYTES:
            continue
        if any(part in low for part in SKIP_PARTS):
            continue
        score = sum(4 for k in keywords if k in low)
        if any(x in low for x in ("readme", "architecture", "strategy", "signal", "wallet", "trade", "agent", "scanner", "engine", "portfolio")):
            score += 2
        if ext in {".py", ".rs", ".go", ".ts"}:
            score += 1
        scored.append((score, size, path))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [p for _, _, p in scored[:MAX_SOURCE_FILES]]


def _fetch_text_file(repo: str, path: str, commit_sha: str, token: str) -> str:
    try:
        r = _github_get(
            f"https://api.github.com/repos/{repo}/contents/{path}", token,
            params={"ref": commit_sha}, timeout=10, accept="application/vnd.github.raw+json",
        )
        if r.status_code != 200 or len(r.content) > MAX_SOURCE_BYTES:
            return ""
        return r.content.decode("utf-8")
    except (UnicodeDecodeError, Exception):
        return ""


def _screen_text(text: str) -> list[str]:
    findings = []
    for p in PROMPT_PATTERNS:
        if p.search(text):
            findings.append("PROMPT_INJECTION_PATTERN")
            break
    # Presence of subprocess/eval is not malware proof. It is a reason to keep
    # the finding away from automatic code reuse and under closer inspection.
    for p in EXECUTION_PATTERNS:
        if p.search(text):
            findings.append("EXECUTION_CAPABILITY_PRESENT")
            break
    return findings


def _extract_principle(repo: dict, readme: str, source_texts: list[tuple[str, str]], keywords: tuple[str, ...]) -> tuple[str, list[str]]:
    combined = "\n".join([repo.get("description", ""), readme[:5000]] + [txt[:5000] for _, txt in source_texts])
    low = combined.lower()
    hits = [k for k in keywords if k in low]
    paths = [p for p, _ in source_texts]
    principle = (
        f"{repo['name']} exposes a potentially transferable {', '.join(hits[:6]) or 'architecture'} pattern. "
        f"Council should compare the design boundaries and data flow in {', '.join(paths[:4]) or 'README / repository metadata'} "
        "against Sentinuity before writing any native implementation. Repository code remains untrusted inspiration, not authority."
    )
    return principle[:1000], hits


def _score(repo: dict, commit_sha: str, licence: str, paths: list[str], hits: list[str], safety_findings: list[str]) -> float:
    relevance = min(30.0, 8.0 + len(hits) * 4.0)
    maturity = min(15.0, math.log10(max(1, int(repo.get("stars", 0)) + 1)) * 4.0)
    provenance = 15.0 if commit_sha and paths else (8.0 if commit_sha else 2.0)
    transferability = min(20.0, 5.0 + len(paths) * 1.5 + len(hits) * 1.5)
    licence_score = 12.0 if licence.upper() in PERMISSIVE_LICENCES else (5.0 if licence not in ("", "NOT_RESOLVED", "NOASSERTION") else 0.0)
    safety = 8.0
    if "PROMPT_INJECTION_PATTERN" in safety_findings:
        safety = 0.0
    elif "EXECUTION_CAPABILITY_PRESENT" in safety_findings:
        safety = 4.0
    if repo.get("archived"):
        maturity *= 0.55
    return round(max(0.0, min(100.0, relevance + maturity + provenance + transferability + licence_score + safety)), 1)


def _band(score: float, safety: str) -> tuple[str, str]:
    if safety == "BLOCKED":
        return "RED", "SECURITY HOLD"
    if score >= 85:
        return "GREEN", "READY FOR COUNCIL ABSTRACTION"
    if score >= 70:
        return "GOLD", "HIGH-VALUE FIND"
    if score >= 55:
        return "CYAN", "PROMISING CLUE"
    if score >= 40:
        return "AMBER", "NEEDS CLOSER INSPECTION"
    return "GREY", "BACKGROUND NOISE"


def _active_nugget_model(conn) -> str:
    try:
        r = conn.execute("SELECT model_id FROM council_model_assignments WHERE UPPER(agent_name)='NUGGET' LIMIT 1").fetchone()
        return str(r[0] or "NIM_RECONNAISSANCE") if r else "NIM_RECONNAISSANCE"
    except Exception:
        return "NIM_RECONNAISSANCE"


def _choose_mode(total_cycles: int) -> str:
    return _MODE_TRAIL[int(total_cycles) % len(_MODE_TRAIL)]


def _gap_driven_targets() -> list[dict]:
    """COUNCIL_INTERNAL_FIRST_20260816.

    Expeditions that answer a measured internal question take priority over the
    static domain rotation. A brief only reaches EXTERNAL_REQUESTED after the
    organism has been examined and the internal capability search returned a
    genuine gap, so anything here is already gap-anchored by construction.

    Failure is total and silent by design: if the Council layer is unavailable
    the Scout falls back to its existing rotation. Council infrastructure must
    never be able to stop reconnaissance, and it must never be a dependency of
    anything on the trading hot path.
    """
    try:
        from services.organism_causal_brief import load_brief, is_known
        from services.causal_investigation import ensure_schema
        from services.organism_causal_brief import connect_brief_db
        ensure_schema()
        conn = connect_brief_db()
        try:
            rows = conn.execute(
                "SELECT brief_id FROM organism_causal_briefs "
                "WHERE state='EXTERNAL_REQUESTED' ORDER BY updated_at DESC LIMIT 2"
            ).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            brief = load_brief(row["brief_id"])
            if brief is None:
                continue
            question = brief.external_question
            if not is_known(question):
                continue
            # Mechanism-level primitives derived from the measured gap, not
            # repo-popularity browsing. A repo is not a finding.
            primitives = [p for p in (brief.owning_code
                                      if is_known(brief.owning_code) else []) if p]
            queries = [str(question)[:250]]
            for prim in primitives[:2]:
                stem = str(prim).split("/")[-1].split(":")[0].replace(".py", "")
                if stem:
                    queries.append(f"{brief.capability} {stem} implementation")
            out.append({
                "project_key": f"gap_{brief.brief_id}",
                "queries": queries,
                "keywords": tuple(str(brief.capability).lower().split()[:6]),
                "_brief_id": brief.brief_id,
                "_gap_driven": True,
            })
        return out
    except Exception:
        return []


def _targets_for_cycle(mode: str, cycle_no: int) -> list[dict]:
    # COUNCIL_INTERNAL_FIRST_20260816: measured gaps outrank the rotation.
    gap_targets = _gap_driven_targets()
    if gap_targets:
        return gap_targets[:2]
    targets = EXPEDITIONS[mode]
    if not targets:
        return []
    # Two targets per cycle, rotating through the domain rather than hammering
    # every query every time.
    a = cycle_no % len(targets)
    b = (a + 1) % len(targets)
    return [targets[a], targets[b]] if b != a else [targets[a]]


def _set_state(conn, **fields) -> None:
    fields["updated_at"] = time.time()
    sql = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE github_expedition_state SET {sql} WHERE singleton=1", tuple(fields.values()))


def _write_cache_entry(conn, project_key: str, topic: str, summary: str, confidence: float) -> None:
    now = time.time()
    row = conn.execute("SELECT id FROM forge_research_cache WHERE project_key=? AND topic=? AND created_at>? LIMIT 1", (project_key, topic, now - 3600)).fetchone()
    if row:
        conn.execute("UPDATE forge_research_cache SET summary=?,confidence=?,created_at=?,expires_at=? WHERE id=?", (summary[:800], confidence, now, now + ENTRY_TTL_SEC, row[0]))
    else:
        conn.execute("INSERT INTO forge_research_cache(project_key,topic,summary,source,confidence,created_at,expires_at) VALUES(?,?,?,?,?,?,?)", (project_key, topic, summary[:800], "github", confidence, now, now + ENTRY_TTL_SEC))


def _ensure_project(conn, project_key: str, mode: str) -> None:
    now = time.time()
    try:
        conn.execute("INSERT OR IGNORE INTO forge_projects(project_key,title,description,status,priority,current_stage,created_at,updated_at) VALUES(?,?,?,'active',10,'RESEARCH',?,?)",
                     (project_key, project_key.replace("_", " ").title(), f"GitHub {mode} expedition evidence; inspiration only.", now, now))
    except Exception as exc:
        log.debug("forge project seed unavailable %s: %s", project_key, exc)


def _record_inspiration(conn, finding: dict) -> int | None:
    try:
        from services.inspiration_intake_ledger import record_inspiration, advance_stage
        source_ref = f"{finding['repository_url']}@{finding['commit_sha']}" if finding.get("commit_sha") else finding["repository_url"]
        iid = record_inspiration(
            source_type="github_commit" if finding.get("commit_sha") else "github_repo",
            source_ref=source_ref,
            extracted_concept=finding["extracted_principle"],
            topic_tags=f"{finding['topic']},{finding['project_key']},{finding['mode']}",
            standing_task=finding["project_key"],
            expected_benefit=finding["relevance_reason"],
            novelty=f"field score={finding['score']}; stars={finding['stars']}; language={finding['language']}",
            system_overlap=f"Sentinuity expedition mode={finding['mode']}",
            risks=finding["safety_findings"] or "No obvious static intake warnings; external code remains untrusted.",
            council_sponsor="NUGGET",
            author=finding["repository"].split("/", 1)[0],
            licence=finding["licence"],
            relevance=finding["relevance_reason"],
            security_concerns=finding["safety_status"],
            files_examined=finding["files_examined"],
            conn=conn,
        )
        # Scout may establish relevance and a clean licence/security screen.
        # Research/falsification/debate remain Council work.
        try:
            advance_stage(iid, "RELEVANCE_SCREENING", note=f"GitHub field score {finding['score']}; {finding['value_label']}", conn=conn)
            if finding["safety_status"] == "SCREENED" and finding["licence"] not in ("", "NOT_RESOLVED", "NOASSERTION"):
                advance_stage(iid, "SECURITY_LICENCE_SCREEN", note=f"licence={finding['licence']}; static intake screen={finding['safety_status']}", conn=conn)
                # SIGNOFF_20260811_GITHUB_PROVENANCE_GATE: repository metadata or
                # an unpinned README is a promising clue, not an abstraction-ready
                # source. Runtime showed INTERNAL_ABSTRACTION with a blank commit.
                # Require commit-pinned source inspection before Council may treat
                # the finding as a transferable architecture principle.
                _files = str(finding.get("files_examined") or "").strip()
                _deep = bool(
                    str(finding.get("commit_sha") or "").strip()
                    and _files
                    and _files.lower() not in {"metadata only", "readme"}
                )
                if _deep:
                    advance_stage(iid, "INTERNAL_ABSTRACTION", note="Commit-pinned source files inspected; architecture principle extracted only; no external source copied", conn=conn)
        except Exception as exc:
            log.debug("inspiration advancement paused iid=%s: %s", iid, exc)
        return int(iid)
    except Exception as exc:
        log.warning("inspiration ledger unavailable: %s", exc)
        return None


def _record_discovery(conn, finding: dict) -> int:
    iid = _record_inspiration(conn, finding)
    finding["inspiration_id"] = iid
    cols = ["created_at","cycle_no","mode","project_key","topic","found_by","model_id","repository","repository_url","commit_sha","licence","stars","language","updated_at","files_examined","extracted_principle","relevance_reason","safety_status","safety_findings","score","colour_band","value_label","disposition","inspiration_id","metadata_json"]
    vals = [time.time()] + [finding.get(k) for k in cols[1:-1]] + [json.dumps(finding.get("metadata", {}), sort_keys=True)]
    cur = conn.execute(f"INSERT INTO github_discovery_ledger({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", vals)
    model = finding["model_id"] or "NIM_RECONNAISSANCE"
    high = 1 if finding["score"] >= 70 and finding["safety_status"] != "BLOCKED" else 0
    noise = 1 if finding["score"] < 40 or finding["safety_status"] == "BLOCKED" else 0
    conn.execute("""INSERT INTO github_agent_field_score(agent_name,model_id,discoveries,high_value,noise_rejected,cumulative_score,last_score,last_cycle,updated_at)
      VALUES('NUGGET',?,1,?,?,?, ?,?,?)
      ON CONFLICT(agent_name,model_id) DO UPDATE SET discoveries=discoveries+1,
      high_value=high_value+excluded.high_value, noise_rejected=noise_rejected+excluded.noise_rejected,
      cumulative_score=cumulative_score+excluded.cumulative_score,last_score=excluded.last_score,
      last_cycle=excluded.last_cycle,updated_at=excluded.updated_at""",
      (model, high, noise, float(finding["score"]), float(finding["score"]), int(finding["cycle_no"]), time.time()))
    return int(cur.lastrowid or 0)


def _inspect_repository(repo: dict, target: dict, token: str, mode: str, cycle_no: int, model_id: str) -> dict:
    commit_sha, tree = "", []
    try:
        commit_sha, tree = _commit_and_tree(repo, token)
    except Exception as exc:
        log.debug("commit/tree unavailable for %s: %s", repo.get("name"), exc)
    readme = _fetch_readme(repo["name"], token, commit_sha)
    paths = _candidate_source_paths(tree, target["keywords"])
    source_texts = []
    findings = _screen_text(readme)
    for path in paths:
        text = _fetch_text_file(repo["name"], path, commit_sha, token)
        if not text:
            continue
        source_texts.append((path, text))
        findings.extend(_screen_text(text))
    findings = sorted(set(findings))
    principle, hits = _extract_principle(repo, readme, source_texts, target["keywords"])
    licence = str(repo.get("licence") or "NOT_RESOLVED")
    if "PROMPT_INJECTION_PATTERN" in findings:
        safety = "BLOCKED"
    elif findings or licence in ("NOT_RESOLVED", "NOASSERTION", ""):
        safety = "CAUTION"
    else:
        safety = "SCREENED"
    score = _score(repo, commit_sha, licence, [p for p, _ in source_texts], hits, findings)
    colour, value_label = _band(score, safety)
    relevance = f"{MODE_LABELS[mode]} · supports {target['project_key']} · matched {', '.join(hits[:6]) or 'repository architecture'}"
    return {
        "cycle_no": cycle_no, "mode": mode, "project_key": target["project_key"], "topic": target["topic"],
        "found_by": "NUGGET", "model_id": model_id, "repository": repo["name"], "repository_url": repo.get("url", ""),
        "commit_sha": commit_sha, "licence": licence, "stars": repo.get("stars", 0), "language": repo.get("language", ""),
        "updated_at": repo.get("updated", ""), "files_examined": ", ".join([p for p, _ in source_texts]) or ("README" if readme else "metadata only"),
        "extracted_principle": principle, "relevance_reason": relevance, "safety_status": safety,
        "safety_findings": ", ".join(findings), "score": score, "colour_band": colour, "value_label": value_label,
        # A repository may be scored as interesting from metadata/README, but
        # COUNCIL_REVIEW requires pinned source provenance.
        "disposition": ("SECURITY_HOLD" if safety == "BLOCKED" else
                        ("COUNCIL_REVIEW" if score >= 40 and commit_sha and source_texts else
                         ("PROMISING_CLUE" if score >= 40 else "BACKGROUND"))),
        "metadata": {"topics": repo.get("topics", []), "fork": repo.get("fork", False), "archived": repo.get("archived", False), "matched_keywords": hits},
    }


def _broad_fallback_query(target: dict) -> str:
    """Return a deliberately broad second search when a precise trail is empty.

    GitHub search rewards concise repository vocabulary; the original long
    research-language queries repeatedly returned zero repositories. The broad
    query changes discovery only, never scoring, safety, provenance or authority.
    """
    key = str(target.get("project_key") or "")
    return {
        "intelligence_tab_evolution": "agent observability dashboard python",
        "substrate_node_evolution": "crypto paper trading framework python",
        "autonomous_organism_buildout": "autonomous coding agent python",
        "solana_entry_pipeline_research": "pumpfun trading bot solana",
        "wallet_convergence": "solana wallet tracker copy trading",
        "solana_manipulation_research": "solana bundle detector wallet rug",
        "sniper_lane_research": "pumpfun sniper solana",
        "runner_harvest_research": "solana trailing profit trading bot",
        "market_structure_edge_research": "solana arbitrage market making bot",
        "automated_income_claim_research": "solana automated trading bot",
        "substrate_passive_strategy_research": "systematic crypto trading python",
        "organism_frontier_architecture": "agent orchestration memory python",
        "data_truth_frontier": "event sourcing trading python",
    }.get(key, " ".join(str(target.get("topic") or key).replace("_", " ").split()[:4]))


def _cycle() -> dict:
    import sqlite3 as _sq
    with get_connection() as conn:
        conn.row_factory = _sq.Row
        _ensure_schema(conn)
        row = conn.execute("SELECT * FROM github_expedition_state WHERE singleton=1").fetchone()
        total_cycles = int(row["total_cycles"] or 0)
        cycle_no = total_cycles + 1
        mode = _choose_mode(total_cycles)
        model_id = _active_nugget_model(conn)
        targets = _targets_for_cycle(mode, total_cycles)
        _set_state(conn, current_mode=mode, mode_label=MODE_LABELS[mode], status="LEAVING THE TRAILHEAD",
                   cycle_started_at=time.time(), active_query="", current_project="", repositories_seen=0,
                   discoveries_recorded=0, last_note=MODE_DESCRIPTIONS[mode])
        conn.commit()

        token = os.getenv("GITHUB_TOKEN", "").strip()
        anonymous = not bool(token)
        # Public GitHub repositories can be researched without a token.  Anonymous
        # mode deliberately reduces scope so it stays inside GitHub's lower public
        # API allowance; a token only increases depth, it is not authority.
        if anonymous:
            targets = targets[:1]
            _set_state(conn, status="FOLLOWING A PUBLIC TRAIL",
                       last_note="No GITHUB_TOKEN configured; using reduced public read-only reconnaissance")
            conn.commit()

        repos_seen, discoveries = 0, 0
        searches_completed = 0
        summaries = []
        try:
            for target in targets:
                _ensure_project(conn, target["project_key"], mode)
                query = target["queries"][cycle_no % len(target["queries"])]
                _set_state(conn, status="FOLLOWING A NEW TRAIL", active_query=query, current_project=target["project_key"])
                planned = _research_event(conn, "SEARCH_PLANNED", task_id=target["project_key"], query=query,
                                          summary=f"{MODE_LABELS[mode]}: GitHub expedition search planned", disposition="PLANNED",
                                          metadata={"mode": mode, "cycle_no": cycle_no})
                conn.commit()
                repos = _github_search(query, token, max_results=(2 if anonymous else MAX_REPOS_PER_QUERY))
                searches_completed += 1
                # Precise research-language searches repeatedly returned zero in
                # runtime. Broaden once, visibly and with its own ledger event.
                if not repos:
                    _bq = _broad_fallback_query(target)
                    if _bq and _bq.lower() != query.lower():
                        _research_event(
                            conn, "SEARCH_BROADENED", task_id=target["project_key"],
                            query=_bq, parent_event_id=planned,
                            summary=f"No repositories for precise query; broadening trail from: {query[:180]}",
                            disposition="PLANNED", metadata={"mode": mode, "cycle_no": cycle_no},
                        )
                        conn.commit()
                        repos = _github_search(_bq, token, max_results=(2 if anonymous else MAX_REPOS_PER_QUERY))
                        searches_completed += 1
                        _research_event(
                            conn, "SEARCH_EXECUTED", task_id=target["project_key"],
                            query=_bq, parent_event_id=planned,
                            summary=f"repositories_returned={len(repos)} (broadened)",
                            disposition="RESULTS" if repos else "NO_RESULTS",
                            metadata={"mode": mode, "cycle_no": cycle_no, "broadened_from": query},
                        )
                repos_seen += len(repos)
                _research_event(conn, "SEARCH_EXECUTED", task_id=target["project_key"], query=query, parent_event_id=planned,
                                summary=f"repositories_returned={len(repos)}", disposition="RESULTS" if repos else "NO_RESULTS",
                                metadata={"mode": mode, "cycle_no": cycle_no})
                _set_state(conn, status="LOOKING CLOSER", repositories_seen=repos_seen)
                conn.commit()

                target_findings = []
                for repo in repos[:1 if anonymous else 3]:
                    try:
                        finding = _inspect_repository(repo, target, token, mode, cycle_no, model_id)
                        _record_discovery(conn, finding)
                        discoveries += 1
                        target_findings.append(finding)
                        _research_event(conn, "SOURCE_INSPECTED", task_id=target["project_key"], query=query,
                                        source_ref=repo.get("url"), commit_sha=finding.get("commit_sha"), licence=finding.get("licence"),
                                        safety_status=finding["safety_status"], summary=f"{finding['value_label']} · score={finding['score']} · {finding['extracted_principle'][:420]}",
                                        confidence=min(0.95, max(0.35, finding["score"] / 100.0)), disposition=finding["disposition"],
                                        metadata={"mode": mode, "cycle_no": cycle_no, "colour_band": finding["colour_band"], "files_examined": finding["files_examined"]})
                    except Exception as exc:
                        log.warning("repository inspection failed %s: %s", repo.get("name"), exc)
                if target_findings:
                    best = max(target_findings, key=lambda x: x["score"])
                    summary = (f"{MODE_LABELS[mode]} | {target['project_key']} | best finding {best['repository']} "
                               f"score={best['score']} {best['value_label']} | {best['extracted_principle']}")
                    _write_cache_entry(conn, target["project_key"], f"github_{target['topic']}_{cycle_no}", summary, min(0.95, best["score"] / 100.0))
                    summaries.append(summary)
                time.sleep(0.8)

        except GitHubAccessUnavailable as exc:
            # Do not pretend an access/rate/provider failure was a completed expedition.
            _set_state(conn, status="WAITING AT THE TRAILHEAD", cycle_finished_at=time.time(),
                       active_query="", repositories_seen=repos_seen, discoveries_recorded=discoveries,
                       last_note=f"GitHub access unavailable — {exc}")
            conn.commit()
            return {"mode": mode, "cycle_no": total_cycles, "repos": repos_seen,
                    "discoveries": discoveries, "idle": True,
                    "bootstrap_remaining": int(row["bootstrap_remaining"] or BOOTSTRAP_CYCLES)}

        if searches_completed <= 0:
            _set_state(conn, status="WAITING AT THE TRAILHEAD", active_query="",
                       last_note="No GitHub search completed; expedition not counted")
            conn.commit()
            return {"mode": mode, "cycle_no": total_cycles, "repos": 0, "discoveries": 0,
                    "idle": True, "bootstrap_remaining": int(row["bootstrap_remaining"] or BOOTSTRAP_CYCLES)}

        remaining = max(0, BOOTSTRAP_CYCLES - cycle_no)
        _set_state(conn, status="BACK AT COUNCIL CAMP", cycle_finished_at=time.time(), total_cycles=cycle_no,
                   bootstrap_remaining=remaining, repositories_seen=repos_seen, discoveries_recorded=discoveries,
                   last_note=(summaries[0][:500] if summaries else
                              f"Search completed: {repos_seen} repositories seen; no high-signal finding recorded during {MODE_LABELS[mode].lower()}"))
        conn.commit()
        return {"mode": mode, "cycle_no": cycle_no, "repos": repos_seen, "discoveries": discoveries, "bootstrap_remaining": remaining}


def run() -> None:
    log.info("GitHub expedition scout online — allocation BUILDOUT 50%% / EDGE_HUNT 35%% / FRONTIER 15%%")
    update_heartbeat(SERVICE_NAME, "starting", "GitHub expedition scout online")
    while True:
        try:
            stats = _cycle()
            note = f"mode={stats['mode']} cycle={stats['cycle_no']} repos={stats['repos']} discoveries={stats['discoveries']}"
            update_heartbeat(SERVICE_NAME, "idle" if stats.get("idle") else "alive", note, work_processed=stats["discoveries"])
            sleep_for = BOOTSTRAP_CYCLE_SECONDS if int(stats.get("bootstrap_remaining", 0)) > 0 else NORMAL_CYCLE_SECONDS
        except Exception as exc:
            log.exception("[GITHUB_EXPEDITION_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"expedition error: {type(exc).__name__}: {exc}"[:300])
            sleep_for = NORMAL_CYCLE_SECONDS
        time.sleep(max(300, sleep_for))


if __name__ == "__main__":
    run()
