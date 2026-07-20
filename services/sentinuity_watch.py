from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "sentinuity_matrix.db"
MANIFEST = ROOT / "PROTECTED_0707_EDGE_MANIFEST.json"
INTERVAL = 30
STALE_SECONDS = 300


def ro_connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    return con


def _epoch_seconds(value: Any) -> float:
    """Normalize seconds/milliseconds/microseconds/nanoseconds to Unix seconds."""
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if ts > 1e17:      # nanoseconds
        ts /= 1e9
    elif ts > 1e14:    # microseconds
        ts /= 1e6
    elif ts > 1e11:    # milliseconds
        ts /= 1e3
    return ts


def protected_ok() -> list[tuple[str, str]]:
    if not MANIFEST.exists():
        return [(MANIFEST.name, "MANIFEST_MISSING")]
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        return [(MANIFEST.name, f"MANIFEST_READ_ERROR:{exc}")]

    bad: list[tuple[str, str]] = []
    for row in data.get("protected", []):
        rel = str(row.get("path", ""))
        expected = str(row.get("sha256", ""))
        path = ROOT / rel
        got = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
        if got != expected:
            bad.append((rel, got))
    return bad


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})")}


def _canonical_heartbeats(con: sqlite3.Connection, tables: set[str]) -> list[dict[str, Any]]:
    """
    Canonical source is system_heartbeat, which is where core.schema.update_heartbeat
    writes every live service pulse. service_heartbeats is guardian lease/legacy state
    only and must never override a fresh canonical pulse.
    """
    now = time.time()
    merged: dict[str, dict[str, Any]] = {}

    if "system_heartbeat" in tables:
        columns = _columns(con, "system_heartbeat")
        if {"service_name", "last_pulse"}.issubset(columns):
            select = ["service_name", "last_pulse"]
            select.append("status" if "status" in columns else "NULL AS status")
            select.append("note" if "note" in columns else "NULL AS note")
            rows = con.execute(
                f"SELECT {', '.join(select)} FROM system_heartbeat ORDER BY service_name"
            ).fetchall()
            for row in rows:
                name = str(row["service_name"] or "").strip()
                if not name:
                    continue
                pulse = _epoch_seconds(row["last_pulse"])
                age = max(0.0, now - pulse) if pulse > 0 else float("inf")
                merged[name] = {
                    "name": name,
                    "age_s": round(age, 1),
                    "status": row["status"] or "UNKNOWN",
                    "note": row["note"] or "",
                    "source": "system_heartbeat",
                }

    # Add only legacy/guardian rows for names absent from the canonical table.
    if "service_heartbeats" in tables:
        columns = _columns(con, "service_heartbeats")
        timestamp_col = next(
            (c for c in ("last_heartbeat", "last_seen", "updated_at") if c in columns),
            None,
        )
        if "service_name" in columns and timestamp_col:
            select = ["service_name", f"{timestamp_col} AS pulse"]
            select.append("status" if "status" in columns else "NULL AS status")
            select.append("note" if "note" in columns else "NULL AS note")
            rows = con.execute(
                f"SELECT {', '.join(select)} FROM service_heartbeats ORDER BY service_name"
            ).fetchall()
            for row in rows:
                name = str(row["service_name"] or "").strip()
                if not name or name in merged:
                    continue
                pulse = _epoch_seconds(row["pulse"])
                age = max(0.0, now - pulse) if pulse > 0 else float("inf")
                merged[name] = {
                    "name": name,
                    "age_s": round(age, 1),
                    "status": row["status"] or "UNKNOWN",
                    "note": row["note"] or "",
                    "source": "service_heartbeats_legacy",
                }

    return sorted(merged.values(), key=lambda x: x["name"])


def db_snapshot() -> dict[str, Any]:
    if not DB.exists():
        return {"db": "MISSING"}

    out: dict[str, Any] = {"db_mb": round(DB.stat().st_size / 1048576, 2)}
    try:
        con = ro_connect()
        tables = {str(r[0]) for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        out["services"] = _canonical_heartbeats(con, tables)
        out["heartbeat_tables"] = {
            "system_heartbeat": "system_heartbeat" in tables,
            "service_heartbeats": "service_heartbeats" in tables,
        }

        if "system_config" in tables:
            keys = [
                "TRADING_MODE",
                "PAPER_TRADING_ENABLED",
                "LIVE_TRADING_ENABLED",
                "LIVE_MAX_OPEN_POSITIONS",
                "SUPERVISOR_MIN_MINT_CONFIDENCE",
                "EXECUTOR_MAX_SIGNAL_AGE_SEC",
            ]
            qmarks = ",".join("?" for _ in keys)
            out["config"] = {
                r[0]: r[1]
                for r in con.execute(
                    f"SELECT key,value FROM system_config WHERE key IN ({qmarks})", keys
                )
            }
        con.close()
    except Exception as exc:
        out["db_error"] = str(exc)
    return out


def main() -> None:
    print("SENTINUITY WATCH - READ ONLY")
    print("Canonical heartbeats: system_heartbeat; legacy guardian rows are fallback only.")
    print("No restarts, configuration writes, position changes, or pipeline mutations.")

    while True:
        bad = protected_ok()
        snap = db_snapshot()
        print()
        print("=" * 72)
        print(time.strftime("%Y-%m-%d %H:%M:%S"))
        print("PROTECTED EDGE:", "PASS" if not bad else "FAIL")
        for path, digest in bad:
            print("  CHANGED:", path, digest)

        print("DB:", snap.get("db_mb", snap.get("db")), "MB")
        cfg = snap.get("config", {})
        if cfg:
            print("MODE:", cfg)

        services = snap.get("services", [])
        if services:
            stale = [x for x in services if x["age_s"] > STALE_SECONDS]
            canonical = sum(1 for x in services if x["source"] == "system_heartbeat")
            legacy = len(services) - canonical
            print(
                f"HEARTBEATS: {len(services)} services; canonical={canonical}; "
                f"legacy-only={legacy}; stale>{STALE_SECONDS}s={len(stale)}"
            )
            for item in stale[:20]:
                print(
                    f"  STALE {item['name']}: {item['age_s']}s "
                    f"status={item['status']} source={item['source']}"
                )
        else:
            print("HEARTBEATS: none found in system_heartbeat or service_heartbeats")

        if snap.get("db_error"):
            print("DB READ ERROR:", snap["db_error"])
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
