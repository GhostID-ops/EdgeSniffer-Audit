# EXECUTABLE_OPPORTUNITY_CENSUS_V1

## Frozen Identity

- Contract SHA256: `82223cf3598d42f89d7a0869c73e19d4d55f1f27dbb2686c716ed0733fd28f62`
- Discovery prefix SHA256: `d85533d6c246161bc5992f1d61cae4273d50d8c67fca8d4b38a65c23ad994fdd`
- Discovery ordinals: `1-6848`
- Validation rows inspected: `0`
- Holdout rows inspected: `0`
- Fingerprints searched: `0`
- Models trained: `0`
- Live/collector changes: `0`

## Direct Verdict

The market contains recurring executable opportunity at 0.5-1 SOL, but the
census does not yet prove a tradeable selector.

- Primary bottleneck: `COUNTERFACTUAL_DIVERGENCE_BLOCKER`
- Secondary bottleneck: `LATER_HORIZON_OPPORTUNITY`
- Tertiary diagnosis: `SELECTION_PROBLEM`

At 1 SOL, our inserted order causes enough later historical transactions to
fail their original constraints that a no-resubmission replay becomes
materially uncertain. Separately, most profitable opportunities appear later
than the ultra-early window. Opportunity supply and frozen exit-policy capture
are sufficient to justify selector research once the divergence envelope is
made decision-safe.

## Population

| Measure | Result |
|---|---:|
| All observed launches | 12,860 |
| Intentional Mayhem exclusions | 3,171 |
| Eligibility-adjusted observed launches | 9,689 |
| Exact-admitted discovery mints | 6,848 |
| Exact-evaluable admitted mints | 6,773 |
| Evaluable within admitted prefix | 98.90% |
| Evaluable across eligible observed universe | 69.90% |
| Exact-evaluable across all observed launches | 52.67% |
| Event-state rows | 21,514 |

The eligibility-adjusted 69.90% coverage passes the frozen 60% data-coverage
gate, but the missing 30.10% remains population-selection risk. The largest
non-Mayhem exclusions were missing/failed creation evidence, reserve-ordering
continuity mismatches, and unsupported trade-event schemas.

## Opportunity Supply

Reference: P50 empirical entry/sellability, exact Pump fees and own impact,
0.001 SOL configurable round-trip delivery cost, any frozen event state.

| Size | +5% | +10% | +20% |
|---:|---:|---:|---:|
| 0.5 SOL | 637 / 6,773 (9.40%) | 396 / 6,773 (5.85%) | 227 / 6,773 (3.35%) |
| 1.0 SOL | 626 / 6,773 (9.24%) | 379 / 6,773 (5.60%) | 219 / 6,773 (3.23%) |

Mint-clustered 95% CI for the 0.5 SOL +10% reference prevalence:
`5.32%-6.39%`.

Capacity retention from 0.5 to 1 SOL is `95.71%`. This rejects
`CAPACITY_PROBLEM` for this population and size range.

## Latency

| Entry/sellability scenario | 0.5 SOL +10% mints | Prevalence | Retention vs zero-delay |
|---|---:|---:|---:|
| Zero-delay ordering ceiling | 637 | 9.40% | 100.00% |
| P50: 37ms / 261ms | 396 | 5.85% | 62.17% |
| P75: 42ms / 372ms | 391 | 5.77% | 61.38% |
| P90: 48ms / 440ms | 381 | 5.63% | 59.81% |

The zero-delay-to-P50 loss is meaningful, but P50-to-P90 loses only 15 mints.
Once an observable event state exists, shaving 37ms to 48ms is not the dominant
constraint in this census. The larger problem is opportunity ordering before a
causal decision can land.

## Opportunity Timing

Among the 396 reference opportunity mints:

| Metric | P25 | Median | P75 | P90 |
|---|---:|---:|---:|---:|
| Earliest +10% hit after entry | 0.946s | 5.691s | 18.497s | 46.142s |
| Best longest continuous +10% window | 11.020s | 67.656s | 110.475s | 119.452s |
| Best total +10% window | 11.257s | 71.471s | 112.376s | 119.452s |

