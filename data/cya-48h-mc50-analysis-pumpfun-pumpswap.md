# CyA Last 48h MC >= $0 PumpFun/PumpSwap-Only Analysis

Generated: 2026-06-19T17:01:04.774Z

Assumptions:
- Fresh Helius wallet scan; one campaign per mint.
- Time window: last 48h.
- Route filter: PumpFun/PumpSwap mints only (`...pump`).
- Entry MC is inferred from CyA's first fill, SOL/USD=69.07, supply=1,000,000,000.
- TP rows sell 100% if target is hit before CyA's first sell; otherwise they fall back to CyA first sell.
- Trailing row uses `50:25,100:75,200:125,300:200,500:350,600:400,800:500,1200:1000` before CyA's first sell.

## Coverage

- Wallet txs fetched: 2251
- Swaps reconstructed: 1706
- Campaigns in analysis window: 269
- MC >= $0 candidates before route filter: 269
- Candidates after route filter: 206
- Measured rows: 99
- Skips: 107
- Median entry MC: $4,288
- Median MFE/MAE: +24.5% / -42.9%

## Target Hit Before CyA First Sell

| Target | Hits | Hit Rate | Median Hit Min |
|---|---:|---:|---:|
| +30% | 44/99 | +44.4% | 0.4 |
| +50% | 28/99 | +28.3% | 0.4 |
| +75% | 18/99 | +18.2% | 0.4 |
| +100% | 13/99 | +13.1% | 1.1 |

## Strategy Comparison

| Strategy | Trades | Win | Avg | Median | PF | Total |
|---|---:|---:|---:|---:|---:|---:|
| mirror CyA first sell | 99 | +45.5% | +11.3% | -2.3% | 1.91 | +1120.0% |
| TP30 sell 100% | 99 | +66.7% | +11.9% | +30.0% | 2.42 | +1176.3% |
| TP50 sell 100% | 99 | +58.6% | +19.5% | +11.2% | 2.99 | +1927.8% |
| TP75 sell 100% | 99 | +53.5% | +18.7% | +2.6% | 2.69 | +1854.2% |
| TP100 sell 100% | 99 | +49.5% | +18.2% | -0.6% | 2.52 | +1802.0% |
| custom trailing | 99 | +58.6% | +16.4% | +11.2% | 2.68 | +1627.8% |

## Trades

