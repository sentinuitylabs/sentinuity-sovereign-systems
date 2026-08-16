"""services/schema.py — HARDENED RE-EXPORT SHIM
================================================================================
MODE3_FINAL_SIGNOFF_20260806

WHY THIS FILE WAS REPLACED
--------------------------
Two divergent schema modules existed in the workspace:

  core/schema.py      busy_timeout=30000, ResilientConnection + ResilientCursor,
                      _retry_locked() lock retry, foreign_keys=ON, WAL-if-needed.

  services/schema.py  busy_timeout=5000, NO ResilientConnection, NO retry
                      wrapper, unconditional `PRAGMA journal_mode=WAL` on every
                      connect (itself a lock request during a writer storm).

Both carried the identical banner "CONNECTION LAYER (HARDENED — PRODUCTION SAFE)",
so the unhardened one read as authoritative on inspection.

Several execution-path modules resolve `schema` by fallback, e.g.

  services/ws_price_oracle.py:1135        from schema import get_config_value
  services/copytrade_shadow_scanner.py:50 from schema import get_connection, ...
  services/copytrade_influence.py:61      from schema import DB_PATH, ...
  services/council_build_orchestrator.py:873

Any of those falling back would silently obtain the 5-second, no-retry
connection while the audit reported the hardened path as active.

Function-surface comparison proved services/schema.py exported NO symbol that
core/schema.py does not also export (services-only functions: none). The module
is therefore replaced with a strict re-export of core.schema, collapsing the two
paths into one.

ROLLBACK: restore services/schema.py.mode3_rollback_<timestamp>
================================================================================
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# core/ lives one level above services/. Guarantee the package root is importable
# whether this module is reached as `schema`, `services.schema`, or via a
# sys.path entry pointing directly at services/.
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

try:
    import core.schema as _core_schema
except Exception as _exc:  # pragma: no cover - fail loud, never fail quiet
    raise ImportError(
        "services/schema.py is a re-export shim for core.schema and core.schema "
        f"could not be imported from {_ROOT}: {_exc!r}. "
        "Execution-critical SQLite access must not silently fall back to an "
        "unhardened connection factory."
    ) from _exc

# ── Explicit re-exports (hardened connection factories) ───────────────────────
from core.schema import (  # noqa: F401,E402
    BASE_DIR,
    DB_PATH,
    LOCK_RETRY_MAX_SEC,
    LOCK_RETRY_BASE_SEC,
    ResilientConnection,
    ResilientCursor,
    get_connection,
    get_intel_connection,
    get_config_value,
    init_db,
    update_heartbeat,
)

# Optional symbols added by APPLY_MODE3_FINAL_SIGNOFF.py. Re-exported when
# present so the critical-write path is reachable through either import name.
for _optional in (
    "get_critical_connection",
    "CRITICAL_LOCK_RETRY_MAX_SEC",
    "record_critical_write_failure",
    "critical_write_blocker",
    "clear_critical_write_blocker",
):
    if hasattr(_core_schema, _optional):
        globals()[_optional] = getattr(_core_schema, _optional)

# Anything else core.schema exposes (helpers, table builders, migrations) is
# mirrored so no existing caller breaks.
for _name in dir(_core_schema):
    if _name.startswith("__"):
        continue
    globals().setdefault(_name, getattr(_core_schema, _name))

MODE3_SCHEMA_SHIM = True
MODE3_SCHEMA_SHIM_VERSION = "MODE3_FINAL_SIGNOFF_20260806"


def schema_connection_provenance() -> dict:
    """Report which module is actually serving connections. Used by preflight."""
    return {
        "shim": True,
        "version": MODE3_SCHEMA_SHIM_VERSION,
        "delegates_to": getattr(_core_schema, "__file__", "unknown"),
        "busy_timeout_ms": 30000,
        "resilient_cursor": hasattr(_core_schema, "ResilientCursor"),
        "critical_path": hasattr(_core_schema, "get_critical_connection"),
    }
