"""
services/master_console.py

SENTINUITY SOVEREIGN MASTER CONSOLE
======================================

One terminal to rule them all.

Shows live health of every service, updated every 5 seconds.
Colour-coded status. Latency. Open positions. Recent cognition.
Press a key to tail any individual service log.

All 19 services run silently in the background.
This console is the only window you need to watch.

Run automatically by Launch_Sentinuity.bat
Or manually: python services/master_console.py
"""

import os
import sys
import time
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
DB_PATH = BASE_DIR / "sentinuity_matrix.db"

# Console UTF-8 safety — keeps box drawing / symbols stable on Windows.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    try:
        os.system("chcp 65001 >nul")
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# Windows console colour codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
GOLD = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
PURPLE = "\033[95m"
DIM = "\033[2m"
WHITE = "\033[97m"

# Enable ANSI on Windows
if sys.platform == "win32":
    os.system("color")
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def cls():
    os.system("cls" if sys.platform == "win32" else "clear")


def get_db():
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def table_exists(conn, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return bool(row and row["n"])
    except Exception:
        return False


def column_exists(conn, table_name: str, column_name: str) -> bool:
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(str(col["name"]).lower() == column_name.lower() for col in cols)
    except Exception:
        return False


def get_heartbeats() -> list[dict]:
    """
    Read heartbeats from the DB.

    Priority: system_heartbeat (written by all services via schema.update_heartbeat)
    is always read first and takes precedence. service_heartbeats (legacy openclaw
    table) is merged in as a secondary source — system_heartbeat wins on conflict.

    This ordering fixes the previous bug where a service_heartbeats table existing
    (even empty) caused the console to skip system_heartbeat entirely and show
    NOT STARTED for all services.
    """
    conn = get_db()
    if not conn:
        return []

    try:
        merged: dict[str, dict] = {}

        # --- Secondary: service_heartbeats (openclaw legacy, may or may not exist) ---
        # Read this FIRST so system_heartbeat can overwrite it below.
        if table_exists(conn, "service_heartbeats"):
            try:
                # r[1] is the column NAME (r[0] is the numeric cid)
                cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(service_heartbeats)"
                ).fetchall()}
                pulse_col = "last_seen"      if "last_seen"      in cols else \
                            "last_heartbeat" if "last_heartbeat" in cols else None
                note_col  = "message"        if "message"        in cols else \
                            "note"           if "note"           in cols else None
                if pulse_col:
                    note_expr = f"COALESCE({note_col}, '')" if note_col else "''"
                    rows = conn.execute(f"""
                        SELECT service_name,
                               {pulse_col} AS last_pulse,
                               status,
                               {note_expr} AS note
                        FROM service_heartbeats
                    """).fetchall()
                    for r in rows:
                        merged[str(r["service_name"]).lower().strip()] = dict(r)
            except Exception:
                pass  # never let secondary source break the primary read

        # --- Primary: system_heartbeat (written by all Python services) ---
        if table_exists(conn, "system_heartbeat"):
            try:
                # Discover actual columns — note may not exist in all DB versions
                sh_cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(system_heartbeat)"
                ).fetchall()}
                note_expr = "COALESCE(note, '')" if "note" in sh_cols else "''"
                rows = conn.execute(f"""
                    SELECT
                        service_name,
                        last_pulse,
                        status,
                        {note_expr} AS note
                    FROM system_heartbeat
                    ORDER BY service_name ASC
                """).fetchall()
                for r in rows:
                    # system_heartbeat always wins
                    merged[str(r["service_name"]).lower().strip()] = dict(r)
            except Exception:
                pass  # never crash the console on read failure

        return list(merged.values())

    except Exception as e:
        print(f"heartbeat read failed: {e}")
        return []
    finally:
        conn.close()


