# CyA 48h-72h Ago MC >= $50,000 PumpFun/PumpSwap-Only Analysis

Generated: 2026-06-19T15:56:01.354Z

Assumptions:
- Fresh Helius wallet scan; one campaign per mint.
- Time window: 2026-06-16T15:54:06.000Z through 2026-06-17T15:54:06.000Z.
- Route filter: PumpFun/PumpSwap mints only (`...pump`).
- Entry MC is inferred from CyA's first fill, SOL/USD=69.07, supply=1,000,000,000.
- TP rows sell 100% if target is hit before CyA's first sell; otherwise they fall back to CyA first sell.
- Trailing row uses `50:25,100:75,200:125,300:200,500:350,600:400,800:500,1200:1000` before CyA's first sell.

## Coverage

- Wallet txs fetched: 4067
- Swaps reconstructed: 3163
- Campaigns in analysis window: 192
- MC >= $50,000 candidates before route filter: 14
- Candidates after route filter: 9
- Measured rows: 5
- Skips: 4
- Median entry MC: $254,054
- Median MFE/MAE: +4.2% / -17.8%

## Target Hit Before CyA First Sell

| Target | Hits | Hit Rate | Median Hit Min |
|---|---:|---:|---:|
| +30% | 2/5 | +40.0% | 0.6 |
| +50% | 1/5 | +20.0% | 7.2 |
| +75% | 0/5 | +0.0% | - |
| +100% | 0/5 | +0.0% | - |

## Strategy Comparison

| Strategy | Trades | Win | Avg | Median | PF | Total |
|---|---:|---:|---:|---:|---:|---:|
| mirror CyA first sell | 5 | +20.0% | -13.1% | -8.9% | 0.00 | -65.5% |
| TP30 sell 100% | 5 | +60.0% | +9.7% | +0.1% | 5.25 | +48.6% |
| TP50 sell 100% | 5 | +40.0% | +2.5% | -2.5% | 1.33 | +12.5% |
| TP75 sell 100% | 5 | +20.0% | -13.1% | -8.9% | 0.00 | -65.5% |
| TP100 sell 100% | 5 | +20.0% | -13.1% | -8.9% | 0.00 | -65.5% |
| custom trailing | 5 | +40.0% | -2.5% | -2.5% | 0.67 | -12.5% |

## Trades

| Token | Mint | Entry UTC | Entry MC | CyA Buy | CyA Exit | MFE | MAE | Trailing Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Merlin | `AvxFBj...pump` | 2026-06-16T19:06:34.000Z | $1.94M | 5.003 SOL | -8.9% | +2.2% | -15.1% | - |
| RIZZMAS | `ADA9mt...pump` | 2026-06-16T18:18:29.000Z | $486,882 | 5.001 SOL | +0.1% | +4.2% | -6.8% | - |
| GAYLON | `pu4obk...pump` | 2026-06-16T21:38:50.000Z | $254,054 | 5.003 SOL | -2.5% | +1.8% | -55.4% | - |
| OGFLOKI | `4gZ8BD...pump` | 2026-06-16T20:48:48.000Z | $187,898 | 5.003 SOL | -28.1% | +66.7% | -40.0% | +25.0% @ 9m |
| MUTUMBO | `4StuLH...pump` | 2026-06-17T00:12:59.000Z | $80,878 | 5.001 SOL | -26.1% | +35.8% | -17.8% | - |

## Skips

- `8J69rb...pump`: missing entry/path candles across 1 pair(s): unknown:FDrY5i...NSP7 no entry/path candles
- `9cRCn9...pump`: missing entry/path candles across 1 pair(s): unknown:FnzKY6...L3CC no entry/path candles
- `EUwXuw...pump`: missing entry/path candles across 1 pair(s): unknown:7EARNR...jDAw no entry/path candles
- `8MifqH...pump`: missing entry/path candles across 1 pair(s): unknown:6gmvaD...d3qu no entry/path candles
