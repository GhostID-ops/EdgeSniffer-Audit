# 2CQ Preliminary Choice Fingerprint Peek V1

NON-AUTHORITATIVE, outcome-blind, development-only hypothesis peek.

## SOL-Pump forward check

- Fair decisions: 123
- Frozen historical model Top-1: 88.62% (chance 9.33%)
- Top-3: 96.75%
- MRR: 0.931
- Chronological halves Top-1: 88.52% / 88.71%

## Preliminary SOL-Pump fingerprint

- Very young: selected median 67ms from CREATE versus 49003ms for the contemporaneous control median.
- Immediate continuation: selected median 46ms since the latest external event versus 20243ms for controls.
- Fast multi-buy impulse: median 3 buys and 4.17 SOL gross buy flow in 250ms.
- Sustained first-second flow: median 4.94 SOL gross and 4.00 SOL net buy flow in 1s.
- Meaningful participants: median 3 unique buyers, mean buy 1.73 SOL, largest buy 2.02 SOL.

This describes what 2CQ chose, not whether following those choices is profitable after execution.

| Feature | Direction | Selected median | Control median | Mean choice percentile | Stable blocks |
|---|---|---:|---:|---:|---|
| log_ms_since_last_external | LOW | 3.8501 | 9.9156 | 0.084 | yes |
| gross_buy_sol_1000ms | HIGH | 4.9363 | 0.0000 | 0.897 | yes |
| mean_buy_sol_1000ms | HIGH | 1.7297 | 0.0000 | 0.897 | yes |
| gross_buy_sol_250ms | HIGH | 4.1707 | 0.0000 | 0.897 | yes |
| largest_buy_sol_1000ms | HIGH | 2.0250 | 0.0000 | 0.896 | yes |
| buy_count_250ms | HIGH | 3.0000 | 0.0000 | 0.894 | yes |
| net_flow_sol_1000ms | HIGH | 4.0000 | 0.0000 | 0.892 | yes |
| net_flow_sol_250ms | HIGH | 4.0000 | 0.0000 | 0.890 | yes |
| gross_buy_sol_2000ms | HIGH | 5.2000 | 0.0000 | 0.889 | yes |
| buy_count_1000ms | HIGH | 3.0000 | 0.0000 | 0.877 | yes |

## Route siblings

### LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET
N=46. NON_AUTHORITATIVE_DESCRIPTIVE_HYPOTHESIS_ONLY
- lastEventAgeMs: LOW, mean within-choice percentile 0.261, stable blocks=yes
- buyCount: HIGH, mean within-choice percentile 0.645, stable blocks=yes
- eventCount: HIGH, mean within-choice percentile 0.608, stable blocks=yes
- uniqueActorCount: HIGH, mean within-choice percentile 0.605, stable blocks=yes
- sellCount: HIGH, mean within-choice percentile 0.520, stable blocks=no

### PUMPSWAP_ACTIVE_5000MS_SAME_SPEND_ASSET
N=1. TINY_N_NO_FINGERPRINT

## Integrity

- Outcome/PnL fields read: 0
- Validation rows read: 0
- Submissions: 0
- Policies modified: 0
- Routes pooled: no
- Current sealed validation consumed: no
- This report may generate hypotheses but may not freeze or promote a strategy

Verdict: `PRELIMINARY_TEACHER_CHOICE_FINGERPRINT_VISIBLE_NOT_ALPHA_PROOF`
