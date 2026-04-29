# Strategy Backtest Report
Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-04-28T00:17:31.510Z
## Assumptions
- Backtest uses the source wallet's observed fills from `source-wallet-analysis-summary.json`.
- Copied buys receive the same token/SOL rate, then apply 150 bps entry penalty.
- Copied sells mirror the source wallet's sell fraction, then apply 150 bps exit penalty.
- Transaction costs are 0.006 SOL per copied buy and 0.004 SOL per copied sell.
- Leftover tokens are marked at zero. This is intentionally conservative for unresolved bags.
- Source-size multiplier is 1x.
- Deep attribution coverage: 78/98 requested mints.
## Executive Read
Best live-feasible policy by net was `avoid_negative_lead_actor`: 135.033 SOL net on 333.120 SOL spent, 40.5% ROI.
Best live-feasible policy by ROI with at least 5 SOL deployed was `first_wave_conviction`: 73.9% ROI, 125.000 SOL net.
The first-pass result should be treated as a policy ranking tool, not a final execution guarantee. Latency, liquidity impact, and hidden off-chain signals still need live shadow testing.
## Data Quality Flags
1 campaigns include a source sell before the first observed buy in this sample. Those campaigns contain hidden inventory from before the captured window.
Those flagged campaigns account for 16.443 SOL of source net and 16.206 SOL of pre-buy sells. Fresh-only policies are included to avoid treating that as copyable edge.
## Policy Results
| policy | live | campaigns | buys | sells | spent | costs | net | roi | winRate | maxLoss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| avoid_negative_lead_actor | yes | 65 | 295 | 112 | 333.120 | 2.162 | 135.033 | 40.5% | 60.0% | -3.622 |
| skip_crowded_rank_3_25 | yes | 65 | 292 | 111 | 334.506 | 2.141 | 134.777 | 40.3% | 58.5% | -3.622 |
| conviction_buys_only | yes | 93 | 114 | 141 | 398.658 | 1.177 | 132.286 | 33.2% | 60.2% | -6.135 |
| first_wave_only | yes | 32 | 186 | 70 | 207.308 | 1.361 | 126.365 | 61.0% | 75.0% | -3.622 |
| first_wave_conviction | yes | 32 | 44 | 70 | 169.229 | 0.509 | 125.000 | 73.9% | 75.0% | -3.565 |
| fresh_first_wave_conviction | yes | 32 | 44 | 70 | 169.229 | 0.509 | 125.000 | 73.9% | 75.0% | -3.565 |
| fresh_campaigns_only | yes | 97 | 390 | 147 | 476.911 | 2.855 | 118.574 | 24.9% | 53.6% | -6.663 |
| copy_all | yes | 98 | 394 | 148 | 477.986 | 2.882 | 117.553 | 24.6% | 53.1% | -6.663 |
| entry_age_lte_10s | yes | 63 | 284 | 106 | 338.092 | 2.075 | 113.380 | 33.5% | 60.3% | -6.663 |
| historical_rapid_multiladder | benchmark | 7 | 96 | 30 | 65.422 | 0.681 | 84.243 | 128.8% | 85.7% | -3.459 |
| fast_second_buy_confirm | yes | 60 | 249 | 105 | 127.484 | 1.862 | 13.561 | 10.6% | 15.0% | -3.066 |
| fast_third_buy_confirm | yes | 48 | 174 | 90 | 67.610 | 1.359 | 7.552 | 11.2% | 10.4% | -2.065 |
| positive_lead_actor_only | yes | 21 | 79 | 26 | 83.877 | 0.565 | 2.176 | 2.6% | 52.4% | -6.663 |
## Lead Actor Sets
Positive lead actors selected: 5
Negative lead actors selected: 5
## Best Live Policy: Top Wins
| mint | net | roi | spent | sourceNet | rank | age | ladder |
| --- | --- | --- | --- | --- | --- | --- | --- |
| oHSWp1...pump | 29.082 | 286.3% | 10.157 | 29.870 | 1 | 0 | rapid multi-ladder |
| EJn4RN...pump | 25.539 | 183.1% | 13.948 | 26.401 | 1 | 0 | rapid multi-ladder |
| 2UPV97...o6Dw | 18.584 | 240.2% | 7.736 | 19.128 | 1 | 0 | rapid multi-ladder |
| HudM42...pump | 17.560 | 487.7% | 3.600 | 17.951 | 1 | 0 | quick add |
| DRRR82...pump | 8.783 | 199.3% | 4.407 | 9.062 | 123 | 25 | extended ladder |
| ErfTQc...pump | 6.585 | 114.5% | 5.752 | 6.893 | 1 | 0 | rapid multi-ladder |
| Hvd3iu...pump | 4.695 | 83.4% | 5.630 | 4.942 | 1 | 0 | quick add |
| Fh3wcU...dSXL | 4.305 | 111.2% | 3.870 | 4.497 | 1 | 0 | quick add |
| 7FPotR...VQbr | 4.153 | 35.6% | 11.649 | 4.594 | 1 | 0 | rapid multi-ladder |
| 6VQGP2...pump | 4.130 | 88.3% | 4.678 | 4.354 | 1 | 0 | extended ladder |
## Best Live Policy: Worst Losses
| mint | net | roi | spent | sourceNet | rank | age | ladder |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3d6LS2...pump | -3.622 | -17.6% | 20.632 | -3.039 | 1 | 0 | extended ladder |
| gBWb8Y...ZtAP | -3.459 | -57.4% | 6.021 | -3.311 | 1 | 0 | rapid multi-ladder |
| FPUMvk...pump | -3.312 | -40.6% | 8.153 | -3.113 | 1 | 0 | measured add |
| BSQtQy...pump | -2.267 | -31.3% | 7.245 | -2.067 | 9 | 2 | extended ladder |
| 4E1VWF...pump | -2.185 | -60.7% | 3.600 | -2.104 | 24 | 2 | quick add |
| Ar9uSW...birb | -1.441 | -47.0% | 3.062 | -1.366 | 1 | 0 | single entry |
| 7U9MY9...pump | -1.433 | -37.0% | 3.869 | -1.327 | 1 | 0 | quick add |
| EWKBc2...pump | -1.171 | -10.9% | 10.721 | -0.855 | 1 | 0 | quick add |
| BgTCej...UEUR | -1.133 | -18.4% | 6.168 | -0.957 | 1 | 0 | extended ladder |
| 2KnytD...pump | -1.022 | -94.9% | 1.076 | 16.443 | 43 | 24 | quick add |
## Interpretation Notes
1. If a policy wins by copying almost every trade, it is probably relying on the wallet's full edge and needs a latency/slippage stress test.
2. If a policy wins by filtering hard, it may be overfit; the next test is live shadow collection.
3. First-wave and entry-age filters are the current strongest signal candidates from attribution.
4. Fast second/third buy confirmation is important because it is live-detectable without needing private token discovery.
5. Historical rapid multi-ladder is a benchmark only; you cannot know that label at first entry.
## Next Investigation Step
Build a live shadow recorder that watches this wallet without trading and records what each policy would do in real time. The recorder should log detection latency, entry age, buyer rank when available, copied size, simulated fill penalty, and outcome after each sell.