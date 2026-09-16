# EVENT_FLOW_COMPETING_RISK_V1

## Verdict

`EVENT_FLOW_HAS_NO_USEFUL_INCREMENTAL_SIGNAL`

Recent event dynamics contain a statistically detectable but economically/research-wise small increment over static causal state. The increment failed the frozen materiality gate, so no top-5%, top-10%, or top-20% economic diagnostic was run.

## Frozen Design

- Discovery only: ordinals 1-6848
- Decision state: after each atomic successful Pump transaction group containing external activity
- Bound: first 25 external groups and mint age <=30,000ms
- Position: 0.5 SOL
- Entry: P50 +37ms
- First sellable: P50 +261ms
- Competing boundaries: executable-net +10% / -10%
- Timeout: 60 seconds after decision
- Delivery overlay: 0.001 SOL
- Replay: exact Pump integer math, exact fees/impact, strict no-resubmission
- Evaluation: expanding chronological walk-forward, mint-equal training/evaluation weighting

## Population

| Measure | Result |
|---|---:|
| Discovery mints processed | 6,848 |
| Mints with exact event states | 3,377 |
| Exact event states | 22,910 |
| Forward-scored mints | 2,700 |
| Forward-scored states | 18,263 |
| PROFITABLE_CONTINUATION | 2,417 (10.55%) |
| ADVERSE_FLOW | 6,847 (29.89%) |
| FLOW_DECAY_TIMEOUT | 13,646 (59.56%) |
| Counterfactual-divergent states | 8,586 (37.48%) |

Integrity: 18 partitions, 6,848 unique mint rows, zero duplicate mint/state keys, zero 30-second age-bound violations, and zero 25-state-bound violations.

## Model Comparison

| Frozen model | Event macro AUC | Mint-weighted macro AUC | Mint-weighted profit AUC | Mint-weighted log loss |
|---|---:|---:|---:|---:|
| Token age only | 0.6079 | 0.6318 | 0.5937 | 0.5237 |
| Cumulative net flow only | 0.7644 | 0.8064 | 0.8074 | 0.4272 |
| Curve state only | 0.7346 | 0.8142 | 0.7675 | 0.4234 |
| Combined static state | 0.7961 | 0.8441 | 0.8142 | 0.3801 |
| Full event HGB | 0.8050 | 0.8515 | 0.8218 | 0.3743 |
| Full event logistic diagnostic | 0.7951 | 0.8337 | 0.8113 | 0.7734 |

The strongest static comparator was `COMBINED_STATIC_HGB`.

## Incremental Gate

| Test | Result | Requirement | Pass? |
|---|---:|---:|---:|
| Mint-weighted macro AUC delta | +0.00739 | >=+0.02000 | No |
| Mint-weighted log-loss improvement | +0.00581 | >0 | Yes |
| Mint-cluster bootstrap 95% CI | +0.00462 to +0.01039 | lower >0 | Yes |
| Positive-delta forward blocks | 4/4 | >=3/4 | Yes |
| Delta after removing top 1% hyperactive mints | +0.00771 | >=+0.01000 | No |
| Non-divergent-only delta | +0.00687 | descriptive sensitivity | Still small |

The result is stable enough to be real, but its magnitude is too small to be useful under the preregistered standard.

## Chronological Results

| Evaluation block | Ordinals | Event AUC | Static AUC | Delta |
|---|---|---:|---:|---:|
| 2 | 1371-2740 | 0.8926 | 0.8884 | +0.00425 |
| 3 | 2741-4110 | 0.8687 | 0.8585 | +0.01016 |
| 4 | 4111-5480 | 0.8003 | 0.7969 | +0.00344 |
| 5 | 5481-6848 | 0.8273 | 0.8059 | +0.02135 |

Direction was consistent, but only the final block reached the +0.02 magnitude target.

## Event Information

Grouped permutation importance against mint-weighted macro AUC:

| Event family | Mean AUC drop | Positive-drop folds | Interpretation |
|---|---:|---:|---|
| Buy-size progression | 0.00391 | 4/4 | Largest, but weak |
| Event timing | 0.00344 | 4/4 | Consistent, but weak |
| Signed sequence | 0.00120 | 4/4 | Detectable, minimal |
| Flow acceleration | 0.00081 | 3/4 | Negligible |
| Recent selling | 0.00079 | 2/4 | Small and unstable |

## Required Answers

1. **Recent event timing:** Yes, but only weakly. It was the second-largest event contribution and remained positive in all folds.
2. **Acceleration/deceleration:** Almost no useful increment; mean AUC contribution was about 0.0008.
3. **Signed sequence structure:** Consistent but very small, about 0.0012 AUC.
4. **Buy-size progression:** Strongest event family, but still only about 0.0039 AUC.
5. **Recent selling:** It did not produce stable incremental risk information; only 2/4 folds showed a positive drop when permuted.
6. **Beyond cumulative net flow:** Full event state beats net flow alone by about 0.045 macro AUC, but curve state and token age explain most of that gap. Beyond the combined static state, true event-dynamics gain is only 0.0074.
7. **Uncertainty:** Lowest-entropy states occurred very early (median age 676ms) and were overwhelmingly timeout predictions; accuracy was 97.54%. Highest-entropy states occurred later (median age 8,290ms), had a much more balanced outcome mix, and only 39.46% accuracy.

## Economic Boundary

`economicDiagnosticRun = false`

Because the predictive materiality gate failed, the experiment did not inspect high-confidence-tail economics. It did not create a selector, threshold, fingerprint, shadow lane, or live lane.

## Final Boundary

- Validation rows inspected: 0
- Holdout rows inspected: 0
- Ordinals 6849+ inspected: 0
- Hyperparameter search: none
- Package installations: none
- Collector/service changes: none
- Frozen net-flow unseen study: untouched

No deeper-research block is justified by this experiment.