| Token | Mint | Entry UTC | Entry MC | CyA Buy | CyA Exit | MFE | MAE | Trailing Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BARRON | `2aKTUD...pump` | 2026-06-19T00:21:49.000Z | $113,555 | 5.007 SOL | -54.6% | +80.1% | -69.3% | +25.0% @ 0m |
| TRENCH | `BzyKa1...pump` | 2026-06-18T20:41:57.000Z | $79,679 | 5.007 SOL | -1.4% | +30.2% | -22.4% | - |
| TRENCHES | `3NMQuw...pump` | 2026-06-18T20:41:51.000Z | $64,219 | 5.007 SOL | +120.1% | +330.8% | -36.6% | +75.0% @ 0m |
| KEVUN | `DXwcaZ...pump` | 2026-06-17T18:02:39.000Z | $51,712 | 5.032 SOL | -11.9% | +24.5% | -25.1% | - |
| DAWN | `EmfAFk...pump` | 2026-06-18T23:42:02.000Z | $44,232 | 5.007 SOL | +3.2% | +56.5% | -45.0% | +25.0% @ 1m |
| CRAFT | `79Jx4S...pump` | 2026-06-19T14:26:54.000Z | $44,194 | 5.007 SOL | -50.5% | +1.9% | -62.1% | - |
| COMOS | `Gn2SDa...pump` | 2026-06-18T22:33:35.000Z | $35,590 | 5.007 SOL | +33.7% | +320.9% | -1.7% | +25.0% @ 1m |
| COLLECTABLE | `BmNASd...pump` | 2026-06-19T14:54:47.000Z | $31,135 | 5.007 SOL | - | +182.9% | -44.0% | +25.0% @ 21m |
| Day | `8vreH8...pump` | 2026-06-17T18:33:07.000Z | $30,544 | 5.032 SOL | -36.8% | +45.1% | -59.6% | - |
| MSTR | `CBxnoa...pump` | 2026-06-18T18:18:34.000Z | $27,099 | 5.007 SOL | +22.4% | +9.5% | -48.0% | - |
| UP | `857tSu...pump` | 2026-06-18T20:05:53.000Z | $23,657 | 5.007 SOL | +15.4% | +10.9% | -34.7% | - |
| VOYAGER | `AWvgKk...pump` | 2026-06-18T23:20:05.000Z | $20,358 | 5.007 SOL | -37.3% | +1.3% | -51.1% | - |
| Asteroid | `HA57Zh...pump` | 2026-06-18T21:40:27.000Z | $19,133 | 5.007 SOL | -33.0% | +11.7% | -77.5% | - |
| ANSEM | `FbBzPW...pump` | 2026-06-18T19:05:14.000Z | $17,899 | 5.007 SOL | -46.4% | +1.1% | -70.6% | - |
| Wersh | `2D1QhV...pump` | 2026-06-17T18:01:30.000Z | $17,691 | 5.030 SOL | -26.7% | +9.5% | -31.2% | - |
| NeGa | `EZamyy...pump` | 2026-06-19T00:07:23.000Z | $17,086 | 5.007 SOL | +35.5% | +41.0% | -31.4% | - |
| HERO | `Ct5ZGE...pump` | 2026-06-19T15:39:35.000Z | $16,447 | 5.007 SOL | -11.8% | +48.8% | -34.3% | - |
| DAWN | `J5ywrH...pump` | 2026-06-18T22:27:45.000Z | $13,535 | 5.007 SOL | -10.8% | +3.6% | -79.5% | - |
| Tagoh | `ByYAq5...pump` | 2026-06-17T16:21:15.000Z | $13,198 | 5.032 SOL | +9.2% | +13.1% | -29.8% | - |
| DAWNNY | `DQtAGs...pump` | 2026-06-18T21:59:40.000Z | $13,078 | 5.007 SOL | -41.1% | +0.0% | -80.3% | - |
| FATTOO | `2bJLva...pump` | 2026-06-19T14:11:54.000Z | $10,185 | 5.007 SOL | +41.2% | +0.0% | -66.0% | - |
| POKE | `DDxA55...pump` | 2026-06-17T20:38:18.000Z | $9,847 | 3.007 SOL | -14.9% | +1.0% | -41.6% | - |
| Anna | `5U7XtJ...pump` | 2026-06-18T21:35:59.000Z | $9,577 | 5.007 SOL | +9.3% | +37.3% | -52.6% | - |
| CELEBRITY | `867NwV...pump` | 2026-06-17T20:00:33.000Z | $9,541 | 5.003 SOL | +33.2% | +8.7% | -39.1% | - |
| Jasper | `8z6P5j...pump` | 2026-06-18T17:39:42.000Z | $9,120 | 5.007 SOL | -34.1% | +63.6% | -46.8% | +25.0% @ 19m |
| ALPACA | `51aYMA...pump` | 2026-06-19T00:48:45.000Z | $9,064 | 5.007 SOL | -8.2% | +47.7% | -17.3% | - |
| mohammad | `FpJfSL...pump` | 2026-06-17T16:26:48.000Z | $8,621 | 5.032 SOL | -3.8% | +67.9% | -15.6% | +25.0% @ 0m |
| Hunch | `BRxt7r...pump` | 2026-06-19T15:02:24.000Z | $8,469 | 5.007 SOL | -52.6% | +2.6% | -77.2% | - |
| Ponyo | `FXcKm6...pump` | 2026-06-17T16:25:16.000Z | $8,367 | 5.032 SOL | -18.4% | +5.4% | -29.6% | - |
| Juanita | `7VKemW...pump` | 2026-06-18T23:54:35.000Z | $8,301 | 5.007 SOL | +11.4% | +0.0% | -69.4% | - |
| Jane | `6QYddd...pump` | 2026-06-17T20:13:57.000Z | $8,011 | 5.003 SOL | -12.3% | +14.2% | -32.8% | - |
| clitcoin | `Brb2MM...pump` | 2026-06-18T19:59:51.000Z | $7,681 | 5.007 SOL | +73.3% | +0.8% | +0.0% | - |
| Sheba | `FdUCnp...pump` | 2026-06-18T17:26:01.000Z | $7,632 | 5.003 SOL | -17.1% | +73.2% | -49.7% | +25.0% @ 13m |
| hold | `DGUL7k...pump` | 2026-06-18T23:25:38.000Z | $7,304 | 5.007 SOL | -4.0% | +0.0% | -73.3% | - |
| Percy | `D3jnVD...pump` | 2026-06-17T20:27:51.000Z | $7,136 | 3.007 SOL | +2.1% | +107.3% | -19.6% | +25.0% @ 0m |
| CLICKISM | `9pLCoq...pump` | 2026-06-19T13:56:57.000Z | $7,049 | 3.007 SOL | -25.1% | +23.3% | -37.8% | - |
| Hydration | `5ZXJMK...pump` | 2026-06-19T00:55:48.000Z | $7,038 | 5.007 SOL | -13.9% | +6.5% | -51.8% | - |
| Citizen | `6FCeoW...pump` | 2026-06-19T04:02:00.000Z | $6,620 | 5.007 SOL | +2.6% | +47.0% | +0.0% | - |
| Cotro | `5fNDto...pump` | 2026-06-18T20:31:34.000Z | $6,579 | 5.007 SOL | -63.4% | +0.0% | -76.4% | - |
| MCAT | `6pTaN8...pump` | 2026-06-19T03:44:49.000Z | $6,280 | 5.007 SOL | -49.0% | +55.2% | -54.5% | +25.0% @ 1m |
| bread | `2GNjBT...pump` | 2026-06-18T21:47:39.000Z | $5,338 | 3.007 SOL | +149.9% | +147.9% | -10.4% | +75.0% @ 0m |
| TAY | `6qBvcm...pump` | 2026-06-18T21:16:23.000Z | $5,152 | 3.007 SOL | +0.7% | +23.7% | -32.9% | - |
| Bitboy | `2xbybp...pump` | 2026-06-18T20:06:57.000Z | $5,143 | 5.007 SOL | -44.4% | +26.1% | -45.0% | - |
| Bartholomew | `GcPPmz...pump` | 2026-06-18T18:44:17.000Z | $4,939 | 3.007 SOL | +5.9% | +106.3% | -4.1% | +75.0% @ 5m |
| MCAT | `5LLNmS...pump` | 2026-06-19T03:37:08.000Z | $4,895 | 5.005 SOL | +15.2% | +71.9% | -26.2% | +25.0% @ 2m |
| Butterfly | `2twAvs...pump` | 2026-06-18T20:13:53.000Z | $4,589 | 3.007 SOL | +53.2% | +9.3% | -38.1% | - |
| bold | `9tvYbZ...pump` | 2026-06-19T14:49:55.000Z | $4,500 | 3.007 SOL | +0.9% | +4.9% | -53.0% | - |
| USPD | `9wHvhk...pump` | 2026-06-19T14:38:18.000Z | $4,472 | 3.007 SOL | -0.6% | +25.8% | -9.7% | - |
| CompCoin | `B5GcQ3...pump` | 2026-06-19T03:15:01.000Z | $4,464 | 3.007 SOL | -19.9% | +31.6% | -33.4% | - |
| LARRY | `6bEQqg...pump` | 2026-06-17T20:45:42.000Z | $4,288 | 3.007 SOL | +11.2% | +34.6% | -30.4% | - |
| gg | `FpGDG2...pump` | 2026-06-18T22:08:39.000Z | $4,202 | 3.007 SOL | -39.5% | +0.2% | -59.7% | - |
| Mizore | `6Qgxuj...pump` | 2026-06-19T13:58:35.000Z | $4,192 | 3.007 SOL | -19.3% | +0.9% | -64.7% | - |
| YOANE | `AiXVEv...pump` | 2026-06-17T19:10:37.000Z | $4,165 | 3.008 SOL | -33.7% | +25.8% | -46.6% | - |
| Onboarding | `92ZLxy...pump` | 2026-06-19T13:36:55.000Z | $3,995 | 3.007 SOL | -46.5% | +0.0% | -63.6% | - |
| MTRG | `9NXV4x...pump` | 2026-06-18T17:47:49.000Z | $3,844 | 3.007 SOL | +687.6% | +32.3% | -2.3% | - |
| DAWNNY | `2WED2W...pump` | 2026-06-18T21:59:18.000Z | $3,841 | 3.008 SOL | -46.3% | +0.0% | -47.2% | - |
| HONEY | `7Adn7u...pump` | 2026-06-19T14:42:52.000Z | $3,744 | 3.007 SOL | -25.2% | +71.2% | -32.4% | +25.0% @ 0m |
| KSPLAY | `FiuuRP...pump` | 2026-06-18T19:32:32.000Z | $3,742 | 5.007 SOL | -39.0% | +37.1% | -55.1% | - |
| C6 | `CecRzG...pump` | 2026-06-19T14:01:53.000Z | $3,699 | 3.007 SOL | -23.5% | +38.8% | -52.5% | - |
| #1 | `C4Q3kx...pump` | 2026-06-19T13:26:41.000Z | $3,679 | 3.008 SOL | +33.1% | +0.2% | -73.2% | - |
| SARAS | `AvL4Uz...pump` | 2026-06-19T14:46:26.000Z | $3,652 | 3.008 SOL | +101.7% | +13.6% | -44.7% | - |
| SICHI | `7SWYEi...pump` | 2026-06-17T18:06:44.000Z | $3,649 | 3.032 SOL | -9.1% | +0.0% | -54.0% | - |
| SILVERGOONER | `BmTEjC...pump` | 2026-06-18T20:27:04.000Z | $3,643 | 3.007 SOL | +9.2% | +59.4% | -43.2% | +25.0% @ 2m |
| CHUCKY | `DWzjr7...pump` | 2026-06-18T19:30:39.000Z | $3,598 | 3.008 SOL | +49.6% | +25.1% | -58.4% | - |
| OpiumChicken | `AtPqyV...pump` | 2026-06-19T14:33:36.000Z | $3,503 | 1.079 SOL | +116.7% | +26.5% | -28.3% | - |
| CCM | `9LoH9P...pump` | 2026-06-19T00:34:50.000Z | $3,442 | 3.007 SOL | -12.6% | +22.0% | -22.3% | - |
| Clitcoin | `ooMnSR...pump` | 2026-06-18T18:55:43.000Z | $3,415 | 3.008 SOL | +80.4% | +88.9% | -17.2% | +25.0% @ 0m |
| #1 | `MXQcb9...pump` | 2026-06-19T03:24:22.000Z | $3,380 | 3.008 SOL | +4.8% | +139.5% | -65.9% | +75.0% @ 5m |
| Hero | `Btfqie...pump` | 2026-06-19T01:04:46.000Z | $3,287 | 3.007 SOL | +157.0% | +148.4% | +0.0% | +75.0% @ 0m |
| ANIMALS | `VhkSVj...pump` | 2026-06-18T22:25:31.000Z | $3,165 | 3.007 SOL | -23.8% | +0.8% | -29.3% | - |
| LEWINE | `FkxonF...pump` | 2026-06-17T20:43:53.000Z | $3,163 | 3.007 SOL | -23.0% | +3.6% | -41.8% | - |
| FRANKLIN | `73ksFw...pump` | 2026-06-17T20:24:39.000Z | $3,123 | 3.007 SOL | -14.9% | +15.2% | -32.8% | - |
| RICH | `F6VFdg...pump` | 2026-06-19T14:28:47.000Z | $3,087 | 3.007 SOL | +2.6% | +0.0% | -45.4% | - |
| niggacoin | `EStGTg...pump` | 2026-06-19T01:15:05.000Z | $2,992 | 3.007 SOL | -14.8% | +132.2% | -51.7% | +25.0% @ 1m |
| Fan | `6Q9XEK...pump` | 2026-06-17T19:08:05.000Z | $2,927 | 3.008 SOL | +10.5% | +31.3% | -34.2% | - |
| PINGO | `9QEmaV...pump` | 2026-06-19T00:01:32.000Z | $2,923 | 3.007 SOL | +13.4% | +0.0% | -59.6% | - |
| esh | `7dA4HN...pump` | 2026-06-17T19:19:35.000Z | $2,872 | 3.008 SOL | +39.6% | +61.7% | -16.5% | +25.0% @ 1m |
| BOINKS | `J3BGRu...pump` | 2026-06-18T18:50:55.000Z | $2,841 | 3.008 SOL | -33.7% | +151.9% | -42.9% | +75.0% @ 0m |
| WEEB | `GxbETz...pump` | 2026-06-17T17:06:25.000Z | $2,770 | 3.032 SOL | -17.0% | +91.1% | -59.9% | +25.0% @ 1m |
| DAWNNY | `9wYLEq...pump` | 2026-06-18T22:00:57.000Z | $2,766 | 3.008 SOL | -7.7% | +13.6% | -61.1% | - |
| Niggi | `88x9ZV...pump` | 2026-06-19T14:08:25.000Z | $2,763 | 3.008 SOL | -9.4% | +16.8% | -22.5% | - |
| DAD | `BYfT3v...pump` | 2026-06-19T13:31:16.000Z | $2,763 | 3.008 SOL | -14.3% | +1.3% | -53.2% | - |
| Juanita | `CwL29J...pump` | 2026-06-18T23:55:35.000Z | $2,744 | 3.008 SOL | -8.9% | +77.0% | -62.3% | +25.0% @ 0m |
| TNB | `5WXXEC...pump` | 2026-06-18T18:40:57.000Z | $2,728 | 3.007 SOL | -0.0% | +154.3% | -45.4% | +75.0% @ 0m |
| Sheba | `7kKZWH...pump` | 2026-06-18T17:32:21.000Z | $2,683 | 3.003 SOL | +40.1% | +38.1% | -37.1% | - |
| way | `DVLZeW...pump` | 2026-06-19T13:41:52.000Z | $2,609 | 3.007 SOL | +27.1% | +10.9% | -52.6% | - |
| SHEEP | `HWakxK...pump` | 2026-06-19T00:28:43.000Z | $2,580 | 3.007 SOL | +16.9% | +24.4% | -45.8% | - |
| QWANT | `EHkYhw...pump` | 2026-06-19T13:46:56.000Z | $2,578 | 3.008 SOL | +93.7% | +148.9% | -0.5% | +75.0% @ 0m |
| poopwich | `D9FLT8...pump` | 2026-06-18T20:04:41.000Z | $2,562 | 3.008 SOL | +20.2% | +7.0% | -50.5% | - |
| FPS | `AwnUtD...pump` | 2026-06-18T18:46:38.000Z | $2,552 | 3.008 SOL | -2.4% | +100.6% | -42.4% | +25.0% @ 0m |
| ALONA | `9q3sdU...pump` | 2026-06-18T22:26:53.000Z | $2,550 | 3.008 SOL | +17.8% | +35.0% | -28.9% | - |
| FENN | `BN7t5A...pump` | 2026-06-19T03:12:10.000Z | $2,517 | 3.007 SOL | +17.1% | +11.7% | -47.3% | - |
| BP | `CWi3yB...pump` | 2026-06-18T22:30:51.000Z | $2,480 | 3.008 SOL | -3.1% | +1.1% | -34.4% | - |
| nigga  | `9Buhdx...pump` | 2026-06-19T01:12:59.000Z | $2,345 | 6.010 SOL | +38.3% | +64.2% | -20.4% | +25.0% @ 0m |
| dogwifihat | `3YiNLm...pump` | 2026-06-18T22:11:18.000Z | $2,312 | 3.008 SOL | -17.2% | +3.8% | -44.9% | - |
| SETOSHI | `8NzQmy...pump` | 2026-06-18T17:52:56.000Z | $2,215 | 3.008 SOL | -2.1% | +81.4% | -30.5% | +25.0% @ 0m |
| HAMMY | `FYeKyS...pump` | 2026-06-18T23:44:55.000Z | $2,213 | 3.008 SOL | +4.7% | +15.3% | -36.8% | - |
| Bartholom | `AcWexg...pump` | 2026-06-18T18:43:58.000Z | $2,179 | 3.008 SOL | +20.2% | +0.0% | -34.6% | - |
| CompCoin | `96Udi6...pump` | 2026-06-19T03:16:24.000Z | $2,173 | 3.007 SOL | -2.3% | +32.0% | -33.6% | - |

