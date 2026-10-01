"""Sentinuity Labs approved motion background helper (2026-10-02).

Presentation-only. Uses a lightweight excerpt cut from the approved 27-Sep
Sentinuity Labs logo-upgrade reveal. No DB reads/writes, no trading authority.

The clip is intentionally limited to the lattice-formation passage before/into
the mark resolve. It replaces the synthetic runtime lattice canvas.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

ASSET_DIR = Path(__file__).resolve().parent / "assets"
VIDEO_PATH = ASSET_DIR / "sentinuity-labs-lattice-loop-20261002.mp4"


def _video_data_uri(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    return "data:video/mp4;base64," + base64.b64encode(raw).decode("ascii")


def inject_sentinuity_reel_background() -> bool:
    """Install the approved reveal excerpt as the fixed page atmosphere."""
    try:
        import streamlit.components.v1 as components
    except Exception:
        return False

    uri = _video_data_uri(VIDEO_PATH)
    if not uri:
        return False

    # Parent-DOM installation is deliberate: Streamlit component iframes are
    # otherwise clipped to their own zero-height mount and cannot be a page bg.
    components.html(
        f"""
<script>
(() => {{
  try {{
    const doc = window.parent.document;
    const ID = 'snty-approved-reveal-background';
    const STYLE = 'snty-approved-reveal-background-style';

    // Remove the superseded synthetic lattice/hex/facet canvas and stale media.
    ['#snty-labs-lattice-background', '#'+ID].forEach(sel => {{
      try {{ doc.querySelectorAll(sel).forEach(el => el.remove()); }} catch (_) {{}}
    }});
    ['#snty-labs-lattice-background-style', '#'+STYLE].forEach(sel => {{
      try {{ doc.querySelectorAll(sel).forEach(el => el.remove()); }} catch (_) {{}}
    }});

    const style = doc.createElement('style');
    style.id = STYLE;
    style.textContent = `
      html,body,[data-testid="stAppViewContainer"]{{background:#071417!important;}}
      [data-testid="stAppViewContainer"]{{position:relative!important;isolation:isolate!important;background-image:none!important;}}
      #${{ID}}{{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden;background:#071417;}}
      #${{ID}} video{{position:absolute;inset:-2%;width:104%;height:104%;object-fit:cover;object-position:50% 48%;
        opacity:.34;filter:saturate(1.08) contrast(1.08) brightness(.57);}}
      #${{ID}}::before{{content:"";position:absolute;inset:0;z-index:1;background:
        radial-gradient(ellipse 62% 48% at 50% 24%,rgba(153,69,255,.08),transparent 70%),
        linear-gradient(180deg,rgba(7,20,23,.36) 0%,rgba(7,20,23,.54) 48%,rgba(7,20,23,.83) 100%);}}
      #${{ID}}::after{{content:"";position:absolute;inset:0;z-index:2;background:
        linear-gradient(90deg,rgba(7,20,23,.40),transparent 28%,transparent 72%,rgba(7,20,23,.40));}}
      [data-testid="stAppViewContainer"] > *:not(#${{ID}}),[data-testid="stMain"],[data-testid="stMainBlockContainer"],
      [data-testid="stHeader"],main{{position:relative!important;z-index:2!important;background-color:transparent!important;}}
      [data-testid="stHeader"]{{background:rgba(7,20,23,.62)!important;backdrop-filter:blur(14px);}}
      @media(max-width:760px){{#${{ID}} video{{opacity:.29;object-position:50% 48%;filter:saturate(1.06) contrast(1.06) brightness(.50);}}
        #${{ID}}::before{{background:linear-gradient(180deg,rgba(7,20,23,.42),rgba(7,20,23,.62) 52%,rgba(7,20,23,.88));}}}}
      @media(prefers-reduced-motion:reduce){{#${{ID}} video{{display:none!important;}}}}
    `;
    doc.head.appendChild(style);

    const host = doc.querySelector('[data-testid="stAppViewContainer"]') || doc.body;
    const bg = doc.createElement('div');
    bg.id = ID;
    bg.setAttribute('aria-hidden','true');
    bg.innerHTML = `<video autoplay muted loop playsinline preload="auto" tabindex="-1"><source src="{uri}" type="video/mp4"></video>`;
    host.prepend(bg);
    const v = bg.querySelector('video');
    if (v) {{ v.play().catch(() => {{}}); }}
  }} catch (_) {{}}
}})();
</script>
""",
        height=0,
        width=0,
    )
    return True
