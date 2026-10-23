# 2CQ System Architecture Audit V1

Generated from an outcome-blind snapshot at `2026-09-11T21:40:21.219193Z`.

## Verdict

The current N350 pace is not caused by insufficient 2CQ activity or slow execution infrastructure. It is caused by the research materializer and by using forensic Tier-A completeness as the sole admission gate for the cross-sectional training cohort.

Preserve the capture stack and raw cohort. Replace the materialization layer before waiting for N350.

## Frozen Integrity

- Outcomes read: 0
- Validation rows read: 0
- Submissions: 0
- Validation remains sealed.
- The running cohort and collectors were not modified by this audit.

## Current Funnel

| Stage | N | Interpretation |
|---|---:|---|
| Canonical independent 2CQ entry episodes | 556 | Raw teacher decisions exist in volume. |
| Declared pause interval | 213 | Correctly invalid for realtime research. |
| Non-paused episodes | 343 | About 40.37 episodes per active collection hour. |
| Deferred, partitions not ready | 73 | Work queue, not terminal attrition. |
| Terminal exclusions | 473 | Includes pause and structural/materializer exclusions. |
| Development eligible | 10 / 350 | Only 2.9% of non-paused episodes admitted so far. |

At the observed eligible rate of 1.18 per active hour, the current design projects about 289 additional active hours to reach N350. That pace is unacceptable for the available raw episode supply.

## Attrition

| Reason | N | Finding |
|---|---:|---|
| Paused invalid interval | 213 | Expected and correct. |
| Unsupported route | 131 | Route/economic families are being mixed under coarse labels, then rejected late. |
| Selected entry not exactly joined | 74 | 40 are non-SOL quote Pump V2; 34 are independently decodable teacher buys missing from the materialized realtime universe. |
| Selected mint absent from universe | 53 | 32 are pre-boundary causal-arrival/order failures; remaining cases include exposure, reserve continuity, decode, and prior-state failures. |
| Canonical entry without realtime candidate | 1 | Rare detector miss. |
| Global continuity failure | 1 | Correct fail-closed exclusion. |

### Clock Hypothesis

Replaying selected entries and controls using one compact AllenHark clock recovered 0 additional eligible decisions. Mixed detector/compact clocks are not the primary defect and should not be patched as though they were.

### Economic-Family Taxonomy

Forty selected events described as zero-SOL-reserve failures are valid Pump V2 acquisitions quoted in a non-SOL mint. They are not generic missing data. They require a separate route/economic-family cohort and must not be silently mixed into the SOL bonding-curve model.

### Reconciliation Lifecycle Defect

`finalized_evidence_paths()` accepts a closed/stable hour even when `exactReplayComplete=false`. The materializer then permits decisions from that incomplete evidence set to become terminal exclusions. Terminal decisions are not revisited when reconciliation later improves.

Observed examples:

- Hour 08 was consumed with `exactReplayComplete=false`, only one input evidence file, 2,611 unresolved finality rows, and 20 missing raw/decode rows.
- Hour 16 was consumed with `exactReplayComplete=false` and five unresolved rows.
- Independently decodable teacher buys in those hours were subsequently classified as not exactly joined.

This is a correctness bug. An incomplete hour may be deferred, but it must never produce an immutable terminal scientific classification.

Twenty-five independently decodable misses also occur in hours marked exact-complete. They require one bounded stage-level join diagnostic before the corrected materializer is trusted. No broad infrastructure census is justified.

## Compute Architecture

The architecture audit processed:

- 395,226 canonical successful Pump transactions
- 369,881 decoded trade events
- 130,030,312 compact arrival records
- 19 evidence paths

It required about 24 wall-clock minutes and peaked near 3.6 GB RSS to audit 137 decisions. Overlapping hourly compressed partitions are repeatedly decompressed and rescanned. Incremental runs are cheap only when no new closed hour must be materialized.

This is archival replay being used as an online research database. Rust-native capture is fast; Python materialization is the bottleneck.

## Operational Debt

