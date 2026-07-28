#!/usr/bin/env python3
"""
MAGIC TRAJECTORY / SENTINUITY EVOLUTION - secondary intelligence chart.

Rules (per directive addendum):
- The straight Cluster Cadence / PnL chart remains the primary truth chart.
- This chart is OPERATIONAL, not decorative. No projected PnL. No invented eras.
- Eras with no cached data render as MISSING (dark violet, dashed) - never faked.
- GOLD only appears when metrics earn it:
    net positive AND profit factor >= 1.5 AND rug share <= 5%
    AND at least one runner/monster captured
    AND (if capital_shadow_outcomes has finalized rows) shadow-PASS net positive.

Data source: sentinuity_intelligence.db
  historical_trade_pnl_cache   (per-trade normalized history)
  historical_era_summary       (per-era rollup, optional - recomputed if absent)
  historical_hourly_edge       (hour-of-day rollup, optional)
  capital_shadow_outcomes      (optional gold gate)

All SQL is defensive: columns are introspected, missing columns tolerated.
Pure renderers (_era_state, _trajectory_svg, _hours_svg) are import-safe and
testable without streamlit.
"""

import os
import html
import sqlite3

# ---------------------------------------------------------------- constants

ERA_ORDER = [
    ("MAY_HIGH_WINRATE",      "May high-winrate"),
    ("EARLY_JUNE_RESTORE",    "Early-June restore"),
    ("JUNE_18_25_MONSTER_ERA","June 18-25 monster"),
    ("ARCHIVE_UNKNOWN",       "Archive (unlabelled)"),
    ("CURRENT_72H",           "Current 72h"),
    ("CURRENT_48H",           "Current 48h"),
    ("HOT_DB_CURRENT",        "Hot DB (now)"),
]

COL = {
    "MISSING":  ("#17102b", "#3b2d5e"),   # void violet / dashed border
    "VOID":     ("#1b1233", "#4a3a72"),
    "DISCOVERY":("#0e2f3f", "#38c7e8"),   # blue/cyan
    "MODEST":   ("#0e3529", "#2fd6a1"),   # teal/green
    "RUNNER":   ("#3a2a08", "#f0b429"),   # amber
    "GOLD":     ("#3d3103", "#ffd54a"),   # gold - earned only
    "FRACTURE": ("#3a0f1e", "#e8386b"),   # rug/drawdown red-violet
}

# ---------------------------------------------------------------- db helpers

def _connect(path):
    if not path or not os.path.exists(path):
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except Exception:
        try:
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            return con
        except Exception:
            return None


def _tables(con):
    try:
        return {r["name"] for r in con.execute(
            "select name from sqlite_master where type in ('table','view')")}
    except Exception:
        return set()


def _cols(con, table):
    try:
        return [r["name"] for r in con.execute(f"pragma table_info({table})")]
    except Exception:
        return []


def _pick(cands, cols):
    for c in cands:
        if c in cols:
            return c
    return None


def _f(v, d=None):
    try:
        if v is None:
            return d
        return float(v)
    except Exception:
        return d

# ---------------------------------------------------------------- load eras

