# Sentinuity Dual Architecture & Stop-Truth Upgrade

## Summary

This release advances Sentinuity toward a cleaner, fail-closed Dual architecture while preserving a hard separation between public distribution and private operator execution.

The public GitHub build supports:

- paper execution;
- Dual candidate evaluation;
- live-shadow and would-fire telemetry;
- executable stop-realisability measurement;
- canonical launch posture enforcement;
- Mode B, pattern, canary and exposure gates;
- hard-stubbed on-chain transaction submission.

Private family canary execution remains unavailable in a normal public checkout. It requires local, untracked operator enablement and still remains subject to every existing risk, executability and canary gate.

## Key advancements

### Canonical Dual distribution contract

Dual mode now resolves through one final posture authority after other launch configuration writers have completed.

This prevents silent re-arming or disarming caused by competing configuration scripts.

Public checkouts resolve to:

- `DECLARED_POSTURE=PUBLIC_DUAL_STUB`
- Dual analytics enabled
- real submission disabled
- transaction backend stubbed

Private family checkouts can resolve to:

- `DECLARED_POSTURE=FAMILY_LIVE_CANARY`
- real sender structurally reachable
- explicit local enable marker required
- explicit private loss acknowledgement required
- all Mode B, pattern, canary, exposure and executability gates preserved

### Hard public transaction stub

Both live buy and live sell paths verify the distribution contract before loading or using private signing material.

A public checkout cannot submit merely by changing database flags.

### Executable stop-realisability truth

The stop-readiness system now:

- uses `SOLANA_USD_PRICE` as the canonical configuration key;
- retains the legacy `SOL_USD_PRICE` fallback with explicit labelling;
- reads fallback market data from `sentinuity_intelligence.db`;
- never queries `mtm_ticks` through the matrix database connection;
- records the SOL/USD basis source and age;
- populates executable PnL and return fields when a valid basis exists;
- distinguishes quote setup, network and end-to-end latency;
- separates legacy non-evidence rows from the new forward cohort;
- preserves all existing readiness thresholds.

### Safe legacy cohort migration

Historical rows that lacked a valid USD basis are retained unchanged and tagged as legacy evidence.

They are excluded from forward stop-readiness decisions instead of being silently treated as valid measurements.

### Deterministic verification

The release includes:

- 44 stop-basis assertions;
- 11 Dual distribution checks;
- migration dry-run and backup support;
- runtime readiness verification;
- public/private posture checks;
- buy and sell submission guards.

## Current boundaries

This release does not claim that:

- stop-readiness has already passed on live runtime evidence;
- a profitable live strategy is proven;
- every historical paper return was executable;
- real-money submission is available in a public checkout;
- Council, wallet-copy or autonomous systems have live capital authority.

The first valid forward readiness cohort must still accumulate and pass the unchanged stop-realisability thresholds.

## Security and distribution

Never commit:

- `.env` files;
- RPC or API secrets;
- wallet keys;
- seed phrases;
- `runtime/family_live.enable`;
- databases;
- runtime logs;
- audit archives containing private runtime data.

## Verification

```powershell
python .\tests\test_stop_realisability_basis.py
python .\tests\verify_dual_distribution_contract.py
```

Expected:

```text
44/44 assertions passed
11/11 checks passed
```
