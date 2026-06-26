# Path-Based Wallet Audit

Wallet: `tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf`
Generated: 2026-06-24T06:14:26.417Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 5000
- swaps: 1633
- campaigns: 359
- measuredEntries: 80
- oldestTx: 2026-06-18T21:09:58.000Z
- newestTx: 2026-06-24T06:09:13.000Z
- horizonHours: 24
- solUsdUsed: 69.79

## Wallet PnL

- realizedPnlSol: +49.3422 SOL
- closedCampaigns: 2
- realizedWinRate: +100.0%
- avgSourceExit: +0.0%
- medianSourceExit: -

## Entry Path Stats

- hit30: 50/80
- hit50: 36/80
- hit100: 23/80
- hit200: 14/80
- medianMfe: +45.0%
- medianMae: -90.0%
- medianEntryMcApprox: $31
- medianTimeTo2x: 15m

## Strategy Backtest

Returns are per-trade ROI. Cumulative DD is the worst drop in the cumulative return curve, expressed in percentage points, not bankroll percent.

| Strategy | Trades | Avg ROI | Median ROI | Win Rate | Profit Factor | Cumulative ROI | Cumulative DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 0 | +0.0% | - | +0.0% | 0.00 | +0.0pp | +0.0pp |
| TP50 SL30 | 80 | -6.3% | -30.0% | +28.7% | 0.69 | -503.5pp | -815.9pp |
| Trail arm65 give35 SL35 | 80 | -6.4% | -35.0% | +25.0% | 0.74 | -510.7pp | -1036.5pp |
| TP100 SL35 | 80 | -6.9% | -35.0% | +21.3% | 0.74 | -548.5pp | -910.9pp |
| TP30 SL25 | 80 | -7.2% | -25.0% | +31.3% | 0.56 | -579.4pp | -746.9pp |
| Trail arm45 give30 SL35 | 80 | -7.4% | -35.0% | +30.0% | 0.68 | -592.0pp | -895.4pp |
| Time exit 4h | 80 | -27.8% | -85.5% | +13.8% | 0.55 | -2226.8pp | -4031.1pp |
| Time exit 30m | 80 | -28.7% | -57.6% | +17.5% | 0.41 | -2298.2pp | -2654.4pp |
| Time exit 1h | 80 | -40.0% | -68.4% | +20.0% | 0.26 | -3202.4pp | -3232.1pp |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 20
- medianSourceOrder: 98.5
- medianSourceDelaySec: 0
- medianEarlierBuys: 0
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| DUMPSTER (7MfL79...pump) | 0.0007 | $25 | +43.9% | +0.0% | - | - | 0 | - |
| SOB (27zbYf...pump) | 0.0007 | $27 | +59.9% | -38.0% | - | - | 0 | - |
| WEN (Eav7v7...pump) | 0.0007 | $19 | +71.4% | -80.1% | - | - | 0 | - |
| EC43 (382Yoj...pump) | 0.0008 | $62 | +219.9% | -48.0% | 11m | - | 0 | - |
| WSB (8QHo7V...pump) | 0.0010 | $42 | +58.3% | -96.2% | - | - | 0 | - |
| wundys (HFpdNs...pump) | 0.0007 | $32 | +101.9% | -80.1% | 4m | - | 0 | - |
| WEN (F8sTbM...pump) | 0.0008 | $29 | +14.4% | -87.7% | - | - | 0 | - |
| EC43 (8Jdjmc...pump) | 0.0013 | $106 | +289.1% | -61.1% | 27m | - | 0 | - |
| ANGIE (3p8Y7B...pump) | 0.0007 | $23 | +80.4% | -56.7% | - | - | 0 | - |
| STARSHIT (FwFuSt...pump) | 0.0009 | $38 | +18.5% | -81.7% | - | - | 0 | - |
| Pokelana (Doqpv6...pump) | 0.0008 | $27 | +203.6% | -76.4% | 10m | - | 0 | - |
| WEN (66pQgf...pump) | 0.0008 | $276 | +3773.6% | -16.2% | 18m | - | 0 | - |
| ATELIER (7newJU...pump) | 0.0041 | $604 | +20.4% | -29.5% | - | - | 0 | - |
| Gronk (HkNZ5L...pump) | 0.0010 | $46 | +43.4% | -66.0% | - | - | 0 | - |
| QRCOIN (qrFncV...TbV6) | 0.0007 | $23 | +0.0% | -93.8% | - | - | 0 | - |
| FC (BKqLgy...pump) | 0.0010 | $43 | +20.0% | -93.9% | - | - | 0 | - |
| GRACIE (7jVXjq...pump) | 0.0007 | $65 | +302.7% | -62.8% | 84m | - | 0 | - |
| Onboard (DQjxb5...pump) | 0.0008 | $33 | +127.3% | -89.0% | 6m | - | 0 | - |
| VASSEL (CJMK1m...pump) | 0.0006 | $14 | +1.7% | -92.1% | - | - | 3 | HagBAc...cReZ |
| NPA (3aMima...pump) | 0.0009 | $39 | +7.2% | -94.3% | - | - | 0 | - |
| Joke (3Egfoo...xXP2) | 0.0008 | $36 | +11.4% | -96.7% | - | - | - | - |
| ABDULLAH (5yJgTd...SsvU) | 0.0008 | $26 | +0.0% | -94.1% | - | - | - | - |
| STARMIND (5qjZLd...pump) | 0.0008 | $43 | +28.3% | -65.1% | - | - | - | - |
| SF (FsoDkM...pump) | 0.0007 | $26 | +13.6% | -95.2% | - | - | - | - |
| STARMIND (45Kt1m...pump) | 0.0008 | $28 | +911.0% | -87.1% | 221m | - | - | - |
| Jonah (8xt6zz...pump) | 0.0007 | $31 | +417.5% | -29.8% | 8m | - | - | - |
| neopets (DocMTi...pump) | 0.0007 | $24 | +24.8% | -77.0% | - | - | - | - |
| LIFE (H7gHym...pump) | 0.0008 | $36 | +44.2% | -88.8% | - | - | - | - |
| XP (9spN3L...pump) | 0.0027 | $218 | +52.6% | -27.5% | - | - | - | - |
| LOOM (4NVtTJ...pump) | 0.0013 | $43 | +197.1% | -94.4% | 7m | - | - | - |
| bounty (6mGN5N...pump) | 0.0011 | $72 | +42.0% | -99.0% | - | - | - | - |
| LOOM (4GNVr4...pump) | 0.0012 | $59 | +0.0% | -65.0% | - | - | - | - |
| runspace (3pWjLB...pump) | 0.0009 | $33 | +0.0% | -12.0% | - | - | - | - |
| SIG (D9X7e7...pump) | 0.0006 | $18 | +0.0% | -17.4% | - | - | - | - |
| LAZARUS (29Mcsn...pump) | 0.0008 | $30 | +268.9% | -65.3% | 3m | - | - | - |
| Pookie (4mt8Ar...pump) | 0.0007 | $24 | +33.2% | -93.1% | - | - | - | - |
| MAINER (HESgF5...pump) | 0.0008 | $21 | +13.3% | -92.8% | - | - | - | - |
| BACK (6xut85...Gbmh) | 0.0008 | $24 | +45.4% | -98.6% | - | - | - | - |
| LAZARUS (7TiedM...pump) | 0.0008 | $26 | +53.3% | -93.9% | - | - | - | - |
| STAR (55LH84...jFkq) | 0.0009 | $32 | +16.7% | -93.8% | - | - | - | - |
