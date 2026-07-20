"""
services/github_scout.py
=========================
GitHub Intelligence Scout — feeds forge_research_cache with real
external inspiration for the AI council's buildout decisions.

The AI council (POLARIS + IVARIS) needs to draw from 100s of real
implementations to understand scope, filter worthy ideas, and build
toward absolute profitable truth. This is their external research lane.

What it does every cycle:
1. Searches GitHub for relevant repos (Solana trading, pump.fun, DeFi bots)
2. Reads README + top files from promising repos
3. Extracts architectural patterns, techniques, insights
4. Writes structured evidence to forge_research_cache keyed by project_key
5. IVARIS can then debate FORGE proposals with real external evidence

Requires: GITHUB_TOKEN in .env (free, read-only scope)
Rate limits: 5000 requests/hour authenticated (vs 60 unauth)
Cycle: every 2 hours (stays well under quota)

Usage:
    Add GITHUB_TOKEN=ghp_xxx to .env
    Add to Launch_Sentinuity.bat:
    start "GithubScout" /b cmd /c "... python -m services.github_scout >> logs\\github_scout.log 2>&1"
"""
from __future__ import annotations

import sys, time, os, json, logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [github_scout] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("github_scout")

SERVICE_NAME  = "github_scout"
CYCLE_SECONDS = 7200   # 2 hours — well under GitHub rate limit
ENTRY_TTL_SEC = 86400  # 24h cache TTL

# Search queries mapped to forge project keys
# Each query targets inspiration for a specific buildout project
SEARCH_TARGETS = [
    {
        "project_key": "quant_grid_dca_research",
        "queries": [
            "solana pump.fun trading bot python",
            "pump.fun sniper bot early launch",
            "solana meme coin trading algorithm",
            "bonding curve trading bot solana",
        ],
        "topic": "pump_fun_trading_patterns",
    },
    {
        "project_key": "agentic_trading_frameworks",
        "queries": [
            "autonomous trading agent AI python",
            "reinforcement learning crypto trading",
            "multi-agent trading system python",
            "LLM trading bot decision making",
        ],
        "topic": "agentic_trading_architecture",
    },
    {
        "project_key": "wallet_convergence",
        "queries": [
            "solana wallet tracking copy trading",
            "whale wallet following bot solana",
            "smart money tracking solana python",
        ],
        "topic": "wallet_intelligence_patterns",
    },
]


def _safe_ts(val) -> float:
    if not val: return 0.0
    try:
        f = float(val)
        return f if f > 1_000_000_000 else 0.0
    except: return 0.0


def _github_search(query: str, token: str, max_results: int = 5) -> list[dict]:
    """Search GitHub repos by query. Returns list of repo summaries."""
    try:
        import requests
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": max_results,
            },
            headers=headers,
            timeout=10,
        )
        if r.status_code == 403:
            log.warning("GitHub rate limit hit — sleeping 60s")
            time.sleep(60)
            return []
        if r.status_code != 200:
            log.warning("GitHub search failed: %d %s", r.status_code, r.text[:100])
            return []
        items = r.json().get("items", [])
        results = []
        for item in items:
            results.append({
                "name":        item.get("full_name", ""),
                "description": item.get("description", "") or "",
                "stars":       item.get("stargazers_count", 0),
                "language":    item.get("language", "") or "",
                "url":         item.get("html_url", ""),
                "updated":     item.get("updated_at", ""),
                "topics":      item.get("topics", []),
                "owner":       (item.get("owner") or {}).get("login", "") or "NOT_RESOLVED",
                "licence":     ((item.get("license") or {}).get("spdx_id") or "NOT_RESOLVED"),
            })
        return results
    except Exception as e:
        log.warning("GitHub search error: %s", e)
        return []


def _fetch_readme(repo_full_name: str, token: str) -> str:
    """Fetch README content for a repo. Returns truncated text."""
    try:
        import requests, base64
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        r = requests.get(
            f"https://api.github.com/repos/{repo_full_name}/readme",
            headers=headers,
            timeout=8,
        )
        if r.status_code != 200:
            return ""
        content = r.json().get("content", "")
        if content:
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
            # Strip markdown formatting, keep substance
            lines = [l.strip() for l in decoded.split("\n")
                     if l.strip() and not l.strip().startswith("!")]
            return " | ".join(lines[:20])[:600]
        return ""
    except Exception:
        return ""


def _synthesise_evidence(project_key: str, topic: str,
                          query: str, repos: list[dict],
                          readmes: dict[str, str]) -> str:
    """Build a structured evidence summary for IVARIS to read."""
    if not repos:
        return f"GitHub search for '{query}' returned no results."

    lines = [f"GitHub research for {project_key} | query: '{query}'"]
    lines.append(f"Found {len(repos)} relevant repositories:")

    for repo in repos[:5]:
        name  = repo["name"]
        desc  = repo["description"][:120] if repo["description"] else "no description"
        stars = repo["stars"]
        lang  = repo["language"]
        url   = repo["url"]
        readme_snippet = readmes.get(name, "")[:200]
        lines.append(
            f"  REPO: {name} | {stars} stars | {lang} | {url}"
            f" | DESC: {desc}"
            + (f" | README: {readme_snippet}" if readme_snippet else "")
        )

    # Extract common patterns
    languages = [r["language"] for r in repos if r["language"]]
    all_topics = []
    for r in repos:
        all_topics.extend(r.get("topics", []))

    if languages:
        from collections import Counter
        top_langs = Counter(languages).most_common(3)
        lines.append(f"Common languages: {', '.join(f'{l}({n})' for l,n in top_langs)}")
    if all_topics:
        from collections import Counter
        top_topics = Counter(all_topics).most_common(5)
        lines.append(f"Common topics: {', '.join(f'{t}' for t,_ in top_topics)}")

    return " | ".join(lines)[:800]


