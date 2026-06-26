# Wallet Strategy Audit: 4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9

Generated: 2026-06-24

## Coverage

- Ledger bucket audit: 12,001 txs, 8,279 reconstructed fills, 1,946 closed measured campaigns.
- Window: 2026-06-03 16:56 UTC to 2026-06-24 19:25 UTC.
- Route mix: 1,635 pump, 21 bags, 290 other.
- Candle-enriched quant audit: 5,000 txs, 842 closed campaigns, 92 candle-measured entries. Moralis free-plan limit was hit, so use candle/MFE results directionally only.

## Recommendation

Best default posture: copy him, but prefer entries below about $20k inferred MC. His edge is mostly early/low-MC and very fast source exits.

Suggested live strategy:

- Buy: copy percentage is reasonable because his source sizing is consistent, mostly 2-5 SOL first buys.
- Entry filter: strongest broad range is under $20k source-fill/inferred MC.
- Hard avoid: $50k-$100k entries. This bucket is small but clearly bad in the high-coverage ledger sample.
- Exits: keep hybrid exit, 50% at +30%, then let the rest follow source exit. Candle slice strongly favored this over pure wallet-sell, pure trailing stop, or fixed time exits.
- Do not use long time exits on this wallet. Median hold is under 1 minute, and 30m/1h/4h/24h time exits were bad in the candle slice.

## Key Buckets

All routes by entry MC:

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median Hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 723 | 58.8% | 18.6% | 7.0% | 4.19 | +349.06 SOL | 0.45m |
| $5k-10k | 773 | 52.3% | 13.8% | 1.3% | 2.59 | +378.40 SOL | 0.80m |
| $10k-20k | 357 | 49.0% | 15.8% | -0.6% | 2.67 | +208.22 SOL | 1.17m |
| $20k-50k | 73 | 43.8% | 28.7% | -5.0% | 3.02 | +12.72 SOL | 1.50m |
| $50k-100k | 12 | 25.0% | -9.8% | -6.2% | 0.31 | -6.36 SOL | 0.37m |
| $100k+ | 8 | 25.0% | 47.0% | -13.4% | 4.63 | +8.48 SOL | 0.13m |

Pump route by entry MC:

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median Hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 621 | 59.1% | 18.8% | 7.2% | 4.24 | +293.06 SOL | 0.45m |
| $5k-10k | 659 | 51.7% | 14.3% | 1.1% | 2.64 | +338.38 SOL | 0.80m |
| $10k-20k | 285 | 51.2% | 18.9% | 1.3% | 3.13 | +202.69 SOL | 1.35m |
| $20k-50k | 65 | 40.0% | 28.6% | -7.2% | 2.96 | +0.34 SOL | 1.38m |
| $50k-100k | 4 | 25.0% | -23.5% | -13.5% | 0.06 | -4.67 SOL | 3.02m |
| $100k+ | 1 | 0.0% | -20.9% | -20.9% | 0.00 | -1.05 SOL | 0.50m |

Strategy cuts from ledger rows:

| Strategy | n | Win | Avg ROI | Median ROI | Source PnL | 25% Copy PnL Approx |
|---|---:|---:|---:|---:|---:|---:|
| All trades | 1,946 | 53.5% | 16.5% | 2.4% | +950.54 SOL | +237.63 SOL |
| All under $20k MC | 1,853 | 54.2% | 16.1% | 2.9% | +935.69 SOL | +233.92 SOL |
| Pump under $20k MC | 1,565 | 54.6% | 16.9% | 3.0% | +834.12 SOL | +208.53 SOL |
| Pump under $10k MC | 1,280 | 55.3% | 16.5% | 3.2% | +631.44 SOL | +157.86 SOL |
| Skip $50k-$100k only | 1,934 | 53.7% | 16.7% | 2.5% | +956.89 SOL | +239.22 SOL |

## Exit Strategy Evidence

Candle-enriched 92-entry slice:

| Exit Strategy | Win | Avg ROI | Median ROI | PF | Max DD |
|---|---:|---:|---:|---:|---:|
| 50% at +30%, 50% source exit | 67.4% | 18.4% | 12.0% | 5.03 | -68.1 pts |
| Source exit only | 55.4% | 15.6% | 5.5% | 3.06 | -151.3 pts |
| 20% trailing stop | 27.2% | -0.7% | -9.9% | 0.93 | -360.7 pts |
| TP30 / SL20 | 17.4% | -8.8% | -20.0% | 0.36 | -904.2 pts |

Conclusion: keep the hybrid model. Do not switch this wallet to pure trailing or fixed stop/TP based on the current sample.

