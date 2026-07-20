# Security Policy

## Public release scope

This repository is distributed for paper trading, research and interface evaluation. Public release does not imply suitability for unattended live-capital deployment.

## Credentials

Never commit private keys, seed phrases, API tokens, `.env` files, databases, logs or wallet exports. Use `.env.example` only as a field list.

## Reporting

Privately report suspected credential exposure, unsafe transaction construction, signature handling defects, reconciliation errors or live-gate bypasses to the repository owner. Do not include usable secrets in a report.

## Live operation

Live operation requires explicit operator approval, paper evidence, canary qualification, chain-confirmed fill reconciliation and monitored rollback capability.
