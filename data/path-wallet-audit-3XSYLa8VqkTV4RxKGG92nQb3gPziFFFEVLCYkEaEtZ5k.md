# Path-Based Wallet Audit

Wallet: `3XSYLa8VqkTV4RxKGG92nQb3gPziFFFEVLCYkEaEtZ5k`
Generated: 2026-06-24T04:51:03.989Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 5000
- swaps: 4888
- campaigns: 297
- measuredEntries: 80
- oldestTx: 2026-06-20T23:35:08.000Z
- newestTx: 2026-06-24T04:39:56.000Z
- horizonHours: 24
- solUsdUsed: 69.27

## Wallet PnL

- realizedPnlSol: +11.8545 SOL
- closedCampaigns: 297
- realizedWinRate: +49.8%
- avgSourceExit: -0.7%
- medianSourceExit: +1.4%

## Entry Path Stats

- hit30: 53/80
- hit50: 38/80
- hit100: 21/80
- hit200: 11/80
- medianMfe: +48.7%
- medianMae: -86.9%
- medianEntryMcApprox: $31,511
- medianTimeTo2x: 35m

## Strategy Backtest

Returns are per-trade ROI. Cumulative DD is the worst drop in the cumulative return curve, expressed in percentage points, not bankroll percent.

| Strategy | Trades | Avg ROI | Median ROI | Win Rate | Profit Factor | Cumulative ROI | Cumulative DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 80 | -0.7% | +1.4% | +55.0% | 0.80 | -53.5pp | -151.6pp |
| TP30 SL25 | 80 | -1.4% | -25.0% | +42.5% | 0.90 | -115.4pp | -246.9pp |
| TP50 SL30 | 80 | -4.8% | -30.0% | +31.3% | 0.76 | -386.5pp | -581.4pp |
| Time exit 4h | 80 | -5.4% | -67.2% | +18.8% | 0.90 | -428.5pp | -3184.5pp |
| Trail arm45 give30 SL35 | 80 | -5.4% | -35.0% | +36.3% | 0.75 | -432.3pp | -580.9pp |
| Trail arm65 give35 SL35 | 80 | -9.7% | -35.0% | +26.3% | 0.61 | -773.1pp | -901.8pp |
| TP100 SL35 | 80 | -13.3% | -35.0% | +16.3% | 0.53 | -1061.5pp | -1392.1pp |
| Time exit 1h | 80 | -21.5% | -32.9% | +22.5% | 0.47 | -1720.8pp | -1783.9pp |
| Time exit 30m | 80 | -23.2% | -32.6% | +27.5% | 0.36 | -1857.4pp | -1929.1pp |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 20
- medianSourceOrder: 106
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| WENDEEZ (8BAZo7...pump) | 0.5890 | $14,433 | +60.7% | -68.6% | - | +11.8% | 0 | - |
| WEN (F8sTbM...pump) | 0.8563 | $23,410 | +14.4% | -77.0% | - | +8.9% | 0 | - |
| EC43 (8Jdjmc...pump) | 0.8453 | $98,849 | +0.0% | -2.8% | - | +4.7% | 0 | - |
| STARCAT (3PETFW...pump) | 0.7443 | $25,643 | +10.6% | -89.5% | - | +4.9% | 0 | - |
| ANGIE (3p8Y7B...pump) | 0.7657 | $21,204 | +80.4% | -56.7% | - | -9.0% | 0 | - |
| STARSHIT (FwFuSt...pump) | 0.8328 | $29,627 | +18.5% | -81.7% | - | -2.3% | 0 | - |
| WEN (66pQgf...pump) | 0.8133 | $169,261 | +3773.6% | -16.2% | 19m | +3.1% | 0 | - |
| hiper (CxQBeP...pump) | 0.8581 | $32,035 | +52.2% | -38.4% | - | -10.8% | 0 | - |
| Gronk (HkNZ5L...pump) | 0.7949 | $37,901 | +164.4% | -79.0% | 1m | -17.1% | 0 | - |
| CHICKWIF (AfvfUb...pump) | 0.7653 | $30,988 | +176.4% | -72.5% | 19m | -7.8% | 0 | - |
| $CWIF (FipQi1...qW2y) | 1.0132 | $37,371 | +41.0% | -73.9% | - | -11.4% | 0 | - |
| LOCOS (HPXNQS...pump) | 0.7304 | $21,445 | +37.4% | -90.1% | - | +12.0% | 0 | - |
| 滑る猫 (3oX9em...pump) | 2.7337 | $334,748 | +34.3% | -11.0% | - | +4.0% | 0 | - |
| QRCOIN (qrFncV...TbV6) | 0.7026 | $20,558 | +0.0% | -93.7% | - | +0.2% | 0 | - |
| FC (BKqLgy...pump) | 0.9163 | $33,509 | +20.0% | -93.9% | - | -35.9% | 0 | - |
| GRACIE (7jVXjq...pump) | 0.3596 | $41,265 | +366.0% | -25.8% | 58m | +1.9% | 0 | - |
| VINTEDGATE (2bthha...pump) | 0.7983 | $26,529 | +18.5% | -50.4% | - | -23.9% | 0 | - |
| Onboard (DQjxb5...pump) | 0.8324 | $40,051 | +127.3% | -89.0% | 6m | +2.8% | 0 | - |
| PROV (9bNNfs...pump) | 0.8411 | $154,216 | +115.9% | -31.0% | 46m | +2.7% | 0 | - |
| NPA (3aMima...pump) | 0.7379 | $25,757 | +7.2% | -94.3% | - | +5.1% | 0 | - |
| Glonzo (rnU1uQ...pump) | 0.7254 | $16,060 | +129.3% | -91.0% | 9m | +4.8% | - | - |
| ABDULLAH (5yJgTd...SsvU) | 0.7729 | $22,048 | +0.0% | -94.1% | - | +3.6% | - | - |
| STARMIND (5qjZLd...pump) | 0.7717 | $51,643 | +28.3% | -65.1% | - | -1.5% | - | - |
| STARMIND (45Kt1m...pump) | 0.7714 | $24,630 | +265.7% | -87.1% | 221m | -5.5% | - | - |
| Jonah (8xt6zz...pump) | 1.2792 | $81,124 | +113.9% | -38.8% | 339m | +1.5% | - | - |
| world (6ggdf1...pump) | 0.7849 | $27,837 | +88.2% | -53.5% | - | -36.7% | - | - |
| RIGBY (GTiNWH...pump) | 0.4265 | $27,119 | +98.5% | -37.6% | - | -3.6% | - | - |
| runspace (3pWjLB...pump) | 0.3172 | $12,550 | +0.0% | -12.0% | - | +21.1% | - | - |
| CSOL (unJpW1...pump) | 0.7290 | $22,715 | +18.4% | -89.3% | - | +2.0% | - | - |
| LAZARUS (7TiedM...pump) | 0.7550 | $27,869 | +53.3% | -93.9% | - | -0.2% | - | - |
| JAMESON (E8UXwq...pump) | 0.6981 | $74,754 | +8.1% | -26.8% | - | -0.4% | - | - |
| pumpbull (93cYWh...CAXR) | 0.8063 | $41,858 | +51.1% | -88.8% | - | -9.2% | - | - |
| Claude (E8L6K5...xUyK) | 0.6900 | $33,794 | +54.2% | -94.7% | - | -0.6% | - | - |
| USCON (91mttN...pump) | 0.6831 | $32,744 | +31.5% | -93.5% | - | +2.1% | - | - |
| VAULTIE (GCp29o...pump) | 0.3103 | $24,475 | +51.0% | -96.3% | - | -1.1% | - | - |
| RONALDO (89Vv9k...pump) | 0.7463 | $33,538 | +48.0% | -92.1% | - | -0.4% | - | - |
| CITTA (9cjcMh...pump) | 0.8078 | $24,640 | +48.5% | -73.7% | - | +1.4% | - | - |
| Ascend (HKHBrJ...pump) | 0.9286 | $54,651 | +695.8% | -67.6% | 63m | +1.0% | - | - |
| Jimoto (2WJCGz...pump) | 0.7029 | $23,831 | +32.0% | -62.5% | - | +11.1% | - | - |
| Billy (D9urWM...pump) | 0.8631 | $43,255 | +0.0% | -85.9% | - | +1.8% | - | - |
