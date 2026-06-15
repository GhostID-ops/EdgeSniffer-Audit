# Path-Based Wallet Audit

Wallet: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`
Generated: 2026-06-07T01:16:25.164Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 4000
- swaps: 3327
- campaigns: 956
- measuredEntries: 50
- oldestTx: 2026-06-05T17:51:43.000Z
- newestTx: 2026-06-07T01:09:17.000Z
- horizonHours: 24
- solUsdUsed: 62.72

## Wallet PnL

- realizedPnlSol: +170.9726 SOL
- closedCampaigns: 928
- realizedWinRate: +46.0%
- avgSourceExit: +28.0%
- medianSourceExit: +2.8%

## Entry Path Stats

- hit30: 14/50
- hit50: 12/50
- hit100: 9/50
- hit200: 1/50
- medianMfe: +0.5%
- medianMae: -14.7%
- medianEntryMcApprox: $2,896
- medianTimeTo2x: 13m

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 47 | +28.0% | +2.8% | +59.6% | 3.44 | 0.15 |
| Trail arm65 give35 SL35 | 50 | +6.1% | +0.0% | +30.0% | 1.58 | 0.14 |
| TP100 SL35 | 50 | +5.4% | -0.0% | +28.0% | 1.48 | 0.12 |
| Trail arm45 give30 SL35 | 50 | +4.4% | +0.0% | +32.0% | 1.45 | 0.12 |
| TP50 SL30 | 50 | +2.5% | +0.0% | +32.0% | 1.29 | 0.09 |
| TP30 SL25 | 50 | -1.7% | +0.0% | +30.0% | 0.79 | -0.09 |
| Time exit 4h | 50 | -10.4% | -1.1% | +20.0% | 0.45 | -0.21 |
| Time exit 30m | 50 | -10.4% | -0.7% | +20.0% | 0.34 | -0.33 |
| Time exit 1h | 50 | -12.9% | -1.1% | +18.0% | 0.26 | -0.41 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 50
- medianSourceOrder: 2
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| WEEDSLUT (GonUhW...pump) | 3.0718 | $2,539 | +27.3% | -35.3% | - | +0.6% | 11 | 14th5G...SMLs |
| LTC (Bqd7ii...pump) | 1.8247 | $2,535 | +0.0% | +0.0% | - | +12.9% | 5 | 26zXzu...CfSP |
| bankvote (At2im4...pump) | 12.4422 | $5,091 | +1.1% | -37.3% | - | -16.8% | 0 | - |
| 1000000 (46jBqP...pump) | 1.8789 | $2,758 | +0.0% | -40.9% | - | +13.9% | 0 | - |
| manlet (Dr14gE...pump) | 0.0022 | $10 | +53.7% | -60.7% | - | +1232.4% | 0 | - |
| 1000000 (EGyo2h...pump) | 1.2111 | $3,781 | +0.0% | -0.7% | - | - | 0 | - |
| FONDJO (FS2Ehu...pump) | 4.3308 | $2,090 | +0.0% | +0.0% | - | -2.4% | 3 | EuVGgn...7oPN |
| RDOG (CgHE4S...pump) | 1.9407 | $2,329 | +134.2% | -15.6% | 13m | +3.8% | 0 | - |
| TRADE (H2VYgV...pump) | 2.0111 | $1,939 | +0.0% | +0.0% | - | +1.1% | 4 | Ez2jYo...ZkTf |
| MONEY (2GUALa...rfNZ) | 3.1232 | $4,567 | +0.0% | +0.0% | - | - | 0 | - |
| unrecprd (it8wRb...pump) | 1.2111 | $2,176 | +0.0% | +0.0% | - | -16.1% | 3 | G9rzNF...Ch2m |
| BabyT (4EL7nm...pump) | 4.9063 | $8,764 | +173.3% | -26.0% | 16m | +4.1% | 0 | - |
| TRUMP80 (HHBtMj...pump) | 1.0106 | $1,975 | +103.2% | -0.0% | 17m | -3.1% | 2 | 77ekJw...xT47 |
| CUMSKIN (9DeqQ8...pump) | 1.2138 | $3,392 | +95.4% | -43.9% | - | +13.2% | 0 | - |
| richdev (2L2W3w...pump) | 4.3633 | $3,598 | +0.0% | -14.1% | - | +8.5% | 5 | DKfDdL...jPhR |
| bottom (EPXmkw...pump) | 8.2242 | $6,480 | +0.0% | -0.7% | - | -44.7% | 20 | 39LLDs...bCtz |
| Free Money (DJEpYN...pump) | 1.2277 | $2,879 | +0.0% | +0.0% | - | -34.5% | 5 | 2mqrin...jwdY |
| UP (4iCFoT...pump) | 3.0397 | $2,904 | +0.0% | +0.0% | - | +1.8% | 0 | - |
| Posthuman (GNRxbR...pump) | 1.8247 | $4,547 | +33.1% | -4.3% | - | +88.4% | 0 | - |
| Dell (FHaL9i...pump) | 1.2138 | $2,106 | +0.0% | +0.0% | - | +5.4% | 3 | H8fY8B...cwtv |
| ROMO (2EQ7SU...pump) | 1.8737 | $4,602 | +29.8% | -31.8% | - | +61.4% | 0 | - |
| OHIO (9vbEfm...pump) | 1.2344 | $2,193 | +0.0% | +0.0% | - | -3.4% | 0 | - |
| SOLDROP (6nDksF...pump) | 1.8613 | $3,986 | +2.4% | -52.4% | - | -0.2% | 0 | - |
| Idiot6900 (CGw7g9...pump) | 1.8247 | $2,888 | +3.2% | -58.5% | - | +114.5% | 0 | - |
| $Crypto (FtjXyf...pump) | 1.8393 | $3,032 | +98.6% | -23.1% | - | +29.8% | 0 | - |
| toga (4fJUdy...pump) | 1.2111 | $2,215 | +0.0% | +0.0% | - | -17.2% | 3 | 4ciCkR...ZYU8 |
| 6/6 (34rn4s...pump) | 1.2172 | $2,693 | +1.4% | -3.0% | - | +10.7% | 6 | 8i5U2u...b97K |
| SPCX6900 (kLAHga...pump) | 1.2138 | $3,724 | +38.1% | -15.2% | - | +43.9% | 14 | BVYnrh...c7Yu |
| BT6 (2kFkFU...pump) | 1.2288 | $2,872 | +104.1% | -55.7% | 13m | -1.5% | 0 | - |
| Gustavo (7Fzxzo...pump) | 1.8688 | $2,853 | +0.0% | -45.0% | - | +30.9% | 10 | 4BHBSZ...PqHP |
| USWR (7THy5o...pump) | 2.0093 | $1,913 | +0.1% | -0.9% | - | -2.8% | 1 | HFprnJ...3pxk |
| BTC (5sSzgc...pump) | 1.2138 | $2,014 | +0.4% | +0.0% | - | +2.8% | 4 | 7nfdUe...i6Xq |
| POKETCG (N9Curn...pump) | 0.0542 | $57 | +0.0% | +0.0% | - | -97.9% | 0 | - |
| Screwed (FGLoAC...q5uu) | 1.2138 | $3,303 | +0.0% | -46.4% | - | -3.6% | 0 | - |
| ZOO (E2oecx...pump) | 1.2033 | $3,551 | +411.4% | -15.7% | 11m | +24.1% | 0 | - |
| SCAT (3H77cq...pump) | 5.8107 | $3,865 | +128.8% | -73.3% | 9m | +6.7% | 0 | - |
| Sleuth (DeY799...pump) | 1.2138 | $2,387 | +0.0% | -24.0% | - | -0.4% | 0 | - |
| AFD (JDmr2Y...pump) | 5.2672 | $3,568 | +2.7% | -19.9% | - | +10.1% | 0 | - |
| MontyMemes (DvRKEc...pump) | 2.7556 | $3,378 | +0.7% | -3.2% | - | +5.2% | 0 | - |
| MOG (4YGDAX...pump) | 18.6257 | $8,497 | +7.6% | -79.5% | - | +13.7% | 0 | - |
