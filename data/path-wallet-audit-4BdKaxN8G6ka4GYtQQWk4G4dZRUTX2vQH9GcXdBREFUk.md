# Path-Based Wallet Audit

Wallet: `4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk`
Generated: 2026-06-17T15:15:59.280Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 3000
- swaps: 812
- campaigns: 135
- measuredEntries: 3
- oldestTx: 2026-05-25T20:53:31.000Z
- newestTx: 2026-06-17T14:49:52.000Z
- horizonHours: 24
- solUsdUsed: 72.55

## Wallet PnL

- realizedPnlSol: +450.6793 SOL
- closedCampaigns: 133
- realizedWinRate: +75.2%
- avgSourceExit: +43.2%
- medianSourceExit: +26.0%

## Entry Path Stats

- hit30: 1/3
- hit50: 0/3
- hit100: 0/3
- hit200: 0/3
- medianMfe: +16.6%
- medianMae: -87.4%
- medianEntryMcApprox: $16,456
- medianTimeTo2x: -

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 3 | +43.2% | +26.0% | +66.7% | 6.70 | 0.70 |
| TP30 SL25 | 3 | -25.0% | -25.0% | +0.0% | 0.00 | 0.00 |
| TP50 SL30 | 3 | -30.0% | -30.0% | +0.0% | 0.00 | 0.00 |
| TP100 SL35 | 3 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Trail arm45 give30 SL35 | 3 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Trail arm65 give35 SL35 | 3 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Time exit 1h | 3 | -69.1% | -87.0% | +0.0% | 0.00 | -2.48 |
| Time exit 30m | 3 | -71.9% | -82.2% | +0.0% | 0.00 | -3.98 |
| Time exit 4h | 3 | -77.1% | -87.0% | +0.0% | 0.00 | -4.04 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 3
- medianSourceOrder: -
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| GOATS (2xoiUV...pump) | 1.2671 | $9,622 | +10.9% | -87.4% | - | +126.3% | 0 | - |
| GAY (H5Zijm...pump) | 7.0881 | $24,321 | +16.6% | -83.8% | - | +26.0% | 0 | - |
| YOGAFY (63xn5W...pump) | 3.0401 | $16,456 | +36.5% | -94.6% | - | -22.7% | 0 | - |
