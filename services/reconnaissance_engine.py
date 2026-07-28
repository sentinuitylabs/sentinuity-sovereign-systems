"""
services/reconnaissance_engine.py

RECONNAISSANCE ENGINE — consolidated external scout lane
========================================================

Purpose
-------
Bring the external intelligence lanes back online as one managed service
instead of separate always-on windows.

Merged operational owner for:
  - wallet_scout.py
  - telegram_scout.py

Design
------
- One process
- One watchdog target
- One heartbeat identity: ``reconnaissance_engine``
- Internal worker threads run the existing scout loops
- The wrapper restarts a dead worker thread without needing a separate batch file

Scope restored
--------------
- wallet-follow intelligence
- Telegram call-channel observation
- channel data accumulation for Polaris auxiliary analysis

Important note
--------------
This service does not pretend the underlying APIs are equally reliable.
If Telegram credentials or Telethon are missing, the wallet scout can still
remain online and useful. The heartbeat note makes that visible.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import update_heartbeat

SERVICE_NAME = "reconnaissance_engine"
SUPERVISOR_PULSE_SECONDS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [RECON] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("reconnaissance_engine")


class Worker:
    def __init__(self, module_name: str, mode: str, startup_delay: int = 0) -> None:
        self.module_name = module_name
        self.mode = mode
        self.startup_delay = startup_delay
        self.thread: Optional[threading.Thread] = None
        self.status: str = "PENDING"
        self.error: str = ""


WORKERS: Dict[str, Worker] = {
    "services.wallet_scout":   Worker("services.wallet_scout",   mode="sync",  startup_delay=0),
    "services.telegram_scout": Worker("services.telegram_scout", mode="async", startup_delay=5),
    "services.x_scout":        Worker("services.x_scout",        mode="sync",  startup_delay=10),
}


def _load(module_name: str):
    module = importlib.import_module(module_name)
    try:
        if hasattr(module, "SERVICE_NAME"):
            setattr(module, "SERVICE_NAME", SERVICE_NAME)
    except Exception:
        pass
    return module


def _run_wallet(module) -> None:
    module.run()


def _run_telegram(module) -> None:
    asyncio.run(module.run_scout())


def _worker_target(worker: Worker) -> None:
    if worker.startup_delay > 0:
        time.sleep(worker.startup_delay)

    worker.status = "STARTING"
    module = _load(worker.module_name)
    worker.status = "RUNNING"
    log.info("Worker online: %s", worker.module_name)

    if worker.mode == "sync":
        _run_wallet(module)
    else:
        _run_telegram(module)


def _start(worker: Worker) -> None:
    def target() -> None:
        try:
            _worker_target(worker)
        except Exception as exc:
            worker.status = "ERROR"
            worker.error = str(exc)[:200]
            log.exception("Recon worker crashed: %s", worker.module_name)
        else:
            worker.status = "STOPPED"
            log.warning("Recon worker exited: %s", worker.module_name)

    worker.thread = threading.Thread(
        target=target,
        name=f"recon::{worker.module_name.split('.')[-1]}",
        daemon=True,
    )
    worker.thread.start()


def _restart_dead_workers() -> None:
    for worker in WORKERS.values():
        alive = bool(worker.thread and worker.thread.is_alive())
        if alive:
            continue
        if worker.status in {"ERROR", "STOPPED"}:
            log.warning("Restarting recon worker: %s", worker.module_name)
            worker.error = ""
            worker.status = "RESTARTING"
            _start(worker)


def _status_note() -> str:
    running = []
    failed = []
    warming = []
    for worker in WORKERS.values():
        alive = bool(worker.thread and worker.thread.is_alive())
        short = worker.module_name.split(".")[-1]
        if alive and worker.status == "RUNNING":
            running.append(short)
        elif worker.status == "ERROR":
            failed.append(short)
        else:
            warming.append(short)

    parts = [f"running={len(running)}"]
    if failed:
        parts.append("failed=" + ",".join(failed[:2]))
    elif warming:
        parts.append("warming=" + ",".join(warming[:2]))
    else:
        parts.append("wallet + telegram scouts active")
    return " | ".join(parts)


def run() -> None:
    log.info("RECONNAISSANCE ENGINE ONLINE — merged scout lane")
    update_heartbeat(SERVICE_NAME, "ALIVE", "booting wallet and telegram scouts")

    for worker in WORKERS.values():
        _start(worker)

    while True:
        try:
            _restart_dead_workers()
            note = _status_note()
            update_heartbeat(SERVICE_NAME, "ALIVE", note)
            log.info(note)
        except Exception as exc:
            log.exception("Recon supervisor error: %s", exc)
            try:
                update_heartbeat(SERVICE_NAME, "ERROR", str(exc)[:120])
            except Exception:
                pass
        time.sleep(SUPERVISOR_PULSE_SECONDS)


if __name__ == "__main__":
    run()