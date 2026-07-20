# Launch Folder Audit â€” V2 Public Paper Sign-off

## Result

The public release launch folder was reduced from **54 files to 18 canonical files**.

The audit followed actual references from:

- root installation, launch and stop entry points;
- `launch/Launch_Sentinuity_Public_Paper.bat`;
- the canonical `launch/Launch_Sentinuity.bat` service graph;
- preflight and security services;
- README and user documentation.

## Safety corrections

1. Removed the private hard-coded `C:\Path\To\Sentinuity` shutdown/restart assumption.
2. Replaced the old blunt shutdown, which could kill every `python.exe`, OpenClaw, tunnels and unrelated localhost services.
3. The public stop path now terminates only processes whose command line belongs to the extracted Sentinuity repository and only releases Sentinuity-owned ports `8501` and `8766`.
4. The canonical launcher no longer falls back to the developer's private workstation path.
5. The watchdog now treats the optional Forge snapshot script as optional instead of printing a startup error.

## Canonical retained files

- `FORCE_PAPER_SAFE_PRESTART_0707.py`
- `Launch_Sentinuity.bat`
- `Launch_Sentinuity_Public_Paper.bat`
- `SENTINUITY_SOVEREIGN_DOCTRINE.md`
- `Sentinuity_Watch.bat`
- `Shutdown_Sentinuity.bat`
- `VERIFY_LAUNCH_READY.py`
- `Watchdog_Sentinuity.bat`
- `db_retention_trim.py`
- `forge_genesis_seed.py`
- `launch_config.py`
- `preflight_verifier.py`
- `prelaunch.py`
- `set_live_mode.py`
- `sovereign_security_preflight.py`
- `startup_freshness_purge.py`
- `startup_restart_position_reset.py`
- `sync_historical_trade_cache.py`

## Removed as obsolete, duplicated or misplaced

### Historical May-22 migration layer

- `APPLY_MAY22_PAPER_SIGNOFF.py`
- `CHECK_MAY22_BOOT_HEALTH.py`
- `CLEAN_MAY22_LAYOUT.py`
- `FIX_MAY22_RUNTIME_SCHEMA.py`
- `FIX_MAY22_SUPERVISOR_SCHEMA_AND_RESET.py`
- `Launch_MAY22_PAPER_ONLY.bat`
- `ROTATE_MAY22_LOGS.py`
- `VERIFY_MAY22_PAPER_PRELAUNCH.py`

### One-off patch and dual/live operator utilities

- `APPLY_DUAL_MODE_LAUNCH_GUARD_FIX.py`
- `AUDIT_DUAL_MODE_PREFLIGHT.py`
- `DISARM_EXECUTION_FLAG.py`
- `arm_dual_mode.py`
- `dual_mode_launch_config.py`
- `kill_live.py`
- `launch_truth.py`

These do not belong in a paper-only public launch surface. The public launcher enforces paper mode directly.

### Duplicate stop, restart, watchdog and prune paths

- `EXPRESS_SHUTDOWN_SAFE_PRUNE.bat`
- `PRUNE_DB_SAFE_NOW.bat`
- `Prune_Sentinuity_Offline.bat`
- `Restart_Sentinuity.bat`
- `Restart_Sentinuity_Tight.bat`
- `Sentinuity_Watch.bat` duplicate variants were reviewed; only the canonical read-only monitor remains.
- `Shutdown_Sentinuity_Express.bat`
- `Stop_All.bat`
- `Stop_Sentinuity.bat`
- `targeted_matrix_compactor_v5.py`

The root `STOP_SENTINUITY.bat` is now the sole user-facing stop command.

### Disconnected audits and maintenance helpers

- `.env shape.txt`
- `AUDIT_DB_SIZE_AND_PRUNE_PLAN.bat`
- `audit_db_retention_coverage.py`
- `checkpoint_dbs.py`
- `db_bloat_audit.py`
- `replay_nto_case.py`
- `shutdown_db_maintenance.py`
- `shutdown_mark_stopped.py`
- `verify_state.py`

### Duplicate or legacy doctrine/configuration files

- `SOVEREIGN_DOCTRINE.md`
- `set_config.py`

The canonical doctrine is `SENTINUITY_SOVEREIGN_DOCTRINE.md`; generic configuration mutation is not exposed from the public launch folder.

## Public entry points

Users should use only:

- `INSTALL_PUBLIC_PAPER.bat`
- `LAUNCH_PUBLIC_PAPER.bat`
- `STOP_SENTINUITY.bat`

