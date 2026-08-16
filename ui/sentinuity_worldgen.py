"""
ui/sentinuity_worldgen.py
=========================
SENTINUITY_WORLD_ART_20260817

Regenerates every art asset the living world needs, deterministically.

    python ui/sentinuity_worldgen.py

Writes into ui/assets/:
    world_backdrop.png   960x600 environment-only island
    world_sites.json     faculty anchor coordinates (percent)
    sprite_atlas.png     10 characters x 3 poses x 2 walk frames
    sprite_atlas.json    row index, labels, roles, npc flag
    relic_atlas.png      10 discovery lifecycle states x 2 frames
    relic_atlas.json     row index per state

Requires Pillow, and ONLY at generation time. The Streamlit runtime never
imports this module — it loads the emitted PNG/JSON files. That is deliberate:
no new dependency is added to the hub.

Two invariants this file exists to protect:

  1. The backdrop contains environment ONLY. No characters, no labels, no
     numbers, no UI chrome. The previous backdrop was a rendered mock-up with
     "Win Rate 68.4%" and "$24,589.31" burned into the pixels while the real
     runtime sat at 6% and $345.73. Art must never be able to assert a metric.

  2. Sprite anchors and structure positions come from the SAME source
     (backdrop.SITES), exported to world_sites.json, so the map and the
     characters standing on it can never drift apart.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ASSETS = _HERE / "assets"

sys.path.insert(0, str(_HERE.parent))

try:
    from PIL import Image
except ImportError:                                        # pragma: no cover
    raise SystemExit("Pillow is required to regenerate art: pip install Pillow")

from ui.worldgen import backdrop as B
from ui.worldgen import sprites as S


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    # ── backdrop ───────────────────────────────────────────────────────────
    img = B.vignette(B.rim_mist(B.generate()))
    img.resize((B.W * B.SCALE, B.H * B.SCALE), Image.NEAREST).save(
        ASSETS / "world_backdrop.png")

    sites = {k: {"x": round(v[0] / B.W * 100, 2), "y": round(v[1] / B.H * 100, 2)}
             for k, v in B.SITES.items()}
    (ASSETS / "world_sites.json").write_text(
        json.dumps({"width": B.W, "height": B.H, "sites": sites}, indent=2))

    # ── sprites ────────────────────────────────────────────────────────────
    atlas, meta = S.build_atlas()
    atlas.resize((atlas.width * S.SCALE_OUT, atlas.height * S.SCALE_OUT),
                 Image.NEAREST).save(ASSETS / "sprite_atlas.png")
    (ASSETS / "sprite_atlas.json").write_text(json.dumps(meta, indent=2))

    relics, rmeta = S.build_relics()
    relics.resize((relics.width * S.SCALE_OUT, relics.height * S.SCALE_OUT),
                  Image.NEAREST).save(ASSETS / "relic_atlas.png")
    (ASSETS / "relic_atlas.json").write_text(json.dumps(rmeta, indent=2))

    print(f"backdrop  {B.W * B.SCALE}x{B.H * B.SCALE}  "
          f"({(ASSETS / 'world_backdrop.png').stat().st_size // 1024} KB)")
    print(f"sprites   {len(meta['rows'])} characters, "
          f"{len(meta['poses']) * meta['frames']} frames each  "
          f"({(ASSETS / 'sprite_atlas.png').stat().st_size // 1024} KB)")
    print(f"relics    {len(rmeta['rows'])} lifecycle states  "
          f"({(ASSETS / 'relic_atlas.png').stat().st_size // 1024} KB)")
    print(f"sites     {len(sites)} anchors -> world_sites.json")


if __name__ == "__main__":
    main()