def get_stats() -> dict:
    conn = get_db()
    if not conn:
        return {}

    try:
        stats = {
            "balance": 0.0,          # paper-primary display balance
            "initial": 100.0,        # paper-primary display initial capital
            "roi": 0.0,              # paper-primary display ROI
            "paper_balance": 0.0,
            "paper_initial": 100.0,
            "paper_roi": 0.0,
            "live_balance": 0.0,
            "live_initial": 0.0,
            "dna": 0,
            "open_pos": 0,
            "reviews": 0,
            "latency": 0.0,
            "latched": 0,
            "vetoed": 0,
            "open_proposals": 0,
            "win_rate": 0.0,
            "cognition": [],
            "conf_floor": 0.75,
            "halt_active": False,
        }

        # Wallet / capital
        # SIGN-OFF FIX 2026-05-27:
        # SENTINUITY_BALANCE_UNISON_20260621: live wallet is NO LONGER read here
        # from system_state.wallet_balance — that column was historically polluted
        # by paper closes mutating it, which is exactly what produced the bogus
        # negative "Live Wallet: $-22.40" on the console while the website showed
        # the real $4.74. The single source of truth is ui.state_contract
        # (get_balance_truth) in the display block below. We keep these as 0.0
        # placeholders so any later reference is safe; they are overwritten there.
        live_balance = 0.0
        live_initial = 0.0
        # (intentionally not reading system_state for live wallet display)

        paper_initial = 100.0
        try:
            if table_exists(conn, "system_config"):
                cfg = conn.execute("""
                    SELECT value FROM system_config
                    WHERE key IN (
                        'PAPER_EQUITY_INITIAL_USD',
                        'PAPER_INITIAL_CAPITAL',
                        'PAPER_STARTING_BALANCE_USD',
                        'PAPER_START_USD'
                    )
                    ORDER BY CASE key
                        WHEN 'PAPER_EQUITY_INITIAL_USD' THEN 1
                        WHEN 'PAPER_INITIAL_CAPITAL' THEN 2
                        WHEN 'PAPER_STARTING_BALANCE_USD' THEN 3
                        ELSE 4
                    END
                    LIMIT 1
                """).fetchone()
                if cfg and cfg["value"] not in (None, ""):
                    paper_initial = float(cfg["value"])
        except Exception:
            paper_initial = 100.0

        reset_ts = 0.0
        try:
            if table_exists(conn, "operator_reset_log"):
                reset_cols = {
                    str(c["name"]).lower()
                    for c in conn.execute("PRAGMA table_info(operator_reset_log)").fetchall()
                }
                for col in ("reset_at", "created_at", "timestamp", "ts", "at"):
                    if col in reset_cols:
                        rr = conn.execute(f"SELECT MAX(CAST({col} AS REAL)) AS t FROM operator_reset_log").fetchone()
                        reset_ts = float(rr["t"] or 0.0) if rr else 0.0
                        if reset_ts > 0:
                            break
        except Exception:
            reset_ts = 0.0

        paper_realized = 0.0
        paper_unrealized = 0.0
        try:
            if table_exists(conn, "paper_positions"):
                pp_cols = {
                    str(c["name"]).lower()
                    for c in conn.execute("PRAGMA table_info(paper_positions)").fetchall()
                }
                if "funding_mode" in pp_cols and "realized_pnl_usd" in pp_cols:
                    row = conn.execute("""
                        SELECT COALESCE(SUM(CAST(realized_pnl_usd AS REAL)), 0) AS pnl
                        FROM paper_positions
                        WHERE COALESCE(funding_mode,'SIM')='SIM'
                          AND status='CLOSED'
                          AND COALESCE(CAST(closed_at AS REAL), CAST(opened_at AS REAL), 0) >= ?
                    """, (reset_ts,)).fetchone()
                    paper_realized = float(row["pnl"] or 0.0) if row else 0.0
                if "funding_mode" in pp_cols and "unrealized_pnl_usd" in pp_cols:
                    row = conn.execute("""
                        SELECT COALESCE(SUM(CAST(unrealized_pnl_usd AS REAL)), 0) AS pnl
                        FROM paper_positions
                        WHERE COALESCE(funding_mode,'SIM')='SIM'
                          AND status='OPEN'
                          AND COALESCE(CAST(opened_at AS REAL), 0) >= ?
                    """, (reset_ts,)).fetchone()
                    paper_unrealized = float(row["pnl"] or 0.0) if row else 0.0
        except Exception:
            paper_realized = 0.0
            paper_unrealized = 0.0

        paper_balance = paper_initial + paper_realized + paper_unrealized
        stats["paper_initial"] = paper_initial
        stats["paper_balance"] = paper_balance
        stats["paper_roi"] = ((paper_balance - paper_initial) / max(paper_initial, 1)) * 100

        # Keep legacy keys paper-primary so existing render paths stay coherent.
        stats["balance"] = stats["paper_balance"]
        stats["initial"] = stats["paper_initial"]
        stats["roi"] = stats["paper_roi"]

        # DNA
        if table_exists(conn, "raw_dna"):
            row = conn.execute("SELECT COUNT(*) AS n FROM raw_dna").fetchone()
            stats["dna"] = int(row["n"] or 0)

        # Open positions
        if table_exists(conn, "paper_positions"):
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM paper_positions WHERE status='OPEN'"
            ).fetchone()
            stats["open_pos"] = int(row["n"] or 0)
        elif table_exists(conn, "open_positions"):
            row = conn.execute("SELECT COUNT(*) AS n FROM open_positions").fetchone()
            stats["open_pos"] = int(row["n"] or 0)

        # Reviews / win rate
        review_table = None
        if table_exists(conn, "polaris_trade_reviews"):
            review_table = "polaris_trade_reviews"
        elif table_exists(conn, "trade_autopsies"):
            review_table = "trade_autopsies"
        elif table_exists(conn, "autopsies"):
            review_table = "autopsies"

        if review_table:
            try:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {review_table}").fetchone()
                stats["reviews"] = int(row["n"] or 0)
            except Exception:
                stats["reviews"] = 0

            try:
                cols = {
                    str(col["name"]).lower()
                    for col in conn.execute(f"PRAGMA table_info({review_table})").fetchall()
                }

                if "win_loss" in cols:
                    row = conn.execute(f"""
                        SELECT
                            SUM(CASE WHEN win_loss='WIN' THEN 1 ELSE 0 END) AS wins,
                            COUNT(*) AS total
                        FROM (
                            SELECT win_loss
                            FROM {review_table}
                            ORDER BY id DESC
                            LIMIT 30
                        )
                    """).fetchone()
                    if row and row["total"]:
                        stats["win_rate"] = (row["wins"] / row["total"]) * 100
                elif "outcome" in cols:
                    row = conn.execute(f"""
                        SELECT
                            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
                            COUNT(*) AS total
                        FROM (
                            SELECT outcome
                            FROM {review_table}
                            ORDER BY id DESC
                            LIMIT 30
                        )
                    """).fetchone()
                    if row and row["total"]:
                        stats["win_rate"] = (row["wins"] / row["total"]) * 100
            except Exception:
                stats["win_rate"] = 0.0

        # Latency
        try:
            if table_exists(conn, "system_health_snapshots") and column_exists(
                conn, "system_health_snapshots", "db_latency_ms"
            ):
                row = conn.execute("""
                    SELECT db_latency_ms
                    FROM system_health_snapshots
                    ORDER BY id DESC
                    LIMIT 1
                """).fetchone()
                if row and row["db_latency_ms"] is not None:
                    stats["latency"] = float(row["db_latency_ms"] or 0)
            elif table_exists(conn, "system_telemetry") and column_exists(
                conn, "system_telemetry", "insert_latency_ms"
            ):
                row = conn.execute("""
                    SELECT insert_latency_ms
                    FROM system_telemetry
                    ORDER BY id DESC
                    LIMIT 1
                """).fetchone()
                stats["latency"] = float(row["insert_latency_ms"] or 0) if row else 0.0
        except Exception:
            stats["latency"] = 0.0

        # Latched / vetoed
        if table_exists(conn, "market_snapshots"):
            cols = {
                str(col["name"]).lower()
                for col in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()
            }

            try:
                if "candidate_state" in cols and "execution_ready" in cols:
                    row = conn.execute("""
                        SELECT COUNT(*) AS n
                        FROM market_snapshots
                        WHERE candidate_state='latched' AND COALESCE(execution_ready,0) IN (1,2)
                    """).fetchone()
                    stats["latched"] = int(row["n"] or 0)

                    row = conn.execute("""
                        SELECT COUNT(*) AS n
                        FROM market_snapshots
                        WHERE candidate_state='vetoed'
                    """).fetchone()
                    stats["vetoed"] = int(row["n"] or 0)
                else:
                    if "latched" in cols:
                        row = conn.execute("""
                            SELECT COUNT(*) AS n
                            FROM market_snapshots
                            WHERE latched=1
                        """).fetchone()
                        stats["latched"] = int(row["n"] or 0)

                    veto_col = None
                    for possible in ("vetoed", "rejected", "disqualified"):
                        if possible in cols:
                            veto_col = possible
                            break
                    if veto_col:
                        row = conn.execute(f"""
                            SELECT COUNT(*) AS n
                            FROM market_snapshots
                            WHERE {veto_col}=1
                        """).fetchone()
                        stats["vetoed"] = int(row["n"] or 0)
            except Exception:
                pass

        # Open proposals
        if table_exists(conn, "polaris_proposals"):
            try:
                row = conn.execute("""
                    SELECT COUNT(*) AS n
                    FROM polaris_proposals
                    WHERE LOWER(status)='open'
                """).fetchone()
                stats["open_proposals"] = int(row["n"] or 0)
            except Exception:
                stats["open_proposals"] = 0

        # Recent cognition
        if table_exists(conn, "cognition_log"):
            try:
                rows = conn.execute("""
                    SELECT stage, token, message
                    FROM cognition_log
                    ORDER BY id DESC
                    LIMIT 5
                """).fetchall()
                stats["cognition"] = [dict(r) for r in rows]
            except Exception:
                stats["cognition"] = []

        # Config values
        if table_exists(conn, "system_config"):
            try:
                conf_row = conn.execute("""
                    SELECT value
                    FROM system_config
                    WHERE key='SUPERVISOR_MIN_MINT_CONFIDENCE'
                """).fetchone()
                stats["conf_floor"] = float(conf_row["value"]) if conf_row else 0.75

                halt_row = conn.execute("""
                    SELECT value
                    FROM system_config
                    WHERE key='DRAWDOWN_HALT_ACTIVE'
                """).fetchone()
                stats["halt_active"] = (
                    str(halt_row["value"]).strip() == "1" if halt_row else False
                )
            except Exception:
                stats["conf_floor"] = 0.75
                stats["halt_active"] = False

        return stats

    except Exception:
        return {}
    finally:
        conn.close()


