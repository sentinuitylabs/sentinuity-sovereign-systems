# Sentinuity Vault-Triangulated Edge Restore

Surgical restore derived from five profitable archives (8, 9, 16, 17 and 20 July) against the 23 July regression tree.

Changes only:
- restores the resolver executor lifecycle shared by all five profitable builds;
- removes the 23 July paper stale-mark fallthrough that allowed stale marks to trigger hard-stop, runner, trail and max-hold exits;
- restores the profitable hard-stop configuration bound.

Preserved:
- current live fill reconciliation;
- dynamic wallet/account resolution;
- raw integer token truth;
- live emergency exits;
- current oracle outage classification;
- Council isolation;
- UI and all unrelated services.
