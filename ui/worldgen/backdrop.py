"""
sentinuity worldgen — BACKDROP (environment only).

Deterministic. No characters. No text. No numbers. No UI chrome.
Output: 480x300 logical pixels, upscaled x2 with NEAREST -> 960x600.

The island IS the organism. Geography encodes faculties:
    centre  HEART        organism core tree
    N       COUNCIL      chamber ring
    NE      PRICE_TRUTH  crystal spring + observatory
    SW      EXECUTION    forge
    W       INTELLIGENCE library shrine
    E       SMART_MONEY  watchtower
    S       SUBSTRATE    nursery grove
    edge    TRAILHEAD    forest gate (departure point)
    near gate INSPECTION stone circle (relics are placed here)
Everything outside the shore ring is WILD FOREST = external information space.
"""
from __future__ import annotations
import math, random
from PIL import Image, ImageDraw, ImageFilter

W, H = 480, 300
SCALE = 2
SEED = 20260817

# ── palette ────────────────────────────────────────────────────────────────
P = {
    "void":        (5, 4, 14),
    "deep":        (14, 24, 58),
    "water":       (22, 52, 104),
    "shallow":     (38, 104, 150),
    "foam":        (150, 226, 240),
    "sand":        (196, 168, 106),
    "sand_d":      (150, 124, 74),
    "grass_l":     (86, 168, 96),
    "grass":       (54, 122, 74),
    "grass_d":     (34, 86, 58),
    "canopy_d":    (16, 58, 44),
    "canopy":      (26, 84, 58),
    "canopy_l":    (44, 116, 74),
    "trunk":       (58, 40, 32),
    "path":        (146, 122, 86),
    "path_d":      (112, 92, 64),
    "stone":       (112, 112, 136),
    "stone_d":     (72, 72, 94),
    "stone_l":     (154, 154, 178),
    "roof_slate":  (58, 62, 96),
    "roof_warm":   (128, 68, 54),
    "wood":        (110, 76, 50),
    "wood_d":      (78, 52, 36),
    "cyan":        (82, 244, 255),
    "cyan_d":      (30, 132, 168),
    "violet":      (173, 99, 255),
    "gold":        (255, 209, 102),
    "ember":       (255, 138, 61),
    "mint":        (66, 245, 167),
    "heart_leaf":  (126, 240, 176),
    "heart_leaf_d":(52, 168, 118),
    "heart_bark":  (107, 70, 48),
    "heart_bark_d":(72, 46, 32),
    "shadow":      (18, 30, 34),
}

rng = random.Random(SEED)


def _v(x: int, y: int, salt: int = 0) -> float:
    """deterministic hash noise in [0,1)"""
    n = (x * 374761393 + y * 668265263 + salt * 1442695040888963407) & 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65536.0


def _smooth(x: float, y: float, freq: float, salt: int) -> float:
    """value noise with bilinear interpolation"""
    fx, fy = x * freq, y * freq
    x0, y0 = int(math.floor(fx)), int(math.floor(fy))
    tx, ty = fx - x0, fy - y0
    tx = tx * tx * (3 - 2 * tx)
    ty = ty * ty * (3 - 2 * ty)
    a = _v(x0, y0, salt);      b = _v(x0 + 1, y0, salt)
    c = _v(x0, y0 + 1, salt);  d = _v(x0 + 1, y0 + 1, salt)
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def _fbm(x: float, y: float, salt: int, octaves: int = 3) -> float:
    tot, amp, freq, norm = 0.0, 1.0, 0.035, 0.0
    for _ in range(octaves):
        tot += _smooth(x, y, freq, salt) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.1
    return tot / norm


# ── island field ───────────────────────────────────────────────────────────
CX, CY = W / 2, H / 2 + 6


def island_dist(x: float, y: float) -> float:
    """<0 inside island, >0 outside. Organic blob, wider than tall."""
    dx = (x - CX) / 196.0
    dy = (y - CY) / 124.0
    r = math.sqrt(dx * dx + dy * dy)
    warp = (_fbm(x, y, 11, 3) - 0.5) * 0.30
    lobe = 0.05 * math.sin(math.atan2(dy, dx) * 3.0 + 1.2)
    return r + warp + lobe - 1.0


