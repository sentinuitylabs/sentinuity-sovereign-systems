"""
preflight_verifier.py

SENTINUITY STARTUP PREFLIGHT — HARDENED
=========================================
Validates the full organism package before launch.
Runs AFTER prelaunch.py — assumes boot purge already done.

Boot classification:
  FINAL_SIGN_OFF_READY   — all checks passed
  BOOT_TEST_READY        — trading spine OK, cognition lanes degraded
  BLOCKED                — critical import or DB failure

Exit codes:
  0 = proceed
  1 = critical failure
"""

import importlib
import os
import sqlite3
import sys
import time
from pathlib import Path

# Resolve ROOT regardless of where this script is invoked from.
# Script may live in services/ or root — always walk up to find root.
_here = Path(__file__).resolve().parent
BASE_DIR = _here if ((_here / "core").exists() and (_here / "services").exists()) else _here.parent
DB_PATH = BASE_DIR / "sentinuity_matrix.db"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SEP = "=" * 62
critical_failures = []
warnings = []
fixes_applied = []
import_skipped = []


def ok(msg):    print(f"  ✓ {msg}")
def warn(msg):  print(f"  ⚠ {msg}"); warnings.append(msg)
def fail(msg):  print(f"  ✗ {msg}"); critical_failures.append(msg)
def info(msg):  print(f"  . {msg}")
def fixed(msg): print(f"  >> AUTO-FIXED: {msg}"); fixes_applied.append(msg)


REQUIRED_SERVICES = [
    "services/pump_monitor.py",
    "services/ingest_pipeline.py",
    "services/market_intelligence.py",
    "services/ws_price_oracle.py",
    "services/neural_supervisor.py",
    "services/execution_engine.py",
    "services/sovereign_governor.py",
    "services/polaris.py",
    "services/sovereign_parameter_engine.py",
    "services/replay_engine.py",
    "services/system_guardian.py",
    "services/cognition_logger.py",
    "services/price_router.py",
    "services/intelligence_orchestrator.py",
    "services/forge_notifier.py",
    "core/schema.py",
    "core/sovereign_identity.py",
    # Sovereign Forge doctrine + seed (run-once, not a service)
    "docs/SENTINUITY_SOVEREIGN_DOCTRINE.md",
    "launch/forge_genesis_seed.py",
]

CRITICAL_IMPORTS = [
    "core.schema",
    "services.pump_monitor",
    "services.ingest_pipeline",
    "services.market_intelligence",
    "services.neural_supervisor",
    "services.execution_engine",
    "services.sovereign_governor",
    "services.sovereign_parameter_engine",
    "services.replay_engine",
    "services.system_guardian",
    "services.master_console",
    "services.cognition_logger",
    "services.intelligence_orchestrator",
]

NON_CRITICAL_IMPORTS = [
    "services.polaris",
    "services.polaris_auxiliary",
    "services.reconnaissance_engine",
    "services.x_scout",
    "services.code_vault",
    "services.sovereign_hub",
    "services.ivaris",
]

REQUIRED_SCHEMA_FUNCTIONS = [
    "get_connection", "init_db", "update_heartbeat",
    "get_config_value", "insert_polaris_proposal", "queue_improvement",
]


def check_files():
    print("\n-- SERVICE FILES -------------------------------------------------")
    for f in REQUIRED_SERVICES:
        p = BASE_DIR / f
        if p.exists():
            ok(f)
        else:
            fail(f"MISSING: {f}")


def check_imports():
    print("\n-- IMPORT VALIDATION ---------------------------------------------")
    for m in CRITICAL_IMPORTS:
        try:
            importlib.import_module(m)
            ok(f"import {m}")
        except ImportError as e:
            fail(f"IMPORT FAILED (critical): {m} — {e}")
        except Exception as e:
            warn(f"import {m} raised {type(e).__name__}: {str(e)[:120]}")

    for m in NON_CRITICAL_IMPORTS:
        try:
            importlib.import_module(m)
            ok(f"import {m}")
        except (ImportError, ModuleNotFoundError) as e:
            warn(f"import {m} skipped (non-critical): {e}")
            import_skipped.append(m)
        except Exception as e:
            warn(f"import {m} raised {type(e).__name__}: {str(e)[:120]}")
            import_skipped.append(m)


def check_schema_functions():
    print("\n-- SCHEMA FUNCTION VALIDATION ------------------------------------")
    try:
        import core.schema as s
        for fn in REQUIRED_SCHEMA_FUNCTIONS:
            if hasattr(s, fn):
                ok(f"core.schema.{fn}")
            else:
                fail(f"MISSING: core.schema.{fn}")
    except Exception as e:
        fail(f"core.schema import failed: {e}")


