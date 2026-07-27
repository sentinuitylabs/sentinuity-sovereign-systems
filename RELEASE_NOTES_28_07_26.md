# Sentinuity Current Clean Source Release — 28 July 2026

This release replaces the prior public source snapshot with the current audited source tree.

## Included improvements
- canonical realised PnL truth for capped paper stops;
- realised-outcome pattern and smart-wallet grading;
- cached token name and symbol enrichment;
- reduced copytrade lock backoff;
- quarantine of unverified Substrate history;
- unsupported council-task quarantine;
- current Solana, council, copytrade, Substrate and UI source stack.

## Preserved safety
- no lowering of Mode B thresholds;
- no weakening of token, route, price-age or liquidity safeguards;
- no live copytrade authority;
- no automatic Substrate live execution;
- no private runtime databases, logs, keys, environment files or caches.

## Verification
Run `python -m compileall -q .` from the repository root. Configure secrets locally from a private `.env`; never commit it.