`69.95%` of opportunity mints first reach +10% after 2 seconds. The earlier raw
84.06% figure was event-state weighted; 69.95% is the corrected unique-mint
number and still exceeds the frozen 60% later-horizon gate.

## Event States

These are conditional opportunity rates, not promoted fingerprints.

| Event state | Evaluable N | 0.5 SOL +10% |
|---|---:|---:|
| After external gross buy >= 5 SOL | 662 | 22.81% |
| After external gross buy >= 2 SOL | 1,209 | 17.95% |
| After external gross buy >= 1 SOL | 1,518 | 15.94% |
| After external buyer 5 | 1,212 | 15.18% |
| After external gross buy >= 0.5 SOL | 1,824 | 14.36% |
| After remaining real token < 50% | 600 | 14.00% |
| After remaining real token < 75% | 1,465 | 13.99% |
| After remaining real token < 90% | 2,401 | 12.16% |
| After external buyer 3 | 1,918 | 12.04% |
| After first sell then external buy | 1,207 | 11.68% |
| After external buyer 2 | 2,577 | 10.55% |
| After external buyer 1 | 3,565 | 8.70% |
| After first external sell | 2,529 | 6.33% |
| Initial observable pre-first-trade state | 6,773 | 5.65% |

This is strong evidence that later follow-through states contain denser
opportunity than blind launch entry. It does not establish that gross-buy
thresholds alone are profitable selectors.

## Delivery Cost

P50, 0.5 SOL, +10% opportunity prevalence:

| Configurable round-trip delivery cost | Opportunity mints | Prevalence |
|---:|---:|---:|
| 0.001 SOL | 396 | 5.85% |
| 0.0025 SOL | 390 | 5.76% |
| 0.005 SOL | 373 | 5.51% |
| 0.010 SOL | 354 | 5.23% |
| 0.020 SOL | 316 | 4.67% |

Exact Pump protocol/creator fees, own entry impact, own exit impact, and base
transaction fees are already mandatory in every row. The table changes only
the additional configurable delivery-cost overlay.

## Frozen Policies

For 0.5 SOL/P50/+10%/0.001 SOL delivery cost:

| Frozen policy | Captured mints | Population prevalence | Share of 396 economic opportunities |
|---|---:|---:|---:|
| TP20 / SL20 / 120s | 292 | 4.31% | 73.74% |
| Trail20_12 / SL20 / 120s | 241 | 3.56% | 60.86% |
| Fixed 5s / SL20 | 153 | 2.26% | 38.64% |

The best frozen policy leaves 104 of 396 opportunity mints uncaptured. That
26.26% economic-opportunity/policy gap is material but does not make exits the
primary bottleneck. These are oracle-across-event-state capture counts, not a
strategy PF or live PnL claim.

## Counterfactual Divergence

| Scenario | Size | Affected state rows | Affected mints |
|---|---:|---:|---:|
| P50 | 0.5 SOL | 23.67% | 16.36% |
| P50 | 1.0 SOL | 26.39% | 18.43% |
| P90 | 0.5 SOL | 23.66% | 16.29% |
| P90 | 1.0 SOL | 26.33% | 18.32% |

The frozen all-cell rate is 25.32%, just above its 25% blocker threshold. The
0.5 SOL reference path is below that threshold; 1 SOL is above it. The exact
engine correctly re-evaluates original min/max constraints and applies zero
state mutation when a historical transaction would fail after our inserted
order. What is unknown is trader resubmission behavior, which V1 deliberately
does not invent.

## Decision

1. There is enough recurring opportunity supply to continue the project.
2. Capacity does not collapse between 0.5 and 1 SOL.
3. Ultra-early entry is not the sole or best opportunity horizon; later causal
   follow-through deserves primary selector attention.
4. Before treating 1 SOL replay labels as promotion-quality, bracket the
   counterfactual divergence uncertainty without fitting to outcomes.
5. After that, the correct alpha task is a selector for later follow-through
   opportunities, evaluated on untouched chronological data. It is not another
   broad handcrafted fingerprint leaderboard.

Final classification: `EXECUTABLE_OPPORTUNITY_CENSUS_V1_COMPLETE`.
