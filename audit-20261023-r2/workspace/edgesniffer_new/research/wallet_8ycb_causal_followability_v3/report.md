# 8YCB Causal Flow + Third-Party Followability Audit V3

## 1. Executive conclusion

8YCB's public self-CREATE plus atomic initial buy is followed by unusually fast, repeatable external demand and retains positive **curve-only** follower economics through several latency assumptions. The edge is materially reduced by 8YCB's own rapid selling and by a +5% entry penalty. The evidence supports a prospective no-submit shadow, not implementation or live trading.

The signal is primarily **8YCB identity + atomic initial-buy action**, with reflexive/recurrent follower attention. Metadata content alone generalizes weakly, and generic CREATE structure is substantially worse.

## 2. Exact coverage and integrity

- Native V2 ledger window: `2026-08-03T23:20:05Z` through `2026-09-02T23:20:05Z`.
- Frozen V3 stream: `2026-08-28T13:39:25.262000Z` through `2026-09-02T23:44:16.229000Z` — **5.4200 days**, exactly **17,416,449,429 bytes**.
- Full frozen universe: **220,079 CREATEs** and **3,879,386** compact rows.
- Complete target horizons: **219**. Time-matched generic controls: **1,094**, five nearest controls per target before excluding one 8YCB-authored control.
- Generic match timing: median 2.595 seconds, maximum 41.074 seconds from its target.
- Nine selected flow rows above 100 SOL were treated as decoder/sentinel anomalies and excluded. No >1.1B-token sell rows were found.
- The native stream omitted routed 8YCB sells. **374** native sell deltas were therefore replayed at the midpoint of their block second; collision timing has about ±500 ms uncertainty.

The 5.42-day stream is below the requested seven-day completeness target. Results are a bounded sample, not a complete-period proof.

## 3. Native PnL decomposition and PF integrity

- Gross curve-side/tracker-comparable closed PnL: **+2166.118648 SOL**.
- Native realized closed PnL: **+2035.779175 SOL**.
- Gross-to-native drag: **130.339473 SOL**, including **3.213340 SOL** known network fees and **127.126133 SOL** residual Pump/tip/rent/other lamport flows.
- Closed positions: **1,618**; native WR **95.18%**; PF **461.40**; expectancy **+1.258207 SOL**; median **+0.634544 SOL**.
- Unresolved: **61** positions, **-224.171574 SOL** cash PnL on **276.945985 SOL** cost.
- If unresolved inventory is valued at zero: **+1811.607601 SOL**, WR **92.02%**, PF **8.64**, max drawdown **230.267 SOL**.
- Counting every unresolved position as a loss gives WR **91.72%**.

The enormous closed-only PF is real under the reconstructed ledger but is not a follower PF and is strongly flattered by excluding unresolved inventory. The zero-inventory stress is the safer integrity view.

## 4. 8YCB exit timing

- First sell: P10/P50/P75/P90/P99 = **1/4/6/8/43 seconds**.
- Last sell: P10/P50/P75/P90/P99 = **1/4/7/11/47 seconds**.
- **68.42%** begin selling by 5 seconds; **93.88%** by 10 seconds.
- Exit classes: `{'DELAYED_EXIT': 74, 'FAST_FULL_EXIT': 1132, 'FAST_PARTIAL_EXIT': 314, 'LONG_HOLD': 4, 'SCALE_OUT': 94}`.

This is a very short creator-distribution window. Third parties must acquire and become safely sellable before a material fraction of target exits.

## 5. External buyer arrival

For 8YCB launches, first external buy delay P10/P50/P75/P90 is **15/37/70/117 ms**. External demand appears on **59.82%** by 50 ms, **86.30%** by 100 ms, and **98.63%** by 250 ms. Mean gross external buy flow is **12.198 SOL** in the first second.

Time-matched generic launches are much weaker: first-buy median **120 ms**, only **25.59%** with a buy by 100 ms and **37.48%** by 1 second, with **2.153 SOL** mean first-second flow.

All measured external demand follows the public CREATE/atomic-buy boundary and cannot explain 8YCB's private decision to launch.

