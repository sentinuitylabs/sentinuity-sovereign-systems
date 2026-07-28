# SENTINUITY_GLASS_CADENCE_SIGNOFF_20260712
# Cluster Cadence — realized PnL chart. Restores the proven 10/11_07 behaviour
# and hardens it per the 12_07 sign-off directive (§10–§12):
#
#   * Deterministic source chain, MERGED with dedup (never double-counts a
#     close that exists in both canonical history and a cache):
#       1. canonical realized closed-position history  (hot db: paper_positions)
#       2. historical_trade_pnl_cache                  (root sentinuity_intelligence.db)
#       3. ui_recent_trade_feed_cache                  (root sentinuity_intelligence.db)
#       4. legacy: paper_positions inside the intelligence db (if present)
#       5. honest-empty ONLY when every source is genuinely empty
#   * DB path resolution anchored to the repository root — never creates or
#     reads an accidental empty db inside ui/ or services/ (Test E).
#   * Survives Streamlit reruns and service restarts: every render re-reads
#     durable sources; nothing depends on session-created rows (Tests C/D).
#   * Preserves older buckets when a new close arrives (Test B).
#   * Visible diagnostic line: SOURCE · ROWS LOADED · ROWS DEDUPED · LATEST
#     CLOSE · WINDOW · NET PNL · LAST REFRESH · DB PATH.
#   * Mobile: reduced chart height + readable labels via CSS clamp (Test G).
#
# READ-ONLY. Opens every database with mode=ro. Writes nothing, ever.
#
# Public API (backward compatible):
#   render_glass_cadence(db_path, table="paper_positions", key_prefix="sol",
#                        st=None, empty_label=...)
#   fetch_cadence_buckets(db_path, table="paper_positions",
#                         bucket_min=10, window_h=4, now=None)
#   invalidate_cadence_cache()   # call after a position close persists
#   _cadence_svg(...)            # pure renderer, testable

import html
import sqlite3
import time
from pathlib import Path

_C_GREEN_TOP = "#5DFFC4"; _C_GREEN_MID = "#14F195"; _C_GREEN_LOW = "#0B7A55"
_C_RED_TOP = "#FF4D6D";   _C_RED_MID = "#FF073A";   _C_RED_LOW = "#7A1B6B"
_C_CYAN = "#8EF9FF"; _C_VOID = "#050210"; _C_GRID = "#1a2430"; _C_AXIS = "#3a4754"

# window presets: label -> (bucket_minutes, window_hours). window_hours == 0
# is the sentinel for the ALL-TIME view (span derived from earliest close;
# bucket size auto-scaled to keep the bar count sane).
CADENCE_PRESETS = {
    "1m · 1h":   (1, 1),
    "3m · 1h":   (3, 1),
    "5m · 4h":   (5, 4),      # sign-off 4h filter, fine buckets
    "10m · 4h":  (10, 4),     # sign-off 4h filter (proven default)
    "15m · 12h": (15, 12),
    "30m · 24h": (30, 24),
    "1h · 24h":  (60, 24),
    "2h · 48h":  (120, 48),
    "3h · 72h":  (180, 72),
    "6h · 7d":   (360, 168),
    "ALL · all-time": (0, 0),  # full 5,001-trade history, auto-bucketed
}
DEFAULT_PRESET = "10m · 4h"    # sign-off proven default: 4h live window
ALLTIME_MAX_BARS = 132         # cap on bars in the all-time view

# tiny memo so a burst of reruns doesn't hammer sqlite; invalidated on close
_MEMO = {"key": None, "at": 0.0, "val": None}
_MEMO_TTL = 4.0


def invalidate_cadence_cache():
    """Call after a position close is committed so the next render reloads."""
    _MEMO["key"] = None
    _MEMO["val"] = None


def _ro(db):
    p = Path(db).resolve()
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _f(v, d=0.0):
    try:
        if v is None or str(v).strip() == "":
            return d
        x = float(v)
        return x if x == x else d
    except Exception:
        return d


def _table_exists(conn, table):
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    except Exception:
        return False


def _repo_root(db_path) -> Path:
    """Resolve the repository root from the hot-db path.

    Test E guard: the canonical hot db lives at <root>/sentinuity_matrix.db and
    the intelligence cache at <root>/sentinuity_intelligence.db. If a caller
    hands us the intelligence db (legacy hub behaviour) we still resolve the
    same root. We never *create* a db here — mode=ro only.
    """
    p = Path(str(db_path)).resolve()
    root = p.parent
    # If someone launched from ui/ or services/ and passed a relative path
    # that resolved inside those dirs, hop up when the real root db is above.
    for cand in (root, root.parent):
        if (cand / "sentinuity_matrix.db").exists():
            return cand
    return root


