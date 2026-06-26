# CyA Last 24h MC >= $50,000 Analysis

Generated: 2026-06-19T04:24:27.401Z

Assumptions:
- Fresh Helius wallet scan; one campaign per mint.
- Entry MC is inferred from CyA's first fill, SOL/USD=150, supply=1,000,000,000.
- TP rows sell 100% if target is hit before CyA's first sell; otherwise they fall back to CyA first sell.
- Trailing row uses `50:25,100:75,200:125,300:200,500:350,600:400,800:500,1200:1000` before CyA's first sell.

## Coverage

- Wallet txs fetched: 1007
- Swaps reconstructed: 739
- Campaigns in last 24h: 123
- MC >= $50,000 candidates: 23
- Measured rows: 16
- Skips: 7
- Median entry MC: $203,452
- Median MFE/MAE: +44.2% / -40.8%

## Target Hit Before CyA First Sell

| Target | Hits | Hit Rate | Median Hit Min |
|---|---:|---:|---:|
| +30% | 10/16 | +62.5% | 0.4 |
| +50% | 8/16 | +50.0% | 0.4 |
| +75% | 7/16 | +43.8% | 0.2 |
| +100% | 6/16 | +37.5% | 0.4 |

## Strategy Comparison

| Strategy | Trades | Win | Avg | Median | PF | Total |
|---|---:|---:|---:|---:|---:|---:|
| mirror CyA first sell | 16 | +43.8% | +27.0% | -0.5% | 3.12 | +431.5% |
| TP30 sell 100% | 16 | +75.0% | +12.1% | +30.0% | 2.34 | +193.4% |
| TP50 sell 100% | 16 | +62.5% | +18.1% | +36.2% | 2.95 | +289.6% |
| TP75 sell 100% | 16 | +62.5% | +26.1% | +18.9% | 3.82 | +417.8% |
| TP100 sell 100% | 16 | +56.3% | +27.4% | +9.3% | 3.16 | +438.2% |
| custom trailing | 16 | +62.5% | +24.3% | +23.7% | 3.63 | +389.6% |

## Trades

| Token | Mint | Entry UTC | Entry MC | CyA Buy | CyA Exit | MFE | MAE | Trailing Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1B | `EkHFmm...BAGS` | 2026-06-18T21:12:21.000Z | $66.37M | 5.007 SOL | +169.9% | +250.8% | -0.3% | +75.0% @ 1m |
| DYMO | `6dwpot...BAGS` | 2026-06-18T20:09:52.000Z | $2.68M | 5.007 SOL | -6.9% | +22.8% | -57.3% | - |
| ASTEROID | `F2GLrm...BAGS` | 2026-06-18T21:26:47.000Z | $1.60M | 5.007 SOL | -56.5% | +26.0% | -83.3% | - |
| TOPO | `8aiK3h...BAGS` | 2026-06-18T21:21:56.000Z | $796,282 | 5.007 SOL | -37.4% | +24.1% | -80.2% | - |
| CNTBAGWORK | `CAhc23...BAGS` | 2026-06-18T19:22:18.000Z | $612,986 | 5.007 SOL | -43.7% | +2.1% | -66.3% | - |
| ALON | `9ufT5F...BAGS` | 2026-06-18T22:01:57.000Z | $320,642 | 3.007 SOL | -0.0% | +101.0% | -53.5% | +75.0% @ 0m |
| BARRON | `2aKTUD...pump` | 2026-06-19T00:21:49.000Z | $246,608 | 5.007 SOL | -54.6% | +80.1% | -69.3% | +25.0% @ 0m |
| ANSEM | `61Vt4r...BAGS` | 2026-06-19T03:58:58.000Z | $224,773 | 5.007 SOL | -0.9% | +150.5% | -22.9% | +75.0% @ 0m |
| TRENCH | `BzyKa1...pump` | 2026-06-18T20:41:57.000Z | $182,131 | 5.005 SOL | -1.4% | +30.2% | -22.4% | - |
| TRENCHES | `3NMQuw...pump` | 2026-06-18T20:41:51.000Z | $139,465 | 5.007 SOL | +120.1% | +330.8% | -36.6% | +75.0% @ 0m |
| gary | `7vJsvn...ZBWC` | 2026-06-19T01:34:25.000Z | $110,010 | 5.007 SOL | +270.6% | +206.1% | -33.5% | +125.0% @ 1m |
| DAWN | `EmfAFk...pump` | 2026-06-18T23:42:02.000Z | $96,060 | 5.007 SOL | +3.2% | +56.5% | -45.0% | +25.0% @ 1m |
| EJEN | `H6dbdE...ptkU` | 2026-06-19T00:56:42.000Z | $89,061 | 5.007 SOL | -2.4% | +31.9% | -3.4% | - |
| COMOS | `Gn2SDa...pump` | 2026-06-18T22:33:35.000Z | $77,291 | 5.007 SOL | +33.7% | +320.9% | -1.7% | +25.0% @ 1m |
| MSTR | `CBxnoa...pump` | 2026-06-18T18:18:34.000Z | $58,852 | 5.007 SOL | +22.4% | +9.5% | -48.0% | - |
| UP | `857tSu...pump` | 2026-06-18T20:05:53.000Z | $51,377 | 5.007 SOL | +15.4% | +10.9% | -34.7% | - |

## Skips

- `Ag7EKa...pump`: missing entry/path candles
- `FTxJjf...BAGS`: missing entry/path candles
- `2CZ9Ay...BAGS`: missing entry/path candles
- `8uMoWW...BAGS`: missing entry/path candles
- `B5ahtE...BAGS`: missing entry/path candles
- `EgSAgE...BAGS`: missing entry/path candles
- `9Kpa4G...BAGS`: missing entry/path candles
