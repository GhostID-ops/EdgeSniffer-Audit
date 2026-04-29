# Strategy Backtest Report
Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-04-28T00:17:13.949Z
## Assumptions
- Backtest uses the source wallet's observed fills from `source-wallet-analysis-summary.json`.
- Copied buys receive the same token/SOL rate, then apply 300 bps entry penalty.
- Copied sells mirror the source wallet's sell fraction, then apply 300 bps exit penalty.
- Transaction costs are 0.008 SOL per copied buy and 0.005 SOL per copied sell.
- Leftover tokens are marked at zero. This is intentionally conservative for unresolved bags.
- Source-size multiplier is 1x with max buy cap 3.000 SOL.
- Deep attribution coverage: 78/98 requested mints.
## Executive Read
Best live-feasible policy by net was `skip_crowded_rank_3_25`: 111.198 SOL net on 298.103 SOL spent, 37.3% ROI.
Best live-feasible policy by ROI with at least 5 SOL deployed was `first_wave_conviction`: 79.1% ROI, 107.522 SOL net.
The first-pass result should be treated as a policy ranking tool, not a final execution guarantee. Latency, liquidity impact, and hidden off-chain signals still need live shadow testing.
## Data Quality Flags
1 campaigns include a source sell before the first observed buy in this sample. Those campaigns contain hidden inventory from before the captured window.
Those flagged campaigns account for 16.443 SOL of source net and 16.206 SOL of pre-buy sells. Fresh-only policies are included to avoid treating that as copyable edge.
## Policy Results
| policy | live | campaigns | buys | sells | spent | costs | net | roi | winRate | maxLoss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| skip_crowded_rank_3_25 | yes | 65 | 292 | 111 | 298.103 | 2.891 | 111.198 | 37.3% | 49.2% | -3.607 |
| avoid_negative_lead_actor | yes | 65 | 295 | 112 | 294.499 | 2.920 | 110.949 | 37.7% | 50.8% | -3.607 |
| conviction_buys_only | yes | 93 | 114 | 141 | 352.260 | 1.617 | 109.405 | 31.1% | 55.9% | -6.196 |
| first_wave_conviction | yes | 32 | 44 | 70 | 135.960 | 0.702 | 107.522 | 79.1% | 75.0% | -2.518 |
| fresh_first_wave_conviction | yes | 32 | 44 | 70 | 135.960 | 0.702 | 107.522 | 79.1% | 75.0% | -2.518 |
| first_wave_only | yes | 32 | 186 | 70 | 174.601 | 1.838 | 107.428 | 61.5% | 75.0% | -3.607 |
| fresh_campaigns_only | yes | 97 | 390 | 147 | 431.669 | 3.855 | 92.967 | 21.5% | 47.4% | -6.196 |
| entry_age_lte_10s | yes | 63 | 284 | 106 | 298.505 | 2.802 | 92.279 | 30.9% | 60.3% | -6.196 |
| copy_all | yes | 98 | 394 | 148 | 432.760 | 3.892 | 91.919 | 21.2% | 46.9% | -6.196 |
| historical_rapid_multiladder | benchmark | 7 | 96 | 30 | 60.008 | 0.918 | 70.253 | 117.1% | 85.7% | -3.607 |
| fast_second_buy_confirm | yes | 60 | 249 | 105 | 115.856 | 2.517 | 4.666 | 4.0% | 10.0% | -3.098 |
| positive_lead_actor_only | yes | 21 | 79 | 26 | 76.693 | 0.762 | 2.203 | 2.9% | 52.4% | -3.660 |
| fast_third_buy_confirm | yes | 48 | 174 | 90 | 64.396 | 1.842 | -0.744 | -1.2% | 10.4% | -2.136 |
## Lead Actor Sets
Positive lead actors selected: 5
Negative lead actors selected: 5
## Best Live Policy: Top Wins
| mint | net | roi | spent | sourceNet | rank | age | ladder |
| --- | --- | --- | --- | --- | --- | --- | --- |
| oHSWp1...pump | 28.150 | 274.0% | 10.274 | 29.870 | 1 | 0 | rapid multi-ladder |
| 2UPV97...o6Dw | 17.950 | 229.6% | 7.818 | 19.128 | 1 | 0 | rapid multi-ladder |
| HudM42...pump | 17.080 | 469.8% | 3.636 | 17.951 | 1 | 0 | quick add |
| EJn4RN...pump | 15.874 | 158.7% | 10.001 | 26.401 | 1 | 0 | rapid multi-ladder |
| DRRR82...pump | 8.453 | 189.8% | 4.455 | 9.062 | 123 | 25 | extended ladder |
| ErfTQc...pump | 6.250 | 107.4% | 5.820 | 6.893 | 1 | 0 | rapid multi-ladder |
| Fh3wcU...dSXL | 4.086 | 104.5% | 3.909 | 4.497 | 1 | 0 | quick add |
| 6VQGP2...pump | 3.884 | 82.1% | 4.728 | 4.354 | 1 | 0 | extended ladder |
| EnrsEd...pump | 3.334 | 32.5% | 10.274 | 4.152 | 1 | 0 | rapid multi-ladder |
| 2QqbUz...4TM9 | 3.134 | 101.4% | 3.090 | 3.448 | 1 | 0 | single entry |
## Best Live Policy: Worst Losses
| mint | net | roi | spent | sourceNet | rank | age | ladder |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gBWb8Y...ZtAP | -3.607 | -59.2% | 6.093 | -3.311 | 1 | 0 | rapid multi-ladder |
| 3d6LS2...pump | -2.558 | -20.2% | 12.633 | -3.039 | 1 | 0 | extended ladder |
| FPUMvk...pump | -2.518 | -40.7% | 6.180 | -3.113 | 1 | 0 | measured add |
| 7U9MY9...pump | -1.532 | -39.2% | 3.909 | -1.327 | 1 | 0 | quick add |
| Ar9uSW...birb | -1.506 | -48.7% | 3.090 | -1.366 | 1 | 0 | single entry |
| BAedLY...kfUi | -1.069 | -31.8% | 3.363 | -0.893 | 78 | 19 | measured add |
| Ejti7z...birb | -1.059 | -13.5% | 7.818 | -0.581 | 172 | 426 | extended ladder |
| 2KnytD...pump | -1.048 | -96.0% | 1.092 | 16.443 | 43 | 24 | quick add |
| EWKBc2...pump | -1.026 | -15.3% | 6.726 | -0.855 | 1 | 0 | quick add |
| Fej9eQ...pump | -0.971 | -40.1% | 2.425 | 0.562 | 26 | 3 | quick add |
## Interpretation Notes
1. If a policy wins by copying almost every trade, it is probably relying on the wallet's full edge and needs a latency/slippage stress test.
2. If a policy wins by filtering hard, it may be overfit; the next test is live shadow collection.
3. First-wave and entry-age filters are the current strongest signal candidates from attribution.
4. Fast second/third buy confirmation is important because it is live-detectable without needing private token discovery.
5. Historical rapid multi-ladder is a benchmark only; you cannot know that label at first entry.
## Next Investigation Step
Build a live shadow recorder that watches this wallet without trading and records what each policy would do in real time. The recorder should log detection latency, entry age, buyer rank when available, copied size, simulated fill penalty, and outcome after each sell.