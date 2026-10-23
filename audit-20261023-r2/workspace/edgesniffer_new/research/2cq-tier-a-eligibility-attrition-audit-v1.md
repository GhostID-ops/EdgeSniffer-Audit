# 2CQ_TIER_A_ELIGIBILITY_ATTRITION_AUDIT_V1

Generated: 2026-09-10 UTC

## Frozen population

- Cohort start: `2026-09-09T22:10:20.911651625Z`
- Population: original first 25 canonical independent entry episodes
- Original eligible: 17/25 (68%)
- Outcomes, PnL, models, rankings, and thresholds inspected: 0
- Capture-first detector, AllenHark/proxy, and Tier-A scientific rules changed: no

## Root-cause table

| Entry mint | Teacher entry signature | Original reason | Root cause | Canonical information exists | Repairable without semantic change | Final classification |
|---|---|---|---|---:|---:|---|
| `AsPfrNeLdL8gDcC5TyeXcZ9qja6UhGMPJ5bcZnwHpump` | `sUr8zj26GH2qo2Uppg3F7RB4JSUWYkerHYmSCbUuPskCEZbTjhen9w9aYjsisGLkJd7mGXNTqZQJTsKq6yYraMC` | `PREENTRY_FINALIZED_TRANSACTION_FETCH_INCOMPLETE` | A finalized failed prior buy (`5cJht...kmBt`, Custom 6042) had been cached as a null RPC result. A single independent diagnostic retry returned complete v0 metadata. Failed transaction mutates no state. | yes | yes | repaired transient fetch/cache limitation |
| `G5TSop5JjNSyB2iQsgbV2EuCJzZABmC5qWFzAHD48Jji` | `4qYuxg2HA8dYEwEG9uDKRwT7VBbfpMPqGarhpgraEytB7n2TFNcoAq3T4XnjyNtBcc661hayFVWW43DkMGXCVfYa` | `NO_VALID_EARLIER_SAME_MINT_CONTROL` | Entry was the first successful Pump trade after creation; zero earlier successful Pump transactions and zero controls. | yes | no | `LEGITIMATE_NO_CONTROL_EXCLUSION` |
| `34DyXGFhCs3GxLppG6LBu4rn6qhpVX7FsmD555WWpump` | `2PvvrEsSDSpPX4shQQQLyPQ2HhVfeG45j9d7R7sW8QPd3hcVc983nunxx5AS8krmnmDYxK3MoMDdYDp9QLLnmdPr` | `PREENTRY_SIGNATURE_HISTORY_BOUNDARY_UNPROVEN` | Fixed 5,000 cap stopped early. Complete mint history is 6,017 signatures over 18 seconds and reaches create `4Qiiz...x9kT`. | yes | yes | `REPAIRABLE_HISTORY_QUERY_LIMITATION`, repaired |
| `3gkbrHRxDmE7uYSqEyGChy2Wi9KzZ7ggftKGi1pYKom2` | `63V21vjPwxAUEcMTe5L8RsX9LHNsBHgMN5kKaWjw2Ps7pUMDQjNtX8MnMA1PRbCKSdSKAebGLMronZoqzqhAjfqk` | `NO_VALID_EARLIER_SAME_MINT_CONTROL` | Entry was the first successful Pump trade after creation; zero earlier successful Pump transactions and zero controls. | yes | no | `LEGITIMATE_NO_CONTROL_EXCLUSION` |
| `CWMy6qBC8P5kAKES7wMD8ttMuvHJxVJgnRasBpvxpump` | `4EjbtBQA6vYCubP3h2uB5mSUuajYpGsg2TFJ15RtRfBeRcYZFP8BaDC7G6nR1JdjEGidvHnSe6ujjUyJzM8n3Dnd` | `PREENTRY_EXACT_EVENT_RECONSTRUCTION_INCOMPLETE` | Entry/prior are valid `buy_exact_quote_in_v2` against quote mint `XsoC...DF2W`; TradeEvent has zero SOL reserves and separate quote reserves. Frozen SOL-reserve semantics cannot represent it exactly. | yes | no | legitimate unsupported non-SOL quote-state exclusion |
| `85BugzpKBCxfZNPAarmnS91mXktWbdU8QjtjMwZvpump` | `2io3pAXngeridmYF7KHDeziPzLvfbFXoXJjbpayd2fdC98SMVUiR4AXzcgx8pRBJJoS7JGJuoGS4mNbWLPaPxwg3` | `PREENTRY_EXACT_EVENT_RECONSTRUCTION_INCOMPLETE` | Entry is `buy_exact_quote_in_v2`; prior is `buy_v2`, both against quote mint `XsvN...bzpg`; zero SOL reserves with separate quote reserves. | yes | no | legitimate unsupported non-SOL quote-state exclusion |
| `ByMgDXEsd9Fmm2qezKsCJRPAz2L7H1uezy4ZofV4pump` | `4PdydDbKcXjPmJUsZLRL4oyLTHbarzVLWS8tgn9cJeEU6rUmvjxNo5Mz4X62aN4q9b14P7bQWds9sKaN8YN3AWY4` | `PREENTRY_SIGNATURE_HISTORY_BOUNDARY_UNPROVEN` | Fixed 5,000 cap stopped early. Complete mint history is 6,903 signatures over 34 seconds and reaches create `3m2S...yd7S`. | yes | yes | `REPAIRABLE_HISTORY_QUERY_LIMITATION`, repaired |
| `BqeAxzZqwpobmMGtAAXFYWScHD5Ypo5s8yMWEB7dW1mE` | `4Fa9Kbpqco27A6Ahom3Z7ERGQRpH4Y8VxGvfc8iDhQxFaNeHWp5dbX6mGBMjwWPybrbrCSpdtxUpPCpZSMDf5G56` | `PREENTRY_EXACT_EVENT_RECONSTRUCTION_INCOMPLETE` | Entry/prior are valid `buy_exact_quote_in_v2` against quote mint `Xsc9...9qEh`; zero SOL reserves with separate quote reserves. | yes | no | legitimate unsupported non-SOL quote-state exclusion |

