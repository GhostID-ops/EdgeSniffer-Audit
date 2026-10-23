# 2CQ Symmetric Choice Census V2 Implementation Report

## Classification

`TWO_POPULATION_RESEARCH_ARCHITECTURE_ACTIVE`

## Independent-Audit Roadmap

| Required change | Status | Production evidence |
|---|---|---|
| Exact readiness + generation-aware supersession | Complete | Inexact hours remain `DEFERRED`; append-only decision versions preserve every generation and current rows supersede automatically. |
| Economic family before eligibility | Complete | SOL curve/CPI, quote-native V2, PumpSwap, router, other DEX, and ambiguous families are separated before choice construction. |
| Bounded selected-event join diagnosis | Complete | Capture-first AllenHark evidence proves the teacher action and decision boundary. The redundant compact-observer copy is retained as forensic coverage, not incorrectly required as a pre-action feature. |
| Linked online and forensic populations | Complete | Online imitation rows are admitted independently; strict Tier-A status is attached only as an audit field. |
| Indexed causal state plane | Complete | Immutable finalized Pump evidence is normalized once into SQLite. A bounded two-partition parity run produced identical universe and choice hashes to the frozen scanner. |

The old V1 timer is disabled, the V2 timer owns materialization, and all raw collectors continue unchanged.

## Preserved Boundaries

- Original prospective start: `2026-09-11T07:15:24.437413402Z`
- V1 contract SHA-256: `aa326867c78eb05d09cd1dc105648455d2a55adb407f750b36ea247af063fb36`
- Invalid-interval SHA-256 in both V1 and V2: `dec785f84ad73109f5e3bd7fcda5966c8ad8145bef68d3b62a8a5803c4bb1fef`
- Outcomes read: 0
- Validation rows read: 0
- Submissions: 0
- V1 artifacts preserved; V1 ordinals are not mixed into V2.

## Implemented

1. Evidence-dependent terminal decisions require every covering reconciliation hour to have `exactReplayComplete=true`.
2. Every decision is stored in append-only `decision_versions` and a replaceable `current_decisions` view keyed by reconciliation generation.
3. Reconciliation-generation changes supersede and re-evaluate prior classifications.
4. Economic family is assigned before online eligibility.
5. Non-SOL quote Pump V2 is explicitly separated from the SOL Pump cohort.
6. The online population uses the capture-first AllenHark teacher action as its causal boundary.
7. A redundant compact-observer copy of the teacher action is retained as a forensic Tier-A condition, not an online feature gate.
8. Finalized transactions that were not causally observed before the teacher action remain absent from the online snapshot instead of being used retrospectively to reject it.
9. Selected and control mints retain identical pre-action state, route, history, executability, exposure, and continuity requirements.
10. Reconstructed causal universes are cached by immutable evidence identity and boundary set.
11. SQLite is used for incremental status/join state; append-only raw evidence remains authoritative.
12. Development remains N350, followed by sealed N150 validation under V2 semantics.
13. Reconciliation partition generations are indexed explicitly, with exact/incomplete frontier telemetry and per-run scan/cache/runtime measurements.
14. Finalized Pump evidence is normalized once per immutable evidence file into an append-only SQLite causal state plane; future decision generations query this state instead of replaying overlapping archives.

## Verified Result

| Metric | V1 | V2 |
|---|---:|---:|
| Eligible development rows | 15 | 36 |
| Relative admission | 1.0x | 2.4x |
| Raw non-paused episodes/hour | approximately 42 | approximately 42 |
| Estimated sustainable V2 online rows/hour | approximately 6-7 | approximately 6-7 |

The earlier 15-30 rows/hour estimate was too optimistic. Only about 17-19 raw decisions/hour belong to the coherent SOL Pump route family, and approximately 38% of exact-ready route-compatible decisions currently form a valid symmetric risk set.

At 6-7 rows/hour, the remaining N350 development collection is approximately 45-55 active hours plus exact-reconciliation lag. This is materially better than the old 248-hour projection but is not instantaneous.

## Route Census

Current classified population at the first complete V2.1 replay:

- SOL Pump curve: 184
- SOL Pump CPI: 73
- non-SOL Pump V2: 112
- PumpSwap: 15
- router: 50
- other DEX: 234
- ambiguous Pump family: 1

The 112 quote-native rows were independently confirmed with the existing quote-native decoder. Their native SOL delta represented rent/transaction cost, not SOL-denominated trade input. They are correctly excluded from the SOL choice cohort and preserved as a separately named family.

## Remaining SOL Attrition

- Exact reconciliation not ready: 94
- Selected mint absent from online universe: 57
- Declared invalid interval: 68
- No realtime candidate: 1
- Global continuity failure: 1

Selected-mint absence details:

- Realtime CREATE not indexed: 41
- Teacher already exposed under observed state: 9
- Outside launched 120 seconds: 3
- CREATE not causally before boundary: 2
- Trade-log decode failure: 1
- No prior external state: 1

The 41 missing-CREATE cases are not a hidden easy recovery population. A cache-backed audit found:

- only 10 had any prior realtime events in the bounded universe
- none began at a provably complete initial curve state
- several were clearly older than 120 seconds
- zero supported a complete first-observed-activity replacement under the frozen early-launch semantics

They remain legitimate exclusions.

## Performance

- Initial V2 rematerialization: approximately 12 minutes, approximately 4.6 GB peak while scanning the historical backlog.
- Persisted causal cache: approximately 45 MB.
- Subsequent no-new-generation checkpoint: approximately 3 seconds CPU / 15 seconds first measured wall, then approximately 3 seconds under the timer.
- Indexed-state real-data parity: exact universe, CREATE, incomplete-evidence, reason, feature-detail, and choice-row hashes matched the frozen archive scanner for a two-partition/9,832-Pump-transaction boundary.
- Indexed-state repeat query reused both imported evidence files; immutable evidence is only decoded on first import.
- Obsolete 18-day Python report process using approximately one CPU core was terminated.
- V1 materializer timer disabled after V2 assumed status ownership.
- V2 timer enabled every 10 minutes.

## Runtime Health

- V2 SQLite integrity: `ok`
- Capture-first detector: active
- AllenHark compact observer: active
- Exact evidence ledger: active
- V2 timer: active
- V1 timer: disabled
- Volume free: approximately 505 GB

## Next Gate

Continue collecting V2 unchanged toward development N350. Do not combine non-SOL, PumpSwap, router, or other-DEX decisions into this SOL cohort. They may become separate named research populations later, with their own causal state and execution semantics.

At V2 N350:

1. Fit exactly one cross-sectional policy.
2. Freeze it.
3. Test once on the subsequent sealed V2 N150.
4. Combine with the preserved transition/timing component.
5. Run prospective no-submit imitation.
6. Evaluate exact executable profitability.
