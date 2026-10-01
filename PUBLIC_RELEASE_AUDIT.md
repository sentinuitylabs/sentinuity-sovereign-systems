# Public release audit

## Sources

- Base: `SENTINUITY_CURRENT_CLEAN_GITHUB_RELEASE_28_07_26`
- Runtime overlay: six files from `SENTINUITY_RUNTIME_EDGE_AND_IDENTITY_SIGNOFF_28_07_26`
- Identity source: latest supplied Sentinuity crystalline lockup pack

## Preserved runtime authority

The release retains the signed-off latest execution engine and WebSocket oracle. No thresholds, runner ladders, hard stops, live gates or chain-fill contracts were changed during repository preparation.

## Safe repository-only advancements

- Added brand imagery and source identity components.
- Added README, architecture, setup, safety, contribution and security documentation.
- Added public `.gitignore`, `.env.example`, dependency list and GitHub compile/hygiene workflow.
- Removed only generated/non-source debris: 1 files.
- Normalised UTF-8 BOM in 8 Python files without changing code semantics.

## Removed artefacts

- `services/sovereign_hub.py.before_ax_model_fix_20260716_231811`

## Licence gate

The repository root contains the **Apache License 2.0** in `LICENSE`. Public descriptions of the repository should identify Apache-2.0 consistently and must not claim that no licence has been selected.
