# Path-Based Wallet Audit

Wallet: `omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq`
Generated: 2026-06-06T23:50:56.687Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 6000
- swaps: 5951
- campaigns: 388
- measuredEntries: 119
- oldestTx: 2026-06-05T14:51:30.000Z
- newestTx: 2026-06-06T23:40:55.000Z
- horizonHours: 24
- solUsdUsed: 62.15

## Wallet PnL

- realizedPnlSol: +41.4188 SOL
- closedCampaigns: 386
- realizedWinRate: +47.2%
- avgSourceExit: -0.6%
- medianSourceExit: +0.1%

## Entry Path Stats

- hit30: 41/119
- hit50: 26/119
- hit100: 17/119
- hit200: 4/119
- medianMfe: +17.5%
- medianMae: -52.6%
- medianEntryMcApprox: $5,732
- medianTimeTo2x: 47m

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 119 | -0.6% | +0.1% | +51.3% | 0.86 | -0.05 |
| TP30 SL25 | 119 | -9.5% | -25.0% | +26.1% | 0.44 | -0.40 |
| TP50 SL30 | 119 | -13.0% | -30.0% | +21.0% | 0.40 | -0.44 |
| Trail arm65 give35 SL35 | 119 | -14.8% | -35.0% | +20.2% | 0.41 | -0.39 |
| TP100 SL35 | 119 | -14.9% | -35.0% | +16.8% | 0.43 | -0.36 |
| Trail arm45 give30 SL35 | 119 | -15.1% | -35.0% | +22.7% | 0.38 | -0.43 |
| Time exit 30m | 119 | -24.4% | -29.6% | +24.4% | 0.22 | -0.61 |
| Time exit 1h | 119 | -29.3% | -36.7% | +18.5% | 0.23 | -0.59 |
| Time exit 4h | 119 | -40.5% | -49.5% | +13.4% | 0.13 | -0.90 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 80
- medianSourceOrder: 40.5
- medianSourceDelaySec: 0
- medianEarlierBuys: 2.5
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| AZI (78avmi...pump) | 0.7555 | $7,939 | +13.9% | -66.3% | - | -1.6% | 81 | Cf1qTW...Pvd8 |
| STRANGE (AXmpQx...pump) | 1.2037 | $20,957 | +12.7% | -66.3% | - | +0.1% | 0 | - |
| Toledo (Ct6ALZ...pump) | 0.9562 | $10,746 | +21.9% | -61.4% | - | -0.8% | 0 | - |
| Toledo (FidATn...pump) | 1.0092 | $7,322 | +15.0% | -76.7% | - | -8.2% | 0 | - |
| BT6 (2kFkFU...pump) | 0.8637 | $6,544 | +37.5% | -2.9% | - | -4.6% | 26 | Ep1N4H...Y5T3 |
| SELFIE (2TbA8r...pump) | 0.7841 | $10,822 | +20.8% | +0.0% | - | +6.8% | 0 | - |
| Twiga (FtReVi...pump) | 0.8661 | $7,784 | +37.5% | -20.7% | - | +5.7% | 0 | - |
| Showntell (Gdzbpn...pump) | 0.7092 | $7,529 | +16.2% | -62.9% | - | -7.5% | 55 | G2q7J2...spA9 |
| IM (B8cZ6j...pump) | 0.7567 | $9,312 | +40.8% | -84.2% | - | +1.9% | 0 | - |
| PHOTO (GeKRbs...pump) | 0.6405 | $5,174 | +169.0% | -47.8% | 39m | +1.9% | 0 | - |
| PHOTO (BgJb11...pump) | 0.7163 | $14,518 | +243.6% | -39.0% | 47m | +3.4% | 0 | - |
| PRV (9NeDsu...pump) | 0.5673 | $5,719 | +83.4% | -61.7% | - | +23.3% | 53 | 5bxGXV...md82 |
| PV1000 (8AZhaY...pump) | 0.9597 | $20,133 | +33.1% | -95.1% | - | +2.3% | 0 | - |
| HONTER (6UtFvH...pump) | 0.5588 | $4,337 | +17.5% | -32.8% | - | -2.6% | 51 | FZU56d...tEzb |
| SABRE (J1Ye6j...pump) | 0.4837 | $3,492 | +0.0% | -40.8% | - | +24.2% | 19 | 7xPp16...JsBm |
| OIL (FbCUAS...pump) | 0.7583 | $7,597 | +0.6% | -84.6% | - | +1.3% | 0 | - |
| AEROPLANE (2nR3UW...pump) | 0.7091 | $6,932 | +11.6% | -74.3% | - | -9.0% | 47 | HociXD...Yf7V |
| JIHADI (DW5hrx...pump) | 0.4498 | $2,807 | +0.0% | -32.0% | - | -5.7% | 34 | CvGN9c...6AZa |
| BOOP (D5qTvM...pump) | 0.7662 | $8,164 | +28.8% | -4.5% | - | -5.3% | 27 | 2uVVfQ...LBuE |
| tame (BTEdeu...pump) | 0.6296 | $14,177 | +58.5% | -90.8% | - | +14.7% | 0 | - |
| ELONWIFHAT (81NJSy...pump) | 0.5697 | $4,509 | +0.1% | -52.1% | - | -5.3% | 16 | GaXDfa...eYjB |
| ELON (CmSAes...pump) | 0.7336 | $7,484 | +0.0% | -76.7% | - | -2.6% | 0 | - |
|  NORMIE (8Uy9Qz...pump) | 0.6921 | $11,504 | +116.4% | -1.7% | 71m | +7.8% | 4 | FjNJNH...HTBc |
| DOGE² (F2ocfU...pump) | 0.5631 | $6,621 | +106.7% | -15.5% | 70m | +1.3% | 0 | - |
| VAR (H1e24p...pump) | 0.4785 | $3,259 | +18.0% | -39.2% | - | +5.0% | 26 | ECStdL...HX3Q |
| Money (Adu5hg...pump) | 0.8123 | $9,180 | +0.0% | -48.0% | - | -15.5% | 77 | DhcYLK...cv18 |
| CUBE (J87kZK...pump) | 0.7485 | $7,565 | +39.0% | -77.3% | - | -2.1% | 0 | - |
| HAPPY (A3Zk1y...pump) | 0.6320 | $5,231 | +0.0% | -71.4% | - | -3.8% | 7 | Af9Fq1...4WaT |
| EXTRA (DwM4FH...pump) | 0.5904 | $4,842 | +0.0% | -55.1% | - | -12.8% | 81 | 3tnh1L...ArLX |
| Cupagent (4HEThm...pump) | 0.5746 | $5,232 | +34.3% | -51.7% | - | -4.6% | 0 | - |
| PALLET (3CsQQ4...pump) | 0.4784 | $3,176 | +18.3% | -8.8% | - | +26.6% | 82 | DzRTVt...VaWJ |
| $NEW (6K4543...pump) | 0.5894 | $4,826 | +0.7% | -52.2% | - | -3.1% | 10 | 5Fms62...E1os |
| Money (BUwVMB...pump) | 0.7895 | $7,092 | +101.2% | -57.8% | 24m | -0.5% | 0 | - |
| $NEW (EHSg3x...pump) | 0.5235 | $3,805 | +0.0% | -9.8% | - | -17.1% | 29 | k515RB...UMjm |
| DEGEN (VfNoeb...pump) | 0.8919 | $15,596 | +35.2% | -83.3% | - | +7.0% | 0 | - |
| Retarded (BqJVxP...pump) | 0.8603 | $7,933 | +17.6% | -79.0% | - | +4.6% | 0 | - |
| DOIT (4vPLcN...pump) | 0.8618 | $6,333 | +28.2% | -74.3% | - | -1.9% | 0 | - |
| FREE (3Xxr37...pump) | 0.9030 | $9,895 | +72.9% | -39.1% | - | -1.1% | 0 | - |
| UDC (2adcdq...pump) | 0.5356 | $8,370 | +177.7% | -53.9% | 15m | +0.2% | 0 | - |
| SMOKING (5nMxkA...pump) | 0.5047 | $4,330 | +23.7% | -61.0% | - | +15.0% | 76 | Z5fH9r...uSVp |
