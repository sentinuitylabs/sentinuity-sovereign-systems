# Provider and Council Configuration

## Minimum useful configuration

```env
CHAINSTACK_RPC=https://your-chainstack-solana-rpc
CHAINSTACK_WSS=wss://your-chainstack-solana-websocket
NVIDIA_NIM_API_KEY=your_nvidia_nim_key
```

The RPC supplies Solana network access. NVIDIA NIM supplies model inference for Council roles that support NIM.

## Using NVIDIA NIM for Polaris and the Council

The shared key is:

```env
NVIDIA_NIM_API_KEY=...
```

For routine Council calls, `services/llm_client.py` can route through NIM. The default NIM model can be set with:

```env
COUNCIL_NIM_MODEL=meta/llama-3.3-70b-instruct
FAST_NIM_MODEL=meta/llama-3.3-70b-instruct
POLARIS_PROVIDER_ORDER=nim,openai
```

Role-specific NIM assignments can be supplied where supported:

```env
IVARIS_NIM_MODEL=qwen/qwen3.5-397b-a17b
NUGGET_NIM_MODEL=meta/llama-3.3-70b-instruct
AXIOM_NIM_MODEL=meta/llama-3.3-70b-instruct
FORGE_NIM_MODEL=meta/llama-3.3-70b-instruct
VISION_NIM_MODEL=
```

Model availability changes over time. Use model IDs currently available to your NVIDIA account. Invalid or retired models should be replaced rather than assumed to work forever.

### Polaris caveat

Some legacy Polaris methods still have direct OpenAI-specific paths. The Council execution spine and shared model client can use NIM-first routing, but an NVIDIA key alone does not guarantee every legacy Polaris feature is active. Adding `OPENAI_API_KEY` enables those optional fallback paths. The public release must continue to operate visibly when a provider is unavailable; unavailable agents should report that condition rather than fabricate output.

## Optional providers

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
XAI_API_KEY=
BRAVE_SEARCH_API_KEY=
X_BEARER_TOKEN=
BIRDEYE_API_KEY=
JUPITER_PRICE_API_KEY=
```

- OpenAI: Polaris fallback and legacy direct Polaris paths.
- Anthropic: optional independent Ivaris critic.
- xAI: optional research/model source.
- Brave: public web research.
- X: hashtag scout; API access may require a paid or approved tier.
- Birdeye/Jupiter: richer pricing and token metadata.

## Chainstack fields

Use the exact HTTP and WebSocket URLs from the Chainstack console:

```env
CHAINSTACK_RPC=https://...
CHAINSTACK_WSS=wss://...
SOLANA_RPC_URL=https://...
SOLANA_WSS_URL=wss://...
```

`SOLANA_RPC_URL` and `SOLANA_WSS_URL` may be set to the same Chainstack endpoints for compatibility with services that read generic variable names.

## Paper safety

Keep these values unchanged in the public release:

```env
TRADING_MODE=paper
PAPER_TRADING_ENABLED=1
LIVE_TRADING_ENABLED=0
LIVE_MONEY_MODE=0
LIVE_ARMED=0
EXECUTION_ARMED=0
SUBSTRATE_LIVE_ENABLED=0
SUBSTRATE_LIVE_ARMED=0
```

Do not place wallet private keys in the public paper installation.
