"""Sentinuity Live Gate Constellation.

Read-only responsive observability surface. It renders the exact runtime gate map,
recent outcome rhythm, and the most recent blocker without changing execution,
configuration, schema, sizing, risk, or live-fire policy.

SIGNOFF_LIVE_LEDGER_20260716 — EXECUTOR/UI PARITY.
This surface NO LONGER computes its own FINAL FIRE verdict. Previously it called
evaluate_pattern_permission() independently and derived readiness in the UI —
two code paths, two verdicts, converging only by coincidence. The central state
now comes from services.live_decision_contract, which ONLY the executor writes.
If the executor has not published, or its contract is stale, this surface renders
UNAVAILABLE rather than a remembered or inferred verdict. Directive Part 5:
"No UI-only inference may claim live readiness."

Flow-count nodes below remain UI-derived observability (how many candidates were
seen/priced/qualified in the last 10 minutes). They are descriptive telemetry and
are explicitly NOT part of the fire decision.
"""
from __future__ import annotations

import html
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


def _safe(v: Any) -> str:
    return html.escape(str(v if v is not None else "—"))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _connect(path: str | Path) -> sqlite3.Connection | None:
    try:
        c = sqlite3.connect(str(path), timeout=1.5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        c.execute("PRAGMA busy_timeout=900")
        return c
    except Exception:
        return None


def _tables(c: sqlite3.Connection) -> set[str]:
    try:
        return {str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception:
        return set()


def _cols(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in c.execute(f'PRAGMA table_info("{table}")')}
    except Exception:
        return set()


def _cfg(c: sqlite3.Connection | None, key: str, default: Any = None) -> Any:
    if c is None:
        return os.getenv(key, default)
    for table in ("system_config", "config"):
        if table not in _tables(c):
            continue
        cols = _cols(c, table)
        k = "key" if "key" in cols else ("name" if "name" in cols else None)
        v = "value" if "value" in cols else ("val" if "val" in cols else None)
        if k and v:
            try:
                row = c.execute(f'SELECT "{v}" FROM "{table}" WHERE "{k}"=? LIMIT 1', (key,)).fetchone()
                if row:
                    return row[0]
            except Exception:
                pass
    return os.getenv(key, default)


def _outcomes(c: sqlite3.Connection | None, minutes: int = 20) -> dict[str, Any]:
    out = {"rows": [], "gold": 0, "breakeven": 0, "loss": 0, "ratio": 0.0,
           "cycles": 0, "probability": None, "sample": 0, "gold_min": 25.0,
           "source": "paper_positions", "note": "No canonical closes found"}
    if c is None:
        return out
    gold_min = _f(_cfg(c, "LIVE_PATTERN_GOLD_MIN_PCT", 25), 25)
    be_abs = _f(_cfg(c, "LIVE_PATTERN_BREAKEVEN_ABS_PCT", 5), 5)
    out["gold_min"] = gold_min
    tables = _tables(c)
    table = next((t for t in ("paper_positions", "positions", "trade_outcomes") if t in tables), None)
    if not table:
        return out
    cols = _cols(c, table)
    pnl = next((x for x in ("realized_pnl_pct", "realized_pct", "pnl_pct", "return_pct") if x in cols), None)
    usd_derived = pnl is None and {"realized_pnl_usd", "position_size_usd"}.issubset(cols)
    ts = next((x for x in ("closed_at", "exit_time", "updated_at", "timestamp", "created_at") if x in cols), None)
    status = "status" if "status" in cols else None
    if not pnl and not usd_derived:
        out["note"] = f"{table} has no canonical realized return columns"
        return out
    where = []
    params: list[Any] = []
    if status:
        where.append("UPPER(COALESCE(status,'')) IN ('CLOSED','EXITED','SOLD')")
    if "funding_mode" in cols:
        where.append("UPPER(COALESCE(funding_mode,'SIM'))='SIM'")
    if "mode" in cols:
        where.append("LOWER(COALESCE(mode,'paper')) NOT LIKE '%substrate%'")
    if ts:
        where.append(f'CAST(COALESCE("{ts}",0) AS REAL) >= ?')
        params.append(time.time() - minutes * 60)
    pnl_expr = (f'CAST(COALESCE("{pnl}",0) AS REAL)' if pnl else
                'CASE WHEN ABS(CAST(COALESCE(position_size_usd,0) AS REAL))>0.000000001 '
                'THEN CAST(COALESCE(realized_pnl_usd,0) AS REAL)/ABS(CAST(position_size_usd AS REAL))*100.0 ELSE 0 END')
    sql = f'SELECT {pnl_expr} pnl' + (f', "{ts}" ts' if ts else ', 0 ts') + f' FROM "{table}"'
    if where:
        sql += " WHERE " + " AND ".join(where)
    if ts:
        sql += f' ORDER BY CAST(COALESCE("{ts}",0) AS REAL) ASC'
    sql += " LIMIT 250"
    try:
        vals = [(float(r["pnl"]), r["ts"]) for r in c.execute(sql, params).fetchall()]
    except Exception:
        vals = []
    out["source"] = f"{table}." + (pnl or "realized_pnl_usd/position_size_usd")
    out["sample"] = len(vals)
    labels: list[str] = []
    for pct, ts_v in vals:
        if pct >= gold_min:
            label = "GOLD"; out["gold"] += 1
        elif -be_abs <= pct <= be_abs:
            label = "BE"; out["breakeven"] += 1
        else:
            label = "LOSS"; out["loss"] += 1
        labels.append(label)
        out["rows"].append({"label": label, "pnl": pct, "ts": ts_v})
    out["ratio"] = out["gold"] / max(1, out["breakeven"])
    # A completed positive cycle is >=2 golds followed by 1-2 BE rows.
    cycles = 0
    run_gold = 0
    for label in labels:
        if label == "GOLD":
            run_gold += 1
        elif label == "BE":
            if run_gold >= 2:
                cycles += 1
            run_gold = 0
        else:
            run_gold = 0
    out["cycles"] = cycles
    # Smoothed descriptive chance only; never used as an execution gate.
    if vals:
        successes = sum(1 for p, _ in vals if p > be_abs)
        out["probability"] = 100.0 * (successes + 1.0) / (len(vals) + 2.0)
        out["note"] = "Observed rolling outcome estimate · advisory only"
    return out


def _gate_nodes(g: dict[str, Any], c: sqlite3.Connection | None, contract: dict[str, Any]) -> list[dict[str, str]]:
    live = g.get("live", {}) or {}
    oracle = g.get("oracle", {}) or {}
    cand = g.get("candidates", {}) or {}
    hbs = g.get("heartbeats", {}) or {}
    mode = str(_cfg(c, "TRADING_MODE", os.getenv("TRADING_MODE", "paper"))).lower()
    enabled = mode in {"live", "dual"} or str(_cfg(c, "LIVE_TRADING_ENABLED", "0")).lower() in {"1","true","yes","on"}
    current_hour = live.get("current_utc_hour")
    blocked_hours = live.get("blocked_hours") or []
    hour_ok = not live.get("hour_gate_enabled") or current_hour not in blocked_hours
    exec_hb = (hbs.get("execution_engine") or {}).get("state")
    contract_exec_fresh = bool(contract.get("executor_fresh"))
    contract_exec_age = contract.get("executor_heartbeat_age_sec")
    executor_ok = contract_exec_fresh or exec_hb == "fresh"
    if contract_exec_fresh:
        executor_current = (f"heartbeat=fresh · age={contract_exec_age:.1f}s"
                            if isinstance(contract_exec_age, (int, float)) else "heartbeat=fresh")
    elif exec_hb == "fresh":
        executor_current = "heartbeat=fresh · gate-map"
    elif isinstance(contract_exec_age, (int, float)):
        executor_current = f"heartbeat=stale · age={contract_exec_age:.1f}s"
    else:
        executor_current = "heartbeat=unknown"
    # PARITY: pattern state is READ from the executor's published contract.
    # The UI must never call evaluate_pattern_permission() itself — that was the
    # second code path that made UI/executor agreement coincidental.
    pattern_ok = bool(contract.get("pattern_armed")) and not contract.get("stale")
    if not contract.get("available"):
        pattern_current = "executor has not published"
    elif contract.get("stale"):
        pattern_current = f"contract stale ({contract.get('age_sec')}s) — executor truth unknown"
    else:
        _pm = contract.get("pattern_multiplier")
        pattern_current = f"{contract.get('pattern_state')}" + (f" · {_pm:.2f}x" if isinstance(_pm,(int,float)) else "")
    oracle_age = oracle.get("age_sec")
    oracle_gate = oracle.get("gate_sec")
    nodes = [
        ("MODE", enabled, f"TRADING_MODE={mode.upper()}", "TRADING_MODE ∈ {DUAL,LIVE} or LIVE_TRADING_ENABLED=1", "hard"),
        ("EXECUTOR", executor_ok, executor_current,
         "canonical heartbeat from live_decision_contract or gate-map", "hard"),
        ("PRICE", oracle.get("state") == "fresh", f"age={oracle_age:.1f}s" if isinstance(oracle_age,(int,float)) else "age=unknown", f"oracle age ≤ {oracle_gate or 300:.0f}s", "hard"),
        ("HOUR", hour_ok, f"UTC {current_hour}; blocked={blocked_hours}", "HOUR_GATE_BLOCK_UTC does not contain current hour", "hard"),
        ("DISCOVERY", int(cand.get("discovered_10m",0)) > 0, f"{cand.get('discovered_10m',0)} seen / 10m", "market_snapshots newest operational timestamp", "flow"),
        ("PRICED", int(cand.get("priced_10m",0)) > 0, f"{cand.get('priced_10m',0)} priced / 10m", "price_status='priced' or observed_price>0", "flow"),
        ("QUALIFIED", int(cand.get("qualified_10m",0)) > 0, f"{cand.get('qualified_10m',0)} qualified / 10m", "quality_status='qualified'", "flow"),
        ("LATCHED", int(cand.get("latched_10m",0)) > 0, f"{cand.get('latched_10m',0)} latched / 10m", "market_snapshots.latched=1", "flow"),
        ("EXEC READY", int(cand.get("execution_ready_10m",0)) > 0, f"{cand.get('execution_ready_10m',0)} ready / 10m", "market_snapshots.execution_ready=1", "flow"),
        ("WALLET", live.get("state") != "wallet_limited", f"wallet={live.get('wallet_balance')} size={live.get('flat_size')}", "LIVE_WALLET_USD ≥ live flat size", "hard"),
        ("CAPACITY", int(live.get("open_positions",0)) < int(_f(_cfg(c,"LIVE_MAX_OPEN_POSITIONS",1),1)), f"{live.get('open_positions',0)} open", "open live positions < LIVE_MAX_OPEN_POSITIONS", "hard"),
        ("PATTERN", pattern_ok, pattern_current, "2 canonical SIM closes arm half; 3 arm full", "hard"),
        # FINAL FIRE is the executor's verdict, verbatim. Not a UI inference.
        ("FINAL FIRE", str(contract.get("verdict")) == "FIRE_PATH_OPEN",
         str(contract.get("verdict") or "UNAVAILABLE"),
         f"executor contract · {contract.get('authored_by') or 'not published'}", "final"),
    ]
    return [{"name": n, "passed": "1" if ok else "0", "current": cur, "contract": contract, "kind": kind} for n,ok,cur,contract,kind in nodes]


def _arming_ladder(contract: dict[str, Any]) -> str:
    """PATTERN OVERLAY — arming ladder (UPGRADE_20260718_V2).

    The behavioural half of the fire doctrine, rendered as a visible sequence:
    DORMANT → WATCHING (1 independent SIM success) → ARMED (2, half-size canary)
    → CONFIRMED (3, full size still capped pending execution parity). Everything
    is read verbatim from the executor-authored decision contract — nothing here
    is recomputed and nothing here can arm anything. Pattern NEVER bypasses the
    technical underlay; it only sets the SIZE multiplier (0× / 0.5× / 1×-capped).
    """
    state = str(contract.get("pattern_state") or "UNKNOWN").upper()
    reason = str(contract.get("pattern_reason") or "")
    mult = contract.get("pattern_multiplier")
    confirms = 0
    m = re.search(r"(\d+)_independent_successes", reason)
    if m:
        confirms = int(m.group(1))
    elif "first_independent_success" in reason:
        confirms = 1
    stages = [
        ("DORMANT",   "no causal SIM closes",            0),
        ("WATCHING",  "1 independent success",           1),
        ("ARMED ½",   "2 independent · half canary",     2),
        ("CONFIRMED", "3 independent · parity-capped",   3),
    ]
    idx_map = {"DORMANT": 0, "WATCHING": 1, "ARMED": 2, "CONFIRMED": 3}
    cur = idx_map.get(state.split()[0], -1)
    unavailable = state in ("UNKNOWN", "UNAVAILABLE", "NONE") or not contract.get("available")
    cells = []
    for i, (name, rule, need) in enumerate(stages):
        cls = "lgp-done" if (cur >= 0 and i < cur) else ("lgp-now" if i == cur else "lgp-wait")
        if unavailable:
            cls = "lgp-wait"
        pips = "".join(
            f'<i class="{"on" if p < confirms else ""}"></i>' for p in range(max(1, need) if need else 1)
        ) if need else '<i class="zero"></i>'
        cells.append(
            f'<div class="lgp-stage {cls}"><b>{_safe(name)}</b>'
            f'<div class="lgp-pips">{pips}</div>'
            f'<span>{_safe(rule)}</span></div>'
            + ("" if i == len(stages) - 1 else f'<div class="lgp-arrow {cls}">›</div>')
        )
    mult_txt = f"{float(mult):.2f}× size" if isinstance(mult, (int, float)) else "size —"
    head_txt = ("EXECUTOR PATTERN TRUTH UNPUBLISHED" if unavailable
                else f"{state} · {confirms} INDEPENDENT SIM CONFIRM{'S' if confirms != 1 else ''} · {mult_txt}")
    rules = ("Capital doctrine: confirmations must be distinct by mint, position and discovery cohort · "
             "peak/MFE never confirms — only realised closes · one L/H/X realised loss resets the sequence · "
             "the overlay sets SIZE only (0× / 0.5× / 1× parity-capped) and can never bypass the technical "
             "underlay — wallet, oracle, route, capacity and Mode-B gates stay absolute")
    return (
        f'<div class="lgp-shell"><div class="lgp-head">◈ PATTERN CONSTELLATION — ARMING SEQUENCE'
        f'<span class="lgp-state">{_safe(head_txt)}</span></div>'
        f'<div class="lgp-rail">{"".join(cells)}</div>'
        f'<div class="lgp-note">{rules}</div></div>'
    ), confirms, cur, unavailable


def render_live_gate_constellation(st, db_path: str | Path, intel_db_path: str | Path | None = None) -> None:
    try:
        from services.sovereign_gate_map import collect_gate_map
        g = collect_gate_map(db_path, intel_db_path)
    except Exception as exc:
        st.warning(f"Live Gate Constellation unavailable; backend unaffected: {type(exc).__name__}: {exc}")
        return
    # PARITY: the executor's contract is the ONLY source of the central verdict.
    try:
        from services.live_decision_contract import read_contract
        contract = read_contract()
    except Exception as exc:
        contract = {"available": False, "stale": True, "verdict": "UNAVAILABLE",
                    "blocker": f"decision contract unreadable: {type(exc).__name__}",
                    "next_event": None, "gates": [], "pattern_armed": False}
    try:
        from services.live_settlement_recovery import get_recovery_diagnostics
        recovery = get_recovery_diagnostics()
    except Exception as exc:
        recovery = {"available": False, "error": f"{type(exc).__name__}", "rows": [],
                    "pending": 0, "confirmed_unresolved": 0,
                    "manual_intervention": 0, "positions_manual": 0,
                    "heartbeat": None}
    c = _connect(db_path)
    try:
        nodes = _gate_nodes(g, c, contract)
        rhythm = _outcomes(c, 20)
    finally:
        try:
            if c: c.close()
        except Exception:
            pass

    # ── UPGRADE_20260718_V2: split the single 13-node rail into the doctrine's
    # real layers so the interlock is legible instead of guessable:
    #   TECHNICAL UNDERLAY  = hard gates (every one must pass — the circuit)
    #   PATTERN OVERLAY     = arming ladder (sets SIZE only — the charge)
    #   CANDIDATE FLOW      = observation-only telemetry (never a gate)
    #   FINAL FIRE          = executor verdict, verbatim, unchanged.
    # PATTERN leaves the circuit rail: its single representation is the overlay
    # ladder below (it previously appeared twice — as a hard node AND as the
    # ladder — which made the interlock read as guesswork). The circuit is now
    # purely the mechanical technicals; pattern remains a hard requirement via
    # the executor's FINAL FIRE verdict, which is unchanged and verbatim.
    hard_nodes  = [n for n in nodes if n["kind"] == "hard" and n["name"] != "PATTERN"]
    pattern_node = next((n for n in nodes if n["name"] == "PATTERN"), None)
    flow_nodes  = [n for n in nodes if n["kind"] == "flow"]
    final_nodes = [n for n in nodes if n["kind"] == "final"]
    passed = sum(1 for n in nodes if n["passed"] == "1")
    hard_passed = sum(1 for n in hard_nodes if n["passed"] == "1")
    hard_total = max(1, len(hard_nodes))
    hard_block = any(n["passed"] == "0" for n in hard_nodes)
    first_hard_fail = next((n["name"] for n in hard_nodes if n["passed"] == "0"), None)
    readiness = round(100 * passed / len(nodes))

    ladder_html, confirms, ladder_stage, ladder_unavailable = _arming_ladder(contract)

    # STAR ALIGNMENT — advisory aggregate of truths already shown above:
    # 70% technical circuit closure + 30% pattern charge. Display-only.
    pattern_frac = 0.0 if ladder_unavailable else max(0.0, min(1.0, (ladder_stage if ladder_stage >= 0 else 0) / 3.0))
    charge = round(100 * (0.70 * hard_passed / hard_total + 0.30 * pattern_frac))

    _VERDICT_TEXT = {
        "FIRE_PATH_OPEN": "FIRE PATH OPEN",
        "ARMED_WAITING": "ARMED · WAITING FOR CANDIDATE",
        "ALIGNING": "ALIGNING",
        "BLOCKED": "BLOCKED",
        "BUY_SUBMITTED": "BUY SUBMITTED",
        "OPEN_REAL": "OPEN REAL",
        "SELL_SUBMITTED": "SELL SUBMITTED",
        "SETTLED": "SETTLED",
        "MANUAL_INTERVENTION": "MANUAL INTERVENTION",
        "UNAVAILABLE": "UNAVAILABLE · EXECUTOR TRUTH NOT FRESH",
    }
    _raw_verdict = str(contract.get("verdict") or "UNAVAILABLE")
    if int(recovery.get("positions_manual") or 0) > 0 or \
       int(recovery.get("manual_intervention") or 0) > 0:
        _raw_verdict = "MANUAL_INTERVENTION"
    verdict = _VERDICT_TEXT.get(_raw_verdict, _raw_verdict)
    mech_block = hard_block  # mechanical circuit only (PATTERN excluded above)
    hard_block = hard_block or _raw_verdict in ("BLOCKED", "MANUAL_INTERVENTION")
    blocker = (contract.get("blocker")
               or g.get("primary_blocker")
               or g.get("live", {}).get("last_block_reason")
               or "Awaiting executor decision contract")
    next_event = contract.get("next_event") or "—"
    probability = rhythm["probability"]
    probability_text = f"{probability:.0f}%" if probability is not None else "—"
    mult = contract.get("pattern_multiplier")
    mult_txt = f"{float(mult):.2f}×" if isinstance(mult, (int, float)) else "—"

    # NEXT-CANDIDATE banner — a strict re-reading of the same truths, advisory.
    if not contract.get("available") or contract.get("stale"):
        anticipation_cls, anticipation = "wait", "EXECUTOR TRUTH UNPUBLISHED — NO LIVE CLAIM CAN BE MADE"
    elif _raw_verdict in ("FIRE_PATH_OPEN", "ARMED_WAITING"):
        anticipation_cls = "fire"
        anticipation = f"STARS ALIGNED — NEXT QUALIFIED CANDIDATE FIRES LIVE @ {mult_txt} SIZE"
    elif _raw_verdict in ("BUY_SUBMITTED", "OPEN_REAL", "SELL_SUBMITTED"):
        anticipation_cls, anticipation = "fire", f"LIVE CAPITAL IN FLIGHT — {verdict}"
    elif not mech_block and confirms >= 1:
        needed = max(0, 2 - confirms)
        anticipation_cls = "charge"
        anticipation = (f"CIRCUIT CLOSED · PATTERN CHARGING {confirms}/2 — "
                        f"{needed} MORE INDEPENDENT SIM WIN{'S ARM' if needed != 1 else ' ARMS'} THE HALF-SIZE CANARY")
    elif not mech_block:
        anticipation_cls = "charge"
        anticipation = "CIRCUIT CLOSED · PATTERN DORMANT — FIRST INDEPENDENT SIM WIN STARTS THE CHARGE"
    else:
        anticipation_cls = "block"
        anticipation = f"PAPER ONLY — CIRCUIT OPEN AT {first_hard_fail or 'HARD GATE'}"

    def _rail(node_list, start_index):
        cells = []
        for i, n in enumerate(node_list):
            state = "pass" if n["passed"] == "1" else ("block" if n["kind"] == "hard" else "wait")
            if n["kind"] == "final":
                state = "final-open" if n["passed"] == "1" else ("final-block" if hard_block else "final-wait")
            icon = {"pass": "✦", "block": "×", "wait": "◇",
                    "final-open": "✹", "final-block": "×", "final-wait": "◈"}[state]
            connector = "" if i == len(node_list) - 1 else f'<div class="lg-link {state}"><i></i></div>'
            cells.append(
                f'''<div class="lg-stage-wrap">
                      <div class="lg-above">{_safe(n['current'])}</div>
                      <div class="lg-stage {state}">
                        <span class="lg-orbit"></span><span class="lg-core">{icon}</span>
                        <span class="lg-index">{start_index + i:02d}</span>
                      </div>
                      <div class="lg-stage-name">{_safe(n['name'])}</div>
                      <div class="lg-below">{_safe(n['contract'])}</div>
                    </div>{connector}'''
            )
        return "".join(cells)

    hard_rail = _rail(hard_nodes + final_nodes, 1)
    flow_chips = "".join(
        f'<span class="lg-flow-chip {"on" if n["passed"] == "1" else ""}">'
        f'<b>{_safe(n["name"])}</b>{_safe(n["current"])}</span>'
        for n in flow_nodes
    )
    # Charge meter stars: five stars fill with the charge percentage.
    stars = "".join(
        f'<i class="{"lit" if charge >= t else ""}">✦</i>' for t in (18, 38, 58, 78, 96)
    )

    rhythm_cells = "".join(
        f'<span class="rh-{r["label"].lower()}">{"✦" if r["label"]=="GOLD" else ("◆" if r["label"]=="BE" else "×")}&nbsp;{r["pnl"]:+.1f}%</span>'
        for r in rhythm["rows"][-16:]
    ) or '<span class="rh-empty">NO CANONICAL CLOSES IN THE LAST 20 MINUTES</span>'

    verdict_class = "open" if _raw_verdict in ("FIRE_PATH_OPEN", "BUY_SUBMITTED", "OPEN_REAL") else ("blocked" if hard_block else "aligning")
    pulse_text = "CAPITAL PATH OPEN" if verdict_class == "open" else ("SEQUENCE INTERRUPTED" if verdict_class == "blocked" else "SEQUENCE BUILDING")

    # HOLO_HELP_20260718: standard "?" rundown for first-time viewers.
    try:
        from ui.holo_help import glyph as _holo_glyph
        _help_glyph = _holo_glyph("constellation")
    except Exception:
        _help_glyph = ""

    _vcol = "#14F195" if verdict_class == "open" else ("#FF073A" if verdict_class == "blocked" else "#FFD700")
    _acol = {"fire": "#FFD700", "charge": "#14F195", "block": "#FF073A", "wait": "#52606E"}[anticipation_cls]

    st.markdown(f'''
<style>
/* SIGNOFF_CONSTELLATION_V2_20260718 — colour doctrine: green=pass/go, violet=intelligence,
   cyan=price/flow truth, red=veto/break, GOLD=earned apex only (pattern charge, GOLD closes,
   open fire path). Pass cores are green now — gold is no longer a routine pass fill. */
.lg-shell{{position:relative;margin:18px 0 26px;padding:22px 22px 20px;border:1px solid rgba(20,241,149,.19);border-radius:24px;background:radial-gradient(ellipse at 50% -20%,rgba(20,241,149,.10),transparent 48%),radial-gradient(circle at 92% 10%,rgba(153,69,255,.08),transparent 32%),linear-gradient(145deg,rgba(3,12,15,.72),rgba(5,3,16,.66));box-shadow:0 22px 70px rgba(0,0,0,.34),inset 0 1px rgba(142,249,255,.09);backdrop-filter:blur(18px);overflow:hidden}}
.lg-shell:before{{content:"";position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(90deg,transparent 0 31px,rgba(20,241,149,.025) 32px 33px);opacity:.65}}
.lg-shell:after{{content:"";position:absolute;left:8%;right:8%;top:0;height:1px;background:linear-gradient(90deg,transparent,#14F195,#9945FF,#8EF9FF,#FF073A,#FFD700,transparent);box-shadow:0 0 18px rgba(20,241,149,.5)}}
.lg-head{{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:22px;align-items:end}}
.lg-kicker{{font:600 0.66rem/1 'Orbitron',sans-serif;letter-spacing:.28em;color:#14F195;text-shadow:0 0 12px rgba(20,241,149,.5)}}
.lg-title{{font:700 clamp(1.05rem,2vw,1.65rem)/1.08 'Orbitron',sans-serif;letter-spacing:.14em;color:#E9FFFA;margin-top:8px}}
.lg-sub{{font:500 .68rem/1.5 'Share Tech Mono',monospace;color:rgba(142,249,255,.58);margin-top:7px;max-width:960px}}
.lg-score{{min-width:150px;text-align:right}}.lg-score b{{display:block;font:700 2rem/1 'Orbitron',sans-serif;color:{_vcol};text-shadow:0 0 18px currentColor}}.lg-score span{{font:600 0.66rem/1.5 'Share Tech Mono',monospace;letter-spacing:.15em;color:#8EF9FF}}
.lg-verdict{{position:relative;margin:16px 0 8px;padding:10px 13px;display:flex;justify-content:space-between;gap:14px;align-items:center;border-top:1px solid rgba(142,249,255,.10);border-bottom:1px solid rgba(142,249,255,.10);background:linear-gradient(90deg,transparent,rgba(20,241,149,.035),transparent)}}
.lg-verdict strong{{font:700 0.66rem/1 'Orbitron',sans-serif;letter-spacing:.17em;color:{_vcol}}}.lg-verdict code{{font:500 0.66rem/1.4 'Share Tech Mono',monospace;color:#B9D8D3;text-align:right;white-space:normal}}
/* NEXT-CANDIDATE anticipation banner */
.lg-anticipate{{position:relative;margin:10px 0 4px;padding:12px 14px;border-radius:14px;border:1px solid {_acol}44;background:linear-gradient(90deg,{_acol}0F,transparent 62%);display:flex;gap:12px;align-items:center}}
.lg-anticipate b{{font:700 .72rem/1.35 'Orbitron',sans-serif;letter-spacing:.11em;color:{_acol};text-shadow:0 0 12px {_acol}66}}
.lg-anticipate .lg-adv{{margin-left:auto;font:600 .52rem/1 'Share Tech Mono',monospace;letter-spacing:.16em;color:#52606E;border:1px solid rgba(142,249,255,.14);border-radius:999px;padding:4px 8px;white-space:nowrap}}
/* STAR ALIGNMENT meter */
.lg-charge{{margin:10px 0 2px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:12px;align-items:center}}
.lg-charge label{{font:600 0.60rem/1 'Share Tech Mono',monospace;letter-spacing:.18em;color:#8EF9FF}}
.lg-charge-track{{position:relative;height:10px;border-radius:999px;background:rgba(142,249,255,.07);border:1px solid rgba(142,249,255,.12);overflow:hidden}}
.lg-charge-fill{{position:absolute;inset:0 auto 0 0;width:{charge}%;border-radius:999px;background:linear-gradient(90deg,#14F195,#8EF9FF 55%,#FFD700);box-shadow:0 0 14px rgba(20,241,149,.45);transition:width .6s ease}}
.lg-charge-stars i{{font-style:normal;font-size:.86rem;color:#33424D;margin-left:4px;text-shadow:none;transition:color .4s ease}}
.lg-charge-stars i.lit{{color:#FFD700;text-shadow:0 0 12px rgba(255,215,0,.75)}}
/* layer strips */
.lg-layer-head{{margin:14px 0 2px;display:flex;justify-content:space-between;gap:10px;align-items:baseline}}
.lg-layer-head b{{font:700 0.64rem/1 'Orbitron',sans-serif;letter-spacing:.20em}}
.lg-layer-head span{{font:500 0.58rem/1.3 'Share Tech Mono',monospace;color:#55706A}}
.lg-layer-head.tech b{{color:#14F195;text-shadow:0 0 10px rgba(20,241,149,.4)}}
.lg-layer-head.flow b{{color:#8EF9FF;text-shadow:0 0 10px rgba(142,249,255,.35)}}
.lg-rail-viewport{{position:relative;overflow-x:auto;padding:26px 10px 22px;scrollbar-width:thin;scrollbar-color:rgba(20,241,149,.35) transparent}}
.lg-rail{{display:flex;align-items:center;min-width:900px}}
.lg-stage-wrap{{width:112px;flex:0 0 112px;text-align:center;position:relative}}
/* CLIP FIX 20260718: fixed heights + overflow:hidden were slicing the last line
   of node captions in half. Captions now clamp at whole lines (2 above / 3 below)
   with auto height — text is either fully shown or fully elided, never bisected. */
.lg-above{{min-height:2.6em;font:600 0.62rem/1.3 'Share Tech Mono',monospace;color:#8EF9FF;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;align-items:flex-end}}
.lg-below{{margin-top:7px;min-height:2.6em;font:500 0.60rem/1.3 'Share Tech Mono',monospace;color:rgba(184,218,211,.50);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.lg-stage{{position:relative;width:66px;height:66px;margin:8px auto;border-radius:50%;display:grid;place-items:center;border:1px solid rgba(142,249,255,.20);background:radial-gradient(circle,rgba(142,249,255,.10),rgba(2,8,11,.76) 62%);box-shadow:0 0 0 7px rgba(142,249,255,.018),inset 0 0 18px rgba(142,249,255,.06)}}
.lg-stage .lg-orbit{{position:absolute;inset:-6px;border-radius:50%;border:1px dashed rgba(142,249,255,.20);animation:lgSpin 13s linear infinite}}
.lg-stage .lg-core{{font:700 1.15rem/1 'Orbitron',sans-serif;color:#667B7A}}
.lg-stage .lg-index{{position:absolute;right:-3px;bottom:-2px;padding:3px 5px;border-radius:10px;background:#060A0D;border:1px solid rgba(142,249,255,.15);font:600 0.60rem/1 'Share Tech Mono',monospace;color:#63817D}}
.lg-stage.pass{{border-color:rgba(20,241,149,.75);background:radial-gradient(circle,rgba(20,241,149,.24),rgba(4,17,15,.80) 62%);box-shadow:0 0 24px rgba(20,241,149,.24),0 0 0 7px rgba(20,241,149,.035),inset 0 0 22px rgba(20,241,149,.13)}}
.lg-stage.pass .lg-orbit{{border-color:rgba(20,241,149,.55)}}.lg-stage.pass .lg-core{{color:#14F195;text-shadow:0 0 14px rgba(20,241,149,.85)}}.lg-stage.pass .lg-index{{color:#14F195;border-color:rgba(20,241,149,.35)}}
.lg-stage.block{{border-color:rgba(255,7,58,.72);background:radial-gradient(circle,rgba(255,7,58,.20),rgba(18,4,10,.82) 62%);box-shadow:0 0 22px rgba(255,7,58,.23),0 0 0 7px rgba(255,7,58,.025)}}.lg-stage.block .lg-orbit{{border-color:rgba(255,7,58,.38)}}.lg-stage.block .lg-core{{color:#FF4568;text-shadow:0 0 12px rgba(255,7,58,.8)}}
/* FINAL FIRE is the one node allowed to burn gold — the earned apex. */
.lg-stage.final-open{{border-color:rgba(255,215,0,.85);background:radial-gradient(circle,rgba(255,215,0,.26),rgba(20,14,2,.82) 62%);box-shadow:0 0 30px rgba(255,215,0,.36),0 0 0 8px rgba(255,215,0,.05),inset 0 0 24px rgba(255,215,0,.16);animation:lgFirePulse 1.6s ease-in-out infinite}}
.lg-stage.final-open .lg-core{{color:#FFD700;text-shadow:0 0 18px #FFD700}}.lg-stage.final-open .lg-orbit{{border-color:rgba(255,215,0,.60)}}
.lg-stage.final-wait{{border-color:rgba(255,215,0,.28)}}.lg-stage.final-wait .lg-core{{color:#8A7A3A}}
.lg-stage.final-block{{border-color:rgba(255,7,58,.72)}}.lg-stage.final-block .lg-core{{color:#FF4568;text-shadow:0 0 12px rgba(255,7,58,.8)}}
.lg-stage-name{{margin-top:10px;font:700 0.64rem/1 'Orbitron',sans-serif;letter-spacing:.09em;color:#DDF8F2}}
.lg-link{{position:relative;flex:1;height:2px;min-width:16px;margin:-12px -4px 0;background:rgba(142,249,255,.10);overflow:visible}}
.lg-link i{{position:absolute;inset:0;background:linear-gradient(90deg,#14F195,#8EF9FF,#9945FF);transform:scaleX(.18);transform-origin:left;opacity:.45}}
.lg-link.pass i,.lg-link.final-open i{{transform:scaleX(1);opacity:.95;box-shadow:0 0 12px rgba(20,241,149,.7);animation:lgFlow 1.8s ease-in-out infinite}}
.lg-link.block{{background:repeating-linear-gradient(90deg,rgba(255,7,58,.45) 0 5px,transparent 5px 10px)}}.lg-link.block i{{display:none}}
/* observation-only candidate flow chips */
.lg-flow-strip{{display:flex;gap:7px;flex-wrap:wrap;padding:8px 2px 2px}}
.lg-flow-chip{{border:1px solid rgba(142,249,255,.15);border-radius:999px;padding:6px 11px;font:500 0.58rem/1.3 'Share Tech Mono',monospace;color:#55707C;background:rgba(142,249,255,.02)}}
.lg-flow-chip b{{display:block;font:700 0.56rem/1 'Orbitron',sans-serif;letter-spacing:.14em;color:#5E7A86;margin-bottom:3px}}
.lg-flow-chip.on{{border-color:rgba(142,249,255,.42);color:#8EF9FF;box-shadow:0 0 12px rgba(142,249,255,.10)}}
.lg-flow-chip.on b{{color:#8EF9FF}}
.lg-live-pulse{{position:relative;margin-top:10px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;padding:11px 13px;border:1px solid rgba(255,215,0,.16);border-radius:14px;background:rgba(255,215,0,.025)}}
.lg-live-pulse em{{width:9px;height:9px;border-radius:50%;background:{_vcol};box-shadow:0 0 16px currentColor;animation:lgPulse 1.4s ease-in-out infinite}}.lg-live-pulse strong{{font:700 0.66rem/1 'Orbitron',sans-serif;letter-spacing:.15em;color:#EAFEF9}}.lg-live-pulse span{{font:500 0.66rem/1.35 'Share Tech Mono',monospace;color:#779A94;text-align:right}}
.lg-rhythm{{position:relative;margin-top:13px;padding-top:12px;border-top:1px solid rgba(153,69,255,.12);display:grid;grid-template-columns:minmax(280px,1fr) repeat(4,minmax(80px,.18fr));gap:12px;align-items:center}}
.lg-ribbon{{display:flex;gap:5px;overflow:auto;padding-bottom:3px}}.lg-ribbon span{{white-space:nowrap;border-radius:999px;padding:5px 8px;font:600 0.62rem/1 'Share Tech Mono',monospace}}.rh-gold{{color:#FFD700;border:1px solid rgba(255,215,0,.25);background:rgba(255,215,0,.035)}}.rh-be{{color:#8EF9FF;border:1px solid rgba(142,249,255,.18);background:rgba(142,249,255,.025)}}.rh-loss{{color:#FF4568;border:1px solid rgba(255,7,58,.20);background:rgba(255,7,58,.028)}}.rh-empty{{color:#536963!important}}
.lg-stat label{{display:block;font:500 0.60rem/1.2 'Share Tech Mono',monospace;letter-spacing:.11em;color:#58716C}}.lg-stat b{{display:block;margin-top:4px;font:700 .72rem/1 'Orbitron',sans-serif;color:#DFF7F2}}
.lg-forensics{{position:relative;margin-top:12px;padding:10px 12px;border:1px solid rgba(142,249,255,.10);border-radius:12px;background:rgba(2,10,12,.30)}}.lg-fx-head{{font:600 0.62rem/1 'Orbitron',sans-serif;letter-spacing:.18em;color:#8EF9FF;opacity:.66;margin-bottom:7px}}.lg-fx-stats{{display:flex;flex-wrap:wrap;gap:6px}}.lg-fx-stats span{{border-radius:999px;padding:4px 7px;font:600 0.60rem/1 'Share Tech Mono',monospace}}.fx-ok{{color:#14F195;border:1px solid rgba(20,241,149,.20)}}.fx-warn{{color:#FFD700;border:1px solid rgba(255,215,0,.24)}}.fx-crit{{color:#FF4568;border:1px solid rgba(255,7,58,.30)}}.lg-fx-note{{margin-top:7px;font:500 0.60rem/1.4 'Share Tech Mono',monospace;color:#55706A}}
.lg-foot{{position:relative;margin-top:10px;padding-bottom:2px;font:500 0.60rem/1.5 'Share Tech Mono',monospace;color:#435E58}}
.lgp-shell{{position:relative;margin:14px 0 6px;padding:11px 13px;border:1px solid rgba(255,215,0,.20);border-radius:14px;background:linear-gradient(90deg,rgba(255,215,0,.035),rgba(153,69,255,.02),transparent)}}
.lgp-head{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font:700 0.64rem/1 'Orbitron',sans-serif;letter-spacing:.18em;color:#FFD700;margin-bottom:9px}}
.lgp-state{{font:600 0.60rem/1.3 'Share Tech Mono',monospace;letter-spacing:.08em;color:#CFE9DF}}
.lgp-rail{{display:flex;align-items:stretch;gap:6px;overflow-x:auto;padding-bottom:2px}}
.lgp-stage{{flex:1 1 0;min-width:132px;border:1px solid rgba(142,249,255,.14);border-radius:10px;padding:8px 10px;background:rgba(3,9,10,.42)}}
.lgp-stage b{{display:block;font:700 0.62rem/1 'Orbitron',sans-serif;letter-spacing:.12em;color:#8BA6A0}}
.lgp-stage span{{display:block;margin-top:5px;font:500 0.58rem/1.35 'Share Tech Mono',monospace;color:#5E7A74}}
.lgp-pips{{display:flex;gap:4px;margin-top:6px}}
.lgp-pips i{{width:9px;height:9px;border-radius:50%;border:1px solid rgba(255,215,0,.35);background:transparent}}
.lgp-pips i.on{{background:#FFD700;box-shadow:0 0 9px rgba(255,215,0,.75)}}
.lgp-pips i.zero{{border-style:dashed;opacity:.4}}
.lgp-stage.lgp-done{{border-color:rgba(20,241,149,.45)}}.lgp-stage.lgp-done b{{color:#14F195}}
.lgp-stage.lgp-now{{border-color:rgba(255,215,0,.75);background:rgba(255,215,0,.05);box-shadow:0 0 18px rgba(255,215,0,.14),inset 0 0 14px rgba(255,215,0,.06)}}.lgp-stage.lgp-now b{{color:#FFD700;text-shadow:0 0 10px rgba(255,215,0,.6)}}
.lgp-arrow{{align-self:center;font:700 1rem/1 'Orbitron',sans-serif;color:#3F5650}}.lgp-arrow.lgp-done{{color:#14F195}}.lgp-arrow.lgp-now{{color:#FFD700}}
.lgp-note{{margin-top:8px;font:500 0.58rem/1.5 'Share Tech Mono',monospace;color:#55706A}}
@keyframes lgSpin{{to{{transform:rotate(360deg)}}}}@keyframes lgFlow{{0%,100%{{opacity:.55}}50%{{opacity:1}}}}@keyframes lgPulse{{0%,100%{{transform:scale(.75);opacity:.55}}50%{{transform:scale(1.25);opacity:1}}}}
@keyframes lgFirePulse{{0%,100%{{box-shadow:0 0 26px rgba(255,215,0,.30),0 0 0 8px rgba(255,215,0,.05)}}50%{{box-shadow:0 0 42px rgba(255,215,0,.52),0 0 0 10px rgba(255,215,0,.08)}}}}
@media(max-width:900px){{.lg-head{{grid-template-columns:1fr}}.lg-score{{text-align:left}}.lg-rhythm{{grid-template-columns:repeat(2,1fr)}}.lg-ribbon{{grid-column:1/-1}}.lg-charge{{grid-template-columns:auto 1fr}}.lg-charge-stars{{grid-column:1/-1}}}}
@media(max-width:560px){{.lg-shell{{padding:16px 12px 18px;border-radius:18px}}.lg-title{{font-size:.98rem}}.lg-verdict{{align-items:flex-start;flex-direction:column}}.lg-verdict code{{text-align:left}}.lg-live-pulse{{grid-template-columns:auto 1fr}}.lg-live-pulse span{{grid-column:1/-1;text-align:left}}.lg-anticipate{{flex-wrap:wrap}}.lg-anticipate .lg-adv{{margin-left:0}}}}
</style>
<div id="live-gate-constellation" class="lg-shell">
 <div class="lg-head"><div><div class="lg-kicker">✦ SENTINUITY SOVEREIGN STARFIELD{_help_glyph}</div><div class="lg-title">CAPITAL ALIGNMENT INSTRUMENT</div><div class="lg-sub">Every funded fire is shown as an observable alignment, never a hidden score. Two layers must align before real capital moves. The TECHNICAL UNDERLAY is the mechanical circuit — green closes each gate, red opens the circuit at the authoritative blocker. The PATTERN OVERLAY is the behavioural charge — gold marks earned confirmations and only ever sets size. Cyan is observed candidate flow, violet is intelligence. FIRE = circuit closed AND pattern armed.</div></div><div class="lg-score"><b>{readiness}%</b><span>{passed}/{len(nodes)} ALIGNED<br>{_safe(verdict)}</span></div></div>
 <div class="lg-verdict"><strong>{_safe(verdict)}</strong><code>PRIMARY BLOCKER :: {_safe(blocker)}</code></div>
 <div class="lg-anticipate"><b>{_safe(anticipation)}</b><span class="lg-adv">ADVISORY · EXECUTOR VERDICT IS FINAL</span></div>
 <div class="lg-charge"><label>STAR ALIGNMENT {charge}%</label><div class="lg-charge-track"><div class="lg-charge-fill"></div></div><div class="lg-charge-stars">{stars}</div></div>
 <div class="lg-layer-head tech"><b>⚙ TECHNICAL UNDERLAY — HARD CIRCUIT</b><span>{hard_passed}/{hard_total} gates closed · every gate is absolute · ends at the executor's FINAL FIRE star</span></div>
 <div class="lg-rail-viewport"><div class="lg-rail">{hard_rail}</div></div>
 {ladder_html}
 <div class="lg-layer-head flow"><b>◌ CANDIDATE COMET FLOW — OBSERVATION ONLY</b><span>10-minute telemetry · descriptive, never a gate</span></div>
 <div class="lg-flow-strip">{flow_chips}</div>
 <div class="lg-live-pulse"><em></em><strong>{pulse_text}</strong><span>NEXT EVENT :: {_safe(next_event)}</span></div>
 <div class="lg-rhythm"><div class="lg-ribbon">{rhythm_cells}</div><div class="lg-stat"><label>GOLD : BE</label><b>{rhythm['gold']} : {rhythm['breakeven']}</b></div><div class="lg-stat"><label>POSITIVE CYCLES</label><b>{rhythm['cycles']}</b></div><div class="lg-stat"><label>NEXT-TRADE EST.</label><b>{probability_text}</b></div><div class="lg-stat"><label>20M SAMPLE</label><b>{rhythm['sample']}</b></div></div>
 <div class="lg-forensics"><div class="lg-fx-head">SETTLEMENT TRUTH</div><div class="lg-fx-stats"><span class="{'fx-warn' if int(recovery.get('pending') or 0) else 'fx-ok'}">PENDING {recovery.get('pending',0)}</span><span class="{'fx-warn' if int(recovery.get('confirmed_unresolved') or 0) else 'fx-ok'}">UNRESOLVED {recovery.get('confirmed_unresolved',0)}</span><span class="{'fx-crit' if (int(recovery.get('manual_intervention') or 0) + int(recovery.get('positions_manual') or 0)) else 'fx-ok'}">MANUAL {int(recovery.get('manual_intervention') or 0) + int(recovery.get('positions_manual') or 0)}</span><span class="fx-ok">SETTLED {recovery.get('resolved',0)}</span></div><div class="lg-fx-note">Executor: {_safe(contract.get('authored_by') or 'not published')} · decision age {_safe(contract.get('age_sec'))}s · {_safe(contract.get('blocker') or 'healthy')}</div></div>
 <div class="lg-foot">Gold threshold ≥ {rhythm['gold_min']:.1f}% · the charge meter and next-candidate banner are advisory readings of the truths above and cannot bypass wallet, route, freshness, impact, sellability, reconciliation or daily-loss guards · source {_safe(rhythm['source'])}</div>
</div>''', unsafe_allow_html=True)
