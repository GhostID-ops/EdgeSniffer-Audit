# Path-Based Wallet Audit

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-06-17T13:26:50.569Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 6000
- swaps: 4622
- campaigns: 621
- measuredEntries: 3
- oldestTx: 2026-06-14T01:04:29.000Z
- newestTx: 2026-06-17T12:42:25.000Z
- horizonHours: 24
- solUsdUsed: 72.23

## Wallet PnL

- realizedPnlSol: +542.4040 SOL
- closedCampaigns: 620
- realizedWinRate: +55.6%
- avgSourceExit: +34.6%
- medianSourceExit: -1.9%

## Entry Path Stats

- hit30: 2/3
- hit50: 1/3
- hit100: 0/3
- hit200: 0/3
- medianMfe: +49.5%
- medianMae: -75.9%
- medianEntryMcApprox: $93,390
- medianTimeTo2x: -

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 3 | +34.6% | -1.9% | +33.3% | 6.26 | 0.55 |
| Trail arm45 give30 SL35 | 3 | -16.8% | -35.0% | +33.3% | 0.28 | -0.65 |
| TP30 SL25 | 3 | -25.0% | -25.0% | +0.0% | 0.00 | 0.00 |
| TP50 SL30 | 3 | -30.0% | -30.0% | +0.0% | 0.00 | 0.00 |
| TP100 SL35 | 3 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Trail arm65 give35 SL35 | 3 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Time exit 30m | 3 | -69.3% | -75.0% | +0.0% | 0.00 | -4.10 |
| Time exit 1h | 3 | -69.8% | -75.5% | +0.0% | 0.00 | -4.03 |
| Time exit 4h | 3 | -70.0% | -75.9% | +0.0% | 0.00 | -4.01 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 3
- medianSourceOrder: 11
- medianSourceDelaySec: 5
- medianEarlierBuys: 11
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BAGWORK (Aa9Ajz...BAGS) | 5.0320 | $52,897 | +94.3% | -75.9% | - | +123.5% | 11 | J8Uow4...hCfx |
| 10M (HH3Bt7...BAGS) | 3.0320 | $93,390 | +11.2% | -46.3% | - | -1.9% | 14 | J3x7iG...wk9H |
| INDEX (3V3h9a...BAGS) | 5.0320 | $226,880 | +49.5% | -87.8% | - | -17.9% | 0 | - |
