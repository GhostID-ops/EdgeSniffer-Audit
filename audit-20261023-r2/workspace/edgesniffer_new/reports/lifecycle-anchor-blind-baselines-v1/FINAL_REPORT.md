# LIFECYCLE_ANCHOR_BLIND_BASELINES_V1

## Verdict

`PRIMARY_BOTTLENECK_SELECTION`

`SECONDARY_FINDING_LATER_LIFECYCLE_ENRICHMENT_WITH_SURVIVORSHIP_AND_DIVERGENCE_RISK`

The four frozen lifecycle anchors do not produce profitable blind executable
trading at either 0.5 SOL or 1.0 SOL. The later flow anchors do enrich both the
win rate and the prevalence of executable +10% opportunity, but no anchor is a
standalone strategy. Delivery cost, P50-to-P90 latency, 0.5-to-1.0 SOL capacity,
or the tested exit-policy alternatives do not explain away the loss.

The next justified research phase is causal separability inside the 2 SOL and
5 SOL anchor populations, with the 1 SOL anchor retained as a broader control.
This is a selector study, not a promotion or live-trading decision.

## Frozen Run

- Discovery population: chronological ordinals 1-6848 only
- Validation rows inspected: 0
- Holdout rows inspected: 0
- Unique mints qualifying at one or more anchors: 1,679
- Distinct mint-anchor rows: 4,601
- Primary economics: 0.5 SOL, P50 execution, TP20/SL20/120s, 0.001 SOL standardized delivery overlay
- Secondary size: 1.0 SOL
- Robustness latency: P90; P75 explicitly unavailable and not imputed
- Live changes, submissions, shadow deployments: 0
- Contract SHA256: `65cb16764312ecd271ac3f7199762927992d5bdc9c27653c94280fcfca71f0d4`
- Report SHA256: `c963b69d35b409347afbeabc6b537b60b3a1f63216cdb285420efb7ae89508bf`
- Evaluator SHA256: `ef91cbed59e3d1d0e0e3bde3861f54a49ba02f23a36a55d0b70db16898d4aa00`

## Primary Blind Economics

| Frozen anchor | Eligible | Exact N | WR | PF | Expectancy | PnL @0.5 SOL | TP / SL / max hold | MAX_OPEN1 N / PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| External gross buy >=1 SOL | 1,518 | 1,451 | 23.57% | 0.485 | -3.71% | -26.918 SOL | 146 / 163 / 1,142 | 410 / 0.449 |
| External gross buy >=2 SOL | 1,209 | 1,146 | 26.18% | 0.532 | -3.48% | -19.946 SOL | 134 / 149 / 863 | 394 / 0.620 |
| External gross buy >=5 SOL | 662 | 607 | 33.44% | 0.578 | -3.98% | -12.091 SOL | 100 / 113 / 394 | 332 / 0.611 |
| Fifth external buy event | 1,212 | 1,179 | 21.20% | 0.378 | -5.63% | -33.163 SOL | 118 / 172 / 889 | 390 / 0.324 |

All four PFs are materially below 1.0. MAX_OPEN=1 does not make any anchor
profitable. The closest constrained result is the 2 SOL anchor at PF 0.620 and
-2.77% expectancy.

## Opportunity Versus Capture

| Anchor | Exact opportunity N | +10% opportunity | Policy positive | Policy >=+10% | Policy >=10 / opportunity >=10 |
|---|---:|---:|---:|---:|---:|
| >=1 SOL | 1,488 | 16.26% | 22.98% | 12.03% | 73.97% |
| >=2 SOL | 1,179 | 18.41% | 25.45% | 13.49% | 73.27% |
| >=5 SOL | 632 | 23.89% | 32.12% | 18.04% | 75.50% |
| Fifth buy | 1,205 | 15.27% | 20.75% | 11.95% | 78.26% |

The 5 SOL anchor has the strongest enrichment, but only 662 of 6,848 discovery
mints reached it. Its 151 +10% opportunities equal 2.21% of the original
discovery universe. The anchor concentrates opportunity; it does not create a
positive blind policy.

## Capacity, Latency, And Cost

