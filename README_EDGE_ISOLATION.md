# Sentinuity edge-isolation sign-off

This patch restores the overnight runtime boundary without changing trading logic.

## Changes

- Removes `services.council_autobuilder` from the primary trading launcher.
- Adds `launch/Run_Council_Autobuilder_Offline.bat`, which refuses to run while trading/runtime processes are active.
- Adds explicit CouncilAutobuilder matching to shutdown sweeps.
- Adds `VERIFY_EDGE_ISOLATION.py` with immutable hashes for the overnight-profitable trading-core files.

Council research/build work and trading must run in separate windows. The Council engine remains installed; only concurrent execution is prohibited.

## Shutdown retention

`launch/Shutdown_Sentinuity.bat` now invokes `launch/shutdown_retention_runner.py` directly. The retention pass runs whether the system was active or already offline. It waits for database locks, creates a verified backup, performs archive-first pruning, vacuums SQLite, and requires the final matrix DB/WAL/SHM footprint to be no more than 20 MB.