## 6. Repeat follower/copy-like cohort and reflexivity

- **72** wallets bought at least five distinct 8YCB launches within one second.
- This cohort appears within one second on **99.54%** of 8YCB targets versus **20.75%** of time-matched generic controls.
- It supplies **86.05%** of target first-second gross external buy SOL.

| Wallet | Target launches bought ≤1s | Median buy delay ms | Buy SOL ≤1s | Launches sold ≤120s | Median observed sell delay ms |
|---|---:|---:|---:|---:|---:|
| `DnRApgn4QXqkRHrde2W8EQAzXKcX3eXHVU4fQBKUzNpY` | 202 | 109 | 27.068 | 0 | n/a |
| `24678QKx2Dy8ZCw6Ra8o9DeTqPLL5GR9ZQKxt5FddHmq` | 147 | 49 | 146.400 | 99 | 4059 |
| `GDQeTnqkyeLyRSjWaeFY5HsxaWoQmmXFc1N6wyuuHiQD` | 109 | 50 | 205.300 | 0 | n/a |
| `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG` | 68 | 66 | 255.000 | 0 | n/a |
| `FDKdqQc5YfCNM3Y1VFwrc7NjY3SjKvn4dnyJv363yarJ` | 67 | 75 | 33.500 | 0 | n/a |
| `3j72HFDm4LKGEgsmcUUupXcLibL1MQXBefMrXM2je8Kc` | 66 | 74 | 43.191 | 1 | 554 |
| `9iYpKAGTuW1hNrf9A9CYvNYPWz21aV2cwq8jFHhrZ1Gz` | 61 | 111 | 4.390 | 35 | 4096 |
| `DxhpC9c4kGkYJR34xUrfFeDx9vwb6yoHUMWrTCZybwtH` | 48 | 62 | 57.500 | 28 | 2129 |
| `BiSeDbLSoqDVoxftodMNRLh1FKvRnkUMvgBu9Vy2PDQn` | 40 | 41 | 20.000 | 0 | n/a |
| `9VCgsSDDdyga1PPRwhwz3Js6W8yumkrUEmCqyXjmxN3A` | 35 | 103 | 0.175 | 0 | n/a |

These are behavioral recurrence labels, not proof that a wallet is a copybot, owned by one entity, or coordinated.

At a +250 ms entry with P50 safe-sell timing, actual replay is **WR 46.58%, PF 1.37, E +3.34%**. Removing 8YCB's sells improves it to **WR 59.36%, PF 3.28, E +9.54%**, demonstrating that creator distribution materially compresses third-party returns. Removing the recurrent early cohort produces **WR 51.14%, PF 2.20, E +7.56%**; the improvement means this cohort is not the sole positive-alpha source and its later selling is net harmful in this replay.

## 7. Metadata generalization and signal attribution

| Model, trained on earliest 50% only | Train AUC | Validation AUC | Holdout AUC |
|---|---:|---:|---:|
| metadata_content_only | 0.592 | 0.526 | 0.558 |
| atomic_action_only | 0.780 | 0.896 | 0.894 |
| metadata_plus_atomic_action | 0.755 | 0.854 | 0.871 |

- Creator identity is deterministic: 8YCB on 100% of targets and **0.00%** after exclusion in the final generic sample; one raw 8YCB-authored match was removed from generic economics.
- Atomic-buy action/size generalizes much better than text metadata, reaching holdout AUC **0.894**.
- Text-only metadata is near chance chronologically (holdout AUC **0.558**).
- `metadata.j7tracker.io` occurs on **100.00%** of targets versus **17.64%** of controls, but this is infrastructure/identity evidence, not independent narrative quality.
- Among generic controls at +250 ms, the top target-like text-score quintile is **WR 30.32%, PF 1.45, E +1.60%** versus **WR 21.08%, PF 1.00, E +0.02%** for the other four quintiles. This is modest outcome lift, not a stable standalone selector.

Conclusion: the event signal is **mixed, dominated by 8YCB identity and atomic initial-buy size/action, with a reflexive/recurrent follower response**. Generic CREATE structure is weak; content metadata is secondary.

