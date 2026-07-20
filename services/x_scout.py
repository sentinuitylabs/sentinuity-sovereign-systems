"""
services/x_scout.py
===================
X/Twitter hashtag scout — reads public posts and stores insights
in cognition_log for POLARIS to read during debates.

Uses X API v2 Bearer Token (read-only, no posting).
Rate limit: 1 req/s on free tier, 500k tweets/month.

Configurable hashtag list via system_config key X_SCOUT_HASHTAGS
(comma-separated). Defaults to the list below if not set.

Runs every X_SCOUT_INTERVAL_MINUTES (default 60).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat
try:
    from services.provider_firewall import check_provider, log_api_call
    _FIREWALL_AVAILABLE = True
except ImportError:
    _FIREWALL_AVAILABLE = False
    def check_provider(p, c="x_scout"): return True, "NO_FIREWALL"
    def log_api_call(*a, **k): pass

SERVICE_NAME = "x_scout"
log = logging.getLogger("x_scout")

# ── DEFAULT HASHTAG LIST — update via system_config key X_SCOUT_HASHTAGS ──────
DEFAULT_HASHTAGS = [
    "#openclaw",
    "#pumpfun trading",
    "#aitradingbot",
    "#aitradingbots",
    "#solanatrading",
    "#memecoins strategy",
    "#solanabot",
    "#pumpfunbot",
    "#solana sniper",
    "#opensource trading bot",
    "#github trading bot solana",
    "#defi bot 2025",
    "#memecoins bot",
    "#sentinuity",
]

# ── X bearer token health check ───────────────────────────────────────────────
def check_bearer_token() -> tuple[bool, str]:
    """Returns (is_valid, message). Call at startup to verify token."""
    token = _get_bearer_token()
    if not token:
        return False, "TWITTER_BEARER_TOKEN not set in .env"
    try:
        import urllib.request as _ur
        req = _ur.Request(
            "https://api.twitter.com/2/tweets/search/recent?query=%23openclaw&max_results=10",
            headers={"Authorization": f"Bearer {token}", "User-Agent": "SentinuityBot/1.0"}
        )
        resp = _ur.urlopen(req, timeout=8)
        data = resp.read()
        import json as _j
        result = _j.loads(data)
        if "data" in result or "meta" in result:
            count = result.get("meta", {}).get("result_count", 0)
            return True, f"OK — {count} posts for #openclaw"
        return False, f"Unexpected response: {str(result)[:100]}"
    except Exception as e:
        err = str(e)
        if "403" in err: return False, "403 Forbidden — token may lack read permissions"
        if "401" in err: return False, "401 Unauthorized — token invalid or expired"
        if "429" in err: return False, "429 Rate limited — try again in 15 minutes"
        return False, f"Error: {err[:100]}"

X_API_BASE        = "https://api.twitter.com/2/tweets/search/recent"
MAX_RESULTS       = 10       # per hashtag per cycle — conservative for free tier
SEARCH_INTERVAL   = 3600     # seconds between full scan cycles (1 hour default)


def _get_bearer_token() -> str:
    return os.getenv("TWITTER_BEARER_TOKEN", "").strip()


def _get_hashtags() -> list[str]:
    """Read hashtag list from system_config, fall back to defaults."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key='X_SCOUT_HASHTAGS'"
            ).fetchone()
            if row and row["value"]:
                return [h.strip() for h in row["value"].split(",") if h.strip()]
    except Exception:
        pass
    return DEFAULT_HASHTAGS


def _get_interval() -> int:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key='X_SCOUT_INTERVAL_MINUTES'"
            ).fetchone()
            if row and row["value"]:
                return int(row["value"]) * 60
    except Exception:
        pass
    return SEARCH_INTERVAL


