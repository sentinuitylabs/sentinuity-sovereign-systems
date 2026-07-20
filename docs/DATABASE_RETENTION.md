# Database retention and the 10–20 MB target

The launch-clean stop script used only a passive WAL checkpoint. That safer public stop no longer invoked archive-first retention or `VACUUM`, so file growth was expected; it is not evidence that a single added column caused the bloat.

`tools/audit_db_growth.py` reports the largest tables, indexes, row counts and reclaimable free pages. `tools/shutdown_maintenance.py` restores guarded maintenance. It requires a healthy database, no open/active positions and no fresh service heartbeat; creates a full backup; archives removed rows to `sentinuity_archive.db`; reconciles additive columns in archive tables; trims only defined high-churn tails; checkpoints and vacuums; and records whether the 10 MB target or 20 MB ceiling was reached.

Protected state is never deleted merely to meet a cosmetic file-size target. If protected tables or indexes legitimately exceed 20 MB, the report identifies them for a deliberate policy review.