- PID `2038351` is an obsolete one-off Python report scan running for more than 18 days at about one full CPU core. It has written no data and is not part of the live collection graph.
- Completed V1/V2 census timers and a completed capture-first gate timer still wake periodically. They are not the main bottleneck but should be retired after dependency proof.
- `pump-exact-evidence-ledger-v1` was watchdog-restarted after RabbitStream HTTP/2 resets while systemd still reports `NRestarts=0`. Capture remained continuous, but restart provenance needs its own monotonic counter.
- Storage is healthy on the mounted volume. Disk capacity is not the N350 blocker.

## Model Assessment

The scientific decomposition remains sound:

1. **WHEN:** same-mint transition/hazard model.
2. **WHAT:** contemporaneous cross-sectional choice among the live risk set.
3. **IMITATION:** prospective no-submit teacher-policy replay.
4. **ECONOMICS:** exact executable profitability only after the policy is frozen.

The mistake is requiring every cross-sectional training decision to pass the strongest forensic completeness test. Tier-A completeness is an audit standard, not the only scientifically useful observational unit.

The corrected design should maintain two linked populations:

| Population | Purpose | Admission rule |
|---|---|---|
| Online imitation census | Train/test the decision policy using only information available live | Exact teacher acquisition plus a same-clock, point-in-time snapshot of the selected mint and all observable controls |
| Forensic Tier-A audit subset | Measure reconstruction error, missingness, and causal integrity | Existing strict full-history, create, reserve, continuity, and realtime-evidence rules |

The forensic subset audits the larger online cohort. It should not throttle the larger cohort to 1.18 rows/hour.

## Required Changes

### P0: Correctness

1. Require `exactReplayComplete=true` before any evidence-dependent terminal classification.
2. Version every materialized decision with reconciliation generation, evidence hashes, and classifier version.
3. Make decisions produced from incomplete/superseded evidence replayable; terminal means terminal only under a complete frozen generation.
4. Introduce explicit route families before eligibility: SOL Pump curve, non-SOL quote Pump V2, Pump CPI, PumpSwap, router, and other DEX.
5. Run one bounded diagnostic for the 25 exact-complete, decodable selected-event join misses and fix only the proven join stage.
6. Rebuild materialized decisions from the existing prospective raw evidence. Preserve cohort start, pause intervals, hashes, and sealed validation; do not reset collection.

### P1: Throughput

Build a compact causal state plane that normalizes each finalized Pump transaction once and indexes it by signature, mint, route family, canonical order, and AllenHark arrival time.

At each 2CQ acquisition, atomically persist an outcome-blind decision snapshot containing:

- selected mint and teacher action
- the complete live-observable control set
- frozen causal feature vectors for selected and controls
- route/economic-family tags
- source timestamps and evidence identifiers
- continuity and completeness generation

Finality reconciliation may validate, invalidate, or supersede that snapshot asynchronously. It must not require rescanning hundreds of millions of journal rows for each closed hour.

Keep append-only raw evidence as the audit source. Keep outcomes in a physically separate sealed store.

### P2: Research

After corrected materialization reaches N350:

1. Fit exactly one cross-sectional policy on development data.
2. Freeze it.
3. Test it once on the subsequent sealed N150.
4. Combine it with the preserved transition/timing component.
5. Run prospective no-submit imitation.
6. Evaluate exact executable profitability only after policy freeze.

## Do Not Rebuild

- AllenHark/FEC/provider transport
- Rust buy/sell hot path
- Yellowstone or exact-ledger canonical reconciliation
- strategy thresholds or feature definitions
- exit logic
- sealed validation or outcome stores

Those are not the demonstrated bottleneck.

## Required Versus Later

Required now: reconciliation readiness, replayable versioned materialization, route taxonomy, bounded join diagnosis, compact indexed state/snapshots, and retirement of proven orphan work.

Later for production: route-specific execution adapters, add/scale/partial-exit teacher-action modeling, and richer operational dashboards. These are useful only after the independent entry selector is proven.

## Bottom Line

The project has fast capture infrastructure but a slow and over-restrictive research compiler. We should not wait two weeks for the current N350 counter. We should preserve all raw evidence, correct the materializer, split online-policy evidence from forensic Tier-A evidence, and rematerialize the same prospective period under versioned rules.
