"""
ui/sentinuity_world_bridge.py
=============================
SENTINUITY_LIVING_WORLD_BRIDGE_20260817

Mounts sovereign_world_v2.html and feeds it the canonical payload.

Two-slot architecture is preserved from the previous component: the world HTML
is injected ONCE (so the iframe and its sprite positions persist across
Streamlit reruns) and subsequent state arrives as a tiny zero-height script
that postMessages into the frame. Rerunning Streamlit must never teleport the
characters back to their start positions.

Assets live as real files in ui/assets/ and are inlined at mount time, because
components.html renders in a sandboxed srcdoc iframe that cannot read local
paths. Keeping them as files (rather than a 2MB base64 blob pasted into the
HTML) is what makes the art reviewable and diffable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent if _HERE.name.lower() == "ui" else _HERE
ASSETS = _HERE / "assets"
WORLD_HTML = _HERE / "sovereign_world_v2.html"
DB_PATH = ROOT / "sentinuity_matrix.db"

MIN_PUSH_INTERVAL = 0.6

_cache: dict = {"html": "", "mtime": 0.0}


def _b64(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def missing_assets() -> list[str]:
    """Reports exactly which art files are absent — never silently degrades."""
    need = ["world_backdrop.png", "sprite_atlas.png", "sprite_atlas.json",
            "relic_atlas.png", "relic_atlas.json", "world_sites.json"]
    return [n for n in need if not (ASSETS / n).exists()]


def build_world_html() -> str:
    """Inline the art into the template. Cached on template mtime."""
    gone = missing_assets()
    if gone:
        raise FileNotFoundError("missing art assets in ui/assets: " + ", ".join(gone))

    mtime = max(WORLD_HTML.stat().st_mtime,
                *[(ASSETS / n).stat().st_mtime for n in
                  ("world_backdrop.png", "sprite_atlas.png", "relic_atlas.png")])
    if _cache["html"] and _cache["mtime"] == mtime:
        return _cache["html"]

    html = WORLD_HTML.read_text(encoding="utf-8")
    html = (html
            .replace("__BACKDROP__", _b64(ASSETS / "world_backdrop.png", "image/png"))
            .replace("__SPRITES__", _b64(ASSETS / "sprite_atlas.png", "image/png"))
            .replace("__RELICS__", _b64(ASSETS / "relic_atlas.png", "image/png"))
            .replace("__SITES__", json.dumps(
                _read_json(ASSETS / "world_sites.json", {"sites": {}})))
            .replace("__ATLAS_META__", json.dumps(
                _read_json(ASSETS / "sprite_atlas.json", {})))
            .replace("__RELIC_META__", json.dumps(
                _read_json(ASSETS / "relic_atlas.json", {}))))
    _cache.update(html=html, mtime=mtime)
    return html


def _state_hash(state: dict) -> str:
    trimmed = {k: v for k, v in state.items() if k not in ("generated_at", "events",
                                                           "chronicle", "heartbeats")}
    return hashlib.md5(
        json.dumps(trimmed, sort_keys=True, default=str).encode()).hexdigest()[:16]


def render_world(state: Optional[dict] = None, height: int = 620) -> None:
    """Mount once, then push deltas. Import of streamlit is deferred so this
    module stays importable from plain scripts and tests."""
    import streamlit as st
    import streamlit.components.v1 as components

    if state is None:
        from ui.sentinuity_canon import load_canonical_state
        state = load_canonical_state(DB_PATH)

    if "_canon_world_slot" not in st.session_state:
        st.session_state["_canon_world_slot"] = st.empty()
        st.session_state["_canon_update_slot"] = st.empty()
        st.session_state["_canon_mounted"] = False
        st.session_state["_canon_hash"] = ""
        st.session_state["_canon_last_push"] = 0.0

    world_slot = st.session_state["_canon_world_slot"]
    update_slot = st.session_state["_canon_update_slot"]
    now = time.time()

    if not st.session_state["_canon_mounted"]:
        try:
            html = build_world_html()
        except FileNotFoundError as exc:
            st.error(f"Living world cannot mount — {exc}. "
                     f"Run: python ui/sentinuity_worldgen.py")
            return
        boot = ("<script>window.__CANON__=" + json.dumps(state, default=str) + ";"
                "if(window.applyCanonState)window.applyCanonState(window.__CANON__);"
                "</script>")
        full = html.replace("</body>", boot + "</body>")
        with world_slot:
            components.html(full, height=height, scrolling=False)
        st.session_state["_canon_mounted"] = True
        st.session_state["_canon_hash"] = _state_hash(state)
        st.session_state["_canon_last_push"] = now
        return

    if now - st.session_state["_canon_last_push"] < MIN_PUSH_INTERVAL:
        return
    h = _state_hash(state)
    if h == st.session_state["_canon_hash"]:
        return   # nothing changed — do not repaint, do not re-animate

    payload = json.dumps({"type": "sentinuity_canon_update", "state": state},
                         default=str)
    upd = ("<script>(function(){var p=" + payload + ";try{var t=null;"
           "Array.from(window.parent.frames||[]).some(function(f){try{"
           "if(f!==window&&typeof f.applyCanonState==='function'){t=f;return true}"
           "}catch(e){}return false});if(t)t.postMessage(p,'*')}catch(e){}})();</script>")
    with update_slot:
        components.html(upd, height=0, scrolling=False)
    st.session_state["_canon_hash"] = h
    st.session_state["_canon_last_push"] = now
