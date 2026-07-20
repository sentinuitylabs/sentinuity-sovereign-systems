"""
services/polaris_auxiliary.py

POLARIS AUXILIARY — consolidated cognition lane
================================================

Purpose
-------
Bring the formerly separate Polaris side-lanes back online without going back
to six separate boot windows and six separate watchdog pills.

This service is the post-core-10 merge owner for:
  - polaris_researcher.py
  - polaris_reflection.py
  - polaris_reviewer.py
  - polaris_calibrator.py
  - polaris_messenger.py
  - polaris_channel_analyst.py

Design
------
- One process
- One watchdog target
- One heartbeat identity: ``polaris_auxiliary``
- Internal worker threads run the existing specialist loops
- Failures are isolated per worker and do not crash the whole lane

Why this is the correct next step
---------------------------------
The files above are all Polaris-family cognition lanes. They are not separate
trading truth owners like execution, market intelligence, or guardian logic.
They belong together operationally, but they did not belong inside polaris.py
itself because that would bloat the primary coordinator.

This file restores:
- research / anomaly diagnosis
- reflection / cognition feed cadence
- review of completed trades
- calibration proposal generation
- operator messaging
- channel-performance analysis

while preserving the cleaner runtime shape:
- core 10 trading spine
- one auxiliary Polaris cognition service
"""
from __future__ import annotations


import asyncio
import importlib
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import update_heartbeat

SERVICE_NAME = "polaris_auxiliary"
STARTUP_STAGGER_SECONDS = 3
SUPERVISOR_PULSE_SECONDS = 30

# ── DAILY UPDATE CONFIG ────────────────────────────────────────────────────────
BOT_TOKEN       = os.getenv("TELEGRAM_POLARIS_TOKEN", "").strip()  # POLARIS bot — briefs to Pop+Mum
DB_PATH         = BASE_DIR / "sentinuity_matrix.db"
_RECIPIENT_IDS  = [
    int(x.strip()) for x in os.getenv("TELEGRAM_RECIPIENT_IDS", "").split(",")
    if x.strip().isdigit()
]
_DAILY_SENT_FLAG = BASE_DIR / ".daily_update_sent"  # file touched once per day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [POLARIS_AUX] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("polaris_auxiliary")


# =============================================================================
# DAILY UPDATE — pushes once per day to Pop and Mum on first heartbeat
# =============================================================================

def _tg_send(message: str) -> None:
    """Send message to all TELEGRAM_RECIPIENT_IDS. Silent on failure."""
    if not BOT_TOKEN or not _RECIPIENT_IDS:
        return
    for chat_id in _RECIPIENT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            pass