def _rows_from(db_file: Path, table: str, since: float, label: str):
    """Schema-tolerant closed-trade reader → list of dicts with a stable key."""
    out = []
    if not db_file or not Path(db_file).exists():
        return out
    try:
        c = _ro(db_file)
    except Exception:
        return out
    try:
        if not _table_exists(c, table):
            return out
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if "closed_at" not in cols:
            return out
        pnl = ("realized_pnl_usd" if "realized_pnl_usd" in cols
               else ("realized_pnl" if "realized_pnl" in cols
                     else ("pnl" if "pnl" in cols else None)))
        idc = ("position_id" if "position_id" in cols
               else ("id" if "id" in cols else None))
        mint = "mint_address" if "mint_address" in cols else None
        status_sql = ""
        if "status" in cols:
            status_sql = "UPPER(COALESCE(status,'CLOSED'))='CLOSED' AND "
        sel = ["CAST(closed_at AS REAL) AS t",
               (f"COALESCE({pnl},0) AS p" if pnl else "0 AS p"),
               (f"{idc} AS pid" if idc else "NULL AS pid"),
               (f"COALESCE({mint},'') AS mint" if mint else "'' AS mint")]
        rows = c.execute(
            f"SELECT {', '.join(sel)} FROM {table} "
            f"WHERE {status_sql}COALESCE(CAST(closed_at AS REAL),0) >= ?",
            (since,),
        ).fetchall()
        for r in rows:
            t = _f(r["t"])
            if t <= 0:
                continue
            pid = r["pid"]
            # stable close identifier for dedup across sources
            if pid is not None and str(pid) != "":
                key = f"id:{pid}"
            elif r["mint"]:
                key = f"mx:{r['mint']}:{round(t, 1)}"
            else:
                key = f"tp:{round(t, 3)}:{round(_f(r['p']), 4)}"
            out.append({"t": t, "p": _f(r["p"]), "key": key, "src": label})
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass
    return out


def _load_closed(db_path, table, since):
    """CACHE+HOT merge (sign-off 12_07_26). Returns (rows, meta).

    Order of truth, per the sign-off directive:
      1. Load the full deep history from the intelligence cache
         (historical_trade_pnl_cache, then ui_recent_trade_feed_cache as a
         secondary cache, then a legacy intel.paper_positions table).
      2. Compute cache_max_ts = newest closed_at present in the cache.
      3. Read newly CLOSED rows from the hot db (paper_positions) — the LIVE
         source that accumulates between shutdown-time cache syncs. These are
         the closes the cache has not seen yet.
      4. Merge + dedup in memory (stable close key). The hot db wins ties.
      5. Label the result CACHE+HOT_MERGE when both contribute, so the live
         chart is never blocked on the shutdown-time synchroniser.

    Nothing here writes to either database (mode=ro everywhere).
    """
    root = _repo_root(db_path)
    hot_db = root / "sentinuity_matrix.db"
    if not hot_db.exists():
        hot_db = Path(str(db_path)).resolve()
    intel_db = root / "sentinuity_intelligence.db"

    seen = set()
    merged = []
    loaded = 0
    cache_rows = 0
    hot_rows = 0

    # ── 1) deep history from the cache tables ────────────────────────────────
    cache_chain = [
        (intel_db, "historical_trade_pnl_cache"),
        (intel_db, "ui_recent_trade_feed_cache"),
        (intel_db, "paper_positions"),   # legacy location, tolerated
    ]
    for dbf, tbl in cache_chain:
        rows = _rows_from(dbf, tbl, since, "CACHE")
        loaded += len(rows)
        for r in rows:
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            merged.append(r)
            cache_rows += 1

    # ── 2) newest timestamp the cache already knows about ───────────────────
    cache_max_ts = max((r["t"] for r in merged), default=0.0)

    # ── 3) live hot closes (everything, then dedup — robust to overlap) ─────
    hot = _rows_from(hot_db, table, since, "HOT")
    loaded += len(hot)
    hot_after_cache = 0
    for r in hot:
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        merged.append(r)
        hot_rows += 1
        if r["t"] > cache_max_ts:
            hot_after_cache += 1

    # ── 4/5) label ──────────────────────────────────────────────────────────
    if cache_rows and hot_rows:
        source = "CACHE+HOT_MERGE"
    elif cache_rows:
        source = "CACHE"
    elif hot_rows:
        source = "HOT"
    else:
        source = "EMPTY"

    meta = {
        "rows_loaded": loaded,
        "rows_deduped": loaded - len(merged),
        "cache_rows": cache_rows,
        "hot_rows": hot_rows,
        "hot_after_cache": hot_after_cache,
        "cache_max_ts": cache_max_ts,
        "source": source,
        "primary_source": source,
        "sources": [source],
        "db_root": str(root),
        "hot_db": str(hot_db),
        "intel_db": str(intel_db),
        "latest_close": max((r["t"] for r in merged), default=0.0),
    }
    return merged, meta


