# 2CQ Decision System Decomposition V1

Development-only, outcome-blind result. Official validation remains sealed.

## Population

- Development entries: 350
- Timing-identifiable entries: 253
- Unique mints: 334

## Same-mint timing (internal chronological assessment)

| Model | N | ROC AUC | Top-1 | Top-3 | Chance Top-1 |
|---|---:|---:|---:|---:|---:|
| FULL_CAUSAL | 76 | 0.704 | 68.42% | 69.74% | 19.62% |
| LIFECYCLE | 76 | 0.168 | 56.58% | 76.32% | 19.62% |
| LIFECYCLE_FLOW | 76 | 0.630 | 65.79% | 67.11% | 19.62% |

## Cross-sectional choice (internal chronological assessment)

| Universe | Model | N | Mean choices | ROC AUC | Top-1 | Chance Top-1 | Missing chosen |
|---|---|---:|---:|---:|---:|---:|---:|
| ACTIVE_1000MS | FULL_CAUSAL | 45 | 5.86 | 0.995 | 100.00% | 24.48% | 17 |
| ACTIVE_1000MS | LIFECYCLE | 45 | 5.86 | 0.931 | 86.67% | 24.48% | 17 |
| ACTIVE_1000MS | LIFECYCLE_FLOW | 45 | 5.86 | 0.972 | 86.67% | 24.48% | 17 |
| ACTIVE_250MS | FULL_CAUSAL | 37 | 3.62 | 0.992 | 100.00% | 34.35% | 17 |
| ACTIVE_250MS | LIFECYCLE | 37 | 3.62 | 0.919 | 86.49% | 34.35% | 17 |
| ACTIVE_250MS | LIFECYCLE_FLOW | 37 | 3.62 | 0.947 | 91.89% | 34.35% | 17 |
| ACTIVE_5000MS | FULL_CAUSAL | 46 | 9.33 | 0.995 | 100.00% | 15.00% | 16 |
| ACTIVE_5000MS | LIFECYCLE | 46 | 9.33 | 0.948 | 84.78% | 15.00% | 16 |
| ACTIVE_5000MS | LIFECYCLE_FLOW | 46 | 9.33 | 0.981 | 86.96% | 15.00% | 16 |
| LAUNCHED_120S | FULL_CAUSAL | 55 | 23.08 | 0.982 | 85.45% | 7.97% | 136 |
| LAUNCHED_120S | LIFECYCLE | 55 | 23.08 | 0.961 | 83.64% | 7.97% | 136 |
| LAUNCHED_120S | LIFECYCLE_FLOW | 55 | 23.08 | 0.978 | 83.64% | 7.97% | 136 |

## Conviction / size

- Assessment N: 105
- Exact tier accuracy: 44.76%
- Majority baseline: 28.57%
- Expected-tier Spearman: 0.411
- Tier MAE: 0.733

## Integrity

- Validation rows read: 0
- Outcome labels read: 0
- Submissions: 0
- Teacher entry excluded from features: yes
- Cross-sectional states are strictly pre-teacher-boundary: yes

## Scope

ADD_WHILE_EXPOSED, PARTIAL_EXIT, and FINAL_FLATTEN remain deferred because the frozen V3 tape is an independent-entry census.

Verdict: `2CQ_DECISION_SYSTEM_DECOMPOSITION_V1_DEVELOPMENT_COMPLETE`
