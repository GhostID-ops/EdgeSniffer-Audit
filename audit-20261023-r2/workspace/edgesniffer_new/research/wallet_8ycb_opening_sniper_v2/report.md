# 8YCB Opening-Sniper Reverse Engineering V2

## A. DATA COVERAGE

- Requested UTC window: `2026-08-03T23:20:05Z` through `2026-09-02T23:20:05Z`. First/last successful target transaction: `2026-08-03T23:21:09Z` through `2026-09-02T21:49:24Z`.
- Successful target-fee-payer transactions: **5,869**.
- Decoded Pump.fun self-launches: **2,255** — **1,624** with atomic initial buy, **626** without initial buy, **5** ambiguous; decode errors: **0**.
- Independent all-launch stream snapshot: `2026-08-28T13:39:25.262000Z` through `2026-09-02T23:44:16.229000Z` (**5.42 days**). This is below seven days and omits some routed inner-instruction flow, so it is not sufficient for complete 30-day control/follower reconstruction.

## B. WALLET ECONOMICS INTEGRITY

Native accounting uses 8YCB's actual lamport delta per transaction, so reported PnL includes network, priority/tip, Pump-fee, and rent movements. Closed positions only are used for realized WR/PF. **61** unresolved positions and **11** left-censored sell-only positions are excluded rather than silently scored as wins. There are **408** closed positions with multiple sells, **9** closed rows with 1–3 raw token units of residual dust, and **168** non-trade transfer events kept outside position PnL but linked by mint/time/signature in the ledger. **58** unresolved rows overlap a transfer and remain unresolved rather than being mislabeled as sales. Another **1,439** successful transactions did not expose exactly one non-zero target token delta and were not forced into the ledger; CREATE controls/identity were still decoded independently from the full transaction set. Reconstructed network fees total **3.318635 SOL**; Pump fees cannot be isolated reliably from this indexed payload and are not fabricated. If every unresolved position is counted as a loss, WR falls to **91.72%**. If remaining inventory is valued at zero, closed+open cash PnL is **+1811.607601 SOL** with PF **8.641**.

The submitted-signature endpoint retained only `2026-08-30T19:39:28Z` through `2026-09-02T22:34:19Z`. Within that bounded slice, **102,452/120,523 (85.01%)** submissions failed; this rate is not extrapolated across 30 days.

## C. NATIVE WR / PF / EXPECTANCY

- Closed positions: **1,618**
- NATIVE_REALIZED_WR: **95.18%**
- Native PF: **461.399**
- Total realized PnL: **+2035.779175 SOL**
- Expectancy / median: **+1.258207 / +0.634544 SOL**
- Max chronological drawdown: **0.546791 SOL**
- TRACKER_COMPARABLE_WR: **97.78%**, tracker-like PF **1452.172**
- Positive-PnL concentration: top 1 / top 5 = **0.96% / 3.79%**; loss concentration: top 1 / top 5 = **9.93% / 28.15%**.
- Best / worst native trade: **+19.578573 / -0.439043 SOL**. Exact top-five rows and signatures are in `native_ledger_summary.json`.

## D. POSITION SIZE ARCHETYPES

On-chain initial principal clusters: `{'~1_SOL': 813, '~3_SOL': 768, 'OTHER': 43}`. Closed self-launch outcomes by cluster: ~1 SOL **n=809, WR=97.16%, mean PnL=+0.6093 SOL**; ~3 SOL **n=725, WR=96.41%, mean PnL=+1.7633 SOL**. Principal-vs-ROI Pearson correlation is only **+0.065**. The ~1/~3 SOL metadata means and median ROIs are very similar, so they do not establish separate selection families; the **36** closed OTHER-size rows are too small and heterogeneous for a robust third family. These are creator initial-allocation sizes, not established conviction tiers for outside launches.

## E. CREATE -> BUY TIMING

All decoded positives are an initial buy in the same atomic transaction as CREATE: 0 ms, 0 slots, and no external buyer can precede the initial buy inside that launch transaction.

## F. ENTRY RANK / CURVE STATE