def _write_cache_entry(conn, project_key: str, topic: str,
                       summary: str, source: str) -> None:
    now = time.time()
    existing = conn.execute("""
        SELECT id FROM forge_research_cache
        WHERE project_key = ? AND topic = ? AND created_at > ?
        LIMIT 1
    """, (project_key, topic, now - 3600)).fetchone()

    if existing:
        conn.execute("""
            UPDATE forge_research_cache
            SET summary = ?, created_at = ?, expires_at = ?
            WHERE id = ?
        """, (summary, now, now + ENTRY_TTL_SEC, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO forge_research_cache
                (project_key, topic, summary, source, confidence, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (project_key, topic, summary, source, 0.85, now, now + ENTRY_TTL_SEC))



def _record_repo_inspiration(conn, *, project_key: str, topic: str, query: str,
                              repo: dict, readme: str) -> None:
    """Persist a GitHub discovery in the durable inspiration ledger.

    The legacy forge_research_cache write remains intact for Council evidence.
    This sidecar record adds provenance and lifecycle control. External code is
    inspiration only: no code is copied or applied by this scout.
    """
    source_ref = str(repo.get("url") or f"github:{repo.get('name','unknown')}")
    licence = str(repo.get("licence") or "NOT_RESOLVED")
    owner = str(repo.get("owner") or "NOT_RESOLVED")
    files_examined = "README" if readme else "repository metadata only"
    payload = {
        "extracted_concept": (
            f"{repo.get('name','unknown')}: {repo.get('description','')}; "
            f"README evidence: {readme[:300] if readme else 'NOT_RECORDED'}"
        )[:700],
        "topic_tags": ",".join([topic, project_key] + list(repo.get("topics") or [])[:8])[:500],
        "standing_task": project_key,
        "expected_benefit": f"External architecture evidence for {project_key}",
        "novelty": f"stars={int(repo.get('stars') or 0)}; language={repo.get('language') or 'NOT_RESOLVED'}",
        "system_overlap": f"GitHub query: {query}",
        "risks": "External code is untrusted; inspect dependencies, licence and security before abstraction.",
        "council_sponsor": "POLARIS",
        "author": owner,
        "licence": licence,
        "relevance": f"Active forge project {project_key}; topic={topic}",
        "security_concerns": "UNSCREENED",
        "files_examined": files_examined,
    }
    try:
        from services.inspiration_intake_ledger import record_inspiration
        record_inspiration(
            source_type="github_repo",
            source_ref=source_ref,
            conn=conn,
            **payload,
        )
    except Exception as exc:
        log.warning("GitHub inspiration ledger failed for %s: %s — quarantining", source_ref, exc)
        try:
            from services.inspiration_intake_ledger import quarantine_intake
            quarantine_intake("github_repo", source_ref, payload, str(exc))
        except Exception:
            log.error("GitHub inspiration quarantine also failed for %s; legacy cache remains", source_ref)

def _run_scout_cycle(token: str) -> dict:
    now = time.time()
    total_written = 0
    total_repos   = 0

    import sqlite3 as _sq
    with get_connection() as conn:
        conn.row_factory = _sq.Row
        # Expire old entries
        conn.execute("DELETE FROM forge_research_cache WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))

        for target in SEARCH_TARGETS:
            pk      = target["project_key"]
            topic   = target["topic"]
            queries = target["queries"]

            # Check if project is active
            proj = conn.execute(
                "SELECT status FROM forge_projects WHERE project_key=?", (pk,)
            ).fetchone()
            if not proj or proj["status"] != "active":
                continue

            for query in queries[:2]:  # Max 2 queries per project per cycle
                repos = _github_search(query, token, max_results=5)
                if not repos:
                    continue
                total_repos += len(repos)

                # Fetch README for top 2 repos
                readmes = {}
                for repo in repos[:2]:
                    readme = _fetch_readme(repo["name"], token)
                    if readme:
                        readmes[repo["name"]] = readme
                    time.sleep(0.5)  # be gentle

                summary = _synthesise_evidence(pk, topic, query, repos, readmes)
                _write_cache_entry(conn, pk, f"{topic}_{query[:30].replace(' ','_')}", summary, "github")
                for repo in repos:
                    _record_repo_inspiration(
                        conn,
                        project_key=pk,
                        topic=topic,
                        query=query,
                        repo=repo,
                        readme=readmes.get(repo.get("name", ""), ""),
                    )
                total_written += 1
                log.info("[SCOUT] %s: wrote '%s' (%d repos)", pk, query[:40], len(repos))
                time.sleep(2)  # rate limit buffer

        conn.commit()
        total_cache = conn.execute("SELECT COUNT(*) FROM forge_research_cache").fetchone()[0]

    return {"written": total_written, "repos_found": total_repos, "total_cache": total_cache}


def run() -> None:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        log.warning("GITHUB_TOKEN not set in .env — GitHub scout idle")
        log.warning("Add GITHUB_TOKEN=ghp_xxx to .env (free, read-only scope)")
        update_heartbeat(SERVICE_NAME, "idle", "GITHUB_TOKEN not configured")
        # Keep running but do nothing — don't crash the process
        while True:
            time.sleep(3600)

    log.info("GitHub scout started — cycle=%dh", CYCLE_SECONDS // 3600)
    update_heartbeat(SERVICE_NAME, "starting", "github_scout online")

    while True:
        try:
            stats = _run_scout_cycle(token)
            note = (f"written={stats['written']} repos={stats['repos_found']} "
                    f"cache={stats['total_cache']}")
            log.info("[CYCLE_DONE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note, work_processed=stats["written"])
        except Exception as exc:
            log.warning("[SCOUT_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