## Event evidence

- All eight entry transactions had finalized metadata, canonical slot/index, inner instructions, loaded addresses, and pre/post token balances.
- Non-SOL V2 affected events: `3fJm...foavH` slot 445778235/index 506; `2gvu...qXCA` slot 445778907/index 107; `HgJ6...nZnJ` slot 445779134/index 514. AllenHark timing was present.
- Transient fetch event: `5cJht...kmBt` slot 445775126/index 384, v0 loaded addresses and inner instructions present, finalized failed Custom 6042.
- Complete-history creates: `4Qiiz...x9kT` slot 445776931/index 305 and `3m2S...yd7S` slot 445778988/index 428.

## Repairs

1. Null finalized transaction cache entries are retried deterministically up to three total reads with bounded 250/500ms backoff, then remain fail-closed.
2. Mint history pagination now continues to the provider's finite finalized history boundary rather than stopping at an arbitrary five pages.
3. Unsupported event decoding is isolated per transaction and remains fail-closed; non-SOL quote reserves are never relabeled as SOL reserves.

## Re-evaluation

- Original frozen eligible: 17/25
- Post-repair eligible: 20/25 (80%)
- Newly recovered: AsPfr transient fetch; 34Dy and ByMg complete history boundaries
- Legitimate exclusions: 5
- Repairable pipeline exclusions: 3, all repaired
- Unknown: 0
- Active teacher span used by original N25: 0.468611 hours
- Active eligible rate: 42.679/hour
- Estimated N300 active hours: 7.03
- Estimated N500 active hours: 11.72
- Descriptive wall-clock eligible rate at rerun: 3.310/hour

## Verdict

`2CQ_TIER_A_ATTRITION_REPAIRED`