The target buys from the pristine initial curve as creator. External buy rank and gross prior SOL are zero by construction. Under the explicit canonical-curve estimate in `target_launches.jsonl.gz`, median implied MC moves from **27.959 SOL** before the atomic allocation to **29.842 SOL** after it; median marginal-price impact is **6.73%**. USD MC is null because no historical SOL/USD series was attached.

## G. OPENING-SNIPER CLASSIFICATION

`CREATOR_SELF_INITIAL_BUY_AT_CREATE`: **1,624/1,624** decoded launches. This is not third-party opening sniping.

## H. POSITIVE SET

`target_launches.jsonl.gz` contains one row per decoded self-launch with only atomic decision-time identity, size, token-program, Mayhem-account-presence, strictly prior in-window creator history, and on-chain CREATE metadata. All **2,255** classified self-CREATE rows reference Token-2022.

## I. CONTROL UNIVERSE

The primary all-third-party ignored-launch universe is not causally comparable because 8YCB is a creator. A much stronger matched control was recovered natively: **626** 8YCB-authored CREATE transactions with no atomic initial buy. These share creator, launcher, token program, and CREATE boundary with the positives. Ambiguous multi-asset CREATE transactions are excluded.

## J. CREATOR / FUNDER ENTITY ANALYSIS

Decoded creator equals 8YCB for **1,624/1,624 (100.00%)** positives. Each feature-table row includes only earlier in-window launch count and positions completed strictly before that CREATE; this creator history is explicitly left-censored at the window start. A 100-page indexed TRANSFER scan covering back to 2026-05-24 found no Odin co-occurrence, so the named historical funding clue remains unverified and is not used as alpha evidence.

## K. METADATA / NARRATIVE ANALYSIS

Name, symbol, and metadata URI were decoded directly from CREATE payloads. Repeated normalized-name groups: **128**; repeated metadata-URI groups: **0**. `blindape` launches: **2** across distinct mints listed in the machine summary.

## L. MAYHEM ANALYSIS

Mayhem program and agent accounts appear in **1,624/1,624** positives and **626/626** matched controls: **100% versus 100%, zero observed lift**. This proves only CREATE-transaction account presence, not that Mayhem mode was active.

## M. SAME-SLOT PARTICIPANT ANALYSIS

The bounded retained-stream overlap covered **219** target launches. Canonical block order was available for **103** target slots and missing for **131**. Canonical ordering found **0** participant rows before 8YCB, **425** after, and **693** ambiguous. After-entry participants are not used as selection features.

## N. PRE-ENTRY WALLET-QUALITY ANALYSIS

Not applicable to the positive decision boundary: no external wallet can have a completed prior buy in a token that is created in the same atomic transaction.

## O. PRE-ENTRY FLOW ANALYSIS

Pre-entry buy count, SOL, and unique buyers are all zero by construction. Flow cannot explain which narratives 8YCB chooses to launch.

## P. 8YCB SELECTION FEATURE LIFTS

`feature_lift.json` compares **1,624** self-created buys against **626** self-created no-buy controls using CREATE-time name/symbol/repetition/time features plus strictly prior, completed in-window creator history. The largest descriptive effects are fewer name words (**2.17 vs 4.11, SMD -1.27**), shorter names (**12.87 vs 20.72, SMD -1.10**), and less uppercase text (**0.249 vs 0.660, SMD -1.09**). Machine output includes prevalence, equal-frequency monotonicity buckets, and per-split effect sizes. CREATE fields are simultaneous with CREATE, not available before the atomic buy; creator-history values are prospectively valid but left-censored at the 30-day boundary.

## Q. MODEL-ASSISTED SELECTION FORENSICS

A class-weighted logistic regression was fitted only on the earliest 50%. ROC AUC: train **0.914**, validation **0.822**, final holdout **0.926**. Its largest absolute training coefficients are name uppercase ratio (**-1.106**), name word count (**-0.789**), and name length (**-0.461**). This remains explanatory: those dominant fields arrive at CREATE alongside the atomic buy, while prior-history inputs are window-left-censored.

## R. POST-ENTRY TAPE

