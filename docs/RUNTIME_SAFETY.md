# Runtime safety boundary

- Paper mode is the default public mode.
- Live safety requires current price authority, chain-fill ownership, configured loss limits, human approval and verified wallet state.
- Smart-wallet observations are shadow evidence unless explicitly promoted through reviewed contracts.
- Database contention is a safety issue because stale marks can defeat otherwise-correct exit logic.
- A passing static verifier does not prove profitable runtime behaviour.

Before any live-money test, require a clean paper canary showing primary price updates, bounded mark gaps, materially reduced SQLite locks and no service-death loops.
