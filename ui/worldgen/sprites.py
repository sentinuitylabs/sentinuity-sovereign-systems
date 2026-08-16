"""
sentinuity worldgen — SPRITE ATLAS.

One art family. Every character is drawn by the SAME body template from a small
parameter block, which is what guarantees a common scale, common pixel density,
common proportions and a common ground line. Identity comes from silhouette
(ears, muzzle, tail), palette and one accessory — never from a text label baked
into the image.

Canvas   : 24 x 28 px per frame
Baseline : feet at y=26 for EVERY character (shadow is drawn by CSS, not baked)
Poses    : south (front), north (back), east (side)   [west = CSS scaleX(-1)]
Frames   : 2 walk frames per pose  ->  6 frames per character
Atlas    : columns = frames, rows = characters
"""
from __future__ import annotations
import json, math
from PIL import Image, ImageDraw

CW, CH = 24, 28          # cell
BASE_Y = 26              # ground line — identical for every character
SCALE_OUT = 4            # atlas is emitted at 4x for crisp NEAREST scaling

POSES = ["s", "n", "e"]
FRAMES = 2

T = (0, 0, 0, 0)         # transparent


def _shade(c, f):
    return (max(0, min(255, int(c[0] * f))),
            max(0, min(255, int(c[1] * f))),
            max(0, min(255, int(c[2] * f))), 255)


def _rgba(c):
    return (c[0], c[1], c[2], 255) if len(c) == 3 else c


# ── characters ─────────────────────────────────────────────────────────────
# ears   : floppy | pointed | tuft | round | none
# muzzle : short | long | beak | blunt
# tail   : curl | brush | plume | stub | none
# mark   : none | blaze | mask | patch | spots
# outfit : cloak | vest | scarf | apron | robe | none
# tool   : none | staff | hammer | lens | satchel | lantern | spear
CHARACTERS = {
    # ── COUNCIL (primary party — full saturation, full scale) ──────────────
    "NUGGET": dict(  # golden boxer — auditor / field scout
        fur=(226, 168, 66), belly=(248, 226, 176), ears="floppy", muzzle="blunt",
        tail="stub", mark="blaze", outfit="vest", outfit_col=(58, 96, 120),
        tool="satchel", eye=(58, 40, 26), accent=(255, 209, 102), scale=1.0,
        label="Nugget", role="Field scout · auditor"),
    "POLARIS": dict(  # all-white siberian husky — planner, final safety gate
        fur=(244, 247, 252), belly=(255, 255, 255), ears="pointed", muzzle="short",
        tail="plume", mark="none", outfit="scarf", outfit_col=(82, 244, 255),
        tool="none", eye=(58, 150, 200), accent=(82, 244, 255), scale=0.94,
        label="Polaris", role="Coordinator · safety gate"),
    "IVARIS": dict(  # silver wolf — adversarial critic
        fur=(158, 168, 186), belly=(206, 214, 228), ears="pointed", muzzle="long",
        tail="brush", mark="mask", outfit="cloak", outfit_col=(72, 60, 108),
        tool="none", eye=(255, 178, 74), accent=(255, 178, 74), scale=1.0,
        label="Ivaris", role="Adversarial critic"),
    "ORACLE": dict(  # violet sage cat — external senses
        fur=(126, 106, 168), belly=(196, 176, 228), ears="pointed", muzzle="short",
        tail="curl", mark="none", outfit="robe", outfit_col=(96, 62, 148),
        tool="lens", eye=(126, 240, 176), accent=(173, 99, 255), scale=0.97,
        label="Oracle", role="Senses · price & market truth"),
    "RHIZA": dict(  # lynx archivist — synthesis / memory
        fur=(198, 152, 96), belly=(238, 216, 178), ears="tuft", muzzle="short",
        tail="stub", mark="spots", outfit="cloak", outfit_col=(38, 108, 88),
        tool="staff", eye=(66, 245, 167), accent=(66, 245, 167), scale=0.98,
        label="Rhiza", role="Synthesis · memory"),
    "AXON": dict(  # badger smith — implementation / execution validator
        fur=(84, 84, 96), belly=(232, 234, 240), ears="round", muzzle="long",
        tail="stub", mark="mask", outfit="apron", outfit_col=(140, 78, 48),
        tool="hammer", eye=(46, 46, 56), accent=(255, 138, 61), scale=1.0,
        label="Axon", role="Implementation · execution validator"),

    # ── SETTLEMENT NPCs (secondary — smaller and less saturated) ───────────
    "COURIER": dict(  # owl — carries verified information
        fur=(120, 104, 150), belly=(214, 206, 232), ears="none", muzzle="beak",
        tail="none", mark="none", outfit="none", outfit_col=(80, 70, 110),
        tool="none", eye=(255, 209, 102), accent=(173, 99, 255), scale=0.80,
        npc=True, wings=True, label="Courier", role="Carries evidence"),
    "GUARDIAN": dict(  # bear steward — service health
        fur=(112, 88, 72), belly=(186, 162, 138), ears="round", muzzle="blunt",
        tail="stub", mark="none", outfit="vest", outfit_col=(64, 70, 92),
        tool="spear", eye=(44, 34, 28), accent=(255, 209, 102), scale=0.86,
        npc=True, label="Guardian", role="Service steward"),
    "KEEPER": dict(  # rabbit gardener — substrate nursery
        fur=(176, 168, 152), belly=(226, 222, 210), ears="pointed", muzzle="short",
        tail="stub", mark="none", outfit="apron", outfit_col=(62, 118, 84),
        tool="none", eye=(70, 58, 50), accent=(66, 245, 167), scale=0.80,
        npc=True, long_ears=True, label="Keeper", role="Nursery"),
    "WARDEN": dict(  # turtle quartermaster — stores and tallies
        fur=(96, 130, 96), belly=(190, 200, 158), ears="none", muzzle="blunt",
        tail="stub", mark="none", outfit="none", outfit_col=(90, 78, 54),
        tool="lantern", eye=(48, 60, 44), accent=(255, 209, 102), scale=0.82,
        npc=True, shell=True, label="Warden", role="Stores · tallies"),
}


