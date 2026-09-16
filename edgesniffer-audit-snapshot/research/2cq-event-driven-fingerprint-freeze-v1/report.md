# 2CQ Event-Driven Fingerprint Freeze V1

Development-only, outcome-blind, validation-sealed.

## Population

- Development entries: 350
- Same-mint timing examples: 253
- Cross-mint LAUNCHED_120S examples: 211

## Ablations

| Problem | Model | N | AUC | Top-1 | Top-3 | Chance Top-1 |
|---|---|---:|---:|---:|---:|---:|
| WHEN | LIFECYCLE | 76 | 0.168 | 56.58% | 76.32% | 19.62% |
| WHEN | LIFECYCLE_FLOW | 76 | 0.630 | 65.79% | 67.11% | 19.62% |
| WHEN | FULL_CAUSAL | 76 | 0.704 | 68.42% | 69.74% | 19.62% |
| WHEN | FULL_WITHOUT_AGE_RECENCY | 76 | 0.676 | 68.42% | 69.74% | 19.62% |
| WHEN | FULL_WITHOUT_250MS | 76 | 0.644 | 68.42% | 71.05% | 19.62% |
| WHAT | LIFECYCLE | 55 | 0.961 | 83.64% | 94.55% | 7.97% |
| WHAT | LIFECYCLE_FLOW | 55 | 0.978 | 83.64% | 96.36% | 7.97% |
| WHAT | FULL_CAUSAL | 55 | 0.982 | 85.45% | 98.18% | 7.97% |
| WHAT | FULL_WITHOUT_AGE_RECENCY | 55 | 0.976 | 80.00% | 96.36% | 7.97% |
| WHAT | FULL_WITHOUT_250MS | 55 | 0.977 | 87.27% | 98.18% | 7.97% |

## Event-driven replay

- Frozen timing threshold: 0.462296864
- Assessment teacher-state recall: 78.95%
- First crossing is final selected state: 31.58%
- First-crossing lead P50: 108.93 ms
- Final selected-state to teacher P50: 36.27 ms

## Stable causal shifts

### Within-mint timing

| Feature | Robust effect | 95% clustered bootstrap | Stable blocks | Teacher median | Control median |
|---|---:|---:|---:|---:|---:|
| log_price_change_250ms | 2.154 | [1.978, 2.262] | 5/5 | 0.334 | 0.001 |
| net_flow_sol_250ms | 1.879 | [1.809, 2.080] | 5/5 | 5.951 | 0.028 |
| gross_buy_sol_250ms | 1.885 | [1.719, 2.039] | 5/5 | 6.025 | 0.225 |
| log_price_change_1000ms | 1.157 | [1.066, 1.259] | 5/5 | 0.354 | 0.003 |
| net_flow_sol_1000ms | 0.903 | [0.797, 0.990] | 5/5 | 6.075 | 0.086 |
| consecutive_buys | 0.667 | [0.667, 0.667] | 5/5 | 4.000 | 1.000 |
| log_price_change_2000ms | 0.791 | [0.735, 0.854] | 5/5 | 0.354 | 0.008 |
| gross_buy_sol_1000ms | 0.664 | [0.622, 0.713] | 5/5 | 6.156 | 1.182 |
| buy_count_250ms | 0.667 | [0.667, 0.667] | 5/5 | 4.000 | 1.000 |
| net_acceleration_1s | 0.639 | [0.585, 0.711] | 5/5 | 6.156 | 0.066 |

### Cross-mint selection

| Feature | Direction | Stable blocks | Teacher median | Control median |
|---|---|---:|---:|---:|
| gross_buy_sol_5000ms | higher | 5/5 | 5.488 | 0.000 |
| log_price_change_250ms | higher | 5/5 | 0.261 | -0.000 |
| log_price_change_1000ms | higher | 5/5 | 0.254 | -0.000 |
| log_price_change_2000ms | higher | 5/5 | 0.262 | -0.000 |
| log_price_change_5000ms | higher | 5/5 | 0.305 | -0.000 |
| log_age_ms | lower | 5/5 | 0.000 | 10.857 |
| last_external_trade_sol | higher | 5/5 | 1.200 | 0.050 |
| total_external_net_sol | higher | 5/5 | 6.589 | 0.160 |
| log_median_interarrival_ms_5s | lower | 5/5 | 0.000 | 8.517 |
| log_ms_since_last_external | lower | 5/5 | 0.000 | 9.613 |

## Interpretation

2CQ usually chooses a newly launched, currently moving mint, then enters immediately after a compact externally driven burst: strong positive 250ms flow and price response, several consecutive buys, and less single-buyer concentration. Cross-mint choice is mostly lifecycle/recency; exact within-mint timing depends more on the short flow transition.

The state ranker is not yet a sufficiently specific standalone trigger. Its frozen training-recall threshold passed 78.95% of assessment teacher states, but only 31.58% of episodes first crossed at the final selected state; the upper-quarter first crossings were materially premature. A full transition/cross-sectional event rule must be validated before prospective use.

Full-market unmatched signals/hour is deliberately not claimed from a tape materialized around teacher decisions.

## Integrity

- Outcome labels read: 0
- Validation rows read: 0
- Submissions: 0

Verdict: `2CQ_EVENT_DRIVEN_FINGERPRINT_FROZEN_DEVELOPMENT_ONLY`
