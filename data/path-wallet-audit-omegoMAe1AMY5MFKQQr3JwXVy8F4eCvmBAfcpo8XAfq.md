# Path-Based Wallet Audit

Wallet: `omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq`
Generated: 2026-06-22T00:49:41.433Z

## Methodology

This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.

## Coverage

- partial: false
- txsFetched: 3000
- swaps: 2984
- campaigns: 207
- measuredEntries: 189
- oldestTx: 2026-06-21T11:34:38.000Z
- newestTx: 2026-06-21T23:00:00.000Z
- horizonHours: 14
- solUsdUsed: 73.26

## Wallet PnL

- realizedPnlSol: +6.5808 SOL
- closedCampaigns: 202
- realizedWinRate: +38.1%
- avgSourceExit: -1.6%
- medianSourceExit: -2.3%

## Entry Path Stats

- hit30: 84/189
- hit50: 61/189
- hit100: 30/189
- hit200: 7/189
- medianMfe: +21.4%
- medianMae: -49.2%
- medianEntryMcApprox: $6,768
- medianTimeTo2x: 30m

## Strategy Backtest

Returns are per-trade ROI. Cumulative DD is the worst drop in the cumulative return curve, expressed in percentage points, not bankroll percent.

| Strategy | Trades | Avg ROI | Median ROI | Win Rate | Profit Factor | Cumulative ROI | Cumulative DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 184 | -1.6% | -2.3% | +40.2% | 0.71 | -287.8pp | -321.7pp |
| TP30 SL25 | 189 | -6.4% | -25.0% | +31.7% | 0.59 | -1200.8pp | -1395.4pp |
| TP50 SL30 | 189 | -8.9% | -30.0% | +24.9% | 0.55 | -1683.8pp | -1730.9pp |
| Trail arm45 give30 SL35 | 189 | -9.6% | -35.0% | +28.0% | 0.56 | -1814.5pp | -1955.7pp |
| Trail arm65 give35 SL35 | 189 | -11.6% | -35.0% | +22.2% | 0.50 | -2195.4pp | -2351.8pp |
| TP100 SL35 | 189 | -13.6% | -35.0% | +16.4% | 0.46 | -2579.0pp | -2761.2pp |
| Time exit 30m | 189 | -18.5% | -17.0% | +27.5% | 0.33 | -3491.1pp | -3491.1pp |
| Time exit 1h | 189 | -22.1% | -28.3% | +23.8% | 0.31 | -4168.6pp | -4219.1pp |
| Time exit 4h | 189 | -34.3% | -39.3% | +15.3% | 0.14 | -6481.8pp | -6499.4pp |

## Lead Wallet / Sniping Clues

- profiledLeadEntries: 0
- medianSourceOrder: -
- medianSourceDelaySec: -
- medianEarlierBuys: -
- repeatedTopEarlierBuyers: none

## Recent Measured Entries

| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| $Memeful (ERmgHv...pump) | 0.4527 | $3,338 | +0.0% | -27.8% | - | - | - | - |
| HermesAgnt (59mFZ9...pump) | 0.5627 | $7,007 | +46.1% | -42.3% | - | +13.8% | - | - |
| Kevin (2p8B7V...Vfki) | 0.8005 | $7,712 | +24.2% | -77.3% | - | -3.8% | - | - |
| CROFT (7GjyNr...pump) | 0.5486 | $8,773 | +158.8% | +0.0% | 9m | -3.3% | - | - |
| Vertebrae (9QKFPn...pump) | 0.6336 | $9,124 | +125.5% | -66.0% | 3m | -1.4% | - | - |
| SHILL (3wHcem...nmZS) | 0.6974 | $7,178 | +42.0% | -1.2% | - | -18.0% | - | - |
| ML (JBm8zt...pump) | 0.4647 | $3,899 | +22.8% | -8.4% | - | +2.9% | - | - |
| Hatchers (3wWNGD...pump) | 0.6600 | $7,164 | +2.8% | -11.8% | - | -7.6% | - | - |
| EHC (9gxVJi...iy18) | 0.8783 | $8,848 | +1.2% | -66.9% | - | +0.6% | - | - |
| PEAK (EwT3Mo...pump) | 0.7134 | $6,381 | +13.0% | -81.6% | - | +0.1% | - | - |
| STARFALL (4C4hRD...pump) | 0.7316 | $8,774 | +0.0% | -6.9% | - | -0.4% | - | - |
| GAMer (H1XcWZ...pump) | 0.4773 | $3,727 | +0.0% | -37.0% | - | -4.4% | - | - |
| ONEPUMP (Hv9PR4...pump) | 0.5763 | $6,461 | +34.4% | -40.8% | - | -0.7% | - | - |
| SCASH (Dg1XA1...pump) | 0.7247 | $9,072 | +5.9% | -76.5% | - | -0.8% | - | - |
| FeeMarke (D8jQBd...nPbL) | 0.7766 | $9,888 | +4.9% | -54.0% | - | -4.4% | - | - |
| Vertebra (3PUtL7...iBMP) | 0.6789 | $6,101 | +9.8% | -68.1% | - | +10.9% | - | - |
| BUBA (5PerTf...pump) | 0.7211 | $7,270 | +38.2% | -38.4% | - | +2.8% | - | - |
| MINORI (C6pX9e...pump) | 0.6270 | $8,605 | +160.7% | -29.1% | 197m | +2.2% | - | - |
| test (2oqSdo...6prz) | 0.6229 | $5,999 | +6.4% | -78.3% | - | +16.3% | - | - |
| DOG (4qfH9V...fF6w) | 0.8799 | $13,052 | +70.8% | -88.4% | - | -5.2% | - | - |
| $COLI (8aETuK...pump) | 0.4845 | $3,572 | +3.0% | -33.5% | - | -4.6% | - | - |
| FIERROLAIS (HnY8gP...usCg) | 0.8516 | $14,139 | +72.6% | -89.2% | - | +10.1% | - | - |
| DAWN (5wzbcK...pump) | 0.8741 | $12,533 | +0.0% | -74.3% | - | -9.8% | - | - |
| AGW (24oDT8...pump) | 0.5196 | $4,419 | +0.0% | -32.9% | - | +18.4% | - | - |
| Richard (9f1rEr...7C5n) | 0.7666 | $14,314 | +93.5% | -92.9% | - | +3.4% | - | - |
| infinity (H7t7oK...pump) | 1.1030 | $21,236 | +15.9% | +0.0% | - | -2.4% | - | - |
| MTF (DhQGUd...pump) | 0.6486 | $5,245 | +0.0% | -31.9% | - | -0.5% | - | - |
| DIH (9YPuXy...pump) | 0.5303 | $4,604 | +9.8% | -11.8% | - | -10.9% | - | - |
| RAGNAR (2axLeS...pump) | 0.5853 | $7,369 | +116.7% | -54.7% | 40m | +12.0% | - | - |
| $PUMPSMP (Dffkrd...pump) | 0.6900 | $7,427 | +43.3% | -67.6% | - | +0.9% | - | - |
| GNS101 (6uK7e5...pump) | 0.5688 | $6,055 | +32.5% | -10.0% | - | -4.2% | - | - |
| DAWN (7JGfTT...pump) | 0.6803 | $7,585 | +0.0% | -28.2% | - | +0.5% | - | - |
| Bounded (FnZpHj...pump) | 0.5744 | $4,720 | +139.9% | -20.4% | 13m | +6.7% | - | - |
| UNLCKLDR (DWP8uL...pump) | 0.4835 | $4,485 | +52.5% | -27.5% | - | -3.2% | - | - |
| ANSHUA (CSBSpu...z3YX) | 0.5346 | $4,678 | +0.0% | -56.0% | - | - | - | - |
| XPKT (25vtFH...pump) | 0.6535 | $6,525 | +0.0% | -66.0% | - | -23.9% | - | - |
| Goaty (23c2VG...pump) | 0.9233 | $11,950 | +105.2% | -66.7% | 197m | +7.0% | - | - |
| REJECT (7oeGdK...pump) | 0.4889 | $3,911 | +54.0% | -55.6% | - | - | - | - |
| chillcat (A7aC3N...pump) | 0.6324 | $8,291 | +240.2% | -57.3% | 28m | +2.5% | - | - |
| BULLCEMBER (DTRmPL...pump) | 0.7216 | $6,042 | +18.7% | -53.3% | - | -1.4% | - | - |