# ── drawing primitives ─────────────────────────────────────────────────────
def px(d, x, y, c):
    d.point((x, y), fill=_rgba(c))


def rect(d, x0, y0, x1, y1, c):
    d.rectangle([x0, y0, x1, y1], fill=_rgba(c))


def ell(d, x0, y0, x1, y1, c):
    d.ellipse([x0, y0, x1, y1], fill=_rgba(c))


# ── the shared body template ───────────────────────────────────────────────
def draw_character(cfg: dict, pose: str, frame: int) -> Image.Image:
    im = Image.new("RGBA", (CW, CH), T)
    d = ImageDraw.Draw(im)

    fur = cfg["fur"]
    dark = _shade(fur, 0.66)
    light = _shade(fur, 1.16)
    belly = cfg["belly"]
    out_c = cfg["outfit_col"]
    out_d = _shade(out_c, 0.68)
    eye = cfg["eye"]
    accent = cfg["accent"]

    cx = 12
    step = frame  # 0 = contact, 1 = passing
    body_lift = -1 if step else 0     # whole body bobs 1px while walking

    # ---- legs (drawn first, behind body) ----------------------------------
    ly = BASE_Y
    if pose == "e":
        front, back = (cx + 1, cx - 3) if step == 0 else (cx - 1, cx + 3)
        rect(d, back, ly - 4, back + 2, ly, dark)
        rect(d, front, ly - 4, front + 2, ly, fur)
    else:
        lo = 1 if step else 0
        rect(d, cx - 5, ly - 4 - lo, cx - 3, ly - lo, dark if pose == "n" else fur)
        rect(d, cx + 2, ly - 4 + lo, cx + 4, ly + 0, dark if pose == "n" else fur)
        px(d, cx - 5, ly - lo, dark)
        px(d, cx + 4, ly, dark)

    by0 = 15 + body_lift          # body top
    by1 = BASE_Y - 3 + body_lift  # body bottom

    # ---- tail (behind body) ----------------------------------------------
    t = cfg["tail"]
    if t != "none":
        tx = cx + 6 if pose == "e" else (cx + 6 if pose != "n" else cx - 1)
        if pose == "n":
            if t == "plume":
                ell(d, cx - 3, by0 + 1, cx + 3, by1 + 2, light)
                ell(d, cx - 2, by0 + 2, cx + 1, by1, fur)
            else:
                rect(d, cx - 1, by0 + 2, cx + 1, by1 + 1, dark)
        elif t == "plume":
            ell(d, tx - 3, by0 - 1, tx + 2, by1, light)
            ell(d, tx - 2, by0, tx + 1, by1 - 2, fur)
        elif t == "brush":
            ell(d, tx - 2, by0 + 1, tx + 2, by1, dark)
            ell(d, tx - 1, by0 + 2, tx + 1, by1 - 2, fur)
        elif t == "curl":
            for i, (ox, oy) in enumerate(((0, 0), (1, -2), (0, -4), (-2, -5))):
                px(d, tx + ox, by0 + 2 + oy, fur if i % 2 else dark)
                px(d, tx + ox + 1, by0 + 2 + oy, dark)
        else:  # stub
            rect(d, tx - 1, by0 + 2, tx + 1, by0 + 4, dark)

    # ---- torso ------------------------------------------------------------
    bw = 5 if pose != "e" else 4
    ell(d, cx - bw, by0, cx + bw - 1, by1, fur)
    if pose == "s":
        ell(d, cx - bw + 2, by0 + 2, cx + bw - 3, by1, belly)
    if pose == "n":
        ell(d, cx - bw + 1, by0 + 1, cx + bw - 2, by1 - 2, dark)

    # turtle shell
    if cfg.get("shell"):
        sh = _shade((96, 78, 52), 1.0)
        ell(d, cx - bw - 1, by0 - 1, cx + bw, by1 - 3, sh)
        ell(d, cx - bw, by0, cx + bw - 2, by1 - 5, _shade((96, 78, 52), 1.28))
        for ox in (-3, 0, 3):
            px(d, cx + ox, by0 + 2, _shade((96, 78, 52), 0.7))

    # ---- outfit -----------------------------------------------------------
    o = cfg["outfit"]
    if o == "cloak":
        ell(d, cx - bw - 1, by0 - 1, cx + bw, by1 + 1, out_c)
        ell(d, cx - bw, by0, cx + bw - 2, by1 - 1, _shade(out_c, 1.22))
        if pose != "n":
            rect(d, cx - 1, by0, cx + 1, by1 - 1, out_d)   # front opening
        px(d, cx, by0 + 1, accent)
    elif o == "robe":
        ell(d, cx - bw - 1, by0, cx + bw, by1 + 1, out_c)
        rect(d, cx - bw, by0 + 3, cx + bw - 1, by1, _shade(out_c, 1.18))
        for ox in (-2, 2):
            px(d, cx + ox, by0 + 3, accent)
    elif o == "vest":
        rect(d, cx - bw + 1, by0 + 1, cx + bw - 2, by1 - 1, out_c)
        rect(d, cx - 1, by0 + 1, cx, by1 - 1, out_d)
        px(d, cx - bw + 1, by0 + 2, accent)
    elif o == "apron":
        rect(d, cx - bw + 2, by0 + 2, cx + bw - 3, by1 - 2, out_c)
        rect(d, cx - bw + 2, by0 + 2, cx + bw - 3, by0 + 3, _shade(out_c, 1.25))
        px(d, cx + bw - 3, by1 - 3, accent)
    elif o == "scarf":
        rect(d, cx - bw, by0, cx + bw - 1, by0 + 2, out_c)
        rect(d, cx - bw, by0 + 2, cx - bw + 2, by0 + 5, _shade(out_c, 0.8))

    # ---- arms -------------------------------------------------------------
    arm_y = by0 + 3
    if pose == "e":
        swing = 1 if step == 0 else -1
        rect(d, cx + 3, arm_y + swing, cx + 4, arm_y + 3 + swing, dark)
    else:
        sw = 1 if step else 0
        rect(d, cx - bw - 1, arm_y - sw, cx - bw, arm_y + 2 - sw, fur)
        rect(d, cx + bw - 1, arm_y + sw, cx + bw, arm_y + 2 + sw, fur)

    # ---- head -------------------------------------------------------------
    hy0, hy1 = 3 + body_lift, 16 + body_lift
    hx0, hx1 = cx - 7, cx + 6

    # ears behind the skull
    e = cfg["ears"]
    ear_len = 10 if cfg.get("long_ears") else 0
    if e == "pointed" or e == "tuft":
        h = 5 + ear_len
        for sx in (-1, 1):
            ex = cx + sx * 5
            d.polygon([(ex - 2, hy0 + 3), (ex + 2, hy0 + 3), (ex + sx, hy0 - h + 3)],
                      fill=_rgba(fur if pose != "n" else _shade(fur, 0.82)))
            d.polygon([(ex - 1, hy0 + 2), (ex + 1, hy0 + 2), (ex + sx, hy0 - h + 5)],
                      fill=_rgba(_shade(belly, 0.92) if pose != "n"
                                 else _shade(fur, 0.58)))
            if e == "tuft":
                px(d, ex + sx, hy0 - h + 1, light)
                px(d, ex + sx, hy0 - h + 2, light)
    elif e == "round":
        for sx in (-1, 1):
            ex = cx + sx * 6
            ell(d, ex - 3, hy0 - 1, ex + 2, hy0 + 4,
                fur if pose != "n" else _shade(fur, 0.82))
            ell(d, ex - 2, hy0, ex + 1, hy0 + 3,
                _shade(belly, 0.9) if pose != "n" else _shade(fur, 0.58))

    # skull
    ell(d, hx0, hy0, hx1, hy1, fur)
    if pose == "n":
        ell(d, hx0 + 1, hy0 + 2, hx1 - 1, hy1 - 1, _shade(fur, 0.74))
        ell(d, hx0 + 2, hy0 + 1, hx1 - 2, hy0 + 5, _shade(fur, 1.06))
        # nape tuft — gives the back of the head a readable silhouette
        for ox in (-3, 0, 3):
            px(d, cx + ox, hy1 - 3, _shade(fur, 0.5))
    else:
        ell(d, hx0 + 2, hy0 + 1, hx1 - 2, hy1 - 5, light)

    # floppy ears sit ON the skull
    if e == "floppy":
        for sx in (-1, 1):
            ex = cx + sx * 6
            ell(d, ex - 2, hy0 + 2, ex + 2, hy0 + 9, dark)
            ell(d, ex - 1, hy0 + 3, ex + 1, hy0 + 7, _shade(fur, 0.82))

    # markings
    m = cfg["mark"]
    if pose != "n":
        if m == "blaze":
            rect(d, cx - 1, hy0 + 1, cx, hy1 - 5, belly)
            # boxer mask — narrow dark band, the muzzle overdraws its centre
            ell(d, cx - 4, hy1 - 8, cx + 3, hy1 - 5, _shade(fur, 0.5))
        elif m == "mask":
            rect(d, cx - 6, hy0 + 4, cx - 3, hy0 + 8, _shade(fur, 0.45))
            rect(d, cx + 2, hy0 + 4, cx + 5, hy0 + 8, _shade(fur, 0.45))
            rect(d, cx - 2, hy0 + 1, cx + 1, hy1 - 4, belly)
        elif m == "patch":
            ell(d, cx + 1, hy0 + 3, cx + 5, hy0 + 8, _shade(fur, 0.5))
        elif m == "spots":
            for (ox, oy) in ((-5, 5), (-3, 8), (4, 5), (2, 9)):
                px(d, cx + ox, hy0 + oy, _shade(fur, 0.62))

    # muzzle / beak
    mu = cfg["muzzle"]
    if pose != "n":
        if mu == "beak":
            mx = cx + (3 if pose == "e" else 0)
            d.polygon([(mx - 2, hy1 - 6), (mx + 2, hy1 - 6), (mx, hy1 - 2)],
                      fill=_rgba(accent))
        else:
            mw = {"short": 3, "long": 4, "blunt": 4}[mu]
            mx = cx + (3 if pose == "e" else 0)
            ml = {"short": 3, "long": 5, "blunt": 3}[mu]
            ell(d, mx - mw, hy1 - 6, mx + mw - (0 if pose != "e" else -1), hy1 - 6 + ml,
                belly)
            nose_x = mx + (2 if pose == "e" else 0)
            rect(d, nose_x - 1, hy1 - 5, nose_x, hy1 - 4, _shade(fur, 0.32))

    # eyes
    if pose != "n":
        if pose == "e":
            rect(d, cx + 1, hy0 + 5, cx + 2, hy0 + 6, eye)
            px(d, cx + 2, hy0 + 5, (255, 255, 255))
        else:
            for sx in (-1, 1):
                ex = cx + sx * 3 - (1 if sx < 0 else 0)
                rect(d, ex, hy0 + 5, ex + 1, hy0 + 7, eye)
                px(d, ex + (1 if sx > 0 else 0), hy0 + 5, (255, 255, 255))

    # hood for cloaked/robed characters seen from behind
    if pose == "n" and o in ("cloak", "robe"):
        ell(d, hx0 - 1, hy0 + 2, hx1 + 1, hy1 - 3, out_c)
        ell(d, hx0 + 1, hy0 + 3, hx1 - 1, hy1 - 5, _shade(out_c, 1.2))
        ell(d, hx0 + 1, hy0 + 2, hx1 - 1, hy0 + 4, _shade(out_c, 1.4))
        if e in ("pointed", "tuft"):      # ear tips clear the hood
            for sx in (-1, 1):
                ex = cx + sx * 5
                d.polygon([(ex - 1, hy0 + 2), (ex + 1, hy0 + 2), (ex + sx, hy0 - 2)],
                          fill=_rgba(_shade(fur, 0.82)))

    # ---- wings (courier) --------------------------------------------------
    if cfg.get("wings"):
        spread = 4 if step else 2
        for sx in (-1, 1):
            wx = cx + sx * (bw + 1)
            d.polygon([(wx, by0), (wx + sx * spread, by0 - 2),
                       (wx + sx * (spread - 1), by1 - 2), (wx, by1 - 3)],
                      fill=_rgba(_shade(fur, 0.8)))
            d.polygon([(wx, by0 + 1), (wx + sx * (spread - 1), by0),
                       (wx, by1 - 4)], fill=_rgba(light))

    # ---- tool -------------------------------------------------------------
    tool = cfg["tool"]
    tx = cx + bw + 1
    if tool == "staff":
        rect(d, tx, by0 - 6, tx, BASE_Y - 1, (110, 76, 50))
        ell(d, tx - 2, by0 - 9, tx + 2, by0 - 5, accent)
        px(d, tx, by0 - 7, (255, 255, 255))
    elif tool == "hammer":
        rect(d, tx, by0 + 1, tx, by0 + 6, (110, 76, 50))
        rect(d, tx - 2, by0 - 2, tx + 2, by0 + 1, (150, 150, 168))
        rect(d, tx - 2, by0 - 2, tx + 2, by0 - 1, (196, 196, 214))
    elif tool == "lens":
        ell(d, tx - 1, by0, tx + 3, by0 + 4, (150, 154, 180))
        ell(d, tx, by0 + 1, tx + 2, by0 + 3, accent)
        rect(d, tx, by0 + 4, tx + 1, by0 + 7, (110, 114, 140))
    elif tool == "satchel":
        rect(d, cx - bw - 2, by0 + 4, cx - bw + 1, by0 + 8, (122, 88, 56))
        rect(d, cx - bw - 2, by0 + 4, cx - bw + 1, by0 + 5, (156, 118, 78))
        px(d, cx - bw, by0 + 6, accent)
        d.line([cx - bw + 1, by0 + 4, cx + 1, by0], fill=_rgba((92, 66, 42)))
    elif tool == "lantern":
        rect(d, tx, by0 + 1, tx, by0 + 2, (110, 76, 50))
        rect(d, tx - 2, by0 + 2, tx + 1, by0 + 6, (110, 92, 60))
        rect(d, tx - 1, by0 + 3, tx, by0 + 5, accent)
    elif tool == "spear":
        rect(d, tx, by0 - 7, tx, BASE_Y - 1, (110, 76, 50))
        d.polygon([(tx - 2, by0 - 7), (tx + 2, by0 - 7), (tx, by0 - 11)],
                  fill=(196, 200, 220))

    return im