Native target exits are reconstructed for 30 days. Sub-second curve-tape MFE/MAE is available only for the **219** retained-overlap launches in `stream_target_outcomes.jsonl.gz`; it is not extrapolated to 30 days.

## S. EXIT ARCHETYPES

Counts: `{'FAST_FULL_EXIT': 1132, 'FAST_PARTIAL_EXIT': 314, 'DELAYED_EXIT': 74, 'SCALE_OUT': 94, 'LONG_HOLD': 4}`. Median first/last exit latency: **4.0 / 4.0 seconds**.

## T. FOLLOWER LATENCY ECONOMICS

Curve-only 0.1 SOL `+20/-20/120s` upper bounds on **219** launches:

| Delay | WR | PF on ROI | Mean ROI | +5% worse WR | +5% worse mean ROI |
|---:|---:|---:|---:|---:|---:|
| 25 ms | 93.61% | 34.67 | +23.72% | 86.76% | +20.32% |
| 50 ms | 79.91% | 11.45 | +18.15% | 72.15% | +15.35% |
| 100 ms | 67.58% | 5.14 | +12.93% | 57.99% | +9.99% |
| 250 ms | 59.36% | 3.35 | +9.85% | 49.77% | +6.85% |
| 500 ms | 57.99% | 2.94 | +9.03% | 46.58% | +5.73% |
| 1,000 ms | 48.86% | 1.65 | +4.32% | 38.81% | +1.57% |

These omit network/tips/account readiness and do not prove live landing or full routed-flow completeness.

## U. FOLLOWER + FIRST-SELLABLE ECONOMICS

Not proven. Every follower row explicitly records `first_sellable_adjusted_result=null` because account-ready telemetry was not retained.

## V. PRE-ENTRY FINGERPRINT FOLLOWABILITY

The independently detectable on-chain fingerprint is 8YCB's CREATE transaction plus short/few-word/lowercase metadata. It arrives simultaneously with the atomic target buy. The curve-only overlap is promising only at very low delay and decays sharply by 250 ms-1 s; no production followability claim is warranted.

## W. BOUGHT VS IGNORED CLASSIFICATION

Invalid as originally framed for third-party launches. Reframed matched classification uses 8YCB's own no-initial-buy CREATEs as controls; results are in `positive_control_feature_table.jsonl.gz` and `model_forensics.json`.

## X. WINNERS VS LOSERS / ROBUSTNESS

Native WR falls from **95.18%** at any positive PnL to **87.02%** above +0.10 SOL; ROI WR falls from **95.18%** above 0% to **66.07%** above 20%. The open-inventory-zero and all-unresolved-as-loss stress tests, top-five concentration, and best/worst trades are in `native_ledger_summary.json`.

## Y. CHRONOLOGICAL STABILITY

See the strict chronological 50/25/25 metrics in `native_ledger_summary.json`.

## Z. WHAT INFORMATION APPEARS TO DRIVE 8YCB

The transaction evidence shows launch authorship plus atomic initial inventory acquisition. On-chain pre-entry flow does not drive the decision because no token/flow exists yet.

## AA. WHAT INFORMATION IS MISSING

Narrative sourcing, social/news timing, private launcher inputs, and any off-chain entity workflow before CREATE; complete routed post-entry tape; canonical block order for 131 older overlap slots; account-ready/first-sellable and landing-cost telemetry; archival stream retention >=7 days.

## AB. BEST EXPLAINABLE FINGERPRINT CLUES

1. Creator identity equals 8YCB.
2. Atomic pristine-curve initial buy.
3. Strong ~1 SOL and ~3 SOL initial-principal clusters.
4. Repeated on-chain name/symbol patterns and a shared metadata-host domain (no repeated full URI after deduplication).
5. CREATE-time metadata-only classifier remained discriminative chronologically, but the fields are not visible before the atomic buy.

These explain the observed transactions but do not define a prospectively detectable third-party opening-snipe strategy.

## AC. FINAL CLASSIFICATION

`8YCB_DATASET_PARTIAL_BUT_USEFUL`

The 30-day native economics/identity result is useful and materially changes the hypothesis. Control and follower datasets do not meet the completeness standard, so no detector, strategy, or followability claim is warranted.
