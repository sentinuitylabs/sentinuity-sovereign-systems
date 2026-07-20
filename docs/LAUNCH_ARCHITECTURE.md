# Sentinuity V2 Public Paper Launch Architecture

The public release intentionally contains one canonical launch path and one safe stop path.

## User entry points

- `INSTALL_PUBLIC_PAPER.bat` — first-time virtual-environment and dependency setup.
- `LAUNCH_PUBLIC_PAPER.bat` — normal one-double-click startup.
- `STOP_SENTINUITY.bat` — repository-scoped shutdown; it does not kill unrelated Python applications.

## Canonical launch chain

`LAUNCH_PUBLIC_PAPER.bat`
→ `launch/Launch_Sentinuity_Public_Paper.bat`
→ `launch/FORCE_PAPER_SAFE_PRESTART_0707.py`
→ `launch/VERIFY_LAUNCH_READY.py`
→ `launch/Launch_Sentinuity.bat`

The canonical launcher starts the local Streamlit Sovereign Hub, opens `http://localhost:8501`, and starts the paper-mode service organism.

## Retained launch support files

- `startup_freshness_purge.py` and `startup_restart_position_reset.py`: startup recovery and stale-state control.
- `launch_config.py`, `prelaunch.py`, `preflight_verifier.py`, `set_live_mode.py`: configuration and preflight contracts used by the canonical launcher.
- `forge_genesis_seed.py`: Council/build schema seeding.
- `Watchdog_Sentinuity.bat` and `Sentinuity_Watch.bat`: guardian and read-only monitoring surfaces started by the canonical launcher.
- `Shutdown_Sentinuity.bat`: portable public-safe shutdown.
- `db_retention_trim.py` and `sync_historical_trade_cache.py`: retained for compatibility and controlled maintenance, but the public stop path does not automatically perform destructive retention.
- `sovereign_security_preflight.py`: security validation utility retained for current security-service references.
- `SENTINUITY_SOVEREIGN_DOCTRINE.md`: doctrine contract referenced by prelaunch verification.

## Removed historical files

The public release excludes one-off May 22 migration scripts, dual/live arming utilities, duplicate shutdown/restart/watchdog variants, manual database-prune BAT files, old patch applicators, duplicated doctrine files, and disconnected audit helpers. They were not part of the current public-paper launch chain.