# ── atlas assembly ─────────────────────────────────────────────────────────
def desaturate(im: Image.Image, amount: float) -> Image.Image:
    """NPCs read as background cast: same art family, lower presence."""
    px_ = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px_[x, y]
            if not a:
                continue
            lum = int(0.299 * r + 0.587 * g + 0.114 * b)
            px_[x, y] = (int(r + (lum - r) * amount),
                         int(g + (lum - g) * amount),
                         int(b + (lum - b) * amount), a)
    return im


def build_atlas():
    names = list(CHARACTERS.keys())
    cols = len(POSES) * FRAMES
    atlas = Image.new("RGBA", (cols * CW, len(names) * CH), T)
    meta = {"cell": {"w": CW, "h": CH}, "baseline": BASE_Y, "scale_out": SCALE_OUT,
            "poses": POSES, "frames": FRAMES, "rows": {}, "characters": {}}

    for r, name in enumerate(names):
        cfg = CHARACTERS[name]
        for pi, pose in enumerate(POSES):
            for f in range(FRAMES):
                cell = draw_character(cfg, pose, f)
                if cfg.get("npc"):
                    cell = desaturate(cell, 0.42)
                sc = cfg.get("scale", 1.0)
                if sc != 1.0:
                    # scale about the ground line so every character still
                    # stands on the same baseline
                    nh = max(1, int(round(CH * sc)))
                    nw = max(1, int(round(CW * sc)))
                    small = cell.resize((nw, nh), Image.NEAREST)
                    cell = Image.new("RGBA", (CW, CH), T)
                    cell.paste(small, ((CW - nw) // 2, CH - nh - 1), small)
                atlas.paste(cell, ((pi * FRAMES + f) * CW, r * CH))
        meta["rows"][name] = r
        meta["characters"][name] = {
            "label": cfg["label"], "role": cfg["role"],
            "npc": bool(cfg.get("npc")), "accent": "#%02x%02x%02x" % cfg["accent"],
        }
    return atlas, meta


# ── relic sprites (the discovery object lifecycle) ─────────────────────────
RELIC_STATES = ["unidentified", "inspecting", "promising", "contested",
                "dud", "accepted", "testing", "failed", "rejected", "absorbed"]
RC = 16


def draw_relic(state: str, frame: int) -> Image.Image:
    im = Image.new("RGBA", (RC, RC), T)
    d = ImageDraw.Draw(im)
    cx, cy = 8, 9
    body = {
        "unidentified": (120, 132, 150), "inspecting": (150, 164, 186),
        "promising": (126, 240, 200),    "contested": (255, 178, 74),
        "dud": (96, 96, 104),            "accepted": (255, 209, 102),
        "testing": (255, 138, 61),       "failed": (255, 85, 119),
        "rejected": (120, 120, 140),     "absorbed": (126, 240, 176),
    }[state]
    glow = _shade(body, 1.35)
    dark = _shade(body, 0.6)
    pulse = frame

    # a luminous forest relic: capped stalk, mushroom/crystal hybrid
    d.rectangle([cx - 1, cy, cx, cy + 5], fill=_rgba((214, 206, 190)))
    d.rectangle([cx - 2, cy + 5, cx + 1, cy + 6], fill=_rgba((150, 142, 128)))
    d.ellipse([cx - 5, cy - 5, cx + 4, cy + 1], fill=_rgba(dark))
    d.ellipse([cx - 4, cy - 5, cx + 3, cy], fill=_rgba(body))
    d.ellipse([cx - 3, cy - 4, cx, cy - 2], fill=_rgba(glow))

    if state in ("promising", "accepted", "absorbed") or pulse:
        for (ox, oy) in ((-6, -6), (5, -7), (-5, 1), (6, 0)):
            if (ox + oy + pulse) % 2 == 0:
                d.point((cx + ox, cy + oy), fill=_rgba(glow))
    if state == "contested":
        d.rectangle([cx - 5, cy - 3, cx - 1, cy - 2], fill=_rgba((82, 244, 255)))
    if state == "dud":
        d.line([cx - 4, cy - 6, cx + 3, cy + 1], fill=_rgba((80, 80, 92)))
        d.line([cx + 3, cy - 6, cx - 4, cy + 1], fill=_rgba((80, 80, 92)))
    if state == "failed":
        for (ox, oy) in ((-6, -8), (4, -9), (7, -3)):
            d.point((cx + ox, cy + oy), fill=_rgba((255, 85, 119)))
        d.line([cx - 1, cy - 5, cx + 1, cy - 1], fill=_rgba((60, 20, 30)))
    if state == "rejected":
        d.arc([cx - 7, cy - 9, cx + 6, cy + 4], 200, 340, fill=_rgba((236, 244, 255)))
    if state == "absorbed":
        d.point((cx, cy - 8), fill=_rgba((236, 255, 244)))
        d.point((cx, cy - 10), fill=_rgba(glow))
    return im


def build_relics():
    atlas = Image.new("RGBA", (2 * RC, len(RELIC_STATES) * RC), T)
    meta = {}
    for r, s in enumerate(RELIC_STATES):
        for f in range(2):
            atlas.paste(draw_relic(s, f), (f * RC, r * RC))
        meta[s] = r
    return atlas, {"cell": RC, "rows": meta}
