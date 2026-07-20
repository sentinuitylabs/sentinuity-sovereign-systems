# Sentinuity V2 Public Paper — Windows Quick Start

This release is paper-only. It does not require a wallet private key and must not be used with one in a public checkout.

## What you need

- Windows 10 or 11
- Python 3.11 or 3.12, 64-bit, with **Add Python to PATH** enabled
- A Solana RPC endpoint. A free Chainstack Solana RPC is sufficient for initial paper observation.
- At least one Council model key:
  - NVIDIA NIM API key, recommended for a low-cost/free-start Council, or
  - OpenAI API key as an optional fallback
- Internet access

Optional providers improve coverage but are not required to open the UI:

- Birdeye or Jupiter pricing credentials
- Brave Search for web research
- X API for hashtag scouting
- Anthropic or xAI for independent critics

## First installation

1. Extract the release ZIP to a normal folder such as `C:\SentinuityV2`.
2. Double-click `INSTALL_PUBLIC_PAPER.bat`.
3. The installer creates `.venv`, installs requirements, creates `.env` from `.env.example`, and opens `.env` in Notepad if provider keys are still missing.
4. Add at minimum:

```env
CHAINSTACK_RPC=https://your-chainstack-solana-rpc
CHAINSTACK_WSS=wss://your-chainstack-solana-websocket
NVIDIA_NIM_API_KEY=your_nvidia_nim_key
```

5. Save and close `.env`.
6. Double-click `LAUNCH_PUBLIC_PAPER.bat`.

The launcher forces paper-only settings, performs preflight verification, starts the services, and opens:

```text
http://localhost:8501
```

## Subsequent launches

After `.env` and the virtual environment are prepared, launching is one double-click:

```text
LAUNCH_PUBLIC_PAPER.bat
```

## Stop the system

Double-click:

```text
STOP_SENTINUITY.bat
```

## What should appear

- Sovereign Hub in the default browser
- Paper wallet and paper positions
- Market/oracle health
- Council activity and standing tasks
- GitHub and channel inspiration records when those scouts are configured
- Paper/live divergence surface; public paper installs normally show paper-only or `NOT RECORDED` live fields

## Important expectations

A Chainstack RPC and NVIDIA NIM key can start the core paper organism and Council, but they do not guarantee every optional intelligence panel has data. Some panels depend on additional public or credentialed providers. Missing optional providers should degrade visibly rather than invent information.

Paper results are simulated research outputs, not evidence of future profit.