## 8. Follower-entry reconstruction

All simulations use a 0.1 SOL curve entry after observing the public event, 125 bps Pump fees, frozen +20/-20/120s exits, and injected native target sells. They exclude network fees, priority/tips, contention, failed landing, and account-creation costs.

| Delay ms | Median entry price SOL/token | Median degradation vs 8YCB | P90 degradation | Median immediate round-trip ROI |
|---:|---:|---:|---:|---:|
| 25 | 3.38944548e-08 | +10.21% | +45.89% | -1.25% |
| 50 | 3.65993262e-08 | +16.94% | +88.55% | -1.25% |
| 100 | 4.139217144e-08 | +34.91% | +108.64% | -1.25% |
| 250 | 4.285747887e-08 | +41.19% | +165.59% | -1.25% |
| 500 | 4.3401031e-08 | +42.61% | +165.59% | -1.25% |
| 1000 | 4.285747887e-08 | +42.00% | +357.70% | -1.25% |

## 9. Frozen follower economics and +5% stress

| Delay ms | WR | PF | Expectancy | +5% WR | +5% PF | +5% expectancy |
|---:|---:|---:|---:|---:|---:|---:|
| 25 | 84.02% | 10.79 | +21.48% | 73.97% | 5.18 | +15.67% |
| 50 | 68.49% | 3.97 | +14.33% | 62.10% | 2.55 | +10.11% |
| 100 | 55.25% | 2.04 | +7.57% | 50.23% | 1.49 | +4.60% |
| 250 | 46.58% | 1.37 | +3.34% | 42.47% | 1.06 | +0.64% |
| 500 | 48.86% | 1.48 | +4.11% | 41.55% | 1.08 | +0.92% |
| 1000 | 43.84% | 1.09 | +0.81% | 33.33% | 0.83 | -1.95% |

At 0.1 SOL per trade, unstressed cumulative curve PnL across the 219 targets is **+0.7316 SOL** at +250 ms and **+0.1767 SOL** at +1 second. These are replay totals, not live returns.

At +250 ms, the top five winners contribute **7.69%** of gross positive PnL and the top five losses contribute **7.39%** of gross loss; max chronological drawdown at 0.1 SOL is **0.1303 SOL**.

| UTC day | n | +250 ms PF | Expectancy | +5% PF | +5% expectancy |
|---|---:|---:|---:|---:|---:|
| 2026-08-28 | 50 | 1.13 | +1.38% | 0.86 | -1.79% |
| 2026-08-29 | 64 | 1.39 | +3.77% | 1.15 | +1.70% |
| 2026-08-30 | 22 | 2.27 | +7.73% | 1.78 | +6.45% |
| 2026-08-31 | 48 | 1.81 | +5.37% | 1.16 | +1.49% |
| 2026-09-01 | 25 | 0.96 | -0.46% | 0.69 | -4.00% |
| 2026-09-02 | 10 | 1.05 | +0.48% | 1.06 | +0.72% |

## 10. Empirical P50/P75/P90 first-sellable results

Independent Rust dust telemetry contains **52** unique safe-first-sellable observations: P50 **122 ms**, P75 **178 ms**, P90 **346 ms** from Rust acceptance. These observations are from other launches, not 8YCB trades.

| Entry delay | P50 safe result | P75 safe result | P90 safe result |
|---:|---|---|---|
| 25 | WR 83.11%, PF 17.25, E +36.35% | WR 83.11%, PF 18.18, E +38.42% | WR 82.65%, PF 18.88, E +40.70% |
| 50 | WR 68.04%, PF 5.46, E +21.69% | WR 68.04%, PF 5.82, E +23.41% | WR 68.04%, PF 6.15, E +25.03% |
| 100 | WR 55.25%, PF 2.25, E +9.15% | WR 55.25%, PF 2.42, E +10.40% | WR 55.25%, PF 2.58, E +11.52% |
| 250 | WR 46.58%, PF 1.37, E +3.34% | WR 46.58%, PF 1.37, E +3.34% | WR 46.58%, PF 1.40, E +3.56% |
| 500 | WR 48.86%, PF 1.48, E +4.11% | WR 48.86%, PF 1.48, E +4.11% | WR 48.86%, PF 1.48, E +4.11% |
| 1000 | WR 43.84%, PF 1.09, E +0.81% | WR 43.84%, PF 1.09, E +0.81% | WR 43.84%, PF 1.09, E +0.81% |

