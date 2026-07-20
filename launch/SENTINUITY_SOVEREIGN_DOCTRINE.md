# SENTINUITY SOVEREIGN DOCTRINE

**File:** `SENTINUITY_SOVEREIGN_DOCTRINE.md`  
**Placement:** `launch\SENTINUITY_SOVEREIGN_DOCTRINE.md`  
**System:** Sentinuity Sovereign Trading Organism  
**Authority:** Operator-controlled  
**Status:** Sign-off doctrine

---

## 1. Purpose

Sentinuity is an operator-owned, observable, auditable trading organism.

It is not a black box.

Every material decision must be attributable to visible data, explicit thresholds, recorded reasoning, or an identified service. No autonomous component may silently override the operator, conceal risk, alter capital posture, or mutate proven trading logic without traceable approval.

The system exists to:

- observe markets continuously;
- discover, qualify, score, debate, simulate, and execute opportunities;
- learn from paper-trading and historical outcomes;
- preserve profitable behaviour while improving weak or disconnected infrastructure;
- make risk, confidence, freshness, execution state, and trade lifecycle visible;
- remain recoverable from backups, audits, and known-good code states.

---

## 2. Operator Sovereignty

The operator is the final authority.

No council, agent, supervisor, model, service, script, automation, repair process, migration, optimiser, UI component, or background task may:

- enable live trading without explicit operator selection;
- increase live position sizing without explicit approval;
- remove risk gates from the live lane;
- overwrite a known-good codebase without a backup;
- delete historical data unless the data is safely archived or explicitly disposable;
- modify proven entry or exit logic under the label of a UI, schema, wiring, cleanup, or performance change;
- suppress errors, failed checks, stale signals, blocked entries, or disconnected services;
- present simulated results as live results.

Paper mode may explore. Live mode must remain guarded.

---

## 3. Canonical Runtime Root

The canonical Windows runtime root is:

```text
<EXTRACTED_SENTINUITY_FOLDER>
```

The standard launch surface is:

```text
launch\Launch_Sentinuity.bat
```

The standard Sovereign Hub local address is:

```text
http://localhost:8501
```

The repository must not rely on mystery duplicates, shadow launchers, temporary root copies, or sidecar implementations when a canonical native file exists.

Approved implementation locations are normally:

```text
services\
core\
launch\
ui\
tools\
```

Root-level scripts are permitted for explicit audits, installers, verifiers, migrations, and signed-off maintenance tasks.

---

## 4. Capital Doctrine

### 4.1 Paper Lane

The paper lane is the permanent learning and proving ground.

It may:

- test new metrics;
- observe proposed gates in shadow mode;
- compare alternative exits;
- evaluate runner behaviour;
- record missed opportunities;
- simulate council recommendations;
- produce confidence evidence for future promotion.

Paper trading must still use realistic:

- position sizing;
- liquidity constraints;
- fees;
- slippage;
- execution freshness;
- stop and take-profit behaviour;
- wallet accounting.

Paper mode must not fabricate fills that the live lane could not reasonably obtain.

### 4.2 Live Lane

The live lane is opt-in, constrained, and evidence-based.

Live trading must remain disabled unless the operator explicitly selects it at launch or through an authorised control surface.

Promotion to live requires:

- fresh executable pricing;
- valid token and market identity;
- liquidity and route viability;
- confidence above the live threshold;
- no active fatal veto;
- risk sizing within configured limits;
- an auditable entry reason;
- preserved stop, hard-stop, and maximum-hold protections.

No paper-only experiment automatically graduates to live.

### 4.3 Substrate Lane

The Substrate Node may operate autonomously in paper mode for research, simulation, debate, and learning.

It must not place live capital at risk unless a separate live authority is explicitly implemented and approved.

---

## 5. Known-Good Logic Preservation

A profitable or otherwise validated code state is an asset.

Before changing trading logic, the system must preserve:

- the source file being replaced;
- a timestamped backup;
- relevant configuration;
- schema state where applicable;
- the associated database snapshot or audit evidence;
- file hashes when practical;
- a plain-language change record.

UI improvements, schema repairs, path corrections, database pruning, telemetry upgrades, and service rewiring must not change entry or exit behaviour unless the change is declared as a trading-logic change.

When merging eras:

1. preserve the known-good entry and exit logic;
2. transplant infrastructure around it;
3. verify imports, schema, services, and telemetry;
4. run a no-regression audit;
5. compare behaviour against the reference performance window;
6. reject the merge if the edge is degraded without a clearly justified reason.

---

## 6. Freshness and Execution Integrity

A signal is not executable merely because it once qualified.

Every entry must confirm:

