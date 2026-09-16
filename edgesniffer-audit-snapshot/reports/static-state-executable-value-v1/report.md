# STATIC_STATE_EXECUTABLE_VALUE_V1

## STATIC_STATE_HAS_NO_USEFUL_EXECUTABLE_EDGE

Frozen features: `cumulative_net_external_flow_sol`, `current_virtual_sol`, `hypothetical_entry_impact_bps`, `token_age_ms`

## Coverage

| Population | N | Share | Median states | Feature coverage | Median lifespan ms | Median max net flow | Median max virtual SOL |
|---|---:|---:|---:|---:|---:|---:|---:|
| evaluable | 3377 | 49.31% | 3.0 | 3377 | 5765.0 | 0.341333333 | 33.427509238 |
| nonEvaluable | 3471 | 50.69% | 0.0 | 29 | 0.0 | 34.999999999 | 65.555555551 |

Coverage classification: `SEVERE_SELECTION_BIAS_OBVIOUS`.
Non-evaluable reasons: `{'NO_EXACT_EVENT_STATE': 3392, 'NO_SOL_EVENTS': 79}`. Only `29` of `3471` non-evaluable mints retained comparable causal quantities; their medians must not be treated as population medians.

## Primary policies

| Policy | N | WR | Expectancy SOL | PF | PnL SOL | Median ROI | Max DD SOL |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2700 | 15.07% | -0.01633 | 0.3274883509644504 | -44.084 | -2.671% | 44.224 |
| top20pct | 542 | 30.26% | -0.01967 | 0.4979653680123789 | -10.663 | -3.115% | 10.768 |
| top10pct | 194 | 47.42% | -0.00682 | 0.806783506394982 | -1.324 | -0.623% | 1.945 |
| top5pct | 96 | 52.08% | -0.00712 | 0.8082641454129106 | -0.683 | +6.753% | 1.555 |

### Return distribution and concentration

| Policy | P10 ROI | P50 ROI | P90 ROI | Avg win SOL | Avg loss SOL | Best SOL | Worst SOL | Best / gross profit | Best 5 / gross profit | Top 1% / gross profit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | -11.110% | -2.671% | +3.699% | +0.05274 | -0.02859 | +0.85600 | -0.29604 | 3.99% | 15.16% | 32.73% |
| top20pct | -21.638% | -3.115% | +11.745% | +0.06449 | -0.05619 | +0.85600 | -0.29604 | 8.09% | 27.87% | 30.03% |
| top10pct | -19.793% | -0.623% | +12.159% | +0.06007 | -0.06716 | +0.76640 | -0.29604 | 13.87% | 29.18% | 18.28% |
| top5pct | -23.550% | +6.753% | +11.771% | +0.05759 | -0.07745 | +0.23133 | -0.27568 | 8.03% | 17.68% | 8.03% |

### Best-trade removal and competing outcomes

| Policy | PF after best 1 | PnL after best 1 | PF after best 2 | PnL after best 2 | Profitable continuation | Adverse flow | Timeout | Median hold ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.314 | -44.940 | 0.301 | -45.790 | 7.48% | 13.19% | 79.33% | 60000 |
| top20pct | 0.458 | -11.519 | 0.418 | -12.369 | 19.19% | 26.75% | 54.06% | 60000 |
| top10pct | 0.695 | -2.090 | 0.659 | -2.334 | 31.96% | 26.80% | 41.24% | 21968 |
| top5pct | 0.743 | -0.914 | 0.723 | -0.986 | 50.00% | 28.12% | 21.88% | 13121 |

## Descriptive monotonicity

These bins use the first forward-scored state per mint and full-discovery score quantiles for description only. They are not executable tail thresholds.

| Score quantile | Score bounds | N | WR | Expectancy SOL | PF | PnL SOL | Median ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0-50% | 0.004473-0.021888 | 1350 | 7.85% | -0.01504 | 0.173 | -20.301 | -2.671% |
| 50-80% | 0.021888-0.047462 | 809 | 17.92% | -0.01579 | 0.316 | -12.777 | -2.671% |
| 80-90% | 0.047462-0.057741 | 271 | 17.34% | -0.03278 | 0.280 | -8.884 | -2.671% |
| 90-95% | 0.057741-0.074523 | 130 | 30.00% | -0.03027 | 0.454 | -3.935 | -2.801% |
| 95-100% | 0.074523-0.415724 | 140 | 50.00% | +0.01294 | 1.650 | +1.812 | -0.009% |

