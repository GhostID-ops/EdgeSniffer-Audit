# Path-Based Wallet Audit

Wallet: `4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk`
Generated: 2026-06-07T01:34:06.837Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 1000
- swaps: 625
- campaigns: 113
- measuredEntries: 18
- oldestTx: 2026-05-21T18:10:11.000Z
- newestTx: 2026-06-01T13:25:11.000Z
- horizonHours: 24
- solUsdUsed: 63.59

## Wallet PnL

- realizedPnlSol: +383.8782 SOL
- closedCampaigns: 112
- realizedWinRate: +70.5%
- avgSourceExit: +157.1%
- medianSourceExit: +76.3%

## Entry Path Stats

- hit30: 15/18
- hit50: 10/18
- hit100: 4/18
- hit200: 3/18
- medianMfe: +72.7%
- medianMae: -81.0%
- medianEntryMcApprox: $23,452
- medianTimeTo2x: 81m

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 18 | +157.1% | +76.3% | +77.8% | 25.65 | 0.69 |
| Trail arm45 give30 SL35 | 18 | -2.3% | -35.0% | +33.3% | 0.90 | -0.05 |
| Trail arm65 give35 SL35 | 18 | -2.7% | -35.0% | +33.3% | 0.89 | -0.06 |
| TP50 SL30 | 18 | -3.3% | -30.0% | +33.3% | 0.83 | -0.09 |
| TP30 SL25 | 18 | -6.7% | -25.0% | +33.3% | 0.60 | -0.26 |
| Time exit 30m | 18 | -10.6% | -18.9% | +16.7% | 0.59 | -0.19 |
| TP100 SL35 | 18 | -12.5% | -35.0% | +16.7% | 0.57 | -0.25 |
| Time exit 1h | 18 | -24.1% | -24.3% | +22.2% | 0.35 | -0.38 |
| Time exit 4h | 18 | -44.3% | -61.7% | +11.1% | 0.11 | -0.93 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 18
- medianSourceOrder: -
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| WORLDCUP (E2sHHw...pump) | 7.0801 | $143,854 | +11.8% | -81.6% | - | -35.1% | 0 | - |
| HERO (Ey8Ncx...pump) | 1.1877 | $4,359 | +79.4% | -45.7% | - | +31.6% | 0 | - |
| Odysseus
 (GmshYg...pump) | 10.1101 | $49,540 | +99.0% | -63.5% | - | +227.9% | 0 | - |
| RUMP (5QiTbu...pump) | 2.0271 | $5,619 | +46.6% | -94.9% | - | +129.4% | 0 | - |
| 8 (vhBZqc...pump) | 7.0801 | $27,106 | +126.0% | -79.9% | 138m | +210.3% | 0 | - |
| Cali (6qn3n3...pump) | 7.0801 | $25,943 | +35.8% | -94.0% | - | +275.6% | 0 | - |
| wBULL (gAhvUS...pump) | 7.0801 | $28,656 | +434.3% | -14.9% | 751m | +734.2% | 0 | - |
| Harambe (85bT19...pump) | 7.0771 | $166,536 | +79.5% | -80.3% | - | -41.1% | 0 | - |
| KRUG (3c1LSB...pump) | 5.0571 | $14,990 | +66.0% | -84.1% | - | +101.6% | 0 | - |
| SARAH (Bat33Y...pump) | 2.0301 | $6,404 | +220.4% | -91.8% | 14m | +763.3% | 0 | - |
| BUFFDON (CbyQSD...pump) | 15.1601 | $333,041 | +80.0% | -50.6% | - | -10.7% | 0 | - |
| OPENCOIN (EAWbED...pump) | 1.1491 | $7,898 | +43.7% | -94.8% | - | +51.0% | 0 | - |
| LUCKYSPINS (CB3kWj...pump) | 1.1491 | $4,314 | +13.2% | -86.2% | - | +39.8% | 0 | - |
| Gus (EvTxRE...pump) | 1.1877 | $2,878 | +37.6% | -84.0% | - | +22.1% | 0 | - |
| SQUISH (FmEYSX...pump) | 2.0271 | $20,960 | +29.2% | -70.5% | - | -27.8% | 0 | - |
| usug (GcBijr...pump) | 3.0401 | $9,339 | +90.7% | -93.4% | - | +40.5% | 0 | - |
| GOONC (ENfpbQ...DuAb) | 20.2100 | $119,806 | +35.4% | -78.4% | - | +125.3% | 0 | - |
| Luce (8QWQmA...pump) | 7.0801 | $45,865 | +460.5% | -15.3% | 24m | +189.7% | 0 | - |
