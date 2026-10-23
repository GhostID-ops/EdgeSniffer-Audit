# SANDBOX_NO_NEW_ALPHA_SIGNAL

Boundary: ordinals 1-3920 (sandbox only)

## Top 15 Feature Shapes

| Feature | Family | Age | Shape | Spread | One bucket only? |
|---|---|---:|---|---:|---|
| earlyReserveMovementPct | CURVE-STATE / CURVE-MOVEMENT | 500ms | non-monotonic | 0.0290 | False |
| reserveVelocityPctPerSec | CURVE-STATE / CURVE-MOVEMENT | 500ms | non-monotonic | 0.0290 | False |
| earlyReserveMovementPct | CURVE-STATE / CURVE-MOVEMENT | 250ms | non-monotonic | 0.0288 | False |
| reserveVelocityPctPerSec | CURVE-STATE / CURVE-MOVEMENT | 250ms | non-monotonic | 0.0288 | False |
| reserveMovePerTransaction | CURVE-STATE / CURVE-MOVEMENT | 250ms | non-monotonic | 0.0271 | False |
| reserveMovePerTransaction | CURVE-STATE / CURVE-MOVEMENT | 500ms | non-monotonic | 0.0258 | False |
| medianBuySol | TRADE-SIZE STRUCTURE | 250ms | non-monotonic | 0.0247 | False |
| buyerEntropy | BUYER CONCENTRATION | 250ms | non-monotonic | 0.0237 | False |
| rapidCredibleCapital | EARLY-STATE TRANSITIONS | 500ms | non-monotonic | 0.0229 | False |
| flowAcceleration | FLOW ACCELERATION | 500ms | non-monotonic | 0.0226 | False |
| buyerEntropy | BUYER CONCENTRATION | 500ms | non-monotonic | 0.0225 | False |
| rapidCredibleCapital | EARLY-STATE TRANSITIONS | 250ms | non-monotonic | 0.0225 | False |
| earlyReserveMovementPct | CURVE-STATE / CURVE-MOVEMENT | 100ms | non-monotonic | 0.0220 | False |
| reserveVelocityPctPerSec | CURVE-STATE / CURVE-MOVEMENT | 100ms | non-monotonic | 0.0220 | False |
| flowVelocitySolPerSec | FLOW VELOCITY | 500ms | non-monotonic | 0.0214 | False |

## Top 10 Coherent Fingerprints

| Candidate | N | Age | 0.5 PF | 0.5 Exp | 0.5 PnL | 1.0 PF | 1.0 Exp | Blocks+ | PF ex top3 | Concern |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|

## Closest Rejected Hypotheses

| Candidate | N | Age | 0.5 PF | 0.5 Exp | 1.0 PF | Blocks+ | PF ex top3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| velocity_high_entropy_250ms | 547 | 250ms | 0.41 | -0.0416 | 0.39 | 0/4 | 0.38 |

Rejected rule: `flowVelocitySolPerSec >= 7.8 AND buyerEntropy >= 0.0131186`

| velocity_high_entropy_500ms | 536 | 500ms | 0.40 | -0.0398 | 0.38 | 0/4 | 0.37 |

Rejected rule: `flowVelocitySolPerSec >= 3.92608 AND buyerEntropy >= 0.0640389`

| curve_impulse_100ms | 828 | 100ms | 0.39 | -0.0370 | 0.37 | 0/4 | 0.36 |

Rejected rule: `reserveVelocityPctPerSec >= 65.9663 AND meanBuySol >= 0.405`

| unique_flow_accelerating_250ms | 385 | 250ms | 0.39 | -0.0277 | 0.38 | 0/4 | 0.33 |

Rejected rule: `flowVelocitySolPerSec >= 7.8 AND uniqueAcceleration >= -6`

| unique_flow_accelerating_100ms | 458 | 100ms | 0.35 | -0.0316 | 0.34 | 0/4 | 0.31 |

Rejected rule: `flowVelocitySolPerSec >= 18.9 AND uniqueAcceleration >= -10`

| curve_impulse_250ms | 820 | 250ms | 0.33 | -0.0456 | 0.31 | 0/4 | 0.30 |

Rejected rule: `reserveVelocityPctPerSec >= 26.4743 AND meanBuySol >= 0.376`

| curve_impulse_500ms | 822 | 500ms | 0.30 | -0.0482 | 0.29 | 0/4 | 0.28 |

Rejected rule: `reserveVelocityPctPerSec >= 13.5744 AND meanBuySol >= 0.340822`

| low_size_cv_fast_capital_100ms | 357 | 100ms | 0.29 | -0.0303 | 0.29 | 0/4 | 0.22 |

Rejected rule: `buySizeCv <= 0 AND flowVelocitySolPerSec >= 18.9`

| low_size_cv_fast_capital_250ms | 297 | 250ms | 0.17 | -0.0347 | 0.17 | 0/4 | 0.12 |

Rejected rule: `buySizeCv <= 0 AND flowVelocitySolPerSec >= 7.8`

| low_size_cv_fast_capital_500ms | 273 | 500ms | 0.16 | -0.0364 | 0.15 | 0/4 | 0.10 |

Rejected rule: `buySizeCv <= 0 AND flowVelocitySolPerSec >= 3.92608`


## Direct Answers

- **strongestNewFeatureFamily:** CURVE-STATE / CURVE-MOVEMENT / earlyReserveMovementPct was the most discriminative shape, but every coarse bin remained negative
- **capitalSpeedVsRawCount:** Neither produced positive exact economics; rapid capital arrival was not sufficient at 25-500ms.
- **buyerDiversity:** It did not rescue economics alone or in the controlled velocity/concentration interactions.
- **concentrationLevelVsChange:** Neither level nor checkpoint change produced a positive, temporally persistent pocket.
- **whaleThenFollowers:** no top-ranked recurrence
- **fewTradesHighSol:** no top-ranked recurrence
- **threeStrongestNewCandidates:** []
- **futureTestBoundary:** ordinal 3921 and later

## Integrity

- Validation rows inspected: 0
- Holdout rows inspected: 0
- Live submissions: 0
- Future untouched boundary begins: ordinal 3921