def _alltime_span(rows, now):
    """Derive span + bucket size for the all-time view from real data."""
    earliest = min((r["t"] for r in rows), default=now - 3600.0)
    span = max(3600.0, now - earliest)
    # choose bucket so bar count <= ALLTIME_MAX_BARS, snapped to a clean minute
    raw_min = (span / ALLTIME_MAX_BARS) / 60.0
    for step in (1, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440):
        if step >= raw_min:
            bmin = step
            break
    else:
        bmin = 1440
    return span, bmin, earliest


def fetch_cadence_buckets(db_path, table="paper_positions",
                          bucket_min=10, window_h=4, now=None):
    """Bucket realized PnL over the window from the merged deduped history.

    window_h == 0 selects the ALL-TIME view: the full cached history plus live
    hot closes, span and bucket size auto-scaled from the earliest close.
    """
    now = now or time.time()
    alltime = (float(window_h) == 0.0)

    memo_key = (str(db_path), table, bucket_min, window_h, int(now // 2))
    if _MEMO["key"] == memo_key and (time.time() - _MEMO["at"]) < _MEMO_TTL:
        return _MEMO["val"]

    if alltime:
        rows, src_meta = _load_closed(db_path, table, 0.0)
        span, bucket_min, earliest = _alltime_span(rows, now)
        since = now - span
    else:
        span = float(window_h) * 3600.0
        since = now - span
        rows, src_meta = _load_closed(db_path, table, since)

    bms = float(bucket_min) * 60.0
    n = max(1, int(round(span / bms)))
    buckets = [0.0] * n
    meta = {"trades": 0, "net": 0.0, "table": table,
            "window_h": (round(span / 3600.0, 1) if alltime else window_h),
            "bucket_min": bucket_min, "alltime": alltime,
            "refreshed_at": now}
    meta.update(src_meta)
    for r in rows:
        idx = int((r["t"] - since) / bms)
        if 0 <= idx < n:
            buckets[idx] += r["p"]
            meta["trades"] += 1
            meta["net"] += r["p"]
    result = (buckets, _labels(now, span, bms, n, alltime), meta)
    _MEMO.update({"key": memo_key, "at": time.time(), "val": result})
    return result


def _labels(now, span, bms, n, alltime=False):
    fmt = "%d/%m" if (alltime or span > 3 * 86400) else ("%H:%M" if span <= 86400 else "%d %Hh")
    out = []
    for i in range(n):
        lt = time.localtime(now - span + i * bms)
        out.append(time.strftime(fmt, lt))
    return out


def _cadence_svg(buckets, labels, bucket_min, window_h, meta=None):
    """Pure SVG renderer — green glass profit / red+violet glass loss."""
    meta = meta or {}
    W = 680; H = 300; pad_l = 44; pad_r = 20; top = 70; floor = 250
    plot_w = W - pad_l - pad_r
    n = len(buckets) or 1
    bw = plot_w / n
    barw = max(3.0, bw * 0.66)
    mx = max([abs(v) for v in buckets] + [1.0])
    pos_h = floor - top
    zero_y = top + pos_h * 0.62
    up_scale = (zero_y - top) / mx
    dn_scale = (floor - zero_y) / mx

    bars = []
    for i, v in enumerate(buckets):
        cxx = pad_l + i * bw + (bw - barw) / 2
        if v >= 0:
            h = v * up_scale; y = zero_y - h
            fill = "url(#cadGreen)"; sheen = "url(#cadGreenSheen)"
        else:
            h = abs(v) * dn_scale; y = zero_y
            fill = "url(#cadRed)"; sheen = "url(#cadRedSheen)"
        h = max(h, 0.0)
        if abs(v) < 1e-9:
            continue
        bars.append(
            f"<g><rect x='{cxx:.1f}' y='{y:.1f}' width='{barw:.1f}' height='{h:.1f}' rx='3' fill='{fill}'/>"
            f"<rect x='{cxx:.1f}' y='{y:.1f}' width='{max(2.5, barw*0.32):.1f}' height='{h:.1f}' rx='3' fill='{sheen}'/></g>")

    step = max(1, n // 12)
    xlabels = []
    for i in range(0, n, step):
        cxx = pad_l + i * bw + bw / 2
        xlabels.append(f"<text x='{cxx:.0f}' y='{floor+15:.0f}' fill='{_C_AXIS}' "
                       f"font-family='monospace' font-size='8' text-anchor='middle'>{labels[i]}</text>")

    net = meta.get("net", 0.0); trades = meta.get("trades", 0)
    source = meta.get("primary_source", meta.get("source", "NONE"))
    net_txt = f"{'+' if net >= 0 else '-'}${abs(net):.0f}"
    net_col = _C_GREEN_MID if net >= 0 else _C_RED_MID

    return f"""<svg width="100%" viewBox="0 0 {W} {H+44}" xmlns="http://www.w3.org/2000/svg" role="img" preserveAspectRatio="xMidYMid meet">
<title>Cluster cadence chart</title>
<defs>
<linearGradient id="cadGreen" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="{_C_GREEN_TOP}" stop-opacity="0.95"/>
<stop offset="45%" stop-color="{_C_GREEN_MID}" stop-opacity="0.85"/>
<stop offset="100%" stop-color="{_C_GREEN_LOW}" stop-opacity="0.62"/></linearGradient>
<linearGradient id="cadGreenSheen" x1="0" y1="0" x2="1" y2="0">
<stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.45"/>
<stop offset="60%" stop-color="#FFFFFF" stop-opacity="0.04"/>
<stop offset="100%" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>
<linearGradient id="cadRed" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="{_C_RED_TOP}" stop-opacity="0.92"/>
<stop offset="50%" stop-color="{_C_RED_MID}" stop-opacity="0.82"/>
<stop offset="100%" stop-color="{_C_RED_LOW}" stop-opacity="0.62"/></linearGradient>
<linearGradient id="cadRedSheen" x1="0" y1="0" x2="1" y2="0">
<stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.4"/>
<stop offset="60%" stop-color="#FFFFFF" stop-opacity="0.03"/>
<stop offset="100%" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>
</defs>
<rect x="0" y="0" width="{W}" height="{H+44}" rx="12" fill="{_C_VOID}"/>
<text x="{pad_l}" y="32" fill="{_C_CYAN}" font-family="'Share Tech Mono',monospace" font-size="12" letter-spacing="2">CLUSTER CADENCE — REALIZED PnL</text>
<text x="{W-pad_r}" y="32" fill="{_C_AXIS}" font-family="monospace" font-size="10" text-anchor="end">{bucket_min}m · {window_h}h</text>
<text x="{pad_l}" y="48" fill="{net_col}" font-family="monospace" font-size="11">net {net_txt} · {trades} closed · {html.escape(str(source))}</text>
<line x1="{pad_l}" y1="{zero_y:.0f}" x2="{W-pad_r}" y2="{zero_y:.0f}" stroke="{_C_GRID}" stroke-width="0.5" stroke-dasharray="3 4"/>
<text x="{pad_l-6}" y="{zero_y+3:.0f}" fill="{_C_AXIS}" font-family="monospace" font-size="8" text-anchor="end">$0</text>
{''.join(bars)}
{''.join(xlabels)}
<rect x="{pad_l}" y="{H+18}" width="13" height="13" rx="3" fill="url(#cadGreen)"/>
<text x="{pad_l+20}" y="{H+28}" fill="#9DB5A8" font-family="'Share Tech Mono',monospace" font-size="9">PROFIT</text>
<rect x="{pad_l+90}" y="{H+18}" width="13" height="13" rx="3" fill="url(#cadRed)"/>
<text x="{pad_l+110}" y="{H+28}" fill="#C99" font-family="'Share Tech Mono',monospace" font-size="9">LOSS — red w/ violet depth</text>
</svg>"""


def _diagnostics_html(meta):
    """Subtle but always-present diagnostic strip (directive §10)."""
    lc = meta.get("latest_close", 0.0)
    lc_txt = time.strftime("%d %b %H:%M:%S", time.localtime(lc)) if lc else "—"
    rf = meta.get("refreshed_at", time.time())
    rf_txt = time.strftime("%H:%M:%S", time.localtime(rf))
    net = meta.get("net", 0.0)
    cmt = meta.get("cache_max_ts", 0.0)
    cmt_txt = time.strftime("%d %b %H:%M:%S", time.localtime(cmt)) if cmt else "—"
    parts = [
        f"SOURCE {html.escape(meta.get('source', 'NONE'))}",
        f"CACHE {meta.get('cache_rows', 0)} · HOT {meta.get('hot_rows', 0)}"
        f" (+{meta.get('hot_after_cache', 0)} newer)",
        f"ROWS LOADED {meta.get('rows_loaded', 0)}",
        f"ROWS DEDUPED {meta.get('rows_deduped', 0)}",
        f"CACHE MAX {cmt_txt}",
        f"LATEST CLOSE {lc_txt}",
        f"WINDOW {meta.get('window_h', '?')}h/{meta.get('bucket_min', '?')}m",
        f"NET PNL {'+' if net >= 0 else '-'}${abs(net):.2f}",
        f"LAST REFRESH {rf_txt}",
        f"DB {html.escape(meta.get('db_root', '?'))}",
    ]
    return ("<div style='font-family:monospace;font-size:0.55rem;color:#3a4754;"
            "letter-spacing:0.5px;padding:2px 4px 8px;word-break:break-all;'>"
            + " · ".join(parts) + "</div>")


def render_glass_cadence(db_path, table="paper_positions", key_prefix="sol",
                         st=None, empty_label="No closed trades in window yet"):
    """Streamlit entry: window radio + glass SVG + diagnostics. Mobile-aware."""
    if st is None:
        import streamlit as st
    sel = st.radio("cadence window", list(CADENCE_PRESETS.keys()),
                   index=list(CADENCE_PRESETS.keys()).index(DEFAULT_PRESET),
                   horizontal=True, key=f"{key_prefix}_cadence_window",
                   label_visibility="collapsed")
    bmin, wh = CADENCE_PRESETS[sel]
    buckets, labels, meta = fetch_cadence_buckets(db_path, table, bmin, wh)
    # mobile height clamp (Test G): full chart on desktop, compact on phones
    st.markdown(
        "<style>.sw-cadence-wrap svg{max-height:360px}"
        "@media (max-width:760px){.sw-cadence-wrap svg{max-height:210px}}</style>",
        unsafe_allow_html=True)
    if meta["trades"] == 0 and meta.get("rows_loaded", 0) == 0:
        st.markdown(
            f"<div class='sw-cadence-wrap' style='background:{_C_VOID};border:0.5px solid rgba(142,249,255,0.2);"
            f"border-radius:12px;padding:24px;text-align:center;color:{_C_AXIS};"
            f"font-family:monospace;font-size:11px;'>{html.escape(empty_label)} "
            f"<span style='color:{_C_CYAN};'>· {bmin}m / {wh}h</span></div>"
            + _diagnostics_html(meta),
            unsafe_allow_html=True)
        return
    st.markdown("<div class='sw-cadence-wrap'>" + _cadence_svg(buckets, labels, bmin, wh, meta)
                + "</div>" + _diagnostics_html(meta), unsafe_allow_html=True)


if __name__ == "__main__":
    # ── acceptance-style self-tests (miniature A/B/C/F + dedup) ──────────────
    import math, os, tempfile

    # renderer sanity
    bk = [math.sin(i / 3) * 20 + (15 if i % 7 == 0 else -3) for i in range(24)]
    lbls = [f"{(14 + i // 6) % 24:02d}:{(i * 10) % 60:02d}" for i in range(24)]
    svg = _cadence_svg(bk, lbls, 10, 4, {"net": sum(bk), "trades": len(bk)})
    assert svg.startswith("<svg") and "cadGreen" in svg and "cadRed" in svg
    assert "violet depth" in svg

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        now = time.time()
        hot = root / "sentinuity_matrix.db"
        intel = root / "sentinuity_intelligence.db"
        hc = sqlite3.connect(hot)
        hc.execute("CREATE TABLE paper_positions(id INTEGER PRIMARY KEY, mint_address TEXT,"
                   "token_name TEXT, status TEXT, closed_at REAL, realized_pnl_usd REAL)")
        hc.execute("INSERT INTO paper_positions VALUES (1,'m1','TOK1','CLOSED',?,10.0)", (now - 600,))
        hc.execute("INSERT INTO paper_positions VALUES (2,'m2','TOK2','CLOSED',?,-4.0)", (now - 1200,))
        hc.commit(); hc.close()
        ic = sqlite3.connect(intel)
        ic.execute("CREATE TABLE historical_trade_pnl_cache(position_id INTEGER, mint_address TEXT,"
                   "closed_at REAL, realized_pnl_usd REAL)")
        # duplicate of close #1 (must dedup) + one older unique close
        ic.execute("INSERT INTO historical_trade_pnl_cache VALUES (1,'m1',?,10.0)", (now - 600,))
        ic.execute("INSERT INTO historical_trade_pnl_cache VALUES (3,'m3',?,7.5)", (now - 3600,))
        ic.execute("CREATE TABLE ui_recent_trade_feed_cache(position_id INTEGER, mint_address TEXT,"
                   "closed_at REAL, realized_pnl_usd REAL)")
        ic.execute("INSERT INTO ui_recent_trade_feed_cache VALUES (3,'m3',?,7.5)", (now - 3600,))
        ic.execute("INSERT INTO ui_recent_trade_feed_cache VALUES (4,'m4',?,2.0)", (now - 7200,))
        ic.commit(); ic.close()

        # Test A: initial load merges cache history + hot, dedups shared closes
        invalidate_cadence_cache()
        b, l, m = fetch_cadence_buckets(hot, bucket_min=30, window_h=24)
        assert m["trades"] == 4, m           # 1,2,3,4 — no double count
        assert abs(m["net"] - 15.5) < 1e-6, m
        assert m["rows_deduped"] == 2, m     # id1 dup + id3 dup dropped
        assert m["source"] == "CACHE+HOT_MERGE", m
        assert m["cache_rows"] >= 2 and m["hot_rows"] >= 1, m

        # Test B (LIVE REGRESSION): a brand-new hot close that the cache has NOT
        # seen yet must appear immediately, without running the synchroniser.
        hc = sqlite3.connect(hot)
        hc.execute("INSERT INTO paper_positions VALUES (5,'m5','TOK5','CLOSED',?,3.0)", (now - 60,))
        hc.commit(); hc.close()
        invalidate_cadence_cache()
        b2, l2, m2 = fetch_cadence_buckets(hot, bucket_min=30, window_h=24)
        assert m2["trades"] == 5 and abs(m2["net"] - 18.5) < 1e-6, m2
        assert m2["hot_after_cache"] >= 1, m2   # the new close is newer than cache_max_ts
        assert m2["source"] == "CACHE+HOT_MERGE", m2

        # Test C: rerun (same data) — no duplicates
        invalidate_cadence_cache()
        b3, l3, m3 = fetch_cadence_buckets(hot, bucket_min=30, window_h=24)
        assert m3["trades"] == 5, m3

        # Test D (ALL-TIME): full history view auto-scales and includes every close
        invalidate_cadence_cache()
        b6, l6, m6 = fetch_cadence_buckets(hot, bucket_min=0, window_h=0)
        assert m6["alltime"] is True and m6["trades"] == 5, m6
        assert len(b6) <= ALLTIME_MAX_BARS and sum(1 for x in b6 if abs(x) > 1e-9) >= 1, len(b6)

        # Test F: cache unavailable — falls back to canonical hot history
        os.remove(intel)
        invalidate_cadence_cache()
        b4, l4, m4 = fetch_cadence_buckets(hot, bucket_min=30, window_h=24)
        assert m4["trades"] == 3, m4          # closes 1,2,5 from hot only
        assert m4["source"] == "HOT", m4

        # Test E: passing the intel path still resolves repo-root hot db
        ic = sqlite3.connect(intel); ic.execute("CREATE TABLE x(y)"); ic.commit(); ic.close()
        invalidate_cadence_cache()
        b5, l5, m5 = fetch_cadence_buckets(intel, bucket_min=30, window_h=24)
        assert m5["trades"] == 3 and Path(m5["hot_db"]).name == "sentinuity_matrix.db", m5

        # diagnostics strip exists and carries all required fields
        d = _diagnostics_html(m5)
        for tok in ("SOURCE", "CACHE", "HOT", "ROWS LOADED", "ROWS DEDUPED",
                    "CACHE MAX", "LATEST CLOSE", "WINDOW", "NET PNL",
                    "LAST REFRESH", "DB"):
            assert tok in d, tok

    print("glass_cadence_chart sign-off self-tests PASS")
