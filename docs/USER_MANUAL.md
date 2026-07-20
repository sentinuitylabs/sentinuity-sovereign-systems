# Sentinuity V2 Public Paper User Manual

## Purpose

Sentinuity is a local-first, paper-trading research organism. It combines market observation, pattern tracking, an auditable multi-agent Council, inspiration intake, paper execution, post-exit review, and a Streamlit command interface.

## Main surfaces

### Command Truth
Shows the current paper mode, service health, wallet simulation, gate state, and immediate blockers.

### Trade Truth
Shows paper entries, exits, outcomes, and the divergence instrument. Public paper mode does not make chain-funded trades, so live fields may be absent.

### Pattern Authority
Tracks whether current conditions resemble historically stronger or weaker regimes. This is research logic, not a profit guarantee.

### Council
Polaris coordinates research and proposals. Ivaris challenges them. Nugget audits evidence. Other roles provide specialist functions where configured. Provider availability is displayed and missing models should not be represented as active.

### Inspiration Pipeline
GitHub and configured channel discoveries are recorded with provenance and must pass relevance, licence, security, abstraction, debate, testing, paper evaluation, and operator approval gates before application.

### Intelligence
Combines available market, wallet, pattern, source, and Council evidence. Optional provider credentials determine how much of this surface is populated.

### Substrate
A paper-first strategy laboratory. Public V2 does not promise immediate position generation; candidates must satisfy evidence and risk rules.

## Normal operating sequence

1. Start with `LAUNCH_PUBLIC_PAPER.bat`.
2. Wait for the browser to open `http://localhost:8501`.
3. Confirm the header says paper mode and live authorities are disabled.
4. Check service health and provider status.
5. Allow data services and Council cycles to accumulate observations.
6. Review paper entries and post-exit outcomes.
7. Use the Council and inspiration views to understand why research or build work advanced or became blocked.
8. Stop with `STOP_SENTINUITY.bat` before moving or deleting runtime files.

## Data and privacy

Runtime databases, logs and `.env` remain on the local machine unless the operator deliberately exports them. Do not publish them. The public repository should contain source and examples only.

## Limitations

- This release is Windows-first.
- Python and internet access are required.
- Provider APIs may change, impose quotas, or remove models.
- Free RPC endpoints can be rate-limited.
- Optional panels may remain sparse without optional APIs.
- Paper fills and PnL are simulations.
- The release does not include unattended live-capital approval.

## Troubleshooting

### Browser does not open
Open `http://localhost:8501` manually. Inspect `logs\sovereign_hub.log`.

### Council shows unavailable models
Verify `NVIDIA_NIM_API_KEY`, model IDs, and provider quotas. Add an OpenAI fallback if desired.

### No market data
Verify the Chainstack HTTP and WebSocket endpoints. Set both Chainstack and generic Solana variables.

### Installation fails
Run `INSTALL_PUBLIC_PAPER.bat` from an extracted, writable folder. Confirm Python is on PATH and retry.

### Port 8501 is occupied
Stop an older Sentinuity/Streamlit process, or use the supplied stop script before relaunching.
