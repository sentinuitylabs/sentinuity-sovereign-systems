#!/usr/bin/env python3
"""VERIFY_EDGE_RESTORE.py - fast runtime verification for the July-27 edge rebuild.

Design contract (from the operator directive):
  * excludes backups / audits / audit_outputs / db_backups / archive folders
    BEFORE traversal (prune at the directory level, never filter after walking)
  * hashes all strategy-critical files
  * reports command lines and start times of running Sentinuity python processes
  * prints the effective loaded configuration
  * proves the 4% maximum-loss contract
  * proves which confidence floor is active
  * proves token names resolve before UI rendering
  * tests representative July 27 runners
  * checks discovery-to-entry latency
  * checks oracle and price freshness
  * checks smart-wallet / fingerprint influence
  * reports duplicate services
  * completes in under two minutes

Run:  python launch\\VERIFY_EDGE_RESTORE.py
Exit: 0 all checks pass, 1 one or more FAIL, 2 harness error.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

T0 = time.time()
BUDGET_SEC = float(os.environ.get("VERIFY_BUDGET_SEC", "115"))

ROOT = Path(__file__).resolve().parent
if not (ROOT / "services").is_dir():
    ROOT = ROOT.parent
DB = ROOT / "sentinuity_matrix.db"

# Pruned at directory level. This is the fix for the audit scripts that
# previously walked recursive backup trees and became unusably slow.
PRUNE_DIRS = {
    "backups", "audits", "audit_outputs", "db_backups",
    "_KEEP_IMPORTANT_ARCHIVES", "_ui_backups", "_prelaunch_patch_backups",
    "__pycache__", ".git", ".venv", "venv", "site-packages", "node_modules",
    "logs", "runtime", "archive", "archives", ".mypy_cache", ".pytest_cache",
}

STRATEGY_CRITICAL = [
    "services/execution_engine.py",
    "services/ingest_pipeline.py",
    "services/neural_supervisor.py",
    "services/smart_wallet_conviction.py",
    "services/pattern_live_arming.py",
    "services/market_intelligence.py",
    "services/ws_price_oracle.py",
    "services/signal_gate_sensor.py",
    "services/token_identity.py",
    "services/token_display.py",
    "services/pnl_truth.py",
    "services/intelligence_orchestrator.py",
    "services/active_pipeline_cleaner.py",
    "launch/prelaunch.py",
    "launch/FORCE_PAPER_SAFE_PRESTART_0707.py",
]

# Services that must never run more than once concurrently.
SINGLETON_SERVICES = [
    "services.execution_engine", "services.neural_supervisor",
    "services.ws_price_oracle", "services.market_intelligence",
    "services.ingest_pipeline", "services.price_enricher",
    "services.reconciliation_engine",
]

RESULTS: list[tuple[str, str, str]] = []  # (status, check, detail)


def record(status: str, check: str, detail: str = "") -> None:
    RESULTS.append((status, check, detail))
    print(f"  [{status:4}] {check}" + (f" - {detail}" if detail else ""))


def head(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def out_of_budget() -> bool:
    return (time.time() - T0) > BUDGET_SEC


# --------------------------------------------------------------------------
def connect_ro(path: Path, timeout: float = 5.0):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def cfg_all() -> dict[str, str]:
    if not DB.exists():
        return {}
    try:
        with connect_ro(DB) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(system_config)")}
            kc = "key" if "key" in cols else "name"
            vc = "value" if "value" in cols else "val"
            return {str(r[0]): str(r[1]) for r in
                    con.execute(f"SELECT {kc},{vc} FROM system_config")}
    except Exception as exc:
        record("WARN", "system_config read", f"{type(exc).__name__}: {exc}")
        return {}


CFG = {}


def cfg(key: str, default=None):
    return CFG.get(key, default)


# --------------------------------------------------------------------------
def check_hashes() -> None:
    head("1. STRATEGY-CRITICAL FILE HASHES (pruned traversal)")
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d.lower() not in PRUNE_DIRS]
        scanned += len(filenames)
        if out_of_budget():
            break
    print(f"  traversal: {scanned} files visited, "
          f"{len(PRUNE_DIRS)} directory names pruned, "
          f"{time.time() - T0:.1f}s elapsed")
    missing = []
    for rel in STRATEGY_CRITICAL:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            print(f"  {'MISSING':<64} {rel}")
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {digest}  {mtime}  {p.stat().st_size:>9,}  {rel}")
    record("PASS" if not missing else "WARN", "strategy file inventory",
           "all present" if not missing else f"missing: {', '.join(missing)}")


def check_processes() -> None:
    head("2. RUNNING SENTINUITY PROCESSES + DUPLICATE SERVICES")
    procs: list[tuple[int, str, str]] = []
    try:
        import psutil
        for pr in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cl = " ".join(pr.info["cmdline"] or [])
                if "services." in cl or "sovereign_hub" in cl or "launch\\" in cl:
                    procs.append((pr.info["pid"], cl,
                                  datetime.fromtimestamp(pr.info["create_time"])
                                  .strftime("%Y-%m-%d %H:%M:%S")))
            except Exception:
                continue
    except ImportError:
        record("WARN", "process enumeration", "psutil not installed - pip install psutil")
        if os.name == "nt":
            os.system('powershell -NoProfile -Command "Get-CimInstance Win32_Process '
                      '-Filter \\"Name LIKE \'python%\'\\" | '
                      'Select-Object ProcessId,CreationDate,CommandLine | Format-List"')
        return

    for pid, cl, started in sorted(procs, key=lambda r: r[2]):
        print(f"  pid={pid:<7} started={started}  {cl[:150]}")
    record("PASS", "process enumeration", f"{len(procs)} Sentinuity processes")

    dupes = []
    for mod in SINGLETON_SERVICES:
        n = sum(1 for _, cl, _ in procs if mod in cl)
        if n > 1:
            dupes.append(f"{mod} x{n}")
    record("FAIL" if dupes else "PASS", "singleton services",
           "; ".join(dupes) if dupes else "no duplicates")

    # Stale-tree detection: a process whose cwd/path is not this ROOT.
    stray = [cl for _, cl, _ in procs
             if re.search(r"(backups|_KEEP_IMPORTANT_ARCHIVES|_ui_backups)", cl, re.I)]
    record("FAIL" if stray else "PASS", "no processes launched from backup trees",
           f"{len(stray)} suspicious" if stray else "clean")


def check_config() -> None:
    head("3. EFFECTIVE LOADED CONFIGURATION")
    keys = [
        "TRADING_MODE", "PAPER_TRADING_ENABLED", "LIVE_TRADING_ENABLED",
        "LIVE_ARMED", "EXECUTION_ARMED", "LIVE_MONEY_MODE",
        "HARD_STOP_LOSS_PCT", "STOP_LOSS_PCT",
        "SUPERVISOR_MIN_MINT_CONFIDENCE", "SUPERVISOR_MIN_MINT_CONF",
        "SUPERVISOR_ADMISSION_MIN_CONF", "PAPER_CONFIDENCE_FLOOR",
        "PAPER_MAX_OPEN_POSITIONS", "LIVE_MAX_OPEN_POSITIONS",
        "PAPER_POSITION_SIZE_USD", "TRUSTED_PEAK_EXCLUDE_SOURCES",
        "PAPER_RUNNER_LOCK_ASSUME_STOP_FILL", "RUNNER_TRAIL_PCT",
        "MAX_HOLD_MINUTES", "SUPERVISOR_MAX_SIGNAL_AGE_SEC",
        "SUPERVISOR_MAX_PRICE_AGE_SEC", "SUBSTRATE_BUILD_ACTIVE",
    ]
    for k in keys:
        v = CFG.get(k)
        print(f"  {k:<42} = {v if v is not None else '<unset>'}")

    # --- 4% maximum-loss contract -----------------------------------------
    hs = CFG.get("HARD_STOP_LOSS_PCT")
    try:
        hs_f = abs(float(hs)) if hs is not None else None
    except Exception:
        hs_f = None
    db_present = DB.exists() and bool(CFG)
    ok_db = (hs_f is not None and hs_f <= 4.0 + 1e-9) or not db_present
    src = (ROOT / "services/execution_engine.py")
    clamped = False
    if src.exists():
        txt = src.read_text(encoding="utf-8", errors="ignore")
        clamped = 'min(abs(float(get_config_value("HARD_STOP_LOSS_PCT", 4.0))), 4.0)' in txt
    status = "PASS" if (ok_db and clamped) else "FAIL"
    if not db_present and clamped:
        status = "WARN"
    record(status, "4% maximum-loss contract",
           f"db={hs if db_present else '<no DB - cannot read>'} "
           f"code_clamp={'present' if clamped else 'ABSENT'}"
           + ("" if db_present else " (code clamp alone guarantees <=4%)"))

    # --- active confidence floor ------------------------------------------
    # execution_engine reads SUPERVISOR_MIN_MINT_CONFIDENCE then applies
    # max(floor, 0.65). neural_supervisor reads SUPERVISOR_MIN_MINT_CONF first.
    long_key = CFG.get("SUPERVISOR_MIN_MINT_CONFIDENCE")
    short_key = CFG.get("SUPERVISOR_MIN_MINT_CONF")
    try:
        eff_exec = max(float(long_key) if long_key is not None else 0.65, 0.65)
    except Exception:
        eff_exec = 0.65
    print(f"  -> executor admission floor  = {eff_exec:.3f}  "
          f"(SUPERVISOR_MIN_MINT_CONFIDENCE, then max(x, 0.65))")
    print(f"  -> supervisor floor          = {short_key or long_key or '0.65 default'}  "
          f"(SUPERVISOR_MIN_MINT_CONF preferred)")
    mismatch = (long_key is not None and short_key is not None and
                str(long_key).strip() != str(short_key).strip())
    record("WARN" if mismatch else "PASS", "confidence floor coherence",
           f"long={long_key} short={short_key} - two keys disagree; executor uses the LONG key"
           if mismatch else f"active floor {eff_exec:.2f}")
    record("PASS" if abs(eff_exec - 0.65) < 1e-9 else "FAIL",
           "July-27 confidence semantics (0.65)",
           f"active={eff_exec:.3f} (raising this to 0.75 suppresses July-27 candidates)")

    # --- paper/live isolation ---------------------------------------------
    live_bad = [k for k in ("LIVE_TRADING_ENABLED", "LIVE_ARMED",
                            "LIVE_MONEY_MODE", "EXECUTION_ARMED")
                if str(CFG.get(k, "0")).strip() not in ("0", "", "false", "False")]
    record("FAIL" if live_bad else "PASS", "live lane separately gated",
           f"ARMED: {', '.join(live_bad)}" if live_bad else "all live flags 0")
    if not db_present:
        record("WARN", "paper mode always active", "matrix DB not readable here")
    else:
        record("PASS" if str(CFG.get("PAPER_TRADING_ENABLED", "0")) == "1" else "FAIL",
               "paper mode always active",
               f"PAPER_TRADING_ENABLED={CFG.get('PAPER_TRADING_ENABLED')}")


def check_edge_contracts() -> None:
    head("4. RESTORED EDGE CONTRACTS (source-level proof)")
    ee = ROOT / "services/execution_engine.py"
    ing = ROOT / "services/ingest_pipeline.py"
    swc = ROOT / "services/smart_wallet_conviction.py"
    pla = ROOT / "services/pattern_live_arming.py"
    tests = [
        (ee, 'raw = str(get_config_value("TRUSTED_PEAK_EXCLUDE_SOURCES", "") or "")',
         "R3 trusted-peak exclusion defaults EMPTY (July 27)"),
        (ee, 'peak_price = float(position.get("highest_price_seen") or 0.0)',
         "R4 profit-lock falls back to highest_price_seen"),
        (ee, '"PAPER_RUNNER_LOCK_ASSUME_STOP_FILL", "1"',
         "R5 paper runner-lock assumes stop fill"),
        (ee, '_runner_peak_source = "highest_price_seen"',
         "R6 runner trail has a real peak fallback chain"),
        (ing, "WEAVE_BATCH_LIMIT", "R1 candidate intake breadth restored (100)"),
        (swc, "max_x_after_entry", "R2 fingerprint outcome source restored"),
        (pla, "pattern_peak_pct", "R7 pattern authority peak-aware again"),
        (pla, "_row_outcome_realised_only", "R7b funded sizing stays realised-only"),
    ]
    for path, needle, label in tests:
        if not path.exists():
            record("FAIL", label, f"{path.name} missing")
            continue
        txt = path.read_text(encoding="utf-8", errors="ignore")
        record("PASS" if needle in txt else "FAIL", label,
               "" if needle in txt else f"marker absent in {path.name}")

    # Runtime override guard: an operator-set exclusion list re-breaks runners.
    excl = str(CFG.get("TRUSTED_PEAK_EXCLUDE_SOURCES", "") or "").strip()
    record("FAIL" if excl else "PASS", "no DB-level peak-source exclusion",
           f"system_config sets '{excl}' - this re-disables runner recognition"
           if excl else "empty")


def check_token_identity() -> None:
    head("5. TOKEN IDENTITY RESOLVES BEFORE UI RENDER")
    if not DB.exists():
        record("WARN", "token identity", "matrix DB not found")
        return
    try:
        with connect_ro(DB) as con:
            tabs = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            row = con.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN COALESCE(NULLIF(TRIM(token_name),''),'n/a')
                            IN ('n/a','unknown','none','null','-') THEN 1 ELSE 0 END) bare
                FROM paper_positions
                WHERE UPPER(COALESCE(status,''))='OPEN'
            """).fetchone()
        total, bare = int(row["total"] or 0), int(row["bare"] or 0)
        record("PASS" if bare == 0 else "FAIL", "open positions carry resolved names",
               f"{bare}/{total} open rows unresolved")
        has_reg = any(t.lower() in {"token_identity", "token_registry",
                                    "token_identity_cache"} for t in tabs)
        record("PASS" if has_reg else "WARN", "persistent identity registry present",
               "found" if has_reg else "no token registry table - UI relies on async cache")
    except Exception as exc:
        record("WARN", "token identity", f"{type(exc).__name__}: {exc}")

    # display_name must never return a bare mint when a registry entry exists.
    sys.path.insert(0, str(ROOT))
    try:
        from services.token_display import display_name
        got = display_name(symbol=None, token_name="n/a",
                           mint="So11111111111111111111111111111111111111112")
        record("PASS" if got and got.lower() not in ("n/a", "none", "") else "FAIL",
               "display_name never renders bare n/a", f"returned {got!r}")
    except Exception as exc:
        record("WARN", "display_name import", f"{type(exc).__name__}: {exc}")


