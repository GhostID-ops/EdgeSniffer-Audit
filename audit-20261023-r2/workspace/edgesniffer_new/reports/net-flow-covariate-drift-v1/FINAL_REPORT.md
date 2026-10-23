# NET_FLOW_COVARIATE_DRIFT_V1

## Verdict

`NET_FLOW_DISTRIBUTION_MODEST_DRIFT`

This diagnostic was fully outcome-blind. It read no policy labels, ROI, PnL, exits, future prices, or replay outcomes and performed no economic simulation.

## Overall Distributions

| Population | N | Mean | Median/P50 | P75 | P80 | P90 | P95 | P97.5 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Discovery 1-6848 | 1,209 | 5.0617 | 2.7340 | 3.6653 | 4.6200 | 16.9966 | 20.5415 | 27.0000 | 34.6059 | 86.0579 |
| Unseen 6849-8293 | 254 | 4.3067 | 2.7428 | 3.3900 | 3.6653 | 5.9201 | 19.3440 | 20.9856 | 31.8330 | 86.0679 |

The center is stable, but the upper-middle tail is thinner in unseen data. The median moved only +0.0087 SOL, while P90 moved -11.0765 SOL.

## Frozen Threshold

The frozen threshold remains exactly `16.996559399 SOL`.

| Population | Below | Equal | Above | Midrank percentile |
|---|---:|---:|---:|---:|
| Discovery | 1,085 | 18 | 106 | 90.488 |
| Unseen | 240 | 0 | 14 | 94.488 |

The discovery Q90 behaves like approximately unseen P94.49. It selected 124/1,209 discovery anchors (10.26%, including ties) versus 14/254 unseen anchors (5.51%).

## Discovery Chronology

| Ordinals | Total mints | >=2 anchors | Anchor % | >=threshold | % anchors | % all mints | Median | P75 | P80 | P90 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1-1370 | 1,370 | 215 | 15.69% | 25 | 11.63% | 1.82% | 2.7000 | 3.6576 | 4.1457 | 17.0222 | 20.5415 | 30.0630 | 40.0000 |
| 1371-2740 | 1,370 | 201 | 14.67% | 36 | 17.91% | 2.63% | 3.0000 | 9.0643 | 15.9968 | 19.2274 | 20.9966 | 35.0000 | 35.0000 |
| 2741-4110 | 1,370 | 170 | 12.41% | 20 | 11.76% | 1.46% | 2.7566 | 3.8471 | 4.7200 | 20.1510 | 20.8426 | 28.6196 | 43.2930 |
| 4111-5480 | 1,370 | 270 | 19.71% | 11 | 4.07% | 0.80% | 2.5262 | 3.1843 | 3.3789 | 5.5283 | 14.9828 | 27.0000 | 86.0579 |
| 5481-6848 | 1,368 | 353 | 25.80% | 32 | 9.07% | 2.34% | 2.8000 | 3.5438 | 4.6840 | 6.8040 | 19.9705 | 35.0000 | 43.2930 |

The tail compression began during discovery block 4. It was therefore not created solely by the unseen period.

## Unseen Chronology

| Ordinals | Total mints | >=2 anchors | Anchor % | >=threshold | % anchors | % all mints | Median | P75 | P80 | P90 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6849-7571 | 723 | 131 | 18.12% | 7 | 5.34% | 0.97% | 2.7879 | 3.7000 | 3.8171 | 5.9201 | 17.2563 | 30.0000 | 33.9000 |
| 7572-8293 | 722 | 123 | 17.04% | 7 | 5.69% | 0.97% | 2.7034 | 3.0606 | 3.2704 | 5.3628 | 18.2229 | 32.8000 | 86.0679 |

Both unseen halves show almost identical frozen-threshold frequency. This makes the reduced tail frequency look persistent rather than a short sampling interruption.

## Anchor Frequency

| Population | Total mints | >=2 anchors | Anchor rate | >=threshold | Threshold/all mints |
|---|---:|---:|---:|---:|---:|
| Discovery | 6,848 | 1,209 | 17.65% | 124 | 1.81% |
| Unseen | 1,445 | 254 | 17.58% | 14 | 0.97% |

The rate of reaching the causal >=2 SOL anchor did not materially change. The shift is in the composition above that anchor, especially around the high-flow tail.

## Drift Statistics

| Statistic | Point estimate | Bootstrap 95% interval |
|---|---:|---:|
| KS distance | 0.0758 | 0.0579 to 0.1373 |
| Wasserstein distance | 1.2798 SOL | 0.7871 to 2.0688 SOL |
| Median shift | +0.0087 SOL | -0.2324 to +0.2026 SOL |
| P90 shift | -11.0765 SOL | -13.3073 to -6.5201 SOL |

The small KS distance and stable median argue against broad distributional collapse. The robust negative P90 shift and the threshold moving about four percentile points deeper into the unseen tail support `MODEST_DRIFT`.

## Boundary

The frozen `16.996559399 SOL` candidate remains unchanged and its unseen economic test remains unread.

- Replacement threshold calculated: no
- Economics evaluated: no
- Outcome fields accessed: zero
- Validation/holdout inspected: zero
- Ordinals beyond 8293 inspected: no
- Collector or trading services modified: no