def load_era_rollups(intel_db):
    """Return (rollups_by_era, meta). Never invents data; empty dict on failure."""
    meta = {"cache_rows": 0, "source_dbs": [], "shadow": None, "errors": []}
    con = _connect(intel_db)
    if not con:
        meta["errors"].append("intelligence DB not found")
        return {}, meta

    tabs = _tables(con)
    rollups = {}

    # per-trade cache is ground truth; summary table used only as fallback
    if "historical_trade_pnl_cache" in tabs:
        c = _cols(con, "historical_trade_pnl_cache")
        era_c   = _pick(["era_label", "source_label", "source_window"], c)
        pnl_c   = _pick(["realized_pnl_usd", "pnl_usd"], c)
        pct_c   = _pick(["realized_pnl_pct", "pnl_pct"], c)
        cls_c   = _pick(["classification", "class"], c)
        src_c   = _pick(["source_db"], c)
        closed_c= _pick(["closed_at"], c)
        try:
            rows = con.execute("select * from historical_trade_pnl_cache").fetchall()
        except Exception as e:
            rows = []
            meta["errors"].append(str(e))
        meta["cache_rows"] = len(rows)
        srcs = set()
        for r in rows:
            era = (r[era_c] if era_c else None) or "ARCHIVE_UNKNOWN"
            g = rollups.setdefault(era, {
                "n": 0, "net": 0.0, "wins": 0, "losses": 0,
                "gross_win": 0.0, "gross_loss": 0.0,
                "runners": 0, "monsters": 0, "rugs": 0, "modest": 0,
                "first": None, "last": None,
            })
            g["n"] += 1
            pnl = _f(r[pnl_c]) if pnl_c else None
            pct = _f(r[pct_c]) if pct_c else None
            if pnl is not None:
                g["net"] += pnl
                if pnl > 0:
                    g["wins"] += 1; g["gross_win"] += pnl
                elif pnl < 0:
                    g["losses"] += 1; g["gross_loss"] += abs(pnl)
            elif pct is not None:
                if pct > 0: g["wins"] += 1
                elif pct < 0: g["losses"] += 1
            cl = str(r[cls_c]).upper() if cls_c and r[cls_c] else ""
            if "MONSTER" in cl: g["monsters"] += 1
            elif "RUNNER" in cl: g["runners"] += 1
            elif "MODEST" in cl: g["modest"] += 1
            if "RUG" in cl: g["rugs"] += 1
            if closed_c and r[closed_c] is not None:
                t = _f(r[closed_c])
                if t:
                    g["first"] = t if g["first"] is None else min(g["first"], t)
                    g["last"]  = t if g["last"]  is None else max(g["last"],  t)
            if src_c and r[src_c]:
                srcs.add(str(r[src_c]))
        meta["source_dbs"] = sorted(srcs)

    elif "historical_era_summary" in tabs:
        c = _cols(con, "historical_era_summary")
        era_c = _pick(["era_label", "era", "label"], c)
        try:
            for r in con.execute("select * from historical_era_summary"):
                era = r[era_c] if era_c else "ARCHIVE_UNKNOWN"
                rollups[era] = {
                    "n": int(_f(r["trade_count"], 0) or 0) if "trade_count" in c else 0,
                    "net": _f(r["net_pnl_usd"], 0.0) if "net_pnl_usd" in c else 0.0,
                    "wins": int(_f(r["wins"], 0) or 0) if "wins" in c else 0,
                    "losses": int(_f(r["losses"], 0) or 0) if "losses" in c else 0,
                    "gross_win": _f(r["gross_win"], 0.0) if "gross_win" in c else 0.0,
                    "gross_loss": _f(r["gross_loss"], 0.0) if "gross_loss" in c else 0.0,
                    "runners": int(_f(r["runner_count"], 0) or 0) if "runner_count" in c else 0,
                    "monsters": int(_f(r["monster_count"], 0) or 0) if "monster_count" in c else 0,
                    "rugs": int(_f(r["rug_count"], 0) or 0) if "rug_count" in c else 0,
                    "modest": int(_f(r["modest_winner_count"], 0) or 0) if "modest_winner_count" in c else 0,
                    "first": None, "last": None,
                }
        except Exception as e:
            meta["errors"].append(str(e))
    else:
        meta["errors"].append("no historical cache tables - run MIGRATE_HISTORICAL_CACHE / BUILD_HISTORICAL_PNL_CACHE")

    # capital-shadow gold gate (optional)
    if "capital_shadow_outcomes" in tabs:
        try:
            r = con.execute(
                "select count(*) n, coalesce(sum(realized_pnl_usd),0) net "
                "from capital_shadow_outcomes "
                "where upper(coalesce(decision,''))='PASS' and finalized_at is not null"
            ).fetchone()
            if r and r["n"]:
                meta["shadow"] = {"pass_n": r["n"], "pass_net": _f(r["net"], 0.0)}
        except Exception:
            pass

    try:
        con.close()
    except Exception:
        pass
    return rollups, meta

# ---------------------------------------------------------------- state logic

def _era_state(g, shadow=None):
    """Map an era rollup to a colour state. GOLD must be earned, never default."""
    if not g or not g.get("n"):
        return "MISSING"
    n, net = g["n"], g.get("net", 0.0)
    wins, losses = g.get("wins", 0), g.get("losses", 0)
    gw, gl = g.get("gross_win", 0.0), g.get("gross_loss", 0.0)
    pf = (gw / gl) if gl > 0 else (2.0 if gw > 0 else 0.0)
    decided = wins + losses
    wr = (wins / decided) if decided else 0.0
    rug_share = (g.get("rugs", 0) / n) if n else 0.0
    caught = g.get("runners", 0) + g.get("monsters", 0)

    if net < 0 and (rug_share > 0.15 or pf < 0.6):
        return "FRACTURE"
    if net <= 0:
        return "DISCOVERY"
    gold = (pf >= 1.5 and rug_share <= 0.05 and caught >= 1)
    if gold and shadow is not None:
        gold = shadow.get("pass_net", 0.0) > 0
    if gold:
        return "GOLD"
    if caught >= 1:
        return "RUNNER"
    if wr >= 0.5:
        return "MODEST"
    return "DISCOVERY"

