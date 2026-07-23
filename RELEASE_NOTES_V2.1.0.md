# Sentinuity V2.1.0 — Upgrade Notes

Baseline compared: **Sentinuity V2 GitHub release, 20 July 2026**  
Target snapshot: **23 July 2026 latest codebase**

## Upgrade summary

### Council autonomous build organism
- Added a canonical Council task ledger with durable phases, leases, restart-resume and legacy-task import.
- Added the Council autobuilder, capability-based apply policy, degraded-quorum debate handling, audit artefacts and build retrospectives.
- Unsupported legacy tasks are parked safely while executable tasks continue in the same cycle.
- Added production schema migration and verification utilities.

### Substrate Node truth and charting
- Added canonical Substrate history adapter and provider-aware price feed.
- The chart now consumes normalized records rather than selecting a table merely because it exists.
- Legacy/test positions can be quarantined by evidence without excluding genuine large winners or losers.
- Added lifecycle, sizing, price-probe and provider verification tooling.

### Solana execution and live-lane resilience
- Hardened ingest resolution against hung RPC/DNS futures so partial batches degrade instead of killing the lane.
- Updated live decision, settlement and pattern-arming contracts.
- Refined execution reconciliation, wallet truth and live/paper separation.
- Expanded oracle provider resilience and Guardian recovery behaviour.

### Lilypad, runner protection and PnL truth
- Continued sub-100 runner harvesting and monster-runner graduation logic.
- Improved position lifecycle and mark-truth handling around stale or fallback prices.
- Added paper/live divergence surfaces for operational comparison.

### Intelligence, World and UI
- Expanded Intelligence orchestration and UI surfaces.
- Added world build-state and narrative services plus Grand Vision V2 components.
- Upgraded constellation, cinematic, world-state and task surfaces.
- Added a public paper-only launcher.

### Operations and retention
- Added shutdown-retention runner and updated shutdown/prune scripts.
- Updated prelaunch, restart reset and database-retention handling.
- Removed obsolete duplicate service/UI modules and runtime artefacts from the release.

## Publishable source delta

- Added: **23** files
- Modified: **46** files
- Removed/retired: **7** files

See `UPDATE_MANIFEST.json` and `REMOVED_FILES.txt` for the exact paths.

## Installation

For a clean GitHub release, publish the full `Sentinuity-V2.1.0-GitHub-Release-20260723.zip`.

For an overlay update from V2.0.0:
1. Back up the repository.
2. Extract the update ZIP over the repository root.
3. Delete paths listed in `REMOVED_FILES.txt`.
4. Run Python compilation and project verification before launch.

## Security

The prepared release excludes databases, logs, caches, ZIPs, backups and environment-secret files. Operators must provide their own `.env` locally.