def get_conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def check_db():
    print("\n-- DATABASE ------------------------------------------------------")
    if not DB_PATH.exists():
        warn("DB not found — will be created on first launch")
        return

    db_mb = DB_PATH.stat().st_size / 1024 / 1024
    info(f"DB size: {db_mb:.1f}MB")
    if db_mb < 400:
        ok("DB size acceptable")
    else:
        warn(f"DB large: {db_mb:.0f}MB — run slim_db.py after shutdown")

    try:
        t = time.monotonic()
        c = get_conn()
        c.execute("CREATE TABLE IF NOT EXISTS _pf_probe (ts INTEGER)")
        c.execute("INSERT INTO _pf_probe VALUES (?)", (int(time.time()),))
        c.commit()
        c.execute("DELETE FROM _pf_probe")
        c.commit()
        ms = (time.monotonic() - t) * 1000
        c.close()
        if ms < 5000:
            ok(f"DB write latency: {ms:.0f}ms")
        else:
            warn(f"DB latency HIGH: {ms:.0f}ms — consider slim_db.py")
    except Exception as e:
        fail(f"DB connectivity failed: {e}")
        return

    now = time.time()
    try:
        c = get_conn()

        # Config checks
        if table_exists(c, "system_config"):
            halt = c.execute(
                "SELECT value FROM system_config WHERE key='DRAWDOWN_HALT_ACTIVE'"
            ).fetchone()
            if halt and halt["value"] == "1":
                # SIGN-OFF FIX 15: Previously also cleared DRAWDOWN_ACCUMULATED_PCT to 0.0,
                # wiping exponential drawdown memory on every restart when a halt was active.
                # This allowed the system to resume at full position sizing immediately after
                # a drawdown event, defeating the purpose of the circuit breaker.
                # Fix: clear only the halt flag; leave DRAWDOWN_ACCUMULATED_PCT intact so
                # the guardian's 30-min decay (check_drawdown_halt) controls memory recovery.
                c.execute("UPDATE system_config SET value='0' WHERE key='DRAWDOWN_HALT_ACTIVE'")
                c.commit()
                fixed("Drawdown halt flag cleared (accumulated% preserved for memory continuity)")
            else:
                ok("Drawdown halt: not active")

            lg = c.execute(
                "SELECT value FROM system_config WHERE key='EXECUTOR_WRITE_LATENCY_LIMIT_MS'"
            ).fetchone()
            if lg:
                try:
                    if float(lg["value"]) < 10000:
                        c.execute(
                            "UPDATE system_config SET value='10000' "
                            "WHERE key='EXECUTOR_WRITE_LATENCY_LIMIT_MS'"
                        )
                        c.commit()
                        fixed("Latency gate raised to 10000ms")
                except Exception:
                    warn("Latency gate value unreadable — left unchanged")
        else:
            warn("system_config table missing — skipping config checks")

        # Stuck processing rows
        if table_exists(c, "market_snapshots"):
            try:
                r = c.execute("""
                    UPDATE market_snapshots
                    SET quality_status='pending', quality_reason=''
                    WHERE quality_status='processing'
                      AND COALESCE(price_updated_at,0) < ?
                """, (now - 600,))
                c.commit()
                if r.rowcount:
                    fixed(f"Reset {r.rowcount} stuck processing rows")
                else:
                    ok("No stuck processing rows")
            except Exception as e:
                warn(f"market_snapshots reset skipped: {e}")

            # Warn if stale qualified remain after prelaunch boot purge
            try:
                stale_cutoff = now - 600  # 10 minutes
                stale_qual = c.execute("""
                    SELECT COUNT(*) FROM market_snapshots
                    WHERE quality_status='qualified'
                      AND candidate_state NOT IN ('vetoed','exited')
                      AND COALESCE(created_at, timestamp, 0) < ?
                """, (stale_cutoff,)).fetchone()[0]
                if stale_qual > 0:
                    warn(f"{stale_qual} qualified snapshots still older than 10min — prelaunch may not have run")
                else:
                    ok("No stale qualified snapshots — boot purge clean")
            except Exception:
                pass
        else:
            warn("market_snapshots table missing — skipping snapshot checks")

        # system_state
        if table_exists(c, "system_state"):
            row = c.execute("SELECT id FROM system_state WHERE id=1").fetchone()
            if not row:
                c.execute("""
                    INSERT OR IGNORE INTO system_state (id, wallet_balance, initial_capital)
                    VALUES (1, 1000.0, 1000.0)
                """)
                c.commit()
                fixed("Created system_state row")
            else:
                ok("system_state row present")
        else:
            warn("system_state table missing — will be created by schema init")

        # Check paper_positions for zombies (belt-and-suspenders after prelaunch)
        if table_exists(c, "paper_positions"):
            try:
                zombie_count = c.execute(
                    "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND opened_at < ?",
                    (now - 3600,)
                ).fetchone()[0]
                if zombie_count > 0:
                    warn(f"{zombie_count} zombie paper_positions still open — run unjam.py")
                else:
                    ok("paper_positions: no zombies")
            except Exception:
                pass

        c.close()
    except Exception as e:
        warn(f"DB config checks failed: {e}")


