\
# Release notes — v2.1.4

## Edge reconstruction

The release is based on the intersection of the strongest post-runner-leakage
states captured around 14–16 July 2026 and the latest supplied codebase.

### Corrected

1. Resolver cycles no longer abandon non-waiting worker pools after DNS stalls.
2. Paper hard-stop persistence consumes the policy-selected exit price/reason.
3. Pattern confirmation recognises trusted persisted peaks as well as realised PnL.
4. The post-13-July runner-lock ladder remains enforced:
   - 75–99% peak: maximum 10 percentage-point giveback.
   - 100–149% peak: controlled 18-point giveback with the established floor.

### Preserved

- public paper-only execution boundary;
- current transaction/wallet reconciliation structures;
- Lilypad and runner-profit-lock;
- documentary live-canary maturity checks;
- Council runtime isolation from v2.1.2;
- prior public verification contracts.

## Verification commands

```powershell
python .\VERIFY_PUBLIC_PAPER_ONLY.py
python .\VERIFY_EDGE_ISOLATION.py
python .\VERIFY_PRIVATE_EDGE_RESTORE.py
python -m py_compile `
  .\services\execution_engine.py `
  .\services\ingest_pipeline.py `
  .\services\pattern_live_arming.py
git diff --check
```


### Verifier correction

The release updates `VERIFY_EDGE_ISOLATION.py` from its stale v2.1.3 hash baseline
to the signed v2.1.4 hashes for the three intentionally changed service files.
All untouched trading-core hashes and Council runtime-isolation checks remain strict.
