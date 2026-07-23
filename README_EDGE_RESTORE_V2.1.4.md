\
# Sentinuity v2.1.4 — Confirmed Edge Restore

This release forward-ports the confirmed post-13-July runner and pattern
contracts into the latest public paper/research codebase.

## Restored contracts

- context-owned resolver lifecycle; no non-waiting DNS worker accumulation;
- configured paper hard-stop policy output is persisted;
- 75–99% runners retain the 10-percentage-point peak giveback;
- 100–149% runners retain the controlled 18-point giveback;
- trusted persisted peaks count toward R/P/S pattern confirmation;
- Lilypad and runner-profit-lock ordering remain intact;
- full live-size maturity remains documentary-canary gated.

## Public safety boundary

The public repository remains paper/research only. The installer runs the
existing `VERIFY_PUBLIC_PAPER_ONLY.py` before the update may be committed.
A failed paper-only verifier is a hard stop.

## Apply

Extract this pack, then run from PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& ".\APPLY_GITHUB_V2.1.4.ps1"
```

Review the diff before committing.


## Verifier baseline update

`VERIFY_EDGE_ISOLATION.py` is included as a full replacement. Its three approved
trading-core hashes are advanced to the signed v2.1.4 files, while the remaining
Council-isolation and untouched-core hashes stay unchanged.
