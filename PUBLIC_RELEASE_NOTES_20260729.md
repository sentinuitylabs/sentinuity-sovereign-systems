# Sentinuity public paper release — 29 July 2026

This public update consolidates the latest verified work from the preceding 24–72 hours.

## Included advances

- Evidence-linked Living Organism World with Polaris, Ivaris, Nugget, Courier Owl and backend-derived NPC activity.
- Persistent token-decimal authority, bounded concurrent position marking and mark-first persistence.
- Hot price-table cleanup deferral and smaller database-maintenance batches while positions are open.
- Four-percent hard-stop authority aligned across boot, execution and UI.
- Council typed handlers, attempt-scoped canary stages, evidence-table separation, guarded atomic apply and proposal retention.
- Token-name display recovery and safer public narrative/redaction rules.
- Substrate, wallet-intelligence, pattern, PnL and observability refinements made during the same window.

## Public safety boundary

This package is paper-first. Transaction signing and submission are disabled inside `services/live_trading.py` by `PUBLIC_RELEASE_LIVE_STUB`; the public launcher additionally force-stamps paper-safe configuration. Runtime databases, logs, credentials, backups and private environment files are excluded.

## Verification

Run `python VERIFY_PUBLIC_GITHUB_SIGNOFF.py` from the repository root before committing.
