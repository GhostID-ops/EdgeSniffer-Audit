# Path-Based Wallet Audit

Wallet: `4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9`
Generated: 2026-06-07T01:25:35.819Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 4000
- swaps: 2560
- campaigns: 625
- measuredEntries: 70
- oldestTx: 2026-05-31T21:51:34.000Z
- newestTx: 2026-06-07T00:19:23.000Z
- horizonHours: 24
- solUsdUsed: 63.14

## Wallet PnL

- realizedPnlSol: +235.6738 SOL
- closedCampaigns: 623
- realizedWinRate: +52.2%
- avgSourceExit: +30.1%
- medianSourceExit: +15.6%

## Entry Path Stats

- hit30: 28/70
- hit50: 19/70
- hit100: 7/70
- hit200: 2/70
- medianMfe: +16.8%
- medianMae: -68.5%
- medianEntryMcApprox: $6,485
- medianTimeTo2x: 28m

## Strategy Backtest

| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |
|---|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 69 | +30.1% | +15.6% | +66.7% | 5.11 | 0.51 |
| TP30 SL25 | 70 | -12.8% | -25.0% | +15.7% | 0.27 | -0.64 |
| TP50 SL30 | 70 | -16.2% | -30.0% | +11.4% | 0.26 | -0.63 |
| Trail arm45 give30 SL35 | 70 | -17.5% | -35.0% | +14.3% | 0.28 | -0.56 |
| Trail arm65 give35 SL35 | 70 | -20.2% | -35.0% | +8.6% | 0.23 | -0.64 |
| TP100 SL35 | 70 | -23.5% | -35.0% | +4.3% | 0.15 | -0.82 |
| Time exit 30m | 70 | -42.2% | -64.1% | +11.4% | 0.14 | -0.80 |
| Time exit 1h | 70 | -46.6% | -66.3% | +8.6% | 0.13 | -0.86 |
| Time exit 4h | 70 | -56.0% | -68.5% | +5.7% | 0.04 | -1.34 |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 57
- medianSourceOrder: 43
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| USDC (EPjFWd...Dt1v) | 220.0000 | $1,018,045,332 | +0.0% | +0.0% | - | - | 0 | - |
| dca (AQ37Ep...YN8Z) | 3.0171 | $8,381 | +45.6% | -93.0% | - | +56.8% | 0 | - |
| Design (LxJEYo...pump) | 3.0171 | $8,103 | +0.0% | -51.4% | - | -18.9% | 0 | - |
| Shatterproof (3rZcNp...YKpZ) | 2.0171 | $13,999 | +192.6% | -57.4% | 1m | +4.7% | 0 | - |
| tame (BTEdeu...pump) | 3.0171 | $14,090 | +58.5% | -92.1% | - | +50.6% | 0 | - |
| GS (9fpvvq...pump) | 1.5971 | $2,531 | +9.1% | -59.0% | - | +52.2% | 0 | - |
| GS (2CChP9...pump) | 2.2171 | $2,649 | +0.0% | -1.2% | - | -25.5% | 0 | - |
| BAG (5MEUnw...JBAG) | 3.0171 | $10,590 | +21.8% | -75.2% | - | -35.7% | 0 | - |
| MEME (31rTjG...pump) | 2.0171 | $11,300 | +54.9% | -75.9% | - | -33.2% | 0 | - |
| FEAR (6psXxh...pump) | 3.0171 | $6,251 | +34.8% | -68.8% | - | +46.1% | 0 | - |
| JOB (2SAwGW...pump) | 5.0171 | $39,125 | +0.0% | -68.1% | - | +60.8% | 0 | - |
| INTERNET (BP9aBY...pump) | 3.0171 | $7,215 | +10.8% | -65.6% | - | +21.8% | 0 | - |
| Free Money (61kgMR...pump) | 1.5971 | $3,763 | +80.0% | -94.1% | - | +68.0% | 0 | - |
| CLIPS (CMFSXe...pump) | 3.0171 | $18,119 | +0.0% | -65.3% | - | -51.1% | 0 | - |
| Chair (8eHXWK...pump) | 1.5971 | $2,810 | +0.0% | -0.1% | - | -21.2% | 15 | 4sGJy7...aSp5 |
| Happiness (6nQ9cc...pump) | 2.0171 | $4,096 | +4.0% | -67.6% | - | +25.1% | 58 | A5D5YK...XjmF |
| Money (8QUt8C...pump) | 3.0171 | $13,146 | +10.6% | -87.3% | - | +0.6% | 0 | - |
| Money (BUwVMB...pump) | 3.0171 | $10,663 | +101.2% | -58.3% | 24m | -9.2% | 0 | - |
| AD (nfuaE9...pump) | 3.0171 | $12,201 | +35.5% | -68.8% | - | +7.2% | 0 | - |
| FAST (GkNAYU...pump) | 2.0022 | $4,059 | +0.0% | +0.0% | - | -15.2% | 0 | - |
| GM (5Y8tfS...pump) | 2.2171 | $3,948 | +0.0% | +0.0% | - | +3.6% | 0 | - |
| FuckYou (8JH9gq...pump) | 3.0171 | $11,559 | +7.4% | -94.4% | - | +21.6% | 0 | - |
| elon (4XH8wX...pump) | 2.0171 | $4,510 | +0.0% | -58.0% | - | +37.6% | 0 | - |
| ted (7iHwcD...pump) | 2.2171 | $4,463 | +37.6% | -84.3% | - | +121.5% | 0 | - |
| DEGEN (VfNoeb...pump) | 3.0171 | $18,736 | +35.2% | -83.4% | - | -12.7% | 0 | - |
| DOIT (4vPLcN...pump) | 2.0171 | $5,855 | +28.2% | -74.5% | - | +53.7% | 0 | - |
| MEMENALD (DiuBnE...pump) | 2.0171 | $4,130 | +3.7% | -34.5% | - | -17.0% | 50 | Gc7RRv...Up7R |
| FREE (3Xxr37...pump) | 2.0171 | $5,240 | +65.0% | -82.1% | - | +44.6% | 0 | - |
| Easy (7PYyXf...pump) | 3.0171 | $17,672 | +16.1% | -89.7% | - | -28.9% | 0 | - |
| REPLY (4UdPeZ...pump) | 3.0171 | $16,352 | +7.2% | -92.4% | - | +60.2% | 0 | - |
| EASY (FfPHKM...pump) | 3.0171 | $12,589 | +80.6% | -91.4% | - | +39.2% | 0 | - |
| INTERNET (Cdqjbx...pump) | 2.0171 | $5,481 | +13.0% | -95.2% | - | -3.4% | 0 | - |
| CLEAN (2BL7au...pump) | 2.0171 | $6,749 | +45.9% | -94.1% | - | +265.9% | 0 | - |
| manlet (79p16t...pump) | 3.0171 | $8,899 | +32.7% | -96.7% | - | +218.1% | 0 | - |
| sexcoin (8sufxa...MNEu) | 2.0171 | $6,635 | +17.0% | -93.9% | - | +68.5% | 0 | - |
| sexcoin (DUTppD...pump) | 2.0171 | $5,863 | +0.0% | -7.4% | - | -12.8% | 0 | - |
| John (Aqfua5...pump) | 2.0171 | $7,412 | +0.0% | -0.7% | - | -10.3% | 0 | - |
| Evie (FjMeuv...pump) | 3.0171 | $8,847 | +8.6% | -92.6% | - | +13.7% | 0 | - |
| FEAR (CidPup...pump) | 3.0171 | $11,908 | +93.8% | -92.7% | - | +178.1% | 0 | - |
| GAMES (CQRrcc...pump) | 3.0171 | $11,397 | +51.5% | -94.9% | - | +138.1% | 0 | - |
