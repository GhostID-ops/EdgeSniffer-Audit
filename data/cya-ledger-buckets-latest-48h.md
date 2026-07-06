# Wallet Ledger-First Entry Market-Cap Buckets

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-07-06T21:12:37.208Z

## Coverage

- transactions: 2500
- reconstructedFills: 1863
- campaigns: 283
- closedMeasuredCampaigns: 281
- oldestTransaction: 2026-07-05T23:16:24.000Z
- newestTransaction: 2026-07-06T21:11:54.000Z
- pumpRows: 221
- bagsRows: 3
- otherRows: 57
- SOL/USD source: CoinGecko historical range
- Entry MC: source first-fill price × 1,000,000,000 assumed supply
- Source ROI: campaign proceeds minus proportional sold cost basis; no candle or surviving-pool requirement

## All Routes

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 137 | +62.8% | +26.1% | +7.0% | 6.75 | +189.67 SOL | 0.32m |
| $5k-10k | 64 | +53.1% | +13.4% | +3.9% | 2.29 | +90.69 SOL | 0.32m |
| $10k-20k | 29 | +44.8% | +13.1% | -6.0% | 1.83 | +68.21 SOL | 0.73m |
| $20k-50k | 24 | +58.3% | +52.5% | +10.2% | 6.90 | +36.78 SOL | 0.49m |
| $50k-100k | 11 | +27.3% | +8.6% | -7.0% | 1.63 | +4.09 SOL | 0.82m |
| $100k+ | 16 | +18.8% | -12.6% | -15.8% | 0.26 | -35.20 SOL | 0.84m |
## PUMP Route

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 108 | +58.3% | +20.7% | +5.9% | 5.05 | +107.38 SOL | 0.38m |
| $5k-10k | 50 | +48.0% | +12.0% | -2.6% | 2.07 | +82.78 SOL | 0.32m |
| $10k-20k | 22 | +54.5% | +23.7% | +0.5% | 2.73 | +77.63 SOL | 0.85m |
| $20k-50k | 18 | +50.0% | +66.2% | +5.9% | 7.41 | +35.68 SOL | 0.58m |
| $50k-100k | 10 | +20.0% | -4.3% | -7.4% | 0.71 | -3.91 SOL | 0.79m |
| $100k+ | 13 | +23.1% | -12.1% | -17.2% | 0.31 | -27.82 SOL | 1.12m |
## BAGS Route

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 0 | +0.0% | - | - | 0.00 | +0.00 SOL | -m |
| $5k-10k | 0 | +0.0% | - | - | 0.00 | +0.00 SOL | -m |
| $10k-20k | 0 | +0.0% | - | - | 0.00 | +0.00 SOL | -m |
| $20k-50k | 2 | +100.0% | +21.4% | +21.4% | - | +1.36 SOL | 0.28m |
| $50k-100k | 1 | +100.0% | +137.6% | +137.6% | - | +8.00 SOL | 1.75m |
| $100k+ | 0 | +0.0% | - | - | 0.00 | +0.00 SOL | -m |
## OTHER Route

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 29 | +79.3% | +46.2% | +14.9% | 20.39 | +82.29 SOL | 0.20m |
| $5k-10k | 14 | +71.4% | +18.5% | +7.6% | 3.44 | +7.91 SOL | 0.35m |
| $10k-20k | 7 | +14.3% | -20.1% | -19.2% | 0.12 | -9.42 SOL | 0.47m |
| $20k-50k | 4 | +75.0% | +6.4% | +13.8% | 1.93 | -0.27 SOL | 0.26m |
| $50k-100k | 0 | +0.0% | - | - | 0.00 | +0.00 SOL | -m |
| $100k+ | 3 | +0.0% | -14.7% | -14.1% | 0.00 | -7.38 SOL | 0.32m |

## Interpretation Notes

- This report is the high-coverage source-exit bucket study. MFE/MAE and target-hit statistics remain in the smaller candle-enriched audit.
- Route suffixes are used only for coarse segmentation (`pump`, `BAGS`, other).
- Source-fill MC includes the source wallet's realized fill and therefore price impact; it is intentionally aligned with the live inferred-MC gate.
