# EVENT_FLOW_COMPETING_RISK_V1

## EVENT_FLOW_HAS_NO_USEFUL_INCREMENTAL_SIGNAL

Exact states: `22910` across `3377` mints.

| Model | Event macro AUC | Mint-weighted macro AUC | Profit AUC | Mint-weighted log loss |
|---|---:|---:|---:|---:|
| CUMULATIVE_NET_FLOW_HGB | 0.7644 | 0.8064 | 0.8074 | 0.4272 |
| CURVE_STATE_HGB | 0.7346 | 0.8142 | 0.7675 | 0.4234 |
| TOKEN_AGE_HGB | 0.6079 | 0.6318 | 0.5937 | 0.5237 |
| COMBINED_STATIC_HGB | 0.7961 | 0.8441 | 0.8142 | 0.3801 |
| EVENT_FULL_HGB | 0.8050 | 0.8515 | 0.8218 | 0.3743 |
| EVENT_FULL_LOGISTIC | 0.7951 | 0.8337 | 0.8113 | 0.7734 |

Strongest static baseline: `COMBINED_STATIC_HGB`.

No validation, holdout, fresh data, shadow trading, or live trading was used.