def build_terrain() -> tuple[Image.Image, list[list[str]]]:
    img = Image.new("RGB", (W, H), P["void"])
    px = img.load()
    kind = [["deep"] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            d = island_dist(x, y)
            n = _fbm(x, y, 3, 2)
            if d < -0.20:
                k = "grass"
            elif d < -0.10:
                k = "grass_l" if n > 0.52 else "grass"
            elif d < -0.045:
                k = "sand"
            elif d < 0.0:
                k = "sand_d"
            elif d < 0.055:
                k = "shallow"
            elif d < 0.16:
                k = "water"
            else:
                k = "deep"
            kind[y][x] = k
            c = P[k]
            # subtle organic mottling so flat fills never look vector-clean
            if k in ("grass", "grass_l"):
                j = _fbm(x, y, 7, 3)
                if j > 0.62:
                    c = P["grass_l"]
                elif j < 0.38:
                    c = P["grass_d"]
            if k in ("water", "deep"):
                j = _fbm(x * 1.4, y * 2.2, 9, 2)
                if j > 0.66:
                    c = tuple(min(255, v + 16) for v in c)
            px[x, y] = c
    return img, kind


def draw_foam(img: Image.Image, kind):
    """1px dithered surf where shallow meets sand"""
    px = img.load()
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if kind[y][x] != "shallow":
                continue
            touch = any(kind[y + dy][x + dx].startswith("sand")
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            if touch and _v(x, y, 21) > 0.34:
                px[x, y] = P["foam"]


# ── vegetation ─────────────────────────────────────────────────────────────
def tree(d: ImageDraw.ImageDraw, x: int, y: int, size: int, kind_: str = "conifer",
         tint: tuple | None = None):
    """small pixel tree, anchored at base (x, y)"""
    body = tint or P["canopy"]
    dark = P["canopy_d"]
    light = P["canopy_l"] if tint is None else tuple(min(255, v + 28) for v in body)
    d.ellipse([x - 2, y - 2, x + 2, y], fill=P["shadow"])
    d.rectangle([x - 1, y - size // 3, x, y - 1], fill=P["trunk"])
    if kind_ == "conifer":
        w = size // 2 + 1
        step = max(2, size // 4)
        cy = y - size // 3
        while w > 0:
            d.polygon([(x - w, cy), (x + w, cy), (x, cy - step - 1)], fill=body)
            d.polygon([(x - w, cy), (x, cy), (x, cy - step - 1)], fill=dark)
            cy -= step
            w -= 2
        d.point((x, cy - 1), fill=light)
    else:  # round / broadleaf
        r = size // 2
        cy = y - size // 2 - 1
        d.ellipse([x - r, cy - r, x + r, cy + r], fill=body)
        d.ellipse([x - r, cy - r + 1, x + 1, cy + r], fill=dark)
        d.ellipse([x - r + 1, cy - r, x + r - 3, cy - 1], fill=light)


def scatter_forest(d: ImageDraw.ImageDraw, kind, keepout) -> None:
    """
    WILD FOREST — dense ring just inside the shore, thinning toward the
    settlement clearing. This is external information space.
    """
    pts = []
    for _ in range(2600):
        x = rng.randrange(6, W - 6)
        y = rng.randrange(6, H - 6)
        dd = island_dist(x, y)
        if dd > -0.055 or dd < -0.92:
            continue
        # density: thick at the rim, sparse toward the middle
        rim = min(1.0, max(0.0, (dd + 0.92) / 0.86))     # 0 inner .. 1 outer
        density = 0.10 + 0.86 * (rim ** 1.5)
        if _v(x, y, 31) > density:
            continue
        if keepout(x, y):
            continue
        if any((x - px_) ** 2 + (y - py_) ** 2 < 26 for px_, py_ in pts[-90:]):
            continue
        pts.append((x, y))
    pts.sort(key=lambda p: p[1])
    for (x, y) in pts:
        n = _v(x, y, 41)
        size = 7 + int(_v(x, y, 43) * 6)
        dd = island_dist(x, y)
        deep_wood = dd > -0.42
        if deep_wood:
            # WILD FOREST — colder, denser, less inviting than the settlement
            tint = (14, 52, 46) if n > 0.5 else (18, 62, 52)
            tree(d, x, y, size + 2, "conifer" if n > 0.35 else "round", tint)
        elif n > 0.80:
            tree(d, x, y, size, "round", P["canopy_l"])
        elif n > 0.62:
            tree(d, x, y, size, "round")
        else:
            tree(d, x, y, size, "conifer")


def scrub(d: ImageDraw.ImageDraw, keepout):
    """grass tufts + flowers inside the settlement so it never looks bald"""
    for _ in range(900):
        x = rng.randrange(8, W - 8)
        y = rng.randrange(8, H - 8)
        if island_dist(x, y) > -0.14 or keepout(x, y):
            continue
        n = _v(x, y, 53)
        if n > 0.70:
            d.point((x, y), fill=P["grass_d"])
            d.point((x + 1, y), fill=P["grass_d"])
        elif n > 0.66:
            d.point((x, y), fill=P["grass_l"])
        elif n > 0.645:
            col = [P["gold"], P["violet"], P["cyan"], P["mint"]][int(n * 1000) % 4]
            d.point((x, y - 1), fill=tuple(int(c * 0.85) for c in col))


# ── structures ─────────────────────────────────────────────────────────────
def shadow_blob(d, x, y, w, h):
    d.ellipse([x - w, y - h, x + w, y + h], fill=P["shadow"])


def heart_tree(d: ImageDraw.ImageDraw, x: int, y: int):
    """the organism core — a luminous tree on a stone plaza"""
    # plaza
    d.ellipse([x - 34, y - 17, x + 34, y + 15], fill=P["stone_d"])
    d.ellipse([x - 31, y - 15, x + 31, y + 13], fill=P["stone"])
    d.ellipse([x - 22, y - 11, x + 22, y + 9], fill=P["stone_l"])
    d.ellipse([x - 16, y - 8, x + 16, y + 7], fill=P["grass_d"])
    # runes on the plaza rim (geometry, not glyphs)
    for i in range(8):
        a = i * math.pi / 4 + 0.4
        rx = int(x + math.cos(a) * 26)
        ry = int(y + math.sin(a) * 12)
        d.point((rx, ry), fill=P["cyan"])
        d.point((rx, ry - 1), fill=P["cyan_d"])
    # roots + trunk
    d.polygon([(x - 9, y + 2), (x + 9, y + 2), (x + 5, y - 14), (x - 5, y - 14)],
              fill=P["heart_bark_d"])
    d.polygon([(x - 6, y + 1), (x + 3, y + 1), (x + 2, y - 14), (x - 4, y - 14)],
              fill=P["heart_bark"])
    # boughs
    for (bx, by, ex, ey) in ((-4, -13, -16, -24), (2, -13, 15, -25), (-1, -18, -3, -30)):
        d.line([x + bx, y + by, x + ex, y + ey], fill=P["heart_bark"], width=2)
    # canopy — one broad crown with lobes, not a cone
    for (ox, oy, rx, ry) in ((0, -34, 30, 19), (-19, -27, 15, 11), (19, -27, 15, 11),
                             (-9, -44, 15, 11), (10, -44, 14, 10)):
        cx2, cy2 = x + ox, y + oy
        d.ellipse([cx2 - rx, cy2 - ry, cx2 + rx, cy2 + ry], fill=P["heart_leaf_d"])
        d.ellipse([cx2 - rx + 3, cy2 - ry + 1, cx2 + rx - 6, cy2 + ry - 5],
                  fill=P["heart_leaf"])
    # crown highlight
    d.ellipse([x - 14, y - 50, x + 6, y - 40], fill=(178, 250, 208))
    # motes
    for i in range(26):
        a = _v(i, 7, 61) * math.tau
        rr = 16 + _v(i, 9, 63) * 30
        mx = int(x + math.cos(a) * rr)
        my = int(y - 30 + math.sin(a) * rr * 0.62)
        col = P["gold"] if i % 3 else P["cyan"]
        d.point((mx, my), fill=col)
        if i % 4 == 0:
            d.point((mx + 1, my), fill=tuple(int(c * 0.6) for c in col))


def house(d, x, y, w, h, roof, wall=None, roof_style="gable"):
    """generic small building anchored bottom-centre"""
    wall = wall or P["stone"]
    shadow_blob(d, x, y, w // 2 + 3, 4)
    d.rectangle([x - w // 2, y - h, x + w // 2, y], fill=wall)
    d.rectangle([x - w // 2, y - h, x - w // 2 + 2, y], fill=P["stone_d"])
    d.rectangle([x + w // 2 - 1, y - h, x + w // 2, y], fill=P["stone_d"])
    if roof_style == "gable":
        d.polygon([(x - w // 2 - 3, y - h), (x + w // 2 + 3, y - h), (x, y - h - h // 2 - 2)],
                  fill=roof)
        d.polygon([(x - w // 2 - 3, y - h), (x, y - h), (x, y - h - h // 2 - 2)],
                  fill=tuple(int(c * 0.74) for c in roof))
    else:  # flat / crenellated
        d.rectangle([x - w // 2 - 2, y - h - 3, x + w // 2 + 2, y - h], fill=roof)
        for i in range(x - w // 2 - 2, x + w // 2 + 2, 4):
            d.rectangle([i, y - h - 5, i + 1, y - h - 3], fill=roof)
    # door + window
    d.rectangle([x - 2, y - 5, x, y - 1], fill=P["wood_d"])
    d.rectangle([x + 3, y - h + 3, x + 5, y - h + 5], fill=P["cyan_d"])


def spring_observatory(d, x, y):
    """PRICE TRUTH — crystal spring pool + small observatory"""
    # pool
    d.ellipse([x - 24, y - 12, x + 12, y + 6], fill=P["cyan_d"])
    d.ellipse([x - 22, y - 10, x + 10, y + 4], fill=(46, 190, 226))
    d.ellipse([x - 17, y - 7, x + 3, y + 0], fill=(150, 238, 250))
    for i in range(9):
        a = _v(i, 3, 71) * math.tau
        d.point((int(x - 6 + math.cos(a) * 12), int(y - 3 + math.sin(a) * 6)), fill=P["foam"])
    # crystal shards rising from the pool
    for (ox, hh) in ((-14, 13), (-6, 20), (2, 11)):
        d.polygon([(x + ox - 2, y - 4), (x + ox + 2, y - 4), (x + ox, y - 4 - hh)],
                  fill=P["cyan"])
        d.polygon([(x + ox - 2, y - 4), (x + ox, y - 4), (x + ox, y - 4 - hh)],
                  fill=P["cyan_d"])
    # observatory tower
    tx, ty = x + 20, y + 4
    shadow_blob(d, tx, ty, 9, 4)
    d.rectangle([tx - 6, ty - 24, tx + 6, ty], fill=P["stone"])
    d.rectangle([tx - 6, ty - 24, tx - 4, ty], fill=P["stone_d"])
    d.rectangle([tx - 8, ty - 28, tx + 8, ty - 24], fill=P["roof_slate"])
    d.ellipse([tx - 7, ty - 36, tx + 7, ty - 24], fill=P["roof_slate"])
    d.ellipse([tx - 5, ty - 34, tx + 3, ty - 27], fill=(88, 96, 142))
    d.line([tx + 1, ty - 33, tx + 9, ty - 39], fill=P["stone_l"])   # the lens barrel
    d.point((tx + 9, ty - 39), fill=P["cyan"])
    d.rectangle([tx - 3, ty - 12, tx - 1, ty - 9], fill=P["cyan_d"])


def forge(d, x, y):
    """EXECUTION — forge / mechanical workshop"""
    house(d, x, y, 30, 17, P["roof_warm"], P["stone"])
    # chimney
    d.rectangle([x + 9, y - 34, x + 14, y - 24], fill=P["stone_d"])
    d.rectangle([x + 9, y - 35, x + 14, y - 33], fill=P["stone"])
    # forge mouth glow
    d.rectangle([x - 9, y - 9, x - 3, y - 1], fill=P["wood_d"])
    d.rectangle([x - 8, y - 7, x - 4, y - 1], fill=P["ember"])
    d.rectangle([x - 7, y - 5, x - 5, y - 1], fill=P["gold"])
    # anvil + rack outside
    d.rectangle([x - 20, y - 5, x - 14, y - 3], fill=P["stone_d"])
    d.rectangle([x - 18, y - 3, x - 16, y], fill=P["stone_d"])
    d.line([x + 16, y - 1, x + 16, y - 11], fill=P["wood"])
    d.line([x + 12, y - 11, x + 20, y - 11], fill=P["wood"])


def library(d, x, y):
    """INTELLIGENCE — library / shrine"""
    shadow_blob(d, x, y, 20, 5)
    # stepped base
    d.rectangle([x - 20, y - 4, x + 20, y], fill=P["stone_d"])
    d.rectangle([x - 18, y - 7, x + 18, y - 4], fill=P["stone"])
    # columns
    for ox in (-14, -7, 0, 7, 14):
        d.rectangle([x + ox - 1, y - 22, x + ox + 1, y - 7], fill=P["stone_l"])
        d.rectangle([x + ox + 1, y - 22, x + ox + 2, y - 7], fill=P["stone_d"])
    d.rectangle([x - 18, y - 26, x + 18, y - 22], fill=P["stone"])
    d.polygon([(x - 20, y - 26), (x + 20, y - 26), (x, y - 36)], fill=P["roof_slate"])
    d.polygon([(x - 20, y - 26), (x, y - 26), (x, y - 36)], fill=(44, 48, 78))
    # violet knowledge-light in the pediment
    d.ellipse([x - 3, y - 31, x + 3, y - 27], fill=P["violet"])
    d.point((x, y - 29), fill=(240, 220, 255))


def watchtower(d, x, y):
    """SMART MONEY — watchtower / scout lodge"""
    shadow_blob(d, x, y, 11, 4)
    d.polygon([(x - 10, y), (x + 10, y), (x + 7, y - 26), (x - 7, y - 26)], fill=P["stone"])
    d.polygon([(x - 10, y), (x - 2, y), (x - 2, y - 26), (x - 7, y - 26)], fill=P["stone_d"])
    d.rectangle([x - 11, y - 31, x + 11, y - 26], fill=P["wood"])
    for i in range(x - 11, x + 11, 5):
        d.rectangle([i, y - 34, i + 2, y - 31], fill=P["wood_d"])
    d.rectangle([x - 3, y - 20, x + 1, y - 15], fill=P["gold"])       # lit window
    d.rectangle([x - 2, y - 19, x, y - 16], fill=(255, 244, 210))
    # ladder
    for yy in range(y - 24, y, 4):
        d.line([x + 8, yy, x + 12, yy], fill=P["wood_d"])
    d.line([x + 8, y - 26, x + 8, y], fill=P["wood_d"])
    d.line([x + 12, y - 26, x + 12, y], fill=P["wood_d"])


def nursery(d, x, y):
    """SUBSTRATE — grove / nursery beds"""
    shadow_blob(d, x, y, 26, 6)
    for i, (ox, oy) in enumerate(((-22, 2), (-6, 5), (11, 2), (-14, -6), (2, -6), (18, -6))):
        bx, by = x + ox, y + oy
        d.rectangle([bx - 8, by - 5, bx + 8, by], fill=P["wood_d"])
        d.rectangle([bx - 7, by - 5, bx + 7, by - 1], fill=(74, 56, 42))
        for k in range(4):
            sx = bx - 5 + k * 3
            d.line([sx, by - 2, sx, by - 7], fill=P["mint"])
            d.point((sx, by - 8), fill=P["heart_leaf"])
            d.point((sx + 1, by - 6), fill=P["heart_leaf_d"])
    # sapling under a glass cloche — a capability being grown
    d.ellipse([x - 34, y - 20, x - 18, y - 2], fill=(56, 112, 122))
    d.ellipse([x - 33, y - 19, x - 21, y - 4], fill=(100, 184, 188))
    d.line([x - 26, y - 5, x - 26, y - 12], fill=P["trunk"])
    d.ellipse([x - 30, y - 17, x - 22, y - 10], fill=P["heart_leaf"])
    d.point((x - 26, y - 14), fill=(200, 255, 226))


def council_ring(d, x, y):
    """COUNCIL — open chamber: ring of standing stones + canopy"""
    d.ellipse([x - 30, y - 14, x + 30, y + 8], fill=P["stone_d"])
    d.ellipse([x - 27, y - 12, x + 27, y + 6], fill=P["stone"])
    d.ellipse([x - 20, y - 8, x + 20, y + 2], fill=P["stone_l"])
    # standing stones
    for i in range(7):
        a = math.pi * (0.10 + 0.80 * i / 6)
        sx = int(x - math.cos(a) * 28)
        sy = int(y - 4 + math.sin(a) * 12)
        h = 12 if i % 2 == 0 else 9
        d.rectangle([sx - 2, sy - h, sx + 2, sy], fill=P["stone"])
        d.rectangle([sx + 1, sy - h, sx + 2, sy], fill=P["stone_d"])
        d.rectangle([sx - 2, sy - h - 1, sx + 2, sy - h], fill=P["stone_l"])
        d.point((sx, sy - h + 3), fill=P["violet"])
    # central brazier
    d.rectangle([x - 3, y - 6, x + 3, y - 2], fill=P["stone_d"])
    d.ellipse([x - 3, y - 9, x + 3, y - 5], fill=P["ember"])
    d.ellipse([x - 2, y - 10, x + 2, y - 7], fill=P["gold"])


def inspection_circle(d, x, y):
    """where a returned discovery is set down for the Council to inspect"""
    d.ellipse([x - 24, y - 13, x + 24, y + 10], fill=P["grass_d"])
    d.ellipse([x - 22, y - 12, x + 22, y + 9], fill=(70, 104, 78))
    d.ellipse([x - 15, y - 8, x + 15, y + 6], fill=(88, 122, 90))
    for i in range(12):
        a = i * math.tau / 12
        sx = int(x + math.cos(a) * 21)
        sy = int(y - 2 + math.sin(a) * 10)
        h = 5 if i % 2 == 0 else 4
        d.rectangle([sx - 1, sy - h, sx + 1, sy], fill=P["stone"])
        d.point((sx, sy - h - 1), fill=P["stone_l"])
        d.point((sx + 1, sy - h + 1), fill=P["stone_d"])
    # empty pedestal — the relic itself is a live sprite, never baked in
    d.rectangle([x - 4, y - 4, x + 4, y - 1], fill=P["stone_d"])
    d.rectangle([x - 3, y - 6, x + 3, y - 4], fill=P["stone"])
    d.rectangle([x - 2, y - 7, x + 2, y - 6], fill=P["stone_l"])


def trailhead(d, x, y):
    """gate in the treeline — expeditions leave here for the wild forest"""
    """the gate in the treeline — expeditions leave the settlement here"""
    d.rectangle([x - 13, y - 22, x - 9, y], fill=P["wood_d"])
    d.rectangle([x + 9, y - 22, x + 13, y], fill=P["wood_d"])
    d.rectangle([x - 15, y - 26, x + 15, y - 22], fill=P["wood"])
    d.polygon([(x - 15, y - 26), (x + 15, y - 26), (x, y - 31)], fill=P["wood_d"])
    for ox in (-11, 11):
        d.point((x + ox, y - 16), fill=P["gold"])
    # lanterns
    d.ellipse([x - 16, y - 25, x - 12, y - 21], fill=P["gold"])
    d.ellipse([x + 12, y - 25, x + 16, y - 21], fill=P["gold"])


# ── paths ──────────────────────────────────────────────────────────────────
def path_between(img: Image.Image, a, b, width: int = 4):
    px = img.load()
    ax, ay = a; bx, by = b
    steps = int(math.hypot(bx - ax, by - ay) * 2)
    for i in range(steps + 1):
        t = i / max(1, steps)
        # gentle sag so paths curve instead of ruling straight lines
        cx = ax + (bx - ax) * t
        cy = ay + (by - ay) * t + math.sin(t * math.pi) * 7
        for oy in range(-width, width + 1):
            for ox in range(-width, width + 1):
                if ox * ox + oy * oy > width * width:
                    continue
                x, y = int(cx + ox), int(cy + oy)
                if not (0 <= x < W and 0 <= y < H):
                    continue
                if island_dist(x, y) > -0.05:
                    continue
                edge = (ox * ox + oy * oy) > (width - 1) ** 2
                n = _v(x, y, 83)
                if edge and n > 0.5:
                    continue
                px[x, y] = P["path_d"] if (edge or n > 0.72) else P["path"]


# ── layout ─────────────────────────────────────────────────────────────────
SITES = {
    "heart":        (240, 156),
    "council":      (240, 100),
    "price_truth":  (330,  96),
    "smart_money":  (388, 158),
    "substrate":    (300, 226),
    "execution":    (128, 218),
    "intelligence": (108, 140),
    "inspection":   (186, 214),
    "trailhead":    (168, 250),
}


def keepout(x: int, y: int) -> bool:
    """no trees inside the settlement clearing or on top of a structure"""
    for name, (sx, sy) in SITES.items():
        r = {"heart": 84, "council": 54, "inspection": 40, "trailhead": 20,
             "price_truth": 46, "execution": 46, "intelligence": 46}.get(name, 42)
        if (x - sx) ** 2 + ((y - sy) * 1.35) ** 2 < r * r:
            return True
    return False


def generate() -> Image.Image:
    img, kind = build_terrain()
    draw_foam(img, kind)
    d = ImageDraw.Draw(img)

    # paths first — structures and trees sit on top
    for site in ("council", "price_truth", "smart_money", "substrate",
                 "execution", "intelligence"):
        path_between(img, SITES["heart"], SITES[site], 4)
    path_between(img, SITES["heart"], SITES["inspection"], 3)
    path_between(img, SITES["inspection"], SITES["trailhead"], 3)
    # the track continues past the gate into the wild forest — where scouts go
    tx, ty = SITES["trailhead"]
    path_between(img, (tx, ty), (tx + 10, ty + 26), 2)

    d = ImageDraw.Draw(img)
    scatter_forest(d, kind, keepout)
    scrub(d, keepout)

    # structures, painted back-to-front
    council_ring(d, *SITES["council"])
    spring_observatory(d, *SITES["price_truth"])
    library(d, *SITES["intelligence"])
    watchtower(d, *SITES["smart_money"])
    heart_tree(d, *SITES["heart"])
    nursery(d, *SITES["substrate"])
    forge(d, *SITES["execution"])
    inspection_circle(d, *SITES["inspection"])
    tx, ty = SITES["trailhead"]
    tree(d, tx - 22, ty + 6, 16, "conifer", (14, 52, 46))
    tree(d, tx + 21, ty + 7, 17, "conifer", (14, 52, 46))
    trailhead(d, tx, ty)
    tree(d, tx - 30, ty + 16, 13, "conifer", (12, 46, 42))
    tree(d, tx + 30, ty + 15, 14, "conifer", (12, 46, 42))

    return img


def rim_mist(img: Image.Image) -> Image.Image:
    """cool haze over the outer treeline — the wild forest recedes into unknown"""
    mist = Image.new("RGB", (W, H), (22, 48, 70))
    mask = Image.new("L", (W, H), 0)
    mp = mask.load()
    for y in range(H):
        for x in range(W):
            d0 = island_dist(x, y)
            if -0.55 < d0 < 0.02:
                t = min(1.0, max(0.0, (d0 + 0.55) / 0.55))
                mp[x, y] = int(96 * (t ** 2))
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    return Image.composite(mist, img, mask)


def vignette(img: Image.Image) -> Image.Image:
    """slight edge darkening so the island reads as the focal subject"""
    ov = Image.new("L", (W, H), 0)
    dv = ImageDraw.Draw(ov)
    dv.ellipse([-70, -50, W + 70, H + 50], fill=110)
    ov = ov.filter(ImageFilter.GaussianBlur(46))
    dark = Image.new("RGB", (W, H), (4, 6, 20))
    return Image.composite(img, Image.blend(img, dark, 0.45), ov.point(lambda v: 255 if v > 96 else v * 2))
