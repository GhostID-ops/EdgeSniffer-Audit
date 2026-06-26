# Wallet Ledger-First Entry Market-Cap Buckets

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-06-26T18:38:20.999Z

## Coverage

- transactions: 12001
- reconstructedFills: 8852
- campaigns: 1260
- closedMeasuredCampaigns: 1259
- oldestTransaction: 2026-06-14T01:35:17.000Z
- newestTransaction: 2026-06-22T18:39:43.000Z
- pumpRows: 984
- bagsRows: 56
- otherRows: 219
- SOL/USD source: CoinGecko range cache
- Entry MC: source first-fill price × 1,000,000,000 assumed supply
- Source ROI: campaign proceeds minus proportional sold cost basis; no candle or surviving-pool requirement

## All Routes

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 758 | +57.4% | +19.4% | +4.5% | 4.16 | +775.55 SOL | 0.45m |
| $5k-10k | 245 | +48.6% | +10.7% | -1.2% | 1.93 | +252.19 SOL | 0.82m |
| $10k-20k | 117 | +40.2% | +0.7% | -10.3% | 1.04 | +18.69 SOL | 1.98m |
| $20k-50k | 64 | +43.8% | +5.9% | -2.8% | 1.39 | +49.65 SOL | 2.63m |
| $50k-100k | 27 | +44.4% | +14.5% | -1.4% | 2.36 | +32.62 SOL | 0.60m |
| $100k+ | 48 | +22.9% | -5.8% | -17.8% | 0.72 | -120.16 SOL | 0.43m |
## PUMP Route

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 616 | +58.9% | +19.0% | +5.6% | 4.23 | +574.16 SOL | 0.45m |
| $5k-10k | 204 | +46.6% | +7.5% | -4.1% | 1.63 | +190.28 SOL | 0.83m |
| $10k-20k | 91 | +44.0% | +4.5% | -8.6% | 1.30 | +40.19 SOL | 1.98m |
| $20k-50k | 50 | +42.0% | +4.6% | -5.6% | 1.28 | +22.12 SOL | 2.63m |
| $50k-100k | 11 | +36.4% | +2.8% | -1.4% | 1.16 | +15.75 SOL | 3.40m |
| $100k+ | 12 | +16.7% | -17.3% | -8.9% | 0.07 | -58.18 SOL | 3.63m |
## BAGS Route

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 6 | +50.0% | +3.9% | +4.0% | 1.52 | +0.07 SOL | 0.05m |
| $5k-10k | 2 | +50.0% | -2.6% | -2.6% | 0.69 | -0.51 SOL | 0.62m |
| $10k-20k | 0 | +0.0% | - | - | 0.00 | +0.00 SOL | -m |
| $20k-50k | 1 | +100.0% | +123.5% | +123.5% | - | +12.43 SOL | 0.22m |
| $50k-100k | 14 | +50.0% | +9.6% | -0.8% | 2.57 | +5.49 SOL | 0.23m |
| $100k+ | 33 | +27.3% | -0.6% | -18.3% | 0.97 | -38.41 SOL | 0.38m |
## OTHER Route

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 136 | +50.7% | +21.8% | +0.2% | 4.00 | +201.32 SOL | 0.49m |
| $5k-10k | 39 | +59.0% | +28.6% | +7.7% | 3.89 | +62.41 SOL | 0.63m |
| $10k-20k | 26 | +26.9% | -12.9% | -13.1% | 0.35 | -21.50 SOL | 1.81m |
| $20k-50k | 13 | +46.2% | +2.0% | -2.4% | 1.16 | +15.10 SOL | 4.20m |
| $50k-100k | 2 | +50.0% | +112.7% | +112.7% | 13.77 | +11.38 SOL | 3.51m |
| $100k+ | 3 | +0.0% | -16.7% | -18.7% | 0.00 | -23.57 SOL | 2.58m |

## Interpretation Notes

- This report is the high-coverage source-exit bucket study. MFE/MAE and target-hit statistics remain in the smaller candle-enriched audit.
- Route suffixes are used only for coarse segmentation (`pump`, `BAGS`, other).
- Source-fill MC includes the source wallet's realized fill and therefore price impact; it is intentionally aligned with the live inferred-MC gate.
