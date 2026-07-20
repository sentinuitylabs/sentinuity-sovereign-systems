# Publishing the public paper edition

Publish only the Public Paper ZIP. Never publish the Family Live package, `.env`, wallet material, databases, logs, backups or runtime folders.

Before tagging a release run:

```powershell
python .\tools\verify_public_v2_release.py
python .\tools\smoke_test_fable5_integration.py
```

Use factual capability language. Do not advertise guaranteed returns, “instant edge”, safety, profitability or personalised asset recommendations. State clearly that the public configuration is paper-only and that enabling live trading requires independent modification, responsibility and legal/risk review.