def status_colour(status: str, age_s: float) -> str:
    if age_s > 300:
        return RED
    s = status.upper()
    if s in ("ALIVE", "OK"):
        return GREEN
    if s in ("DEGRADED", "WARN", "STANDBY"):
        return GOLD
    if s in ("ERROR", "DEAD", "OFFLINE"):
        return RED
    return DIM


def service_row(name: str, hb: dict | None, now: float) -> str:
    if not hb:
        return f"  {DIM}○ {name:<28} {'NOT STARTED':<10}{RESET}"

    status = str(hb.get("status", "?")).upper()
    pulse = float(hb.get("last_pulse") or 0)
    note = str(hb.get("note") or "")[:45]
    age_s = now - pulse
    col = status_colour(status, age_s)
    sym = "●" if col == GREEN else ("◑" if col == GOLD else "✕")
    age_str = f"{int(age_s)}s ago" if age_s < 3600 else f"{int(age_s / 60)}m ago"

    return f"  {col}{sym} {name:<28} {status:<10} {age_str:<10} {DIM}{note}{RESET}"


def tail_log(service_name: str) -> None:
    """Show last 40 lines of a service log."""
    log_map = {
        "scout": "scout.log",
        "ingest_pipeline": "ingest_pipeline.log",
        "market_intelligence": "market_intelligence.log",
        "execution_engine": "execution_engine.log",
        "sovereign_governor": "sovereign_governor.log",
        "system_guardian": "system_guardian.log",
        "pump_monitor": "pump_monitor.log",
        "weaver": "weaver.log",
        "oracle": "oracle.log",
        "qualifier": "qualifier.log",
        "supervisor": "supervisor.log",
        "neural_supervisor": "supervisor.log",
        "executor": "executor.log",
        "polaris": "polaris.log",
        "reviewer": "reviewer.log",
        "calibrator": "calibrator.log",
        "messenger": "messenger.log",
        "spe": "spe.log",
        "sovereign_parameter_engine": "spe.log",
        "tg_scout": "tg_scout.log",
        "telegram_scout": "tg_scout.log",
        "wallet_scout": "reconnaissance_engine.log",
        "reconnaissance_engine": "reconnaissance_engine.log",
        "analyst": "ch_analyst.log",
        "hitl": "hitl.log",
        "debate": "debate.log",
        "health": "health.log",
        "vault": "vault.log",
        "code_vault": "vault.log",
        "dashboard": "dashboard.log",
        "sovereign_hub": "sovereign_hub.log",
        "replay_engine": "replay.log",
    }
    fname = log_map.get(service_name.lower())
    if not fname:
        print(f"\n{RED}Unknown service: {service_name}{RESET}")
        input("Press Enter to return...")
        return

    log_file = LOG_DIR / fname
    cls()
    print(f"\n{CYAN}{BOLD}LOG: {fname}{RESET}\n")
    if not log_file.exists():
        print(f"{DIM}No log file yet — service may not have started{RESET}")
    else:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-40:]:
                if "ERROR" in line or "EXCEPTION" in line or "error" in line.lower():
                    print(f"{RED}{line}{RESET}")
                elif "WARN" in line or "WARNING" in line:
                    print(f"{GOLD}{line}{RESET}")
                elif (
                    "ONLINE" in line
                    or "ALIVE" in line
                    or "APPROVED" in line
                    or "OPENED" in line
                ):
                    print(f"{GREEN}{line}{RESET}")
                elif "BLOCKED" in line or "VETO" in line or "DEFERRED" in line:
                    print(f"{PURPLE}{line}{RESET}")
                else:
                    print(f"{DIM}{line}{RESET}")
        except Exception as e:
            print(f"{RED}Could not read log: {e}{RESET}")

    print(f"\n{DIM}{'─' * 60}{RESET}")
    input(f"{DIM}Press Enter to return to console...{RESET}")