# ---------------------------------------------------------------- renderers

def _fmt_money(x):
    try:
        return f"${x:+,.0f}"
    except Exception:
        return "n/a"


def _trajectory_svg(eras):
    """eras: list of dicts {key,label,state,g}. Pure SVG, no deps."""
    W, H, PAD = 940, 260, 18
    n = max(len(eras), 1)
    bw = (W - 2 * PAD) / n
    nets = [abs(e["g"].get("net", 0.0)) for e in eras if e["g"]]
    scale = max(nets) if nets else 1.0
    mid = H - 78
    parts = [f'<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">',
             f'<rect x="0" y="0" width="{W}" height="{H}" rx="14" fill="#0b0918"/>',
             f'<line x1="{PAD}" y1="{mid}" x2="{W-PAD}" y2="{mid}" stroke="#2a2350" stroke-width="1"/>']
    for i, e in enumerate(eras):
        x = PAD + i * bw + 6
        w = bw - 12
        fill, edge = COL[e["state"]]
        if e["state"] == "MISSING":
            parts.append(
                f'<rect x="{x:.1f}" y="{mid-60}" width="{w:.1f}" height="120" rx="8" '
                f'fill="{fill}" stroke="{edge}" stroke-width="1.5" stroke-dasharray="6 5" opacity="0.7"/>')
            parts.append(
                f'<text x="{x+w/2:.1f}" y="{mid+4}" fill="#6e5fa8" font-size="12" '
                f'text-anchor="middle" font-family="monospace">missing</text>')
        else:
            g = e["g"]
            net = g.get("net", 0.0)
            hgt = max(10.0, 100.0 * (abs(net) / scale)) if scale else 10.0
            y = mid - hgt if net >= 0 else mid
            glow = ' filter="url(#gold)"' if e["state"] == "GOLD" else ""
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{hgt:.1f}" rx="8" '
                f'fill="{edge}" opacity="0.85"{glow}/>')
            parts.append(
                f'<text x="{x+w/2:.1f}" y="{y-8 if net>=0 else y+hgt+16:.1f}" fill="{edge}" '
                f'font-size="13" font-weight="700" text-anchor="middle" '
                f'font-family="monospace">{html.escape(_fmt_money(net))}</text>')
            gw, gl = g.get("gross_win", 0.0), g.get("gross_loss", 0.0)
            pf = (gw / gl) if gl > 0 else 0.0
            dec = g.get("wins", 0) + g.get("losses", 0)
            wr = (100.0 * g.get("wins", 0) / dec) if dec else 0.0
            sub = f'n={g["n"]} wr={wr:.0f}% pf={pf:.2f} R{g.get("runners",0)} M{g.get("monsters",0)} rug{g.get("rugs",0)}'
            parts.append(
                f'<text x="{x+w/2:.1f}" y="{H-40}" fill="#8f86c9" font-size="10.5" '
                f'text-anchor="middle" font-family="monospace">{html.escape(sub)}</text>')
        parts.append(
            f'<text x="{x+w/2:.1f}" y="{H-20}" fill="#cfc8f2" font-size="11.5" '
            f'text-anchor="middle" font-family="monospace">{html.escape(e["label"])}</text>')
    parts.append(
        '<defs><filter id="gold" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur stdDeviation="4" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        '</filter></defs></svg>')
    return "".join(parts)


def load_hourly_edge(intel_db):
    con = _connect(intel_db)
    if not con:
        return []
    tabs = _tables(con)
    out = []
    if "historical_hourly_edge" in tabs:
        c = _cols(con, "historical_hourly_edge")
        h_c = _pick(["hour_of_day", "hour"], c)
        p_c = _pick(["net_pnl_usd", "net_pnl", "pnl_usd"], c)
        n_c = _pick(["trade_count", "n", "trades"], c)
        if h_c and p_c:
            try:
                for r in con.execute("select * from historical_hourly_edge"):
                    out.append({"hour": int(_f(r[h_c], 0) or 0),
                                "net": _f(r[p_c], 0.0) or 0.0,
                                "n": int(_f(r[n_c], 0) or 0) if n_c else 0})
            except Exception:
                pass
    try:
        con.close()
    except Exception:
        pass
    return out


def _hours_svg(hours, tz_label="AEST"):
    by_h = {h["hour"] % 24: h for h in hours}
    W, H, PAD = 940, 96, 18
    cw = (W - 2 * PAD) / 24
    vals = [abs(v["net"]) for v in by_h.values()] or [1.0]
    mx = max(vals) or 1.0
    parts = [f'<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">',
             f'<rect x="0" y="0" width="{W}" height="{H}" rx="12" fill="#0b0918"/>']
    for h in range(24):
        x = PAD + h * cw + 2
        d = by_h.get(h)
        if not d or not d.get("n"):
            fill, op = "#17102b", 0.8
        else:
            net = d["net"]
            t = min(1.0, abs(net) / mx)
            if net > 0:
                fill = "#ffd54a" if t > 0.66 else ("#2fd6a1" if t > 0.25 else "#38c7e8")
            else:
                fill = "#e8386b" if t > 0.4 else "#7a3b8f"
            op = 0.35 + 0.6 * t
        parts.append(f'<rect x="{x:.1f}" y="18" width="{cw-4:.1f}" height="40" rx="6" fill="{fill}" opacity="{op:.2f}"/>')
        parts.append(f'<text x="{x+(cw-4)/2:.1f}" y="76" fill="#8f86c9" font-size="10" text-anchor="middle" font-family="monospace">{h:02d}</text>')
    parts.append(f'<text x="{W-PAD}" y="90" fill="#5d548f" font-size="9.5" text-anchor="end" font-family="monospace">hour of day ({html.escape(tz_label)}) - cold violet, discovery cyan, modest teal, golden gold, loss red</text>')
    parts.append("</svg>")
    return "".join(parts)

# ---------------------------------------------------------------- streamlit

def render_magic_trajectory(intel_db=None, hot_db=None, tz_label="AEST"):
    import streamlit as st

    root = os.getcwd()
    intel_db = intel_db or os.path.join(root, "sentinuity_intelligence.db")

    rollups, meta = load_era_rollups(intel_db)
    eras = []
    seen = set()
    for key, label in ERA_ORDER:
        g = rollups.get(key)
        eras.append({"key": key, "label": label,
                     "state": _era_state(g, meta.get("shadow")), "g": g or {}})
        seen.add(key)
    for key, g in rollups.items():          # any era label we didn't predefine
        if key not in seen:
            eras.append({"key": key, "label": key[:18],
                         "state": _era_state(g, meta.get("shadow")), "g": g})

    st.markdown("#### MAGIC TRAJECTORY - SENTINUITY EVOLUTION")
    st.caption("Secondary intelligence view. Real cached history only - no projections. "
               "The Cluster Cadence chart remains the primary truth chart.")

    if meta["errors"]:
        st.warning(" / ".join(meta["errors"]))
    if not meta["cache_rows"] and not rollups:
        st.info("No historical cache yet. Run BUILD_HISTORICAL_PNL_CACHE.py, "
                "then reload. Missing eras will stay marked missing - they are never invented.")
        return

    st.markdown(f'<div style="border:1px solid #2a2350;border-radius:14px;'
                f'padding:6px;background:#0b0918;">{_trajectory_svg(eras)}</div>',
                unsafe_allow_html=True)

    lit = [e for e in eras if e["state"] not in ("MISSING", "VOID")]
    missing = [e["label"] for e in eras if e["state"] == "MISSING"
               and e["key"] != "ARCHIVE_UNKNOWN"]
    gold = [e["label"] for e in lit if e["state"] == "GOLD"]
    line = f"eras with data: {len(lit)} | cached trades: {meta['cache_rows']} | sources: {', '.join(meta['source_dbs']) or 'n/a'}"
    if gold:
        line += f" | GOLD earned by: {', '.join(gold)}"
    if meta.get("shadow"):
        s = meta["shadow"]
        line += f" | capital-shadow PASS: {s['pass_n']} finalized, net {_fmt_money(s['pass_net'])}"
    else:
        line += " | gold gate: capital-shadow not yet reporting (metrics-only gating)"
    st.caption(line)
    if missing:
        st.caption("missing eras (archive DBs not found on disk): " + ", ".join(missing)
                   + " - run EXTRACT_ARCHIVE_DBS_FROM_ZIPS.py then rebuild the cache to recover them.")

    hours = load_hourly_edge(intel_db)
    if hours:
        st.markdown("##### GOLDEN HOURS - historical hour-of-day edge")
        st.markdown(f'<div style="border:1px solid #2a2350;border-radius:12px;'
                    f'padding:6px;background:#0b0918;">{_hours_svg(hours, tz_label)}</div>',
                    unsafe_allow_html=True)

    st.caption("Note: runner/monster classes derived from MFE can include single-tick "
               "spike contamination until the peak-tracking fix (Part F) lands; "
               "treat runner counts as upper bounds for now.")
