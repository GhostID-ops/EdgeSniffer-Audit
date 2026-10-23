# NET_EXTERNAL_FLOW_FROZEN_UNSEEN_V1

## Verdict

`TOO_FEW_UNSEEN_MATCHES`

The frozen unseen sample-size gate did not pass. No unseen economic outcomes were read and no PF, expectancy, PnL, or robustness analysis was performed.

## Frozen Contract

| Field | Value |
|---|---:|
| Discovery ordinals | 1-6848 |
| Eligible discovery >=2 SOL anchors | 1,209 |
| Feature | `net_external_flow_sol` |
| Quantile | empirical Q90, linear interpolation at `(N-1)*0.90` |
| Frozen threshold | 16.996559399 SOL |
| Fingerprint | first causal external gross buy >=2 SOL AND net external flow >=16.996559399 SOL |
| Minimum exact-evaluable unseen matches | 40 |

## Frozen Unseen Gate

| Measure | Result |
|---|---:|
| Unseen ordinals | 6849-8293 |
| Unseen population | 1,445 |
| Eligible causal >=2 SOL anchors | 254 |
| Feature matches | 14 |
| Exact-evaluable feature matches | 13 |
| Duplicate eligible mints | 0 |
| Outcome labels read | 0 |
| Economics evaluated | No |

The 13 exact-evaluable matches are below the required 40. The study therefore stopped at the preregistered feature-only gate. The threshold was not loosened and no neighboring percentile was inspected.

## Integrity

- Same-timestamp post-trigger rows were excluded.
- The unseen feature artifact declares `outcomeFieldsPresent=false`.
- Fifteen same-timestamp post-trigger rows were excluded across the 14 feature matches.
- Validation and holdout were not inspected.
- No model, feature combination, exit optimization, shadow trade, or live trade was run.
- Collector remained active with PID `1921840` and `NRestarts=0` after the study.

## Immutable Identities

| Artifact | SHA256 |
|---|---|
| Contract | `a575e940d14e6eb0c10695963b2513f72ab6e0bcb96bc3fc7fc5e06e7a936208` |
| Runner | `1f13c01e562c1e485b5ee4eab701ac3ddb5a3dcf215add55ca3e2d2132d9d646` |
| Parent corrected same-anchor rows | `331a5239bb2ae595976b5ba92b080ce2f92452ca1bbe20a099ff9f9006c7dba7` |
| Threshold freeze | `c58f327c5aa4e932cd397bc96e36d9e35c1faa59466b99a1f6c41e589e647243` |
| Discovery prefix 1-6848 | `d85533d6c246161bc5992f1d61cae4273d50d8c67fca8d4b38a65c23ad994fdd` |
| Unseen prefix 6849-8293 | `d353e345a7156d2e1cd92e538e704e4846ab39041b88e7cac6bef0f1065b07fa` |
| Combined prefix 1-8293 | `a1dee24328c9a808435aa8a1ff83d569b052873fa7be865d423d5882bfb5f419` |
| Unseen feature rows | `7aff7d71c6c6efe7887b979c6c6523363832ad7d509734c0459eade1eda0a19d` |
| Feature-gate report | `21e6659a7e05f34ee46536f86ec7a81b45c8635379866f0400ffd6d690e004a4` |

This result is a sample-readiness verdict, not evidence that the fingerprint passes or fails economically.
