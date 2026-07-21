# Sentinuity V3 Final Sign-Off Update

This update contains the files changed since the morning V2 GitHub release.

## Runtime changes

- Hardened Solana chain-truth settlement and positive-notional handoff.
- Polaris auxiliary honesty and bounded missing-module handling.
- IVARIS provider-routing guards and corrected Anthropic request fields.
- Canonical Intelligence build entry point.
- Real-source Substrate price feed, opportunity scanner, supervisor and paper ledger.
- Shutdown Retention V9/V10 with archive-first pruning and total SQLite footprint enforcement.

## Verification changes

- Portable live canary.
- Mainnet-shaped token-balance fixtures with `accountIndex`.
- Current resolver contract assertions using `wallet_account_indexes`.
- Windows UTF-8-safe verifier output.
- Corrected `_account_key_strings(transaction, meta)` replay call.
- Correct PowerShell fixture exit-code capture.

## Local sign-off evidence

The assembled Windows workspace passed:

- NTO BUY/SELL chain-truth replay.
- Dynamic wallet index and loaded-address resolution.
- Raw integer token truth.
- Fee-payer separation and blockTime ownership.
- Sizing gate.
- IVARIS routing.
- Substrate lifecycle.
- Limited-live canary.
- Full V3 package verifier.

The shutdown retention flow reduced the matrix database from 205.00 MB to
11.16 MB with `quick_check=ok`.

## Safety boundary

This is a code and limited-canary sign-off. It is not a profitability claim.
Capital-sensitive and signing targets remain human-gated.
