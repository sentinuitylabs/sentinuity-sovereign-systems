# Architecture

Sentinuity is organised around observable authority rather than a single monolithic bot.

1. **Intake and intelligence** collect market, wallet and research signals.
2. **Price truth** reconciles primary and fallback observations.
3. **Gates and contracts** decide whether a proposal is eligible for paper or live consideration.
4. **Execution** owns position lifecycle and exit authority.
5. **Evidence and post-exit analysis** retain outcomes for later review.
6. **Council and Forge services** research and propose bounded changes but must yield to execution and human approval.
7. **Sovereign UI** presents command truth, blockers, trade truth and provenance.

SQLite is a shared coordination surface. Auxiliary writers must use short transactions, bounded batches and lock backoff so they never starve price truth or execution.