- signal age;
- discovery age;
- price age;
- route availability;
- price source;
- market identity;
- liquidity state;
- confidence state;
- current veto state;
- slot availability;
- duplicate-position protection.

Stale qualification rows must never become fresh executions through UI caching, database lag, or fallback pricing.

The execution engine must not use qualification price as a substitute for a current executable price.

If the system cannot obtain a sufficiently fresh and trustworthy price, it must block the entry and record the reason.

---

## 7. Risk Doctrine

Risk controls are mandatory system components, not optional suggestions.

At minimum, each position must carry an explicit policy for:

- position size;
- stop loss;
- hard stop;
- take profit;
- maximum hold;
- time cut where enabled;
- trailing behaviour where enabled;
- abnormal-price handling;
- stale-price handling;
- restart recovery.

Live risk must be stricter than paper risk.

Suspect price moves, oracle disagreement, impossible candles, malformed decimals, poisoned enrichment, or route anomalies must not trigger irreversible live actions without consensus or validation.

A service failure must fail safe, not fail open.

---

## 8. Runner Doctrine

Sentinuity must distinguish ordinary profitable trades from genuine runners.

Runner analysis should preserve:

- early velocity;
- acceleration;
- liquidity expansion;
- volume acceleration;
- holder or wallet quality where available;
- post-entry peak;
- maximum favourable excursion;
- maximum adverse excursion;
- time to peak;
- exit reason;
- unrealised continuation after exit;
- missed-runner evidence.

The system must not cap every winner at a fixed percentage merely because a historical threshold exists.

Any runner extension or trailing system must be:

- measurable;
- replayable;
- paper-proven;
- protected against round-trip collapse;
- compared with the original exit logic.

Known-good modest-win behaviour must not be sacrificed blindly in pursuit of rare extreme runners.

---

## 9. Council, Polaris, Debate, and Intelligence

The council and intelligence systems exist to improve observation, research, and explainability.

They may:

- propose metrics;
- investigate trade clusters;
- compare profitable and unprofitable cohorts;
- identify blind spots;
- analyse missed runners;
- recommend paper-only experiments;
- inspect service health;
- rank proposed upgrades.

They may not silently patch live trading logic.

Every meaningful recommendation should include:

- the problem observed;
- evidence;
- affected files or services;
- expected benefit;
- risk of regression;
- proposed test;
- success criteria;
- rollback path.

The Debate Chamber should remain visibly active when enabled. Empty debate telemetry must be treated as a wiring or scheduling issue, not assumed to mean there is nothing to research.

---

## 10. Database Doctrine

The hot database exists for active runtime speed and current operational truth.

Historical evidence belongs in archives and derived caches.

The system should retain in the hot database only what is necessary for:

- open positions;
- current pipeline state;
- fresh market observations;
- recent qualification and veto context;
- active telemetry;
- recent trade review;
- wallet and equity continuity;
- schema and configuration state.

Before pruning:

- checkpoint or stop active writers safely;
- create a backup where required;
- preserve open positions;
- preserve wallet state;
- preserve recent review evidence;
- preserve historical aggregates that are not reproducible.

Pruning must not destroy the evidence needed to explain performance.

Archive files should be readable by audit tooling without being loaded continuously by the live UI.

Database maintenance must be idempotent and recoverable.

---

## 11. Restart and Recovery Doctrine

A normal shutdown or restart must preserve:

- open paper positions;
- authorised live positions;
- entry prices;
- position sizes;
- stop and exit policy;
- wallet balances;
- launch configuration;
- unresolved but still-valid pipeline state where safe;
- audit trail.

On restart, the system must:

- validate database integrity;
- restore configuration;
- reconcile open positions;
- reject stale prelaunch signals;
- refresh executable prices;
- restart required services;
- verify heartbeats;
- expose any degraded subsystem.

Open positions must not disappear merely because the UI or process restarted.

Stale prelaunch candidates must not poison the new session.

---

## 12. UI Doctrine

The UI is an operational truth surface.

It must distinguish clearly between:

- paper and live;
- balance and equity;
- realised and unrealised PnL;
- open and closed trades;
- fresh and stale data;
- qualified, latched, vetoed, blocked, and executed states;
- active and degraded services;
- observed peak and booked exit;
- current truth and historical cache.

The UI must never show fabricated precision.

Missing data should display as unavailable with a reason, not silently as zero where zero has a different meaning.

Visual upgrades must preserve:

- readability;
- hierarchy;
- responsiveness;
- established colour doctrine;
- consistent typography;
- compact information density;
- alignment with the wider Sentinuity design language.

The UI must not become a second source of trading logic.

---

## 13. Audit Doctrine

No sign-off is complete without verification.

