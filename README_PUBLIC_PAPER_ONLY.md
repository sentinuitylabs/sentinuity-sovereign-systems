# Sentinuity Public Community Edition — Paper Only

This safety patch removes real-money transaction construction, signing, wallet-key derivation and submission from the public repository while preserving paper trading, research, market-data processing, intelligence, UI and execution interfaces.

## Public guarantee

- `services/live_trading.py` is an interface-compatible blocking stub.
- `services/live_wallet_sync.py` never reads or derives a private key.
- Live and dual-mode arming scripts refuse to enable funded execution.
- `launch/launch_config.py` clamps every requested mode to paper.
- UI references to live readiness may remain for research/transparency, but no public execution path can sign or submit a transaction.

Run:

```powershell
python .\VERIFY_PUBLIC_PAPER_ONLY.py
python -m compileall -q .
```

Expected:

```text
PUBLIC PAPER-ONLY VERIFICATION: PASS
```
