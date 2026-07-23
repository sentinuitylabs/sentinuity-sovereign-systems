# Sentinuity v2.1.2 — Runtime Edge Isolation and Offline Retention

This public paper-only release preserves the v2.1.1 real-money execution stubs and restores the overnight runtime boundary.

## Changes

- Removes `services.council_autobuilder` from the primary Sentinuity launcher.
- Adds an offline-only Council autobuilder launcher that refuses to run while trading/runtime processes are active.
- Ensures shutdown explicitly stops any stray Council autobuilder process.
- Replaces shutdown retention control flow with a direct archive-first retention runner.
- Retention runs even when Sentinuity is already offline.
- Waits for SQLite locks, creates a verified backup, archives old data, runs the canonical trim and `VACUUM`, and fails when the final matrix footprint remains above 20 MB.
- Adds edge-isolation verification against immutable overnight trading-core hashes.

No entry, exit, pattern, Lilypad, runner-lock, supervisor, oracle, freshness, signing, or transaction-submission logic is enabled or changed by this public patch.