def _search_hashtag(token: str, query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """Search X API v2. Checks provider firewall before every request."""
    # FIREWALL CHECK — must pass before any network call
    allowed, reason = check_provider("x", "x_scout")
    if not allowed:
        log.debug("x_scout: firewall blocked request — %s", reason)
        return []

    headers = {"Authorization": f"Bearer {token}"}
    params  = {
        "query":        query + " -is:retweet lang:en",
        "max_results":  max(10, min(max_results, 100)),
        "tweet.fields": "created_at,author_id,public_metrics,text",
    }
    try:
        # Use (connect_timeout, read_timeout) tuple — survives slow TLS handshakes
        resp = requests.get(X_API_BASE, headers=headers, params=params,
                            timeout=(10, 30))
        log_api_call("x", "x_scout", X_API_BASE, resp.status_code)
        if resp.status_code == 200:
            return resp.json().get("data", [])
        elif resp.status_code == 402:
            log.warning("x_scout: 402 CreditsDepleted — provider firewall will block future calls")
            update_heartbeat(SERVICE_NAME, "DEGRADED",
                             "DEGRADED_X_CREDITS_DEPLETED — monthly quota exhausted")
            return []
        elif resp.status_code == 429:
            log.warning("x_scout: rate limited — sleeping 60s")
            log_api_call("x", "x_scout", X_API_BASE, 429, error_type="RATE_LIMITED")
            time.sleep(60)
        elif resp.status_code in (401, 403):
            log.error("x_scout: auth error %d — check TWITTER_BEARER_TOKEN in .env",
                      resp.status_code)
        else:
            log.warning("x_scout: HTTP %d for query '%s': %s",
                        resp.status_code, query, resp.text[:200])
    except requests.exceptions.SSLError as e:
        log_api_call("x", "x_scout", X_API_BASE, 0, error_type="SSL_ERROR")
        log.warning("x_scout: SSL handshake timeout for '%s' — X API unreachable from this network: %s",
                    query, str(e)[:80])
    except requests.exceptions.ReadTimeout as e:
        log.warning("x_scout: read timeout for '%s' — X API slow: %s", query, str(e)[:80])
    except requests.exceptions.ConnectTimeout as e:
        log.warning("x_scout: connect timeout for '%s': %s", query, str(e)[:80])
    except requests.exceptions.ConnectionError as e:
        log.warning("x_scout: connection error for '%s' — network unreachable: %s", query, str(e)[:80])
    except Exception as e:
        log.warning("x_scout: request failed for '%s': %s", query, e)
    return []



# ── SECURITY: Blood-Brain Barrier v3 (council-reviewed) ──────────────────────
# Doctrine:
#   1. Sanitize before ANY LLM sees text
#   2. X scout NEVER calls tools, executes code, trades, or mutates files
#   3. Only cleaned summaries reach cognition_log / council
#   4. Suspicious content is FLAGGED with metadata, not silently dropped
#   5. Execution engine is fully isolated from this ingestion path
import re as _re

_SAFE_DOMAINS = {
    "pump.fun", "dexscreener.com", "birdeye.so", "raydium.io",
    "solscan.io", "x.com", "twitter.com",
    "solana.com", "helius.dev", "jup.ag",
}

_INJECTION_PATTERNS = [
    r"ignore\s+previous",
    r"override\s+(instructions?|system|prompt)",
    r"system\s+prompt",
    r"you\s+are\s+now",
    r"disregard\s+(all|previous|your)",
    r"base64:",
    r"<\s*script",
    r"eval\s*\(",
]


def _sanitize_tweet(text: str) -> tuple:
    """
    Sanitize tweet text. Returns (cleaned_text, risk_score 0-100).
    Order matters: preserve safe domains FIRST, then strip everything else.
    Never fetches or follows any link.
    """
    if not text:
        return "", 0

    risk_score = 0

    # Step 1: Preserve safe domain references BEFORE stripping URLs
    def _replace_url(match):
        url = match.group(0).lower()
        for domain in _SAFE_DOMAINS:
            if domain in url:
                return f"[{domain}]"
        return "[LINK]"

    text = _re.sub(r"https?://\S+", _replace_url, text, flags=_re.IGNORECASE)

    # Step 2: Strip remaining dangerous schemes
    text = _re.sub(r"ftp://\S+", "[LINK]", text, flags=_re.IGNORECASE)
    if _re.search(r"javascript:|data:[^\s,]{0,100}", text, flags=_re.IGNORECASE):
        risk_score += 40
    text = _re.sub(r"javascript:\S*", "[SCRIPT REMOVED]", text, flags=_re.IGNORECASE)
    text = _re.sub(r"data:[^\s,]{0,100}", "[DATA URI REMOVED]", text, flags=_re.IGNORECASE)

    # Step 3: Strip HTML tags and entities
    text = _re.sub(r"<[^>]{0,300}>", "", text)
    text = _re.sub(r"&[a-z]{2,8};|&#[0-9]{1,6};|&#x[0-9a-f]{1,6};", "", text, flags=_re.IGNORECASE)

    # Step 4: Strip zero-width and control characters
    text = _re.sub(r"[--]", "", text)

    # Step 5: Flag injection patterns — keep readable, raise risk score
    for pat in _INJECTION_PATTERNS:
        if _re.search(pat, text, flags=_re.IGNORECASE):
            text = _re.sub(pat, "[FLAGGED]", text, flags=_re.IGNORECASE)
            risk_score += 30

    # Step 6: Clean whitespace and cap
    text = _re.sub(r"\s+", " ", text).strip()
    return text[:450], min(risk_score, 100)

# ─────────────────────────────────────────────────────────────────────────────

def _store_insights(hashtag: str, posts: list[dict]) -> None:
    """Store top posts as cognition_log entries for POLARIS to read."""
    if not posts:
        return
    try:
        with get_connection() as conn:
            for post in posts[:5]:  # top 5 per hashtag
                text, risk_score = _sanitize_tweet(str(post.get("text", "")))
                metrics = post.get("public_metrics", {})
                likes   = metrics.get("like_count", 0)
                reposts = metrics.get("retweet_count", 0)
                score   = float(likes + reposts * 2) / 100.0

                # Only store if has some engagement
                if likes + reposts < 2:
                    continue

                risk_tag = f" | RISK={risk_score}" if risk_score > 0 else ""
                message = (
                    f"[X_SCOUT] {hashtag} | "
                    f"likes={likes} rt={reposts}{risk_tag} | "
                    f"{text[:200]}"
                )
                conn.execute(
                    """
                    INSERT INTO cognition_log
                        (stage, token, message, confidence, timestamp)
                    VALUES ('X_SCOUT', ?, ?, ?, ?)
                    """,
                    (hashtag[:30], message[:500], min(score, 1.0), time.time()),
                )
                # SIGNOFF_ACTIVE_INTEGRATION_20260720: accepted channel
                # insights also enter the durable inspiration ledger. The
                # cognition_log write above (POLARIS debate evidence) is
                # preserved unchanged. Public-post licence is not a code
                # licence; recorded as N/A_PUBLIC_POST — the content is
                # sanitised text evidence, never importable code. Author
                # identity is not invented: post id only.
                _post_id = str(post.get("id", "") or "")
                _payload = {
                    "extracted_concept": text[:300],
                    "topic_tags": hashtag[:60],
                    "author": (f"x_post:{_post_id}" if _post_id
                               else "NOT_RESOLVED"),
                    "licence": "N/A_PUBLIC_POST",
                    "relevance": (f"hashtag {hashtag}; engagement "
                                  f"likes={likes} rt={reposts}"),
                    "security_concerns": (f"sanitizer_risk={risk_score}"
                                          if risk_score > 0 else "UNSCREENED"),
                    "council_sponsor": "POLARIS",
                }
                try:
                    from services.inspiration_intake_ledger import record_inspiration
                    record_inspiration(
                        source_type="hashtag_channel",
                        source_ref=(f"x:{hashtag}:{_post_id}" if _post_id
                                    else f"x:{hashtag}:{int(time.time())}"),
                        conn=conn, **_payload)
                except Exception as _led:
                    log.warning("x_scout: intake ledger failed (%s) — quarantining", _led)
                    try:
                        from services.inspiration_intake_ledger import quarantine_intake
                        quarantine_intake("hashtag_channel",
                                          f"x:{hashtag}:{_post_id}", _payload, str(_led))
                    except Exception:
                        log.error("x_scout: quarantine also failed; item survives "
                                  "only in cognition_log")
            conn.commit()
        log.info("x_scout: stored insights for %s (%d posts)", hashtag, len(posts))
    except Exception as e:
        log.warning("x_scout: failed to store insights for %s: %s", hashtag, e)


def _run_scan_cycle(token: str) -> int:
    """Run one full scan across all hashtags. Returns total posts found."""
    hashtags = _get_hashtags()
    total    = 0
    log.info("x_scout: scanning %d hashtags", len(hashtags))

    for tag in hashtags:
        posts = _search_hashtag(token, tag)
        _store_insights(tag, posts)
        total += len(posts)
        time.sleep(2)  # 1 req/s rate limit — 2s to be safe

    log.info("x_scout: cycle complete — %d posts across %d hashtags", total, len(hashtags))
    return total


# ── LOCAL HTTP API — lets Polaris call X search without seeing the token ──────
# Runs as a background thread on localhost:8899
# GET http://localhost:8899/search?q=QUERY&max=10
# GET http://localhost:8899/status

import threading as _threading
import urllib.parse as _urlparse
from http.server import HTTPServer as _HTTPServer, BaseHTTPRequestHandler as _BaseHandler


class _XScoutAPIHandler(_BaseHandler):
    """Tiny local HTTP server so Polaris/council can search X without raw token."""
    def log_message(self, *a): pass  # silence access logs

    def do_GET(self):
        parsed = _urlparse.urlparse(self.path)
        params = _urlparse.parse_qs(parsed.query)

        if parsed.path == "/status":
            token = _get_bearer_token()
            body = json.dumps({
                "status": "ok" if token else "no_token",
                "token_set": bool(token),
                "hashtags": len(_get_hashtags()),
                "service": SERVICE_NAME,
            }).encode()
            self._respond(200, body)

        elif parsed.path == "/search":
            q   = params.get("q", [""])[0].strip()
            max_r = int(params.get("max", ["10"])[0])
            token = _get_bearer_token()
            if not q:
                self._respond(400, json.dumps({"error": "missing q param"}).encode()); return
            if not token:
                self._respond(503, json.dumps({"error": "TWITTER_BEARER_TOKEN not set"}).encode()); return
            posts = _search_hashtag(token, q, max_results=max_r)
            body  = json.dumps({"query": q, "count": len(posts), "posts": posts}).encode()
            self._respond(200, body)

        elif parsed.path == "/hashtags":
            body = json.dumps({"hashtags": _get_hashtags()}).encode()
            self._respond(200, body)

        else:
            self._respond(404, b"{}")

    def _respond(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _start_api_server(port: int = 8899) -> None:
    """Start local X Scout API server in a daemon thread."""
    try:
        server = _HTTPServer(("127.0.0.1", port), _XScoutAPIHandler)
        t = _threading.Thread(target=server.serve_forever, daemon=True, name="x_scout_api")
        t.start()
        log.info("x_scout: local API running on http://localhost:%d", port)
        log.info("x_scout: Polaris can call GET http://localhost:%d/search?q=QUERY", port)
    except Exception as e:
        log.warning("x_scout: could not start local API server: %s", e)


def run() -> None:
    """Main loop — called by reconnaissance_engine worker."""
    # ── WRITE HEARTBEAT IMMEDIATELY so guardian doesn't kill us before first scan ──
    update_heartbeat(SERVICE_NAME, "ALIVE", "x_scout starting — validating token")

    token = _get_bearer_token()
    if not token:
        log.warning("x_scout: TWITTER_BEARER_TOKEN not set — scout idle")
        update_heartbeat(SERVICE_NAME, "IDLE", "TWITTER_BEARER_TOKEN missing in .env")
        while True:
            time.sleep(3600)
            update_heartbeat(SERVICE_NAME, "IDLE", "TWITTER_BEARER_TOKEN missing — still waiting")
        return

    # ── VALIDATE TOKEN before first scan (ChatGPT audit finding: was never called) ──
    token_ok, token_msg = check_bearer_token()
    if not token_ok:
        log.warning("x_scout: token validation failed: %s", token_msg)
        update_heartbeat(SERVICE_NAME, "DEGRADED", f"token_check_failed: {token_msg[:80]}")
        # Don't exit — network may recover. Keep heartbeating and retry.
        _consecutive_network_failures = 0
        while True:
            time.sleep(300)
            _consecutive_network_failures += 1
            token_ok2, msg2 = check_bearer_token()
            if token_ok2:
                log.info("x_scout: token now valid — resuming normal operation")
                break
            update_heartbeat(SERVICE_NAME, "DEGRADED",
                             f"network_retry_{_consecutive_network_failures}: {msg2[:60]}")
            if _consecutive_network_failures > 12:  # 1 hour of retries
                log.warning("x_scout: X API unreachable for 1hr — continuing anyway")
                break

    log.info("x_scout: ONLINE — token valid, starting API server")
    _start_api_server()
    update_heartbeat(SERVICE_NAME, "ALIVE", "api_server_started | beginning first scan cycle")

    _consecutive_failures = 0

    while True:
        try:
            interval = _get_interval()
            total    = _run_scan_cycle(token)
            hashtags = _get_hashtags()

            if total == 0:
                _consecutive_failures += 1
                backoff = min(300, 30 * _consecutive_failures)
                note = (f"scanned={len(hashtags)} hashtags | found=0 posts "
                        f"| X_API_DEGRADED failures={_consecutive_failures} "
                        f"| next_retry={backoff}s")
                update_heartbeat(SERVICE_NAME, "DEGRADED", note)
                log.warning("x_scout: 0 posts — X API may be unreachable. sleeping %ds", backoff)
                time.sleep(backoff)
            else:
                _consecutive_failures = 0
                note = (f"scanned={len(hashtags)} hashtags | found={total} posts "
                        f"| next={interval//60}m")
                update_heartbeat(SERVICE_NAME, "ALIVE", note)
                log.info("x_scout: sleeping %ds until next cycle", interval)
                time.sleep(interval)

        except Exception as exc:
            log.exception("x_scout: cycle error: %s", exc)
            update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            time.sleep(300)