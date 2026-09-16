# CONSTRAINED_CAUSAL_RANKER_V1

Final verdict: `MULTIVARIATE_SIGNAL_NOT_USEFUL`

This is discovery-only forward-style evaluation, not a trading threshold or deployable strategy.

## Inputs

Included: net_external_flow_sol, buy_size_progression_ratio, last_buy_size_vs_prior_median
Excluded by frozen redundancy rule: gross_buy_sol_per_successful_tx

## Pooled forward evaluation

| Metric | Model | Net-flow reference |
|---|---:|---:|
| AUC | 0.639 | 0.665 |
| PR-AUC | 0.225 | 0.268 |
| Top 10% positive rate | 20.21% | 32.98% |
| Top 10% enrichment | 1.47x | 2.39x |

## Chronological blocks

| Test block | Train ordinals | Test ordinals | N | AUC | Top-10 enrichment | Top-10 PF |
|---:|---|---|---:|---:|---:|---:|
| 2 | 1-1370 | 1371-2740 | 192 | 0.679 | 0.82x | 0.5484629597312618 |
| 3 | 1-2740 | 2741-4110 | 163 | 0.535 | 1.92x | 6.356351379797848 |
| 4 | 1-4110 | 4111-5480 | 247 | 0.649 | 2.47x | 1.4278846991148315 |
| 5 | 1-5480 | 5481-6848 | 334 | 0.679 | 1.87x | 2.7937242188253157 |

No validation or holdout rows were inspected. No threshold was selected and no shadow/live service was changed.