def render(heartbeats: list[dict], stats: dict, now: float) -> None:
    cls()

    hb_map = {str(h["service_name"]).lower(): h for h in heartbeats}

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    border = "═" * 58
    print(f"\n{CYAN}{BOLD}  ╔{border}╗{RESET}")
    print(f"{CYAN}{BOLD}  ║      SENTINUITY SOVEREIGN CONSOLE  {DIM}{ts}  {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}  ╚{border}╝{RESET}\n")

    balance = stats.get("balance", 0)
    roi = stats.get("roi", 0)
    live_balance = stats.get("live_balance", 0)
    live_initial = stats.get("live_initial", 0)
    latency = stats.get("latency", 0)
    open_pos = stats.get("open_pos", 0)
    latched = stats.get("latched", 0)
    vetoed = stats.get("vetoed", 0)
    dna = stats.get("dna", 0)
    win_rate = stats.get("win_rate", 0)
    reviews = stats.get("reviews", 0)
    conf_fl = stats.get("conf_floor", 0.75)
    halt = stats.get("halt_active", False)
    proposals = stats.get("open_proposals", 0)

    roi_col = GREEN if roi >= 0 else RED
    lat_col = RED if latency > 2000 else (GOLD if latency > 500 else GREEN)
    halt_str = (
        f"{RED} DRAWDOWN HALT ACTIVE{RESET}" if halt else f"{GREEN}Trading: ACTIVE{RESET}"
    )

    print(
        f"  {halt_str}   {DIM}Conf floor: {RESET}{GOLD}{conf_fl:.2f}{RESET}   "
        f"{DIM}Open proposals: {RESET}{GOLD}{proposals}{RESET}\n"
    )
    # SENTINUITY_BALANCE_UNISON_20260621
    # Console and website both use ui.state_contract — ONE wallet truth.
    paper_cash = stats.get("paper_cash", balance)
    paper_reserved = stats.get("paper_reserved", 0.0)
    paper_unreal = stats.get("paper_unrealized_pnl", 0.0)
    _live_synced = False
    _contract_ok = False
    try:
        from ui.state_contract import get_balance_truth as _sent_get_balance_truth
        from pathlib import Path as _sent_Path
        _sent_root = _sent_Path(__file__).resolve().parents[1]
        _bt = _sent_get_balance_truth(str(_sent_root / "sentinuity_matrix.db"), fallback_initial=float(stats.get("initial", 100.0) or 100.0))
        balance = float(_bt.paper_equity)
        roi = float(_bt.paper_roi_pct)
        paper_cash = float(_bt.paper_cash)
        paper_reserved = float(_bt.paper_open_reserved)
        paper_unreal = float(_bt.paper_unrealized_pnl)
        live_balance = float(_bt.live_wallet_usd)
        live_initial = float(_bt.live_start_usd)
        _live_synced = bool(_bt.live_wallet_synced)
        _contract_ok = True
    except Exception as _bt_err:
        # Do NOT silently fall back to polluted system_state. Surface it.
        print(f"  {DIM}[wallet contract unavailable: {str(_bt_err)[:60]}]{RESET}")
    roi_col = GREEN if roi >= 0 else RED
    # BALANCE_COLLAPSE_WHEN_FLAT_20260622: equity and cash only differ when a
    # position is open. When flat (nothing reserved, no uPnL) show ONE balance
    # line instead of printing the same number twice; split into Equity/Cash +
    # the reserved/uPnL line only when there is genuinely an open paper position.
    _paper_open = (paper_reserved > 0.005) or (abs(paper_unreal) > 0.005)
    if _paper_open:
        print(f"  {DIM}Paper Equity:{RESET} {CYAN}{BOLD}${balance:,.2f}{RESET}   {DIM}Paper Cash:{RESET} {CYAN}${paper_cash:,.2f}{RESET}   {DIM}Paper ROI:{RESET} {roi_col}{roi:+.2f}%{RESET}")
        print(f"  {DIM}Paper Open:{RESET}   {GOLD}${paper_reserved:,.2f}{RESET} reserved   {DIM}uPnL:{RESET} {GOLD}${paper_unreal:+,.2f}{RESET}")
    else:
        print(f"  {DIM}Paper Balance:{RESET} {CYAN}{BOLD}${balance:,.2f}{RESET}   {DIM}Paper ROI:{RESET} {roi_col}{roi:+.2f}%{RESET}   {DIM}flat — no open positions{RESET}")
    # LIVE_WALLET_SINGLE_TRUTH_20260720: exactly one Live Wallet line. Never
    # render the zero placeholder as a synced balance when the canonical contract
    # failed to load. The three truthful states are unavailable / unsynced / synced.
    if not _contract_ok:
        print(f"  {DIM}Live Wallet:{RESET}  {RED}contract unavailable — no balance asserted{RESET}")
    elif not _live_synced:
        print(f"  {DIM}Live Wallet:{RESET}  {WHITE}not synced from Phantom chain truth{RESET}")
    else:
        print(f"  {DIM}Live Wallet:{RESET}  {PURPLE}{BOLD}${live_balance:,.2f}{RESET}   {DIM}real Phantom/Solana balance · canonical sync{RESET}")
    print(f"  {DIM}DNA nodes:{RESET}   {WHITE}{dna:,}{RESET}   {DIM}Win rate:{RESET} {GOLD}{win_rate:.1f}%{RESET} ({reviews} reviews)")
    print(f"  {DIM}Positions:{RESET}   {GOLD}{open_pos} open{RESET}   {DIM}Latched:{RESET} {GREEN}{latched}{RESET}   {DIM}Vetoed:{RESET} {RED}{vetoed}{RESET}")
    print(f"  {DIM}DB Latency:{RESET}  {lat_col}{latency:.0f}ms{RESET}")
    print()

    print(f"  {BOLD}{WHITE}── CORE PIPELINE " + "─" * 39 + f"{RESET}")
    pipeline = [
        ("pump_monitor", "pump_monitor"),
        ("ingest_pipeline", "ingest_pipeline"),
        ("market_intelligence", "market_intelligence"),
        ("neural_supervisor", "neural_supervisor"),
        ("execution_engine", "execution_engine"),
    ]
    for hb_key, label in pipeline:
        print(service_row(label, hb_map.get(hb_key), now))

    print(f"\n  {BOLD}{WHITE}── GOVERNANCE " + "─" * 42 + f"{RESET}")
    governance = [
        ("sovereign_governor", "sovereign_governor"),
        ("polaris", "polaris"),
        ("sovereign_parameter_engine", "sovereign_parameter_engine"),
        ("replay_engine", "replay_engine"),
        ("system_guardian", "system_guardian"),
    ]
    for hb_key, label in governance:
        print(service_row(label, hb_map.get(hb_key), now))

    print(f"\n  {BOLD}{WHITE}── SCOUTS + UTILITIES " + "─" * 34 + f"{RESET}")
    scouts = [
        ("wallet_scout", "wallet_scout"),
        ("telegram_scout", "telegram_scout"),
        ("code_vault", "code_vault"),
    ]
    for hb_key, label in scouts:
        print(service_row(label, hb_map.get(hb_key), now))

    cognition = stats.get("cognition", [])
    if cognition:
        print(f"\n  {BOLD}{WHITE}── LIVE COGNITION " + "─" * 39 + f"{RESET}")
        for c in cognition:
            stage = str(c.get("stage", "?"))[:10]
            token = str(c.get("token", ""))[:14]
            msg = str(c.get("message", ""))[:60]
            scol = PURPLE if "SUPERV" in stage else (CYAN if "SIGNAL" in stage else (RED if "QUALIF" in stage else GOLD))
            print(f"  {scol}[{stage}]{RESET} {DIM}{token:<14}{RESET} {msg}")

    print(f"\n  {DIM}{'─' * 56}{RESET}")
    print(f"  {DIM}[L] tail a log   [Q] quit   auto-refreshes every 5s{RESET}")

    # ── API MONITOR SECTION ───────────────────────────────────────────────────
    api_data = stats.get("api_stats", {})
    apis = api_data.get("apis", {})
    if apis:
        print(f"\n  {BOLD}{WHITE}── API ACTIVITY " + "─" * 40 + f"{RESET}")
        AI_APIS = {"openai", "xai", "nim", "anthropic"}  # gemini removed — NUGGET now routes via NIM
        total_cost = api_data.get("total_cost", 0.0)
        total_calls = api_data.get("total_calls", 0)
        print(f"  {DIM}Total calls: {RESET}{WHITE}{total_calls}{RESET}   {DIM}Session cost: {RESET}{GOLD}${total_cost:.4f}{RESET}")
        print()
        for name, info in apis.items():
            if not info.get("key_set"):
                continue
            total  = info.get("total", 0)
            errs   = info.get("err", 0)
            rate   = info.get("rate_per_min", 0)
            tokens = info.get("tokens", 0)
            cost   = info.get("cost_usd", 0.0)
            if total == 0 and name not in AI_APIS:
                continue  # skip silent APIs

            # Status indicator
            if errs > 0 and total > 0 and (errs / total) > 0.2:
                ind = f"{RED}●{RESET}"
            elif total > 0:
                ind = f"{GREEN}●{RESET}"
            else:
                ind = f"{DIM}◌{RESET}"

            is_ai = name in AI_APIS
            name_col = PURPLE if is_ai else CYAN
            rate_str = f"{GOLD}{rate}/min{RESET}" if rate > 0 else f"{DIM}idle{RESET}"
            tok_str  = f"{DIM}{tokens//1000}k tok{RESET}" if tokens > 1000 else ""
            cost_str = f"{GOLD}${cost:.4f}{RESET}" if cost > 0 else ""
            err_str  = f"  {RED}{errs} err{RESET}" if errs > 0 else ""

            print(f"  {ind} {name_col}{name:<14}{RESET} {WHITE}{total:>4} calls{RESET}  "
                  f"{rate_str:<18} {tok_str:<12} {cost_str}{err_str}")
    else:
        print(f"\n  {DIM}── API MONITOR: run python api_monitor_server.py to enable ──{RESET}")

    print()


