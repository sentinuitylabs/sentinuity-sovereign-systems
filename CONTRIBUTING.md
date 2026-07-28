# Contributing to Sentinuity

Thank you for helping build Sentinuity.

## Contribution rules

1. Keep pull requests focused and explain the affected authority: price truth, execution, gating, persistence, council, UI or tooling.
2. Do not weaken live safety, hard stops, chain-fill ownership, human-in-the-loop controls or paper/live isolation without explicit evidence and review.
3. Include a reproducible test, audit output or replay for behavioural changes.
4. Never include credentials, private keys, wallet exports, databases, logs containing tokens or proprietary third-party data.
5. UI changes must preserve the hierarchy and distinguish observed truth from inference.

## Suggested workflow

```bash
git checkout -b feature/clear-name
python -m compileall -q core services ui wallets launch
python tools/public_release_verify.py
```

Open a pull request describing the observed problem, proposed change, evidence and rollback path.
