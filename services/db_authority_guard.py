"""
Sentinuity DB Authority Guard V5 — runtime canonical DB enforcement.

Approved live databases:
- sentinuity_matrix.db
- sentinuity_intelligence.db

Any accidental sqlite3.connect("trading.db") / legacy fallback is redirected to
sentinuity_matrix.db so SQLite cannot silently create a third live DB.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
MATRIX_DB = ROOT / "sentinuity_matrix.db"
INTELLIGENCE_DB = ROOT / "sentinuity_intelligence.db"

DISALLOWED_DB_NAMES = {
    "trading.db",
    "sentinuity.db",
    "openclaw.db",
    "matrix.db",
    "bot.db",
    "paper_trading.db",
    "sentinuity_trading.db",
    "trading.sqlite",
    "trading.sqlite3",
    "sentinuity.sqlite",
    "sentinuity.sqlite3",
    "openclaw.sqlite",
    "openclaw.sqlite3",
    "intelligence.db",
}

_APPROVED_BASENAMES = {
    "sentinuity_matrix.db",
    "sentinuity_intelligence.db",
}

_ORIGINAL_CONNECT = sqlite3.connect
_INSTALLED = False


def approved_paths() -> Dict[str, str]:
    return {
        "matrix": str(MATRIX_DB),
        "intelligence": str(INTELLIGENCE_DB),
    }


def _normalise_database_arg(database: Any) -> Any:
    if database is None:
        return database

    # Leave file descriptors, memory DBs, and non-string objects alone unless path-like.
    if not isinstance(database, (str, bytes, os.PathLike)):
        return database

    raw = os.fspath(database)
    if isinstance(raw, bytes):
        try:
            raw_s = raw.decode("utf-8", errors="ignore")
        except Exception:
            return database
    else:
        raw_s = str(raw)

    stripped = raw_s.strip()
    lower = stripped.lower()

    if lower in {":memory:", ""}:
        return database

    # URI handling: sqlite accepts file:foo.db?mode=...
    uri_payload = lower
    if uri_payload.startswith("file:"):
        uri_payload = uri_payload[5:].split("?", 1)[0]

    try:
        basename = Path(uri_payload).name.lower()
    except Exception:
        basename = os.path.basename(uri_payload).lower()

    if basename in _APPROVED_BASENAMES:
        return database

    if basename in DISALLOWED_DB_NAMES:
        return str(MATRIX_DB)

    return database


def guarded_connect(database: Any, *args: Any, **kwargs: Any):
    return _ORIGINAL_CONNECT(_normalise_database_arg(database), *args, **kwargs)


def install() -> bool:
    global _INSTALLED
    if sqlite3.connect is not guarded_connect:
        sqlite3.connect = guarded_connect  # type: ignore[assignment]
    _INSTALLED = True
    return True


def status() -> Dict[str, Any]:
    return {
        "installed": sqlite3.connect is guarded_connect,
        "approved_paths": approved_paths(),
        "root": str(ROOT),
    }


# Install on import for normal service startup and sitecustomize.
install()
