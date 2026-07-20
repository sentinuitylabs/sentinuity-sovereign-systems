"""
services.openclaw_security_sentinel
===================================
Continuous OBSERVE/LOCKDOWN security sentinel for Sentinuity + OpenClaw.

Scope v1
--------
- Monitor .env integrity (hash, mtime, size, basic permissions note)
- Monitor likely dashboard/OpenClaw network exposure using netstat
- Detect common tunnel processes (ngrok/cloudflared/localtunnel/tailscale funnel)
- Detect suspicious new root scripts (.bat/.ps1/.py) after baseline
- Watch recent logs for Telegram burst/unknown-chat indicators when visible
- Heartbeat via core.schema.update_heartbeat when available
- Write security_events and related evidence tables

Important
---------
This service does NOT move, delete, encrypt, or print secrets. The key-sweep
"bee-sting" vault is a later v2 after dummy-key testing.

Default mode is OBSERVE to prevent false positives from bricking paper/live
launch. Set SECURITY_SENTINEL_RESPONSE_MODE=lockdown to make HIGH/CRITICAL
findings disable live risk keys automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

DB_PATH = BASE_DIR / "sentinuity_matrix.db"
ENV_PATH = BASE_DIR / ".env"
LOG_DIR = BASE_DIR / "logs"
SERVICE_NAME = "openclaw_security_sentinel"

try:
    from core.schema import get_connection, update_heartbeat, get_config_value  # type: ignore
    HAS_SCHEMA = True
except Exception:
    HAS_SCHEMA = False
    get_connection = None  # type: ignore
    update_heartbeat = None  # type: ignore
    get_config_value = None  # type: ignore


DEFAULT_PORTS = "8501,8502,8000,8080,3000,5000,7860,11434"
TUNNEL_PROCESS_KEYWORDS = ["ngrok", "cloudflared", "localtunnel", "lt.exe", "tailscale"]
SCRIPT_EXTS = {".py", ".bat", ".ps1", ".cmd"}
ROOT_SCRIPT_ALLOWLIST = {
    "INSTALL_PUBLIC_PAPER.bat", "LAUNCH_PUBLIC_PAPER.bat", "STOP_SENTINUITY.bat",
    "Launch_Sentinuity.bat", "Launch_Sentinuity_Public_Paper.bat",
    "Shutdown_Sentinuity.bat", "Watchdog_Sentinuity.bat",
    "Sentinuity_Watch.bat", "sovereign_security_preflight.py",
    "paranoid_scan.py", "p1_runtime_proof.py", "verify_autonomous_build_signoff.py",
    "launch_config.py", "set_live_mode.py",
}


def now() -> float:
    return time.time()


def connect() -> sqlite3.Connection:
    if HAS_SCHEMA and get_connection is not None:
        return get_connection()  # type: ignore[no-any-return]
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def cfg(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        if HAS_SCHEMA and get_config_value is not None:
            return str(get_config_value(key, default))
    except Exception:
        pass
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return str(row["value"] if isinstance(row, sqlite3.Row) else row[0]) if row else default
    except Exception:
        return default


def set_cfg(conn: sqlite3.Connection, key: str, value: str, description: str = "security sentinel") -> None:
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT, description TEXT)"
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(system_config)").fetchall()}
        if "description" in cols:
            conn.execute(
                "INSERT OR REPLACE INTO system_config(key,value,description) VALUES(?,?,?)",
                (key, value, description),
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO system_config(key,value) VALUES(?,?)",
                (key, value),
            )
    except Exception:
        pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            severity TEXT,
            source TEXT,
            event_type TEXT,
            message TEXT,
            details_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS env_integrity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            env_hash TEXT,
            env_mtime REAL,
            env_size INTEGER,
            permission_note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS network_exposure_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            severity TEXT,
            local_address TEXT,
            local_port INTEGER,
            remote_address TEXT,
            process_name TEXT,
            message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_anomaly_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            severity TEXT,
            event_type TEXT,
            message TEXT,
            details_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_baseline (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_lockdown_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            active INTEGER DEFAULT 0,
            severity TEXT,
            reason TEXT,
            updated_at REAL
        )
        """
    )
    conn.commit()