Expectancy rank monotonicity (Spearman): `0.100`.

## Chronological robustness

### top20pct

| Fold | Ordinals | N | WR | Expectancy SOL | PF | PnL SOL |
|---:|---|---:|---:|---:|---:|---:|
| 2 | 1371-2740 | 116 | 35.34% | -0.01642 | 0.555 | -1.905 |
| 3 | 2741-4110 | 87 | 35.63% | -0.00956 | 0.671 | -0.832 |
| 4 | 4111-5480 | 152 | 26.32% | -0.03468 | 0.326 | -5.271 |
| 5 | 5481-6848 | 187 | 27.81% | -0.01420 | 0.598 | -2.656 |

### top10pct

| Fold | Ordinals | N | WR | Expectancy SOL | PF | PnL SOL |
|---:|---|---:|---:|---:|---:|---:|
| 2 | 1371-2740 | 64 | 54.69% | +0.00472 | 1.176 | +0.302 |
| 3 | 2741-4110 | 24 | 50.00% | -0.00035 | 0.988 | -0.009 |
| 4 | 4111-5480 | 37 | 37.84% | -0.01671 | 0.564 | -0.618 |
| 5 | 5481-6848 | 69 | 44.93% | -0.01448 | 0.666 | -0.999 |

### top5pct

| Fold | Ordinals | N | WR | Expectancy SOL | PF | PnL SOL |
|---:|---|---:|---:|---:|---:|---:|
| 2 | 1371-2740 | 39 | 64.10% | +0.01453 | 1.714 | +0.567 |
| 3 | 2741-4110 | 11 | 54.55% | +0.00209 | 1.084 | +0.023 |
| 4 | 4111-5480 | 20 | 45.00% | -0.01613 | 0.682 | -0.323 |
| 5 | 5481-6848 | 26 | 38.46% | -0.03654 | 0.359 | -0.950 |

## Primary robustness

| Policy | Remove top 1% active PF | Expectancy | PnL | Non-divergent N | Non-divergent PF | Expectancy | PnL |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.319 | -0.01645 | -43.969 | 2175 | 0.252 | -0.01662 | -36.140 |
| top20pct | 0.475 | -0.02071 | -10.854 | 295 | 0.476 | -0.02175 | -6.415 |
| top10pct | 0.772 | -0.00811 | -1.477 | 94 | 0.862 | -0.00476 | -0.447 |
| top5pct | 0.672 | -0.01344 | -1.170 | 40 | 0.267 | -0.05157 | -2.063 |

## P75 sensitivity

| Policy | Matched N | Missing | WR | Expectancy SOL | PF | PnL SOL |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 2700 | 0 | 14.67% | -0.01701 | 0.30252719548133633 | -45.933 |
| top20pct | 542 | 0 | 29.34% | -0.02153 | 0.4525273689410699 | -11.669 |
| top10pct | 194 | 0 | 46.39% | -0.00727 | 0.7952191627369012 | -1.410 |
| top5pct | 96 | 0 | 51.04% | -0.00805 | 0.7845651895131805 | -0.773 |

## Walk-forward thresholds

Each threshold is a quantile of frozen combined-static scores from the corresponding fold's strictly prior training population.

| Fold | Top 20% | Top 10% | Top 5% |
|---:|---:|---:|---:|
| 2 | 0.085976446 | 0.130436971 | 0.284257531 |
| 3 | 0.091912791 | 0.372175151 | 0.525666758 |
| 4 | 0.105405490 | 0.266233586 | 0.511205591 |
| 5 | 0.101148877 | 0.214150255 | 0.449801513 |

## Verdict gates

- `minimumTrades`: `True`
- `materialPositiveExpectancy`: `False`
- `profitFactor`: `False`
- `survivesBestTwoRemoval`: `False`
- `chronologicalConsistency`: `False`
- `pessimisticExecution`: `False`
- `descriptiveMonotonicity`: `False`

## Interpretation

The frozen static score materially enriches profitable continuations, but its causal first-entry policies remain below break-even. The top 10% and top 5% tails both have negative expectancy and PF below 1; later chronological folds deteriorate, descriptive bin expectancy is not monotonic, and P75 execution remains negative.

The optional 1 SOL capacity sensitivity was not run because the primary 0.5 SOL result was not clearly promising.

Frozen-score reproduction maximum absolute difference: `0.0`.

No ordinal 6849+, validation, holdout, prospective, or live data was read.