## Skips

- `5p1qSP...pump`: missing entry/path candles across 1 pair(s): pumpfun:HSUP2i...kjMU no entry/path candles
- `Dr8ZFD...pump`: missing entry/path candles across 1 pair(s): pumpfun:JE6KW6...zEEE no entry/path candles
- `DaTC7X...pump`: missing entry/path candles across 1 pair(s): pumpfun:6JpSGr...sSM1 no entry/path candles
- `LdrVtN...pump`: missing entry/path candles across 1 pair(s): pumpfun:FpY9bG...AidJ no entry/path candles
- `3exjVQ...pump`: missing entry/path candles across 1 pair(s): pumpfun:8Sv4JN...yZPR no entry/path candles
- `CoEBgL...pump`: missing entry/path candles across 1 pair(s): pumpfun:BGT19Z...awAB no entry/path candles
- `6D6Mwx...pump`: missing entry/path candles across 1 pair(s): pumpfun:2FkLp3...D5X2 no entry/path candles
- `CUkE1K...pump`: missing entry/path candles across 1 pair(s): pumpfun:ACz2K6...WH7o no entry/path candles
- `3UJebN...pump`: missing entry/path candles across 1 pair(s): pumpfun:CDndNN...jCYB no entry/path candles
- `BV8a6a...pump`: missing entry/path candles across 1 pair(s): pumpfun:EC7CgZ...cBT9 no entry/path candles
- `vv1dX6...pump`: missing entry/path candles across 1 pair(s): pumpfun:DcoT7b...TfHM no entry/path candles
- `4syAGd...pump`: missing entry/path candles across 1 pair(s): pumpfun:8CgmRe...uWxW no entry/path candles
- `BgFQgC...pump`: missing entry/path candles across 1 pair(s): pumpfun:k9JNMT...c7iD no entry/path candles
- `DobFUy...pump`: missing entry/path candles across 1 pair(s): pumpfun:9iHv5t...tnwU no entry/path candles
- `GWf6p9...pump`: missing entry/path candles across 1 pair(s): pumpfun:8FfLGu...LdFc no entry/path candles
- `8qFu6C...pump`: missing entry/path candles across 1 pair(s): pumpfun:EEWXBG...Ag9g no entry/path candles
- `DPcF94...pump`: missing entry/path candles across 1 pair(s): pumpfun:Gt7SnA...M76B no entry/path candles
- `z6f6N5...pump`: missing entry/path candles across 1 pair(s): pumpfun:9Nw9b3...b7ir no entry/path candles
- `CFD536...pump`: missing entry/path candles across 1 pair(s): pumpfun:5RXCEe...88i2 no entry/path candles
- `3GED5d...pump`: missing entry/path candles across 1 pair(s): pumpfun:GD6pQA...TGTu no entry/path candles
- `82NuJq...pump`: missing entry/path candles across 1 pair(s): pumpfun:EGcdjT...k2Ft no entry/path candles
- `57cjUR...pump`: missing entry/path candles across 1 pair(s): pumpfun:AjJxYQ...63cq no entry/path candles
- `uVhhVQ...pump`: missing entry/path candles across 1 pair(s): pumpfun:BXqmWJ...8qDB no entry/path candles
- `7W6o7P...pump`: missing entry/path candles across 1 pair(s): pumpfun:BeG49U...K7cP no entry/path candles
- `AgzpUq...pump`: missing entry/path candles across 1 pair(s): pumpfun:Gboxud...cmMd no entry/path candles
- `8Jorxj...pump`: missing entry/path candles across 1 pair(s): pumpfun:BUUU8w...pW4p no entry/path candles
- `BrPntE...pump`: missing entry/path candles across 1 pair(s): pumpfun:4Bp9jB...DoPg no entry/path candles
- `6sLLyf...pump`: missing entry/path candles across 2 pair(s): pumpswap:788Th7...T2Xx no entry/path candles; pumpfun:Hah72t...g63y no entry/path candles
- `EeAS1V...pump`: missing entry/path candles across 1 pair(s): pumpfun:4SfVpe...6r7u no entry/path candles
- `GAeFsj...pump`: missing entry/path candles across 1 pair(s): pumpfun:BdMzCU...NmZs no entry/path candles
- `EPnQ9e...pump`: missing entry/path candles across 1 pair(s): pumpfun:y4p6qB...rWt9 no entry/path candles
- `3BeiuS...pump`: missing entry/path candles across 1 pair(s): pumpfun:4XtNPY...GnBF no entry/path candles
- `BV8chQ...pump`: missing entry/path candles across 1 pair(s): pumpfun:FW6fBq...2pxD no entry/path candles
- `Ag7EKa...pump`: missing entry/path candles across 1 pair(s): unknown:ft3evq...x3oN no entry/path candles
- `FTcLTV...pump`: missing entry/path candles across 1 pair(s): pumpfun:DVd1aJ...EsWf no entry/path candles
- `4TCdNZ...pump`: missing entry/path candles across 1 pair(s): pumpfun:6vTW3s...gysS no entry/path candles
- `5dbodv...pump`: missing entry/path candles across 1 pair(s): pumpfun:8gM8Pt...bmgn no entry/path candles
- `EiVuRs...pump`: missing entry/path candles across 1 pair(s): pumpfun:8i8V1y...TjZH no entry/path candles
- `29Bhih...pump`: missing entry/path candles across 4 pair(s): pumpswap:4nfU7d...tmXV no entry/path candles; meteora:Dy1ZzB...QmK4 no entry/path candles; meteora:5yYyik...w3qy no entry/path candles; pumpfun:AhuXBv...jsu3 no entry/path candles
- `F1ygN2...pump`: missing entry/path candles across 1 pair(s): pumpfun:7CPZwb...uSvK no entry/path candles
- `6xRmpS...pump`: missing entry/path candles across 1 pair(s): pumpfun:DqKvxj...1sAk no entry/path candles
- `24gh4D...pump`: missing entry/path candles across 1 pair(s): pumpfun:BdwTZK...SB9i no entry/path candles
- `DmXdDz...pump`: missing entry/path candles across 1 pair(s): pumpfun:2UJocm...MbMi no entry/path candles
- `34ptBJ...pump`: missing entry/path candles across 1 pair(s): pumpfun:AgYFor...hz3a no entry/path candles
- `4odydz...pump`: missing entry/path candles across 1 pair(s): pumpfun:3pXn9f...fDaG no entry/path candles
- `BtKrmq...pump`: missing entry/path candles across 1 pair(s): pumpfun:7qTd4z...YGk2 no entry/path candles
- `2rCeo4...pump`: missing entry/path candles across 1 pair(s): pumpfun:Cd46u4...dZGC no entry/path candles
- `3RD5Rq...pump`: missing entry/path candles across 1 pair(s): pumpfun:Enr3Ee...U6RW no entry/path candles
- `95oLHJ...pump`: missing entry/path candles across 1 pair(s): pumpfun:AL1rTL...NwYZ no entry/path candles
- `Nn1eM2...pump`: missing entry/path candles across 2 pair(s): pumpswap:2tToJM...iWmJ no entry/path candles; pumpfun:6AvmRP...ErxC no entry/path candles
- `5bwLWa...pump`: missing entry/path candles across 1 pair(s): pumpfun:9BZJ3v...gx7s no entry/path candles
- `BjNeR4...pump`: missing entry/path candles across 1 pair(s): pumpfun:DjMisQ...U7xb no entry/path candles
- `ETtABB...pump`: missing entry/path candles across 1 pair(s): pumpfun:7zdGwv...7YfL no entry/path candles
- `Bamt4N...pump`: missing entry/path candles across 1 pair(s): pumpfun:4dxrTw...Hvky no entry/path candles
- `26yd9x...pump`: missing entry/path candles across 1 pair(s): pumpfun:AN6fy3...Z6TT no entry/path candles
- `9Areqe...pump`: missing entry/path candles across 1 pair(s): pumpfun:2Z2sTq...gCjk no entry/path candles
- `6LLpcG...pump`: missing entry/path candles across 1 pair(s): pumpfun:9EmtDj...AN7J no entry/path candles
- `6bZKdL...pump`: missing entry/path candles across 3 pair(s): pumpswap:ENgMTt...sPZD no entry/path candles; meteora:H6RSkY...hAj3 no entry/path candles; pumpfun:54bi6W...JbAa no entry/path candles
- `7eLYwh...pump`: missing entry/path candles across 2 pair(s): meteora:5e1Qbw...cWFB no entry/path candles; pumpfun:5wzKjb...C3NA no entry/path candles
- `A1srGG...pump`: missing entry/path candles across 1 pair(s): pumpfun:4eyEHL...28g5 no entry/path candles
- `EXDG69...pump`: no DexScreener/Gecko pair
- `8gZW1Y...pump`: missing entry/path candles across 1 pair(s): pumpfun:wVH9V6...xSEr no entry/path candles
- `DFAM6Q...pump`: no DexScreener/Gecko pair
- `AX37eZ...pump`: no DexScreener/Gecko pair
- `Gbukm1...pump`: no DexScreener/Gecko pair
- `Fr4Ngn...pump`: no DexScreener/Gecko pair
- `8NThYF...pump`: missing entry/path candles across 1 pair(s): pumpfun:87DN39...eWx1 no entry/path candles
- `FLgDJa...pump`: no DexScreener/Gecko pair
- `DAefoy...pump`: no DexScreener/Gecko pair
- `FPWLCB...pump`: no DexScreener/Gecko pair
- `EjzQAb...pump`: no DexScreener/Gecko pair
- `7ewT8c...pump`: missing entry/path candles across 1 pair(s): pumpfun:3eWNpL...s53H no entry/path candles
- `3qKGby...pump`: no DexScreener/Gecko pair
- `fMvshN...pump`: missing entry/path candles across 1 pair(s): pumpfun:6KtjSe...BTQk no entry/path candles
- `4Sfony...pump`: missing entry/path candles across 1 pair(s): pumpfun:3RqJyM...tCY1 no entry/path candles
- `2u1qpe...pump`: no DexScreener/Gecko pair
- `9UH6f8...pump`: no DexScreener/Gecko pair
- `ED6gaT...pump`: no DexScreener/Gecko pair
- `7KLNsc...pump`: no DexScreener/Gecko pair
- `Gy5FHp...pump`: no DexScreener/Gecko pair
- `7c2sea...pump`: no DexScreener/Gecko pair
- `5cvEH5...pump`: missing entry/path candles across 1 pair(s): pumpfun:GFR8Lo...1GhU no entry/path candles
- `BQEWGk...pump`: no DexScreener/Gecko pair
- `4Dkdxa...pump`: no DexScreener/Gecko pair
- `Fzqa8L...pump`: no DexScreener/Gecko pair
- `HKCZgM...pump`: no DexScreener/Gecko pair
- `9eC27d...pump`: missing entry/path candles across 1 pair(s): pumpfun:epjyrQ...5qMz no entry/path candles
- `GFW2z6...pump`: no DexScreener/Gecko pair
- `4obREZ...pump`: no DexScreener/Gecko pair
- `59oVo7...pump`: no DexScreener/Gecko pair
- `FTmneF...pump`: no DexScreener/Gecko pair
- `iaGJA8...pump`: missing entry/path candles across 2 pair(s): pumpswap:BUACYU...PSWY no entry/path candles; meteora:EEDUJU...BSSP no entry/path candles
- `XVZ5i1...pump`: missing entry/path candles across 1 pair(s): pumpswap:CHGFFD...RRtg no entry/path candles
- `35p9qX...pump`: no DexScreener/Gecko pair
- `CbSNfF...pump`: no DexScreener/Gecko pair
- `6VCRcz...pump`: no DexScreener/Gecko pair
- `BhQRqW...pump`: no DexScreener/Gecko pair
- `2a3LXG...pump`: no DexScreener/Gecko pair
- `Hv7ASM...pump`: no DexScreener/Gecko pair
- `GCfhLn...pump`: no DexScreener/Gecko pair
- `CBMctA...pump`: no DexScreener/Gecko pair
- `G4nJ8p...pump`: missing entry/path candles across 1 pair(s): pumpswap:A4DwCb...B1pE no entry/path candles
- `GZsQVV...pump`: no DexScreener/Gecko pair
- `DpV4GL...pump`: no DexScreener/Gecko pair
- `7NTmUi...pump`: no DexScreener/Gecko pair
- `GXB3aN...pump`: missing entry/path candles across 1 pair(s): pumpfun:4LEgsQ...FGsK no entry/path candles
- `FsuyCc...pump`: no DexScreener/Gecko pair