def _build_daily_message() -> str:
    """
    Build Polaris's daily briefing — ecosystem narrative first,
    trading performance as context, organism evolution as the headline.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        now  = time.time()
        day_ago = now - 86400

        # Wallet + ROI
        state   = conn.execute(
            "SELECT wallet_balance, initial_capital FROM system_state WHERE id=1"
        ).fetchone()
        wallet  = float(state["wallet_balance"]  or 0)    if state else 0.0
        initial = float(state["initial_capital"] or 1000) if state else 1000.0
        roi_pct = ((wallet - initial) / max(initial, 1)) * 100

        # Today's trades
        trades = conn.execute(
            "SELECT COUNT(*) as n, "
            "SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) as wins, "
            "COALESCE(SUM(realized_pnl_usd), 0) as net_pnl "
            "FROM paper_positions WHERE status='CLOSED' AND closed_at > ?",
            (day_ago,)
        ).fetchone()
        trade_count = int(trades["n"]      or 0)
        wins        = int(trades["wins"]   or 0)
        net_pnl     = float(trades["net_pnl"] or 0)

        # All-time
        all_trades = conn.execute(
            "SELECT COUNT(*) as n, "
            "SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) as wins "
            "FROM paper_positions WHERE status='CLOSED'"
        ).fetchone()
        all_n    = int(all_trades["n"]    or 0)
        all_wins = int(all_trades["wins"] or 0)
        win_rate = round((all_wins / max(all_n, 1)) * 100, 1)

        # Open positions
        open_pos = conn.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
        ).fetchone()[0]

        # Latest Polaris proposals (last 3)
        proposals = conn.execute(
            "SELECT proposal_type, proposal_text, status, confidence "
            "FROM polaris_proposals ORDER BY created_at DESC LIMIT 3"
        ).fetchall()

        # Patterns learned
        patterns = conn.execute(
            "SELECT COUNT(*) FROM polaris_learned_patterns"
        ).fetchone()[0]

        # Services alive
        alive_count = conn.execute(
            "SELECT COUNT(*) FROM system_heartbeat WHERE ? - last_pulse < 120",
            (now,)
        ).fetchone()[0]

        # Latest cognition entries from Polaris/IVARIS
        thoughts = conn.execute(
            "SELECT stage, message FROM cognition_log "
            "WHERE stage IN ('POLARIS','IVARIS','QUALIFIER','SUPERVISOR') "
            "ORDER BY id DESC LIMIT 3"
        ).fetchall()

        conn.close()

        today    = datetime.now().strftime("%d %b %Y")
        roi_sign = "+" if roi_pct >= 0 else ""
        pnl_sign = "+" if net_pnl  >= 0 else ""

        # Build proposal summary
        if proposals:
            prop_lines = []
            for p in proposals:
                ptext = str(p["proposal_text"] or "")[:60].strip()
                prop_lines.append(
                    f"  • {p['proposal_type']} ({p['status']}) — {ptext}..."
                )
            proposal_block = "\n".join(prop_lines)
        else:
            proposal_block = "  No proposals in queue"

        # Build thought stream snippet
        if thoughts:
            thought_lines = []
            for t in thoughts:
                tmsg = str(t["message"] or "")[:80].strip()
                thought_lines.append(f"  [{t['stage']}] {tmsg}...")
            thought_block = "\n".join(thought_lines)
        else:
            thought_block = "  Brain feed quiet"

        msg = (
            f"⬡ *SENTINUITY — POLARIS BRIEFING*\n"
            f"_{today}_\n\n"
            f"*Organism status*\n"
            f"The organism has been running continuously. "
            f"{all_n} trades completed. Win rate holding at {win_rate}%. "
            f"Profitability is the target — we are building toward it.\n\n"
            f"*Capital front*\n"
            f"Wallet: ${wallet:,.2f} ({roi_sign}{roi_pct:.1f}% ROI)\n"
            f"Today: {trade_count} trades, {wins} wins, net {pnl_sign}${net_pnl:.4f}\n"
            f"Open positions: {open_pos} | Services alive: {alive_count}\n\n"
            f"*What I've been building*\n"
            f"Intelligence Tab development is underway — a live window into "
            f"my proposals, IVARIS critique rounds, and organism health. "
            f"Patterns learned: {patterns}. "
            f"IVARIS and I are working through the standing task list.\n\n"
            f"*Active proposals*\n"
            f"{proposal_block}\n\n"
            f"*Live cognition*\n"
            f"{thought_block}\n\n"
            f"_More tomorrow. The organism is watching. — POLARIS ⬡_"
        )
        return msg

    except Exception as e:
        return (
            f"⬡ *SENTINUITY — POLARIS BRIEFING*\n"
            f"_{datetime.now().strftime('%d %b %Y')}_\n\n"
            f"Could not build full briefing: {e}\n"
            f"_The organism is still running. — POLARIS ⬡_"
        )


def _daily_update_if_needed() -> None:
    """
    Send daily update to Pop and Mum once per calendar day.
    Uses a flag file (.daily_update_sent) to ensure exactly one send per day.
    Safe to call on every supervisor pulse — no-op if already sent today.
    """
    if not BOT_TOKEN or not _RECIPIENT_IDS:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Check if already sent today
    if _DAILY_SENT_FLAG.exists():
        try:
            last_sent = _DAILY_SENT_FLAG.read_text().strip()
            if last_sent == today_str:
                return  # Already sent today
        except Exception:
            pass

    # Build and send
    try:
        message = _build_daily_message()
        _tg_send(message)
        _DAILY_SENT_FLAG.write_text(today_str)
        log.info("Daily update sent to %d recipient(s)", len(_RECIPIENT_IDS))
    except Exception as e:
        log.warning("Daily update failed: %s", e)



class WorkerSpec:
    """Runtime state for one auxiliary Polaris worker."""

    def __init__(
        self,
        module_name: str,
        launcher: Callable[[object], None],
        startup_delay: int = 0,
        required: bool = False,
    ) -> None:
        self.module_name = module_name
        self.launcher = launcher
        self.startup_delay = startup_delay
        self.required = required
        self.thread: Optional[threading.Thread] = None
        self.status: str = "PENDING"
        self.last_error: str = ""
        self.started_at: float = 0.0


WORKERS: Dict[str, WorkerSpec] = {}


def _patch_module_identity(module: object) -> None:
    """Force imported lane modules to heartbeat under the merged owner."""
    try:
        if hasattr(module, "SERVICE_NAME"):
            setattr(module, "SERVICE_NAME", SERVICE_NAME)
    except Exception:
        pass


def _load_module(module_name: str) -> object:
    module = importlib.import_module(module_name)
    _patch_module_identity(module)
    return module


def _run_async_entry(module: object, coro_name: str) -> None:
    coro = getattr(module, coro_name)
    asyncio.run(coro())


def _run_sync_entry(module: object, fn_name: str) -> None:
    fn = getattr(module, fn_name)
    fn()


def _run_messenger_loop(module: object) -> None:
    while True:
        try:
            module.run_cycle()
            try:
                module.update_heartbeat(SERVICE_NAME, "OK", "auxiliary messaging cycle complete")
            except Exception:
                pass
        except Exception as exc:
            try:
                module.update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            except Exception:
                pass
            log.exception("Messenger worker error: %s", exc)
        time.sleep(120)


def _worker_runner(spec: WorkerSpec) -> None:
    if spec.startup_delay > 0:
        time.sleep(spec.startup_delay)

    spec.status = "STARTING"
    spec.started_at = time.time()
    try:
        module = _load_module(spec.module_name)
    except ModuleNotFoundError as e:
        spec.status = "SKIPPED"
        log.warning(
            "Auxiliary worker %s not found — skipping (non-critical cognition lane): %s",
            spec.module_name, e,
        )
        return
    except Exception as e:
        spec.status = "ERROR"
        spec.last_error = str(e)[:200]
        log.warning("Auxiliary worker %s failed to load: %s", spec.module_name, e)
        return

    spec.status = "RUNNING"
    log.info("Worker online: %s", spec.module_name)
    spec.launcher(module)


def _start_worker(spec: WorkerSpec) -> None:
    def target() -> None:
        try:
            _worker_runner(spec)
        except Exception as exc:
            spec.status = "ERROR"
            spec.last_error = str(exc)[:200]
            log.exception("Worker crashed: %s", spec.module_name)
        else:
            spec.status = "STOPPED"
            log.warning("Worker exited cleanly: %s", spec.module_name)

    spec.thread = threading.Thread(
        target=target,
        name=f"aux::{spec.module_name.split('.')[-1]}",
        daemon=True,
    )
    spec.thread.start()


def _bootstrap_workers() -> None:
    worker_defs = [
        WorkerSpec("services.polaris_researcher", lambda m: _run_async_entry(m, "researcher_loop"), startup_delay=0),
        WorkerSpec("services.polaris_reflection", lambda m: _run_sync_entry(m, "run"), startup_delay=STARTUP_STAGGER_SECONDS),
        WorkerSpec("services.polaris_reviewer", lambda m: _run_sync_entry(m, "run"), startup_delay=STARTUP_STAGGER_SECONDS * 2),
        WorkerSpec("services.polaris_calibrator", lambda m: _run_sync_entry(m, "__main__") if False else _run_messenger_loop(m), startup_delay=0),
        WorkerSpec("services.polaris_messenger", _run_messenger_loop, startup_delay=STARTUP_STAGGER_SECONDS),
        WorkerSpec("services.polaris_channel_analyst", lambda m: _run_sync_entry(m, "__main__") if False else _run_channel_analyst_loop(m), startup_delay=STARTUP_STAGGER_SECONDS * 2),
    ]

    # Replace calibrator / channel analyst launchers with explicit loop wrappers.
    worker_defs[3].launcher = _run_calibrator_loop
    worker_defs[5].launcher = _run_channel_analyst_loop

    for spec in worker_defs:
        WORKERS[spec.module_name] = spec
        _start_worker(spec)


def _run_calibrator_loop(module: object) -> None:
    log.info("Calibrator worker booting")
    try:
        time.sleep(20)
    except Exception:
        pass
    while True:
        try:
            module.run_cycle()
        except Exception as exc:
            try:
                update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            except Exception:
                pass
            log.exception("Calibrator worker error: %s", exc)
        time.sleep(300)


def _run_channel_analyst_loop(module: object) -> None:
    log.info("Channel analyst worker booting")
    try:
        time.sleep(15)
    except Exception:
        pass
    while True:
        try:
            module.run_cycle()
        except Exception as exc:
            try:
                module.update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            except Exception:
                pass
            log.exception("Channel analyst worker error: %s", exc)
        time.sleep(getattr(module, "REVIEW_INTERVAL", 600))


def _summarise_workers() -> str:
    running = []
    failed = []
    pending = []

    for spec in WORKERS.values():
        alive = bool(spec.thread and spec.thread.is_alive())
        if alive and spec.status == "RUNNING":
            running.append(spec.module_name.split(".")[-1])
        elif spec.status == "ERROR":
            failed.append(spec.module_name.split(".")[-1])
        elif spec.status == "SKIPPED":
            pass  # Not installed — silently omit from status
        else:
            pending.append(spec.module_name.split(".")[-1])

    parts = [f"running={len(running)}"]
    if failed:
        parts.append("failed=" + ",".join(failed[:3]))
    elif pending:
        parts.append("warming=" + ",".join(pending[:3]))
    else:
        parts.append("all auxiliary lanes active")
    return " | ".join(parts)


def _restart_dead_workers_if_any() -> None:
    for spec in WORKERS.values():
        alive = bool(spec.thread and spec.thread.is_alive())
        if alive:
            continue
        if spec.status in {"ERROR", "STOPPED"}:
            log.warning("Restarting auxiliary worker: %s", spec.module_name)
            spec.status = "RESTARTING"
            spec.last_error = ""
            _start_worker(spec)
        # SKIPPED = module not found — do not restart, it will never succeed
        # elif spec.status == "SKIPPED": pass


def run() -> None:
    log.info("POLARIS AUXILIARY ONLINE — consolidated cognition lane")
    update_heartbeat(SERVICE_NAME, "ALIVE", "booting merged Polaris side-lanes")
    _bootstrap_workers()

    while True:
        try:
            _restart_dead_workers_if_any()
            note = _summarise_workers()
            update_heartbeat(SERVICE_NAME, "ALIVE", note)
            log.info(note)
            # Send daily update to Pop and Mum on first heartbeat of the day
            _daily_update_if_needed()
        except Exception as exc:
            log.exception("Auxiliary supervisor error: %s", exc)
            try:
                update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            except Exception:
                pass
        time.sleep(SUPERVISOR_PULSE_SECONDS)


if __name__ == "__main__":
    run()