| Anchor | 0.5 SOL PF / exp | 1.0 SOL PF / exp | P90 PF / exp | PF at 0.020 delivery cost |
|---|---:|---:|---:|---:|
| >=1 SOL | 0.485 / -3.71% | 0.461 / -3.91% | 0.422 / -4.26% | 0.267 |
| >=2 SOL | 0.532 / -3.48% | 0.502 / -3.74% | 0.458 / -4.15% | 0.299 |
| >=5 SOL | 0.578 / -3.98% | 0.545 / -4.39% | 0.487 / -5.02% | 0.359 |
| Fifth buy | 0.378 / -5.63% | 0.368 / -5.62% | 0.343 / -6.01% | 0.224 |

Moving from 0.5 to 1.0 SOL degrades results modestly but does not cause a
capacity collapse. P90 latency also worsens every lane but does not transform a
profitable baseline into a loser: the baseline is already negative at P50.
Even removing the 0.001 SOL overlay would leave every expectancy negative, so
delivery cost is not the root cause.

P75 policy economics remain `UNAVAILABLE_NOT_IMPUTED` because the parent census
has no empirical P75 sell-landing model. No synthetic midpoint was invented.

## Exit Diagnostics

| Anchor | Primary PF / exp | Trail PF / exp | Fixed 5s PF / exp |
|---|---:|---:|---:|
| >=1 SOL | 0.485 / -3.71% | 0.489 / -3.98% | 0.407 / -2.63% |
| >=2 SOL | 0.532 / -3.48% | 0.532 / -3.83% | 0.461 / -2.45% |
| >=5 SOL | 0.578 / -3.98% | 0.581 / -4.46% | 0.516 / -2.43% |
| Fifth buy | 0.378 / -5.63% | 0.402 / -5.71% | 0.232 / -3.94% |

No frozen diagnostic exit reaches PF 1. Fixed 5s reduces average loss at the
flow anchors but remains decisively negative. This does not support an
exit-optimization detour before selection work.

## Causality And Replay Caveats

- The entry is after the complete crossing transaction, using actual overshoot.
- Median threshold overshoot is 0.553 SOL at the 1 SOL anchor, 0.767 SOL at the
  2 SOL anchor, and 0.816 SOL at the 5 SOL anchor.
- At the 5 SOL anchor, P75 actual cumulative flow is 9.113 SOL and P90 is
  20.491 SOL. Therefore `>=5 SOL` is a heterogeneous post-transaction state,
  not a precise 5 SOL entry.
- Divergence rises from 35.55% at >=1 SOL to 39.69% at >=2 SOL and 46.99% at
  >=5 SOL in the opportunity population.
- The non-divergent sensitivity remains negative: PF 0.324, 0.381, and 0.441
  respectively. Excluding divergence does not reveal hidden blind alpha.
- Anchor populations overlap heavily and must not be summed. All four anchors
  intersect on 578 mints.

## Integrity Audit

- 18 compressed partitions verified against their recorded SHA256 hashes.
- Each partition contains one provenance header plus its declared economic row
  count. Headers explain the superficial 4,619 JSON-line count.
- Economic rows: exactly 4,601.
- Unique mint-anchor pairs: exactly 4,601; duplicates: 0.
- Unique mints: exactly 1,679.
- Parent-state mismatches: 0.
- Rows outside ordinals 1-6848: 0.
- Fingerprint search: false.
- Feature-separability search: false.
- Best-anchor selection: false.
- Canonical exact-evidence collector remained active with NRestarts=0.

## Decision

Blind lifecycle timing is insufficient. The data rejects `buy every mint at
anchor X` at both target sizes. It also rejects delivery fees, modest latency,
or the tested exits as the main explanation.

The useful finding is that opportunity density rises with accumulated organic
flow. The next bounded experiment should test whether strictly causal features
available at the same 2 SOL and 5 SOL crossing separate the profitable minority
from the losing majority. Use the 1 SOL population as a broader control, freeze
all feature definitions on discovery, and do not inspect validation or holdout.
Do not promote the 5 SOL anchor itself and do not resume live trading from this
result.
