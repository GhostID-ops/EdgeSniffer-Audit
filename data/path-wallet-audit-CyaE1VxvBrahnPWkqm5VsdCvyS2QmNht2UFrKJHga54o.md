# Path-Based Wallet Audit

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-06-07T02:45:30.471Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 2000
- swaps: 1165
- campaigns: 167
- measuredEntries: 2
- oldestTx: 2026-06-05T13:40:12.000Z
- newestTx: 2026-06-07T01:49:50.000Z
- horizonHours: 24
- solUsdUsed: 63.22

## Wallet PnL

- realizedPnlSol: +181.3633 SOL
- closedCampaigns: 166
- realizedWinRate: +63.9%
- avgSourceExit: +32.4%
- medianSourceExit: +32.4%

## Entry Path Stats

- hit30: 0/2
- hit50: 0/2
- hit100: 0/2
- hit200: 0/2
- medianMfe: +2.3%
- medianMae: -68.4%
- medianEntryMcApprox: $2,968
- medianTimeTo2x: -

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 2 | +32.4% | +32.4% | +100.0% | Infinity | 2.17 |
| TP30 SL25 | 2 | -25.0% | -25.0% | +0.0% | 0.00 | 0.00 |
| TP50 SL30 | 2 | -30.0% | -30.0% | +0.0% | 0.00 | 0.00 |
| TP100 SL35 | 2 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Trail arm45 give30 SL35 | 2 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Trail arm65 give35 SL35 | 2 | -35.0% | -35.0% | +0.0% | 0.00 | 0.00 |
| Time exit 1h | 2 | -67.7% | -67.7% | +0.0% | 0.00 | -26.11 |
| Time exit 30m | 2 | -67.9% | -67.9% | +0.0% | 0.00 | -24.38 |
| Time exit 4h | 2 | -68.1% | -68.1% | +0.0% | 0.00 | -30.96 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 2
- medianSourceOrder: -
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Chance (75La9W...pump) | 11.0097 | $2,440 | +1.9% | -65.9% | - | +47.3% | 0 | - |
| Tucker (Bqxhpj...pump) | 3.0073 | $3,496 | +2.8% | -70.9% | - | +17.4% | 0 | - |
