#!/usr/bin/env python3
"""
gmgn_cf_bridge.py
=================
Small explicit bridge for GMGN wallet profile ingestion.

Why this exists:
- GMGN often Cloudflare-challenges bare Python requests.
- wallet_scout.py can now use either:
  1) GMGN_COOKIE=<full Cookie header>, or
  2) GMGN_CF_CLEARANCE=<cf_clearance value> + GMGN_CF_UA=<exact browser UA>

This file does not scrape private data, does not bypass auth, and does not trade.
It only attaches operator-provided browser clearance to read public leaderboard data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple

try:
    import requests
except Exception as exc:  # pragma: no cover
    raise RuntimeError("requests is required") from exc

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

ROOT = Path(__file__).resolve().parent
if load_dotenv:
    # Works whether this file is in root or services/.
    load_dotenv(ROOT / ".env", override=True)
    load_dotenv(ROOT.parent / ".env", override=True)

GMGN_TEST_URL = os.getenv(
    "GMGN_TOP_WALLETS_URL",
    "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/top_traders",
).strip()


class CFBridgeError(RuntimeError):
    pass


def build_gmgn_session(base_session: Optional[requests.Session] = None) -> Tuple[requests.Session, dict]:
    sess = base_session or requests.Session()
    cookie = os.getenv("GMGN_COOKIE", "").strip()
    clearance = os.getenv("GMGN_CF_CLEARANCE", "").strip()
    ua = os.getenv("GMGN_CF_UA", "").strip()

    if cookie:
        sess.headers.update({
            "Cookie": cookie,
            "User-Agent": ua or "Mozilla/5.0",
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
            "Accept": "application/json,text/plain,*/*",
        })
        return sess, {"mode": "full_cookie", "ua": bool(ua)}

    if clearance:
        sess.cookies.set("cf_clearance", clearance, domain=".gmgn.ai", path="/")
        sess.headers.update({
            "User-Agent": ua or "Mozilla/5.0",
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
            "Accept": "application/json,text/plain,*/*",
        })
        return sess, {"mode": "cf_clearance", "ua": bool(ua)}

    raise CFBridgeError(
        "No GMGN_COOKIE or GMGN_CF_CLEARANCE found. Open gmgn.ai in Brave, copy "
        "the Cookie header or cf_clearance + exact User-Agent into .env, then retry."
    )


def self_test() -> int:
    try:
        sess, diag = build_gmgn_session()
    except CFBridgeError as exc:
        print(f"[CF BRIDGE] ❌ {exc}")
        return 2

    try:
        resp = sess.get(GMGN_TEST_URL, params={"limit": 3}, timeout=(8, 15))
        ct = resp.headers.get("content-type", "")
        print(f"[CF BRIDGE] mode={diag.get('mode')} status={resp.status_code} content_type={ct}")
        if resp.status_code != 200:
            print(resp.text[:300].replace("\n", " "))
            return 1
        try:
            data = resp.json()
            if isinstance(data, dict):
                print("[CF BRIDGE] ✅ JSON keys:", list(data.keys())[:12])
            elif isinstance(data, list):
                print("[CF BRIDGE] ✅ JSON list rows:", len(data))
            else:
                print("[CF BRIDGE] ✅ JSON type:", type(data).__name__)
            return 0
        except Exception as exc:
            print(f"[CF BRIDGE] ❌ JSON parse failed: {exc}")
            print(resp.text[:300].replace("\n", " "))
            return 1
    except Exception as exc:
        print(f"[CF BRIDGE] ❌ request failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(self_test())