def check_forge_db():
    print("\n-- FORGE SCHEMA + SEED STATE ------------------------------------")
    if not DB_PATH.exists():
        warn("DB not found — skipping forge checks")
        return

    try:
        c = get_conn()

        # ── required tables ──────────────────────────────────────────────
        for tbl in ("forge_projects", "forge_research_cache"):
            if table_exists(c, tbl):
                ok(f"table {tbl} exists")
            else:
                warn(f"table {tbl} missing — run schema init then forge_genesis_seed.py")

        # ── required polaris_proposals columns ───────────────────────────
        if table_exists(c, "polaris_proposals"):
            pp_cols = {r["name"] for r in c.execute(
                "PRAGMA table_info(polaris_proposals)"
            ).fetchall()}
            for col in ("proposal_domain", "stage", "project_key", "retry_count"):
                if col in pp_cols:
                    ok(f"polaris_proposals.{col} present")
                else:
                    warn(f"polaris_proposals.{col} missing — re-run schema init")
        else:
            warn("polaris_proposals table missing — skipping column checks")

        # ── active forge projects count ──────────────────────────────────
        if table_exists(c, "forge_projects"):
            active = c.execute(
                "SELECT COUNT(*) FROM forge_projects WHERE status='active'"
            ).fetchone()[0]
            if active == 4:
                ok(f"forge_projects active: {active} (expected 4)")
            elif active == 0:
                warn(
                    "forge_projects has 0 active projects — "
                    "run: python launch\forge_genesis_seed.py"
                )
            else:
                warn(f"forge_projects active: {active} (expected 4) — "
                     "check seed state")

            # open FORGE proposals
            if table_exists(c, "polaris_proposals"):
                try:
                    open_forge = c.execute(
                        "SELECT COUNT(*) FROM polaris_proposals "
                        "WHERE proposal_domain='FORGE' AND status='open'"
                    ).fetchone()[0]
                    ok(f"open FORGE proposals: {open_forge}")
                except Exception:
                    warn("Could not query FORGE proposals — proposal_domain column may be missing")

        c.close()
    except Exception as e:
        warn(f"Forge DB checks failed: {e}")


def check_packages():
    print("\n-- PYTHON PACKAGES -----------------------------------------------")
    for mod, label, required in [
        ("sqlite3",   "sqlite3",        True),
        ("requests",  "requests",       True),
        ("dotenv",    "python-dotenv",  True),
        ("streamlit", "streamlit",      True),
        ("pandas",    "pandas",         True),
        ("websockets","websockets",     True),
        ("openai",    "openai",         False),
    ]:
        try:
            __import__(mod)
            ok(label)
        except ImportError:
            if required:
                fail(f"MISSING: {label}")
            else:
                warn(f"MISSING: {label} — pip install openai")

    try:
        import google.generativeai
        ok("google-generativeai (IVARIS)")
    except ImportError:
        warn("google-generativeai missing — IVARIS offline")

    try:
        import telegram
        ok("python-telegram-bot (HITL)")
    except ImportError:
        warn("python-telegram-bot missing — Telegram disabled")


def print_summary():
    print(f"\n{SEP}\nPREFLIGHT SUMMARY\n{SEP}")

    if fixes_applied:
        print(f"\n  AUTO-FIXED ({len(fixes_applied)}):")
        for f in fixes_applied:
            print(f"    >> {f}")

    if import_skipped:
        print(f"\n  NON-CRITICAL SKIPPED ({len(import_skipped)}):")
        for m in import_skipped:
            print(f"    -- {m}")

    if warnings:
        print(f"\n  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"    !! {w}")

    if critical_failures:
        print(f"\n  CRITICAL FAILURES ({len(critical_failures)}):")
        for c in critical_failures:
            print(f"    XX {c}")

        trading_fail = any(
            k in f
            for f in critical_failures
            for k in ["ingest", "market", "neural", "execution", "schema", "pump", "guardian"]
        )
        print(f"\n  VERDICT: {'BLOCKED' if trading_fail else 'BOOT_TEST_READY'}")
        return 1

    print(f"\n  VERDICT: {'BOOT_TEST_READY' if import_skipped else 'FINAL_SIGN_OFF_READY'}")
    print("  ALL CLEAR — organism ready for launch")
    return 0


if __name__ == "__main__":
    print(SEP)
    print("SENTINUITY PREFLIGHT VERIFIER — HARDENED")
    print(SEP)
    check_packages()
    check_files()
    check_imports()
    check_schema_functions()
    check_db()
    check_forge_db()
    sys.exit(print_summary())