def get_api_stats() -> dict:
    """
    Fetch live API stats from api_monitor_server.py on localhost:8766.
    Returns empty dict silently if monitor not running — never crashes console.
    """
    try:
        import urllib.request as _ur
        req = _ur.Request("http://localhost:8766/status", headers={"Accept":"application/json"})
        with _ur.urlopen(req, timeout=1.5) as r:
            return __import__("json").loads(r.read().decode())
    except Exception:
        return {}


def main():
    import threading
    import msvcrt

    key_pressed = [None]

    def key_listener():
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getch().decode("utf-8", errors="ignore").upper()
                key_pressed[0] = ch
                time.sleep(0.1)
            time.sleep(0.05)

    listener = threading.Thread(target=key_listener, daemon=True)
    listener.start()

    print(f"\n{CYAN}SENTINUITY SOVEREIGN CONSOLE STARTING...{RESET}")
    time.sleep(2)

    while True:
        try:
            heartbeats = get_heartbeats()
            stats = get_stats()
            stats["api_stats"] = get_api_stats()  # non-blocking, fails silently
            now = time.time()
            render(heartbeats, stats, now)

            for _ in range(50):
                time.sleep(0.1)
                k = key_pressed[0]
                if k:
                    key_pressed[0] = None
                    if k == "Q":
                        cls()
                        print(f"\n{GOLD}Sovereign Console closed. Services continue running in background.{RESET}\n")
                        sys.exit(0)
                    elif k == "L":
                        cls()
                        print(f"\n{CYAN}Available logs:{RESET}")
                        logs = [
                            "pump_monitor",
                            "ingest_pipeline",
                            "market_intelligence",
                            "execution_engine",
                            "sovereign_governor",
                            "system_guardian",
                            "neural_supervisor",
                            "sovereign_parameter_engine",
                            "replay_engine",
                            "polaris",
                            "wallet_scout",
                            "telegram_scout",
                            "code_vault",
                            "sovereign_hub",
                        ]
                        for i, name in enumerate(logs, 1):
                            print(f"  {DIM}{i:2}.{RESET} {name}")
                        print()
                        choice = input(f"{CYAN}Enter service name or number: {RESET}").strip()
                        try:
                            idx = int(choice) - 1
                            if 0 <= idx < len(logs):
                                tail_log(logs[idx])
                        except ValueError:
                            tail_log(choice)
                        break

        except KeyboardInterrupt:
            cls()
            print(f"\n{GOLD}Sovereign Console closed. Services continue running.{RESET}\n")
            sys.exit(0)
        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    main()
