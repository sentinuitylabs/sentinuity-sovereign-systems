# Sentinuity Sovereign Systems
## Sentinuity Terminal V2 Genesis — Public Paper Edition

**Sentinuity Terminal V2 Genesis** is a Windows-first, local paper-trading research organism with a Streamlit command interface, market observation, pattern intelligence, an auditable multi-agent Council, GitHub/channel inspiration intake, paper execution, and post-exit review.

It is released publicly in **paper-only mode**. It does not include public live-capital approval and should not be configured with a wallet private key.

## Fastest Windows start

1. Extract the ZIP.
2. Install Python 3.11 or 3.12 with **Add Python to PATH** enabled.
3. Double-click `INSTALL_PUBLIC_PAPER.bat`.
4. Add a Solana RPC and at least one model-provider key to the `.env` file opened by the installer.
5. Double-click `LAUNCH_PUBLIC_PAPER.bat`.

Sentinuity starts locally and opens:

```text
http://localhost:8501
```

After the first installation and `.env` setup, normal startup is one double-click.

## Minimum practical provider setup

```env
CHAINSTACK_RPC=https://your-chainstack-solana-rpc
CHAINSTACK_WSS=wss://your-chainstack-solana-websocket
SOLANA_RPC_URL=https://your-chainstack-solana-rpc
SOLANA_WSS_URL=wss://your-chainstack-solana-websocket
NVIDIA_NIM_API_KEY=your_nvidia_nim_key
```

A free Chainstack endpoint and a valid NVIDIA NIM key are enough to begin using the core local paper system and NIM-capable Council paths. Some intelligence panels and legacy Polaris functions need optional providers or an OpenAI fallback. Provider absence is surfaced; Sentinuity must not invent unavailable evidence.

## Documentation

- [Windows Quick Start](docs/QUICKSTART_WINDOWS.md)
- [User Manual](docs/USER_MANUAL.md)
- [Provider and Council Setup](docs/PROVIDER_AND_COUNCIL_SETUP.md)
- [Security Policy](SECURITY.md)
- [Sovereign Doctrine](launch/SENTINUITY_SOVEREIGN_DOCTRINE.md)
- [Launch Architecture](docs/LAUNCH_ARCHITECTURE.md)

## What makes V2 useful to builders

- Local-first browser UI rather than a cloud account requirement.
- Paper-safe public launcher with live authorities forcibly disabled.
- Durable inspiration ledger with provenance, licence and security gates.
- Fair standing-task scheduling and visible blockers.
- Multi-agent Council that can research, challenge, propose and record outcomes when providers are configured.
- GitHub and channel discoveries retained as evidence, never treated as automatic authority.
- Build retrospectives linked to proposals, patches and runtime health.
- Paper/live divergence tooling retained for private operators while degrading honestly in public paper mode.
- Extensible Python services and SQLite contracts that builders can inspect and modify locally.

## Verification

```powershell
.\.venv\Scripts\python.exe .\tools\verify_public_v2_release.py
.\.venv\Scripts\python.exe .\tools\smoke_test_fable5_integration.py
```

## Safety and limitations

Paper fills and PnL are simulations and do not establish future profitability. Free providers may impose quotas. Model IDs and APIs can change. The Public Paper Edition is not signed off for unattended live capital.

## First-run terminal setup

The installer creates local, schema-ready `sentinuity_matrix.db` and `sentinuity_intelligence.db` files at first run. No database or fabricated performance history is distributed. Run `PERSONALISE_YOUR_TERMINAL.bat` to set the operator name, terminal heading, Council display names and visual world without changing backend IDs, execution rules or safety gates.

Read `docs/PUBLIC_USE_AND_FINANCIAL_RISK_NOTICE.md` before publishing, demonstrating or discussing the project. The public edition is paper-only by default and is not a promise of profitability or financial advice.