The safe-sell floor is `max(entry delay, empirical percentile)`. Some short-delay results improve because inability to exit immediately avoids an early stop and captures a later price gap; that path dependence is another reason prospective shadow validation is required.

## 11. Target-sell collision and external-demand survival

- Target has begun selling by follower entry on **1.83%** at +25 ms, **7.31%** at +250 ms, **15.53%** at +500 ms, and **29.68%** at +1 second.
- Target sells before the simulated follower exit on **45.21%** at +250 ms and **50.68%** at +1 second.
- From a +25 ms entry, target has sold before P50/P75/P90 safe-sell time on **4.57% / 5.48% / 10.96%**.
- Among **179** targets with a known sell, **92.74%** still receive an external buy afterward. Post-first-sell buys account for **34.23%** of external gross buy SOL in those 120-second tapes.

Demand survives the first creator sell frequently, but actual PF is far lower than the no-creator-sell counterfactual. Third-party alpha is therefore exposed to creator distribution even when external flow persists.

## 12. Generic-launch benchmark and third-party feasibility

| Delay ms | Generic WR | Generic PF | Generic expectancy | Generic +5% PF | Generic +5% expectancy |
|---:|---:|---:|---:|---:|---:|
| 25 | 29.34% | 1.72 | +1.93% | 0.63 | -2.25% |
| 50 | 27.24% | 1.49 | +1.41% | 0.56 | -2.84% |
| 100 | 24.86% | 1.20 | +0.65% | 0.47 | -3.63% |
| 250 | 22.94% | 1.10 | +0.34% | 0.43 | -3.97% |
| 500 | 22.39% | 1.08 | +0.27% | 0.42 | -4.06% |
| 1000 | 21.39% | 0.99 | -0.02% | 0.38 | -4.34% |

8YCB-tagged events substantially outperform generic CREATEs, especially below 250 ms. At +250/+500 ms, the target result remains positive but becomes marginal under +5% entry stress. At +1 second, stressed target economics are negative. The public event is therefore latency-sensitive and cannot be treated as executable alpha from this replay alone.

## 13. Important limitations

- The frozen all-launch stream spans 5.42 days, below the requested seven-day completeness target.
- Routed inner-instruction coverage is not independently complete; source-emitted rows are replayed and 8YCB sells are added from the native ledger at block-second midpoint.
- Injected 8YCB sell timing has approximately +/-500 ms uncertainty because the native index supplies block seconds, not local receive milliseconds.
- Follower entries are deterministic 0.1 SOL curve replays with 125 bps Pump fees but no network fee, priority fee, tip, slippage cap, landing failure, or contention loss.
- Historical flow is replayed after inserting a counterfactual follower; downstream order sizes are not re-equilibrated for that intervention.
- Safe-first-sellable percentiles come from 52 separate Rust dust executions, not the 8YCB target launches.
- CREATE metadata is observable simultaneous with the atomic buy, not before it.
- Repeat-wallet behavior does not prove copybot ownership, coordination, or intent.
- Generic controls are time-matched and transaction-decoded but are a sample of five per target, not all 220,079 CREATEs.
- No prospective shadow or live landing test of the 8YCB event was performed.


## 14. Final classification

`8YCB_PUBLIC_EVENT_PROMISING_NEEDS_PROSPECTIVE_SHADOW`

Reason: the identity/atomic-action event creates a strong, immediate external-demand differential and positive latency replays after adding native creator sells. However, the sample is only 5.42 days, the +250/+500 ms +5% stress margin is thin, +1 second stress is negative, sell times are only block-second precise, and no prospective landing/account-ready shadow exists. Research stops here; nothing is implemented.
