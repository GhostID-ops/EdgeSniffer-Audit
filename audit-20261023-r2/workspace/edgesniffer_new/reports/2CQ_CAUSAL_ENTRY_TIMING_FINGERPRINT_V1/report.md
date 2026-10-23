# 2CQ Causal Entry Timing Fingerprint V1

**Verdict:** `2CQ_ENTRY_TIMING_SIGNAL_WEAK`

## Population

- Frozen-window episodes: 6298
- Structurally entry-usable: 5296
- Causally usable: 99 episodes / 96 mints
- Earlier same-mint controls: 1468
- Train / validation: 70 / 29

## Entry Timing

| Model | Validation AUC | Top 1 | Top 3 | Top 10% | MRR |
|---|---:|---:|---:|---:|---:|
| Generic lifecycle | 0.405 | 72.41% | 86.21% | 79.31% | 0.793 |
| Full causal | 0.850 | 75.86% | 82.76% | 82.76% | 0.816 |

## Conviction

- Exact tier accuracy: 44.83%
- Adjacent-tier accuracy: 82.76%
- Majority baseline: 41.38%
- Spearman expected-vs-actual tier: 0.2900486439858536

## Decision

Available causal state contains some timing information, but its incremental validation advantage over lifecycle progression is not yet strong enough to call a reproducible fingerprint.

Chosen-vs-ignored next study justified: **FALSE**
