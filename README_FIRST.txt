SENTINUITY — SIGNED GITHUB DUAL / STOP-TRUTH UPDATE
Date: 2026-08-04

PURPOSE
This is the public GitHub update only. It includes the signed Dual-distribution and executable stop-truth advancements while keeping all public on-chain transaction submission hard-stubbed.

PUBLIC CONTRACT
- Menu option 2 supports Dual evaluation, paper execution and live-shadow telemetry.
- Public buy and sell submission remain hard-stubbed.
- Changing database flags alone cannot unlock signing.
- No local family marker, acknowledgement, key, database or runtime evidence is included.

SIGNED ADVANCEMENTS
- One final launch-posture authority after other configuration writers.
- Public Dual stub and private/local family profile separated explicitly.
- Hard guards at both live-buy and live-sell boundaries.
- Correct SOLANA_USD_PRICE lookup with labelled legacy fallback.
- Correct sentinuity_intelligence.db market-data fallback.
- Executable stop-return fields can populate from a valid USD basis.
- Quote setup, network and end-to-end latency are measured separately.
- Legacy no-basis evidence is preserved but excluded from forward readiness.
- Migration, verifier and deterministic regression coverage included.

VERIFIED TEST CONTRACT
python .\tests\test_stop_realisability_basis.py
python .\tests\verify_dual_distribution_contract.py

Required:
44/44 assertions passed
11/11 checks passed

INSTALL INTO A LOCAL REPOSITORY
1. Extract this ZIP to a temporary folder.
2. From that temporary folder, run:
   powershell -ExecutionPolicy Bypass -File .\APPLY_GITHUB_UPDATE.ps1 -RepoRoot "C:\Users\Polar\.openclaw\workspace\trading-bot"
3. Enter the repository and rerun both test suites.
4. Review git diff and staged names before pushing.

PUSH
From the repository root:
   powershell -ExecutionPolicy Bypass -File .\PUSH_SIGNED_GITHUB_UPDATE.ps1

The push helper stages only the signed allow-list. It never stages .env, databases, runtime markers, logs or audits.

BOUNDARY
This is GitHub/distribution sign-off. It does not claim that the fresh runtime stop-readiness cohort has passed or that profitable live operation is proven.