A valid sign-off process should include, where applicable:

```text
python -m py_compile <changed_python_files>
```

and:

- schema checks;
- import checks;
- database integrity checks;
- service heartbeat checks;
- launch-path checks;
- open-position recovery checks;
- fresh-price checks;
- no-regression checks;
- paper/live separation checks;
- PnL accounting checks;
- backup existence checks.

A successful launch is not proof of correct trading behaviour.

A green UI is not proof that the execution path is wired.

A profitable sample is not proof that accounting is correct.

Every audit must state what it proved and what it did not prove.

---

## 14. Change Classification

Every material change should be labelled as one of:

### A. Presentation Only

UI layout, typography, colours, labels, chart rendering, or display logic.

Must not alter trading behaviour.

### B. Wiring or Reliability

Imports, paths, heartbeats, service startup, schema compatibility, caching, recovery, telemetry, or database access.

Must not alter thresholds or strategy behaviour unless explicitly declared.

### C. Risk or Execution

Sizing, price validation, stop logic, route handling, live guards, slot limits, or order policy.

Requires elevated review.

### D. Strategy Logic

Qualification, scoring, confidence, vetoes, entries, exits, runner handling, copy-trading influence, or promotion rules.

Requires backup, evidence, replay, comparison, and explicit sign-off.

### E. Research Only

Shadow metrics, offline analysis, paper-only experiments, council proposals, or historical studies.

Must not influence live execution until promoted deliberately.

---

## 15. Prohibited Patterns

The following are prohibited without explicit, documented approval:

- hidden live-mode enablement;
- silent confidence-floor changes;
- silent stop or take-profit changes;
- replacing fresh execution prices with cached qualification prices;
- deleting open positions during cleanup;
- deleting historical evidence before backup;
- duplicate execution engines;
- duplicate launchers used accidentally;
- sidecar strategy logic that bypasses the canonical engine;
- UI-derived execution decisions;
- swallowed exceptions in capital-sensitive paths;
- fake success messages after partial failure;
- automatic strategy self-modification in live mode;
- presenting backtest or paper PnL as realised live PnL;
- changing known-good logic during an unrelated repair.

---

## 16. Sign-Off Standard

A file, patch, build, or codebase is signed off only when:

- its purpose is clear;
- its scope is bounded;
- backups exist;
- compilation succeeds;
- required audits pass;
- paper/live posture is confirmed;
- known-good logic is preserved or intentionally changed;
- database and schema compatibility are confirmed;
- rollback instructions are available;
- remaining uncertainty is disclosed.

The phrase `SIGN-OFF PASS` must mean that the named checks actually ran and passed.

It must not be used as decoration.

---

## 17. Default Safe Posture

Unless explicitly overridden by the operator:

```text
Solana: PAPER
Substrate: PAPER
Live execution: OFF
Autonomous research: ALLOWED
Autonomous live mutation: FORBIDDEN
Historical deletion without backup: FORBIDDEN
Known-good logic replacement without regression proof: FORBIDDEN
```

---

## 18. Final Authority Statement

Sentinuity may observe autonomously.

Sentinuity may research autonomously.

Sentinuity may debate autonomously.

Sentinuity may learn in paper mode autonomously.

Sentinuity may recommend changes autonomously.

Sentinuity may not take sovereignty from the operator.

**The operator owns the capital, the code, the risk, the final decision, and the right to roll back.**

---

## 18. July 2026 Runtime Addendum

This signed-off doctrine preserves the current paper-safe launch posture:

- Solana paper lane remains continuously enabled for learning.
- Live Solana execution remains disabled unless the operator explicitly selects and arms the authorised dual/live path.
- Substrate remains paper-only until separately approved.
- Smart-wallet and copytrade evidence may influence paper admission only through bounded, auditable confidence adjustments; it may not bypass freshness, liquidity, route, age, veto, or position controls.
- The wallet research cycle must periodically refresh approved public wallet observations, persist provenance, and record influence and outcomes for later calibration.
- Council standing tasks must include recurring smart-wallet source refresh and copytrade observation duties.
- Forge Genesis is an idempotent seed, not a substitute for the recurring intelligence, research-bridge, debate, or council services.
- Debate Chamber and World Engine UI changes are presentation changes only and must not mutate trading decisions or capital posture.
- Canonical council identities and their visual assets must remain consistently bound; agents should assemble only when sharing an active task or objective.
- Database pruning must preserve open positions, trade accounting, learned wallet profiles, outcome evidence, approved Forge state, and required audit history.

**Signed-off posture:** PAPER SAFE / OPERATOR CONTROLLED / AUDITABLE / FAIL-CLOSED
**Revision:** 2026-07-12