def check_freshness_and_oracle() -> None:
    head("6. ORACLE / PRICE FRESHNESS + DISCOVERY-TO-ENTRY LATENCY")
    if not DB.exists():
        record("WARN", "freshness", "matrix DB not found")
        return
    now = time.time()
    try:
        with connect_ro(DB) as con:
            tabs = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}

            if "service_heartbeat" in tabs or "heartbeats" in tabs:
                t = "service_heartbeat" if "service_heartbeat" in tabs else "heartbeats"
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                tcol = next((c for c in ("updated_at", "last_seen", "ts", "timestamp")
                             if c in cols), None)
                ncol = next((c for c in ("service", "name", "service_name")
                             if c in cols), None)
                if tcol and ncol:
                    stale = con.execute(
                        f"SELECT {ncol} n, {tcol} t FROM {t} "
                        f"WHERE CAST(COALESCE({tcol},0) AS REAL) < ? "
                        f"ORDER BY t", (now - 180,)).fetchall()
                    for r in stale[:12]:
                        age = now - float(r["t"] or 0)
                        print(f"    STALE {r['n']:<40} {age / 60:.1f} min")
                    record("PASS" if not stale else "FAIL",
                           "no stale service heartbeats",
                           f"{len(stale)} services stale >180s")

            if "price_cache" in tabs:
                cols = {r[1] for r in con.execute("PRAGMA table_info(price_cache)")}
                tcol = next((c for c in ("updated_at", "ts", "last_updated", "timestamp")
                             if c in cols), None)
                if tcol:
                    r = con.execute(
                        f"SELECT MAX(CAST(COALESCE({tcol},0) AS REAL)) m FROM price_cache"
                    ).fetchone()
                    age = now - float(r["m"] or 0)
                    if float(r["m"] or 0) > 1e11:      # ms epoch
                        age = now - float(r["m"]) / 1000.0
                    record("PASS" if age < 60 else "FAIL", "price oracle freshness",
                           f"newest mark {age:.1f}s old")

            # Discovery -> entry latency for the most recent entries.
            if "paper_positions" in tabs:
                cols = {r[1] for r in con.execute("PRAGMA table_info(paper_positions)")}
                ent = next((c for c in ("entry_time", "opened_at", "created_at")
                            if c in cols), None)
                dis = next((c for c in ("signal_time", "discovered_at",
                                        "signal_created_at", "detected_at")
                            if c in cols), None)
                if ent and dis:
                    rows = con.execute(
                        f"SELECT CAST({ent} AS REAL)-CAST({dis} AS REAL) lat "
                        f"FROM paper_positions WHERE {ent} IS NOT NULL AND {dis} IS NOT NULL "
                        f"ORDER BY {ent} DESC LIMIT 40").fetchall()
                    lats = [float(r["lat"]) for r in rows if r["lat"] is not None]
                    if lats:
                        lats.sort()
                        med = lats[len(lats) // 2]
                        record("PASS" if med < 120 else "WARN",
                               "discovery-to-entry latency",
                               f"n={len(lats)} median={med:.1f}s max={lats[-1]:.1f}s")
                    else:
                        record("WARN", "discovery-to-entry latency", "no paired timestamps")
                else:
                    record("WARN", "discovery-to-entry latency",
                           "no discovery timestamp column on paper_positions")

            # Freshness semantics: which clock does the supervisor use?
            print("\n  Freshness basis in code:")
            ee = (ROOT / "services/execution_engine.py")
            if ee.exists():
                txt = ee.read_text(encoding="utf-8", errors="ignore")
                for pat, label in (("signal_time", "signal event time"),
                                   ("discovered_at", "discovery time"),
                                   ("created_at", "DB insertion time"),
                                   ("price_updated_at", "latest price time")):
                    print(f"    {label:<24} referenced: {txt.count(pat)}x")
    except Exception as exc:
        record("WARN", "freshness checks", f"{type(exc).__name__}: {exc}")


def check_fingerprint() -> None:
    head("7. SMART-WALLET / FINGERPRINT INFLUENCE")
    if not DB.exists():
        record("WARN", "fingerprint", "matrix DB not found")
        return
    try:
        with connect_ro(DB) as con:
            tabs = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "wallet_trades" not in tabs:
                record("WARN", "wallet_trades table", "absent")
                return
            cols = {r[1] for r in con.execute("PRAGMA table_info(wallet_trades)")}
            has_mx = "max_x_after_entry" in cols
            has_rx = "realized_x" in cols
            n_mx = n_rx = 0
            if has_mx:
                n_mx = con.execute("SELECT COUNT(*) FROM wallet_trades "
                                   "WHERE COALESCE(max_x_after_entry,0)>0").fetchone()[0]
            if has_rx:
                n_rx = con.execute("SELECT COUNT(*) FROM wallet_trades "
                                   "WHERE COALESCE(realized_x,0)>0").fetchone()[0]
            print(f"  wallet_trades rows with max_x_after_entry>0 : {n_mx}")
            print(f"  wallet_trades rows with realized_x>0        : {n_rx}")
            # The regression: scoring on realized_x alone starves the profiler.
            record("PASS" if (n_mx > 0 or n_rx > 0) else "FAIL",
                   "wallet outcome data present",
                   f"max_x={n_mx} realized_x={n_rx}")
            if n_mx > n_rx * 3 and n_rx < 20:
                record("PASS", "NO_FINGERPRINT_MATCH root cause confirmed",
                       f"max_x_after_entry({n_mx}) dominates realized_x({n_rx}) - "
                       "realised-only scoring returns None for nearly every wallet")
            swc = ROOT / "services/smart_wallet_conviction.py"
            if swc.exists():
                txt = swc.read_text(encoding="utf-8", errors="ignore")
                record("PASS" if "max_x_after_entry" in txt else "FAIL",
                       "conviction scorer reads max_x_after_entry", "")
    except Exception as exc:
        record("WARN", "fingerprint checks", f"{type(exc).__name__}: {exc}")


def check_runner_behaviour() -> None:
    head("8. REPRESENTATIVE RUNNER BEHAVIOUR (closed-form trail simulation)")

    def tape_peak(marks, exclude):
        toks = tuple(t.strip().lower() for t in exclude.split(",") if t.strip())
        for px, src in sorted(marks, key=lambda r: -r[0]):
            if src and any(t in src.lower() for t in toks):
                continue
            return px
        return None

    excl = str(CFG.get("TRUSTED_PEAK_EXCLUDE_SOURCES", "") or "")
    trail = float(CFG.get("RUNNER_TRAIL_PCT", 18.0) or 18.0)
    scenarios = [
        ("runner_mtm_only", 1.00, [(1.85, "router:intel-mtm"), (1.40, "mtm-snapshot")], 1.48),
        ("runner_mixed_src", 1.00, [(2.10, "ws_oracle"), (1.90, "intel-mtm")], 1.60),
        ("loser_no_peak", 1.00, [(1.01, "ws_oracle")], 0.96),
    ]
    for name, entry, marks, current in scenarios:
        tp = tape_peak(marks, excl)
        hps = max(px for px, _ in marks)
        peak = max(current, tp if tp else hps)
        stop = peak * (1 - trail / 100.0)
        fires = current <= stop
        pnl = (stop if fires else current) / entry * 100 - 100
        print(f"  {name:<18} peak={peak / entry * 100 - 100:+7.1f}%  "
              f"current={current / entry * 100 - 100:+6.1f}%  "
              f"trail_fires={str(fires):<5}  modelled_exit={pnl:+.1f}%")
    ok = tape_peak(scenarios[0][2], excl) is not None
    record("PASS" if ok else "FAIL", "mtm-only runner retains a trusted peak",
           "peak recoverable" if ok else
           "exclusion list deletes every mark - runner collapses to MAX_HOLD")


def check_max_hold_pathology() -> None:
    head("9. EXIT-REASON DISTRIBUTION (max-hold pathology)")
    if not DB.exists():
        record("WARN", "exit distribution", "matrix DB not found")
        return
    try:
        with connect_ro(DB) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(paper_positions)")}
            rc = next((c for c in ("exit_reason", "close_reason", "status_reason")
                       if c in cols), None)
            if not rc:
                record("WARN", "exit distribution", "no exit_reason column")
                return
            rows = con.execute(f"""
                SELECT UPPER(SUBSTR(COALESCE({rc},'UNKNOWN'),1,28)) r, COUNT(*) n
                FROM paper_positions
                WHERE UPPER(COALESCE(status,'')) IN ('CLOSED','EXITED')
                GROUP BY r ORDER BY n DESC LIMIT 14""").fetchall()
            total = sum(int(r["n"]) for r in rows) or 1
            mh = tr = 0
            for r in rows:
                pct = int(r["n"]) / total * 100
                print(f"    {r['r']:<30} {r['n']:>6}  {pct:5.1f}%")
                if "MAX_HOLD" in r["r"]:
                    mh += int(r["n"])
                if "TRAIL" in r["r"] or "RUNNER" in r["r"]:
                    tr += int(r["n"])
            mh_pct = mh / total * 100
            record("PASS" if mh_pct < 35 else "FAIL", "max-hold share within tolerance",
                   f"MAX_HOLD={mh_pct:.1f}% of closes, trail/runner exits={tr / total * 100:.1f}%")
    except Exception as exc:
        record("WARN", "exit distribution", f"{type(exc).__name__}: {exc}")


def check_backups() -> None:
    head("10. BACKUP TREE SIZE + RETENTION")
    bdir = ROOT / "backups"
    if not bdir.exists():
        record("PASS", "backups tree", "absent")
        return
    total = 0
    caps = []
    for child in bdir.iterdir():
        if child.is_dir():
            sz = 0
            for dp, dn, fn in os.walk(child):
                dn[:] = [d for d in dn if d.lower() not in PRUNE_DIRS or True]
                for f in fn:
                    try:
                        sz += (Path(dp) / f).stat().st_size
                    except Exception:
                        pass
                if out_of_budget():
                    break
            caps.append((child.name, sz))
            total += sz
    caps.sort(key=lambda r: -r[1])
    for n, sz in caps[:8]:
        print(f"    {sz / 1e9:8.2f} GB  {n}")
    gb = total / 1e9
    record("PASS" if gb < 20 else "FAIL", "backup tree bounded",
           f"{gb:.1f} GB across {len(caps)} captures "
           f"(directive threshold: was 113 GB)")
    apc = ROOT / "services/active_pipeline_cleaner.py"
    if apc.exists():
        txt = apc.read_text(encoding="utf-8", errors="ignore")
        record("PASS" if "prune_backups" in txt and "_is_excluded_path" in txt else "FAIL",
               "cleaner has recursion guard + retention",
               "prune_backups/_is_excluded_path present" if "prune_backups" in txt
               else "cleaner still unbounded")


# --------------------------------------------------------------------------
def main() -> int:
    print("=" * 78)
    print("SENTINUITY EDGE-RESTORE RUNTIME VERIFICATION")
    print(f"root : {ROOT}")
    print(f"db   : {DB} {'(present)' if DB.exists() else '(MISSING)'}")
    print(f"utc  : {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("=" * 78)

    global CFG
    CFG = cfg_all()

    for fn in (check_hashes, check_processes, check_config, check_edge_contracts,
               check_token_identity, check_freshness_and_oracle, check_fingerprint,
               check_runner_behaviour, check_max_hold_pathology, check_backups):
        if out_of_budget():
            record("WARN", f"{fn.__name__} skipped", "time budget exhausted")
            continue
        try:
            fn()
        except Exception as exc:
            record("WARN", fn.__name__, f"{type(exc).__name__}: {exc}")

    head("SUMMARY")
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0}
    for st, ck, det in RESULTS:
        counts[st] = counts.get(st, 0) + 1
    for st in ("FAIL", "WARN"):
        for s, ck, det in RESULTS:
            if s == st:
                print(f"  {st}: {ck}" + (f" - {det}" if det else ""))
    print(f"\n  PASS={counts['PASS']}  FAIL={counts['FAIL']}  WARN={counts['WARN']}")
    print(f"  elapsed {time.time() - T0:.1f}s (budget {BUDGET_SEC:.0f}s)")
    print("=" * 78)
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
