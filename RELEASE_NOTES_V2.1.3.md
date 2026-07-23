# Sentinuity v2.1.3 — Vault-Triangulated Paper Edge Restore

This public update preserves the v2.1.1 paper-only safety boundary and the
v2.1.2 Council/runtime isolation.

## Restored from profitable Code Vault eras

- Restores bounded resolver worker lifecycle in `services/ingest_pipeline.py`.
- Prevents stale/display-only router marks from driving ordinary paper exits.
- Restores bounded paper hard-stop configuration handling.
- Preserves current live-fill reconciliation code, while public funded execution
  remains disabled by the existing public safety stubs.

## Evidence basis

The changes were triangulated against five profitable archived states spanning
8–20 July 2026 rather than copied from a single historical build.

## Verification

Run:

```powershell
python .\VERIFY_PUBLIC_PAPER_ONLY.py
python .\VERIFY_EDGE_ISOLATION.py
python .\VERIFY_VAULT_EDGE_RESTORE.py
python -m compileall -q .
```

All three verifiers must pass before publishing.