def heartbeat(status: str, note: str = "") -> None:
    try:
        if HAS_SCHEMA and update_heartbeat is not None:
            try:
                update_heartbeat(SERVICE_NAME, status, note=note[:240])  # type: ignore[misc]
                return
            except TypeError:
                update_heartbeat(SERVICE_NAME, status)  # type: ignore[misc]
                return
        with connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_heartbeat (
                    service_name TEXT PRIMARY KEY,
                    last_pulse REAL,
                    status TEXT,
                    note TEXT
                )
                """
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(system_heartbeat)").fetchall()}
            if "note" in cols:
                conn.execute(
                    "INSERT OR REPLACE INTO system_heartbeat(service_name,last_pulse,status,note) VALUES(?,?,?,?)",
                    (SERVICE_NAME, now(), status, note[:240]),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO system_heartbeat(service_name,last_pulse,status) VALUES(?,?,?)",
                    (SERVICE_NAME, now(), status),
                )
            conn.commit()
    except Exception:
        pass


def log_event(conn: sqlite3.Connection, severity: str, event_type: str, message: str, details: Optional[dict[str, Any]] = None) -> None:
    conn.execute(
        """
        INSERT INTO security_events(created_at,severity,source,event_type,message,details_json)
        VALUES(?,?,?,?,?,?)
        """,
        (now(), severity, SERVICE_NAME, event_type, message, json.dumps(details or {}, sort_keys=True)),
    )


def maybe_lockdown(conn: sqlite3.Connection, severity: str, reason: str) -> None:
    mode = cfg(conn, "SECURITY_SENTINEL_RESPONSE_MODE", "observe").strip().lower()
    if mode not in {"lockdown", "active", "enforce"}:
        return
    if severity not in {"HIGH", "CRITICAL"}:
        return

    for key, value in [
        ("SECURITY_LOCKDOWN_ACTIVE", "1"),
        ("LIVE_TRADING_ENABLED", "0"),
        ("RUNNER_LIVE_SCALE_ENABLED", "0"),
        ("SMART_WALLET_LIVE_ENABLED", "0"),
        ("LATCHED_OVERRIDE_PATH", "0"),
        ("LATCHED_OVERRIDE_ENABLED", "0"),
    ]:
        set_cfg(conn, key, value, f"{SERVICE_NAME}: {reason}")

    conn.execute(
        """
        INSERT OR REPLACE INTO security_lockdown_state(id,active,severity,reason,updated_at)
        VALUES(1,1,?,?,?)
        """,
        (severity, reason[:500], now()),
    )
    log_event(conn, "CRITICAL", "LOCKDOWN_APPLIED", "Live risk keys disabled by security sentinel", {"reason": reason, "severity": severity})


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def baseline_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM security_baseline WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return str(row["value"] if isinstance(row, sqlite3.Row) else row[0])


def baseline_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO security_baseline(key,value,updated_at) VALUES(?,?,?)",
        (key, value, now()),
    )


def scan_env(conn: sqlite3.Connection) -> list[tuple[str, str, str, dict[str, Any]]]:
    findings: list[tuple[str, str, str, dict[str, Any]]] = []
    if not ENV_PATH.exists():
        findings.append(("HIGH", "ENV_MISSING", ".env is missing", {}))
        return findings

    st = ENV_PATH.stat()
    env_hash = file_sha256(ENV_PATH)
    perm_note = "windows-basic" if os.name == "nt" else oct(st.st_mode & 0o777)
    conn.execute(
        """
        INSERT INTO env_integrity_snapshots(created_at,env_hash,env_mtime,env_size,permission_note)
        VALUES(?,?,?,?,?)
        """,
        (now(), env_hash, st.st_mtime, st.st_size, perm_note),
    )

    prev_hash = baseline_get(conn, "env_hash")
    prev_mtime = baseline_get(conn, "env_mtime")
    if not prev_hash:
        baseline_set(conn, "env_hash", env_hash)
        baseline_set(conn, "env_mtime", str(st.st_mtime))
        findings.append(("INFO", "ENV_BASELINE_SET", ".env integrity baseline recorded", {"size": st.st_size}))
    elif prev_hash != env_hash:
        findings.append(("HIGH", "ENV_HASH_CHANGED", ".env content hash changed since baseline", {"old_hash": prev_hash[:12], "new_hash": env_hash[:12]}))
        # Update baseline only if operator has allowed auto-baseline. Default false.
        if cfg(conn, "SECURITY_AUTO_ACCEPT_ENV_BASELINE", "0") == "1":
            baseline_set(conn, "env_hash", env_hash)
            baseline_set(conn, "env_mtime", str(st.st_mtime))
    elif prev_mtime and str(st.st_mtime) != prev_mtime:
        findings.append(("LOW", "ENV_MTIME_CHANGED", ".env mtime changed but content hash is unchanged", {"mtime": st.st_mtime}))
        baseline_set(conn, "env_mtime", str(st.st_mtime))
    return findings


def run_command(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except Exception:
        return ""


def parse_ports(raw: str) -> set[int]:
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except Exception:
            pass
    return out or {8501, 8502, 8000, 8080, 3000, 5000, 7860, 11434}


def scan_network(conn: sqlite3.Connection) -> list[tuple[str, str, str, dict[str, Any]]]:
    findings: list[tuple[str, str, str, dict[str, Any]]] = []
    watched_ports = parse_ports(cfg(conn, "OPENCLAW_SECURITY_PORTS", DEFAULT_PORTS))
    require_localhost = cfg(conn, "OPENCLAW_REQUIRE_LOCALHOST", "1") == "1"

    output = run_command(["netstat", "-ano"], timeout=6.0)
    if not output.strip():
        findings.append(("LOW", "NETSTAT_UNAVAILABLE", "Could not read netstat output", {}))
        return findings

    for line in output.splitlines():
        if "LISTEN" not in line.upper() and "ESTABLISHED" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0]
        local = parts[1]
        remote = parts[2] if len(parts) > 2 else ""
        state = parts[3] if len(parts) > 3 else ""
        pid = parts[-1] if parts[-1].isdigit() else ""
        m = re.search(r":(\d+)$", local)
        if not m:
            continue
        port = int(m.group(1))
        if port not in watched_ports:
            continue
        local_addr = local.rsplit(":", 1)[0].strip("[]")
        remote_addr = remote.rsplit(":", 1)[0].strip("[]") if remote else ""

        if require_localhost and state.upper().startswith("LISTEN") and local_addr in {"0.0.0.0", "::", "[::]"}:
            msg = f"Watched port {port} is listening on all interfaces ({local_addr})"
            findings.append(("HIGH", "OPENCLAW_PORT_EXPOSED", msg, {"local": local, "pid": pid, "port": port}))
            conn.execute(
                "INSERT INTO network_exposure_events(created_at,severity,local_address,local_port,remote_address,process_name,message) VALUES(?,?,?,?,?,?,?)",
                (now(), "HIGH", local_addr, port, remote_addr, pid, msg),
            )
        elif state.upper().startswith("ESTABLISHED") and remote_addr not in {"", "127.0.0.1", "::1", "localhost"} and not remote_addr.startswith("192.168."):
            msg = f"External established connection detected on watched port {port}"
            findings.append(("MEDIUM", "OPENCLAW_REMOTE_CONNECTION", msg, {"local": local, "remote": remote, "pid": pid, "port": port}))
            conn.execute(
                "INSERT INTO network_exposure_events(created_at,severity,local_address,local_port,remote_address,process_name,message) VALUES(?,?,?,?,?,?,?)",
                (now(), "MEDIUM", local_addr, port, remote_addr, pid, msg),
            )
    return findings


def scan_processes(conn: sqlite3.Connection) -> list[tuple[str, str, str, dict[str, Any]]]:
    findings: list[tuple[str, str, str, dict[str, Any]]] = []
    output = ""
    if os.name == "nt":
        output = run_command(["tasklist"], timeout=5.0)
    else:
        output = run_command(["ps", "aux"], timeout=5.0)
    low = output.lower()
    for keyword in TUNNEL_PROCESS_KEYWORDS:
        if keyword.lower() in low:
            severity = "HIGH" if keyword.lower() in {"ngrok", "cloudflared", "localtunnel", "lt.exe"} else "MEDIUM"
            findings.append((severity, "TUNNEL_PROCESS_DETECTED", f"Tunnel/exposure process detected: {keyword}", {"keyword": keyword}))
    return findings


def scan_root_scripts(conn: sqlite3.Connection) -> list[tuple[str, str, str, dict[str, Any]]]:
    findings: list[tuple[str, str, str, dict[str, Any]]] = []
    current = sorted(p.name for p in BASE_DIR.iterdir() if p.is_file() and p.suffix.lower() in SCRIPT_EXTS)
    current_json = json.dumps(current)
    prev = baseline_get(conn, "root_scripts")
    if prev is None:
        baseline_set(conn, "root_scripts", current_json)
        findings.append(("INFO", "ROOT_SCRIPT_BASELINE_SET", "Root script baseline recorded", {"count": len(current)}))
        return findings
    try:
        prev_set = set(json.loads(prev))
    except Exception:
        prev_set = set()
    new_files = [name for name in current if name not in prev_set and name not in ROOT_SCRIPT_ALLOWLIST]
    if new_files:
        findings.append(("MEDIUM", "NEW_ROOT_SCRIPT", "New root executable/script file(s) detected", {"files": new_files[:20]}))
    baseline_set(conn, "root_scripts", current_json)
    return findings


def scan_telegram_logs(conn: sqlite3.Connection) -> list[tuple[str, str, str, dict[str, Any]]]:
    findings: list[tuple[str, str, str, dict[str, Any]]] = []
    if not LOG_DIR.exists():
        return findings
    max_msgs = int(float(cfg(conn, "TELEGRAM_MAX_MSGS_PER_MINUTE", "20") or 20))
    cutoff = now() - 90
    hits = 0
    unknown_chat = False
    owner_id = cfg(conn, "TELEGRAM_OWNER_ID", "")
    patterns = ["telegram", "sendmessage", "getupdates", "bot"]
    for path in list(LOG_DIR.glob("*.log"))[-40:]:
        try:
            if path.stat().st_mtime < cutoff:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")[-20000:]
        except Exception:
            continue
        low = text.lower()
        if any(p in low for p in patterns):
            hits += sum(low.count(p) for p in patterns)
        if owner_id and "chat_id" in low and owner_id not in text:
            unknown_chat = True
    if hits > max_msgs * 2:
        msg = f"Telegram-related log burst detected ({hits} markers in recent logs)"
        findings.append(("MEDIUM", "TELEGRAM_BURST", msg, {"markers": hits, "threshold": max_msgs}))
        conn.execute(
            "INSERT INTO telegram_anomaly_events(created_at,severity,event_type,message,details_json) VALUES(?,?,?,?,?)",
            (now(), "MEDIUM", "TELEGRAM_BURST", msg, json.dumps({"markers": hits})),
        )
    if unknown_chat:
        msg = "Log evidence suggests Telegram chat_id outside TELEGRAM_OWNER_ID"
        findings.append(("HIGH", "TELEGRAM_UNKNOWN_CHAT", msg, {}))
        conn.execute(
            "INSERT INTO telegram_anomaly_events(created_at,severity,event_type,message,details_json) VALUES(?,?,?,?,?)",
            (now(), "HIGH", "TELEGRAM_UNKNOWN_CHAT", msg, "{}"),
        )
    return findings


def run_once(verbose: bool = True) -> int:
    try:
        with connect() as conn:
            ensure_schema(conn)
            findings: list[tuple[str, str, str, dict[str, Any]]] = []
            findings += scan_env(conn)
            findings += scan_network(conn)
            findings += scan_processes(conn)
            findings += scan_root_scripts(conn)
            findings += scan_telegram_logs(conn)

            max_sev = "INFO"
            sev_rank = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
            for severity, event_type, message, details in findings:
                log_event(conn, severity, event_type, message, details)
                if sev_rank.get(severity, 0) > sev_rank.get(max_sev, 0):
                    max_sev = severity
            if max_sev in {"HIGH", "CRITICAL"}:
                maybe_lockdown(conn, max_sev, "; ".join(f[2] for f in findings if f[0] in {"HIGH", "CRITICAL"})[:500])
            conn.commit()

            status = "OK" if max_sev in {"INFO", "LOW"} else ("WARN" if max_sev == "MEDIUM" else "ALERT")
            heartbeat(status, f"findings={len(findings)} max={max_sev}")
            if verbose:
                print(f"[{SERVICE_NAME}] {status} findings={len(findings)} max={max_sev}")
                for severity, event_type, message, details in findings:
                    print(f"  [{severity:<8}] {event_type}: {message}")
            return 0 if max_sev not in {"CRITICAL"} else 2
    except Exception as exc:
        heartbeat("ERROR", f"{type(exc).__name__}: {exc}"[:240])
        if verbose:
            print(f"[{SERVICE_NAME}] ERROR {type(exc).__name__}: {exc}")
        return 1


def service_loop(interval: float) -> None:
    print(f"[{SERVICE_NAME}] starting interval={interval}s mode=observe/lockdown via SECURITY_SENTINEL_RESPONSE_MODE")
    while True:
        run_once(verbose=True)
        time.sleep(interval)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sentinuity OpenClaw/.env security sentinel")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--interval", type=float, default=30.0, help="Loop interval in seconds")
    args = parser.parse_args(argv)
    if args.once:
        return run_once(verbose=True)
    service_loop(max(10.0, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
