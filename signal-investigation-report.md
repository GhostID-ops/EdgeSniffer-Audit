# Signal Investigation Report

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
Generated: 2026-04-27T23:23:32.290Z

## Executive Read

The sampled wallet is profitable in the captured window: 58 wins, 38 losses, 60.4% win rate, 168.381 SOL net on 470.923 SOL spent.
The execution profile is highly patterned: 0.265 SOL x277, 3.02 SOL x90, 5.02 SOL x21, 6.02 SOL x2 dominate the ladder.
The edge is likely a two-part system: a discovery/filter signal that chooses mints, then a fast fixed-size laddering and partial-exit execution system.

## Core Metrics

| metric | value |
| --- | --- |
| campaigns | 98 |
| closed campaigns | 88 |
| open or residue campaigns | 10 |
| median buy size | 0.265 SOL |
| p75 buy size | 3.017 SOL |
| median add interval | 4.0 sec |
| median first sell delay | 34.5 sec |
| median winner ROI | 36.7% |
| median loser ROI | -15.9% |

## Standard Buy Sizes

| size | count |
| --- | --- |
| 0.265 | 277 |
| 3.02 | 90 |
| 5.02 | 21 |
| 6.02 | 2 |
| 3.87 | 1 |
| 0.150 | 1 |
| 2.78 | 1 |
| 1.82 | 1 |

## By Size Profile

| bucket | campaigns | wins | losses | winRate | spent | net | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| conviction | 75 | 44 | 31 | 58.7% | 298.12 | 74.81 | 25.1% |
| high conviction | 19 | 10 | 9 | 52.6% | 167.01 | 62.76 | 37.6% |
| probe | 3 | 3 | 0 | 100.0% | 3.45 | 30.25 | 878.1% |
| mid | 1 | 1 | 0 | 100.0% | 2.35 | 0.56 | 23.9% |

## By Ladder Profile

| bucket | campaigns | wins | losses | winRate | spent | net | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rapid multi-ladder | 7 | 6 | 1 | 85.7% | 64.45 | 87.73 | 136.1% |
| extended ladder | 20 | 12 | 8 | 60.0% | 121.11 | 33.36 | 27.5% |
| quick add | 40 | 18 | 22 | 45.0% | 165.30 | 24.73 | 15.0% |
| single entry | 19 | 13 | 6 | 68.4% | 61.33 | 11.69 | 19.1% |
| measured add | 12 | 9 | 3 | 75.0% | 58.74 | 10.86 | 18.5% |

## Top Winners

| mint | buys | sells | spent | net | roi | firstBuy | maxBuy | ladder | holdSec |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| oHSWp1...pump | 17 | 6 | 10.01 | 29.87 | 298.5% | 3.017 | 3.017 | rapid multi-ladder | 689 |
| EJn4RN...pump | 16 | 8 | 13.74 | 26.40 | 192.1% | 5.017 | 5.017 | rapid multi-ladder | 838 |
| 2UPV97...o6Dw | 8 | 5 | 7.62 | 19.13 | 251.0% | 3.015 | 3.017 | rapid multi-ladder | 146 |
| HudM42...pump | 3 | 4 | 3.55 | 17.95 | 506.1% | 3.017 | 3.017 | quick add | 89 |
| 2KnytD...pump | 4 | 2 | 1.06 | 16.44 | 1551.2% | 0.265 | 0.265 | quick add | 26 |
| FUTK4c...pump | 7 | 2 | 1.86 | 10.85 | 584.7% | 0.265 | 0.265 | extended ladder | 65 |
| DRRR82...pump | 6 | 2 | 4.34 | 9.06 | 208.7% | 3.017 | 3.017 | extended ladder | 104 |
| ErfTQc...pump | 11 | 2 | 5.67 | 6.89 | 121.6% | 3.017 | 3.017 | rapid multi-ladder | 161 |
| Hvd3iu...pump | 3 | 1 | 5.55 | 4.94 | 89.1% | 5.017 | 5.017 | quick add | 15 |
| 7FPotR...VQbr | 15 | 4 | 11.48 | 4.59 | 40.0% | 3.017 | 5.015 | rapid multi-ladder | 171 |
| Fh3wcU...dSXL | 4 | 2 | 3.81 | 4.50 | 118.0% | 3.018 | 3.018 | quick add | 36 |
| 6VQGP2...pump | 7 | 3 | 4.61 | 4.35 | 94.5% | 3.018 | 3.018 | extended ladder | 59 |

## Top Losers

| mint | buys | sells | spent | net | roi | firstBuy | maxBuy | ladder | holdSec |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8UP6Aj...pump | 3 | 0 | 6.55 | -6.56 | -100.2% | 6.017 | 6.017 | quick add |  |
| 54L2bc...bonk | 2 | 0 | 6.03 | -6.04 | -100.2% | 3.017 | 3.017 | quick add |  |
| gBWb8Y...ZtAP | 12 | 1 | 5.93 | -3.31 | -55.8% | 3.017 | 3.017 | rapid multi-ladder | 298 |
| FPUMvk...pump | 2 | 1 | 8.03 | -3.11 | -38.8% | 5.017 | 5.017 | measured add | 27 |
| 3d6LS2...pump | 5 | 3 | 20.33 | -3.04 | -14.9% | 5.017 | 5.017 | extended ladder | 828 |
| 4E1VWF...pump | 3 | 1 | 3.55 | -2.10 | -59.3% | 3.017 | 3.017 | quick add | 13 |
| BSQtQy...pump | 9 | 1 | 7.14 | -2.07 | -29.0% | 5.018 | 5.018 | extended ladder | 258 |
| CXTBeh...pump | 4 | 1 | 6.56 | -1.45 | -22.2% | 3.017 | 3.017 | quick add | 5 |
| CJCPDU...pump | 3 | 1 | 3.55 | -1.42 | -40.1% | 3.018 | 3.018 | quick add | 24 |
| Ar9uSW...birb | 1 | 1 | 3.02 | -1.37 | -45.3% | 3.017 | 3.017 | single entry | 96 |
| 7U9MY9...pump | 4 | 1 | 3.81 | -1.33 | -34.8% | 3.017 | 3.017 | quick add | 17 |
| 8y7vEr...pump | 5 | 2 | 3.84 | -1.29 | -33.5% | 2.775 | 2.775 | extended ladder | 60 |

## Working Strategy Model

1. Treat 0.265 SOL as the scout/probe unit. It appears far too often to be discretionary sizing.
2. Treat roughly 3.02 SOL and 5.02 SOL as conviction units. These are likely triggered after the token passes a confirmation threshold.
3. First sells often occur within seconds, so exits are part of the signal. Copying only buys would miss the strategy.
4. The biggest winners are not single entries; they are rapid multi-ladders where he keeps adding while the trade is working.
5. The hard edge to uncover is the confirmation threshold that upgrades a token from probe to conviction.

## Deep Chain Attribution

Deep mode analyzed 78/98 requested mints with cached mint-address history. Stopped early: Helius quota reached: 429 {"jsonrpc":"2.0","error":{"code":-32429,"message":"max usage reached"}}.

### Signal Tests

| bucket | campaigns | wins | losses | winRate | net | avgRank |
| --- | --- | --- | --- | --- | --- | --- |
| first wave: rank 1-2 | 32 | 24 | 8 | 75.0% | 135.08 | 1.0 |
| early crowd: rank 3-25 | 33 | 14 | 19 | 42.4% | -1.44 | 13.8 |
| late crowd: rank 26+ | 13 | 9 | 4 | 69.2% | 34.13 | 77.3 |
| entry age <=10s | 63 | 39 | 24 | 61.9% | 138.96 | 8.1 |
| entry age 11-60s | 12 | 7 | 5 | 58.3% | 27.74 | 42.8 |
| entry age >60s | 3 | 1 | 2 | 33.3% | 1.07 | 156.0 |
| no pre-source actors | 32 | 24 | 8 | 75.0% | 135.08 | 1.0 |
| has pre-source actors | 46 | 23 | 23 | 50.0% | 32.69 | 31.8 |

### History Quality

| quality | campaigns | wins | losses | winRate | net |
| --- | --- | --- | --- | --- | --- |
| good | 46 | 23 | 23 | 50.0% | 32.69 |
| partial | 32 | 24 | 8 | 75.0% | 135.08 |

### Recurring Pre-Source Actors

| actor | campaigns | wins | losses | net | avgLeadSec | examples |
| --- | --- | --- | --- | --- | --- | --- |
| GhiJGK...DHzM | 21 | 10 | 11 | -5.20 | 9.2 | 8UP6Aj...pump, Bm81CJ...pump, CWTigf...pump, CXTBeh...pump |
| odin3j...gemd | 11 | 5 | 6 | 7.65 | 10.6 | DRRR82...pump, AMBANU...pump, CJCPDU...pump, EHumjH...pump |
| BnHZ99...obKC | 11 | 4 | 7 | -2.04 | 5.5 | AMBANU...pump, H6WqHf...pump, CJCPDU...pump, 8y7vEr...pump |
| odinqZ...JLaR | 10 | 6 | 4 | 28.61 | 12.0 | 2KnytD...pump, FUTK4c...pump, Bm81CJ...pump, 7dL4P6...pump |
| EgQX9R...77aq | 8 | 5 | 3 | 5.11 | 7.0 | DRRR82...pump, 8UP6Aj...pump, Bm81CJ...pump, CJCPDU...pump |
| FAsTGa...W7PK | 8 | 6 | 2 | 4.27 | 4.4 | EbTaqg...bj7x, H6WqHf...pump, 8y7vEr...pump, 5wvJCw...pump |
| odindb...rG76 | 7 | 4 | 3 | 1.59 | 2.1 | EbTaqg...bj7x, H6WqHf...pump, 8y7vEr...pump, D5E79y...pump |
| ADkquS...sTDs | 7 | 4 | 3 | -4.78 | 4.6 | 8UP6Aj...pump, 2LXBtf...pump, CJCPDU...pump, BAedLY...kfUi |
| haqqiU...QgWq | 6 | 6 | 0 | 9.15 | 3.8 | Bm81CJ...pump, 2LXBtf...pump, AMBANU...pump, CnogVn...pump |
| 8gQGv8...NrAW | 6 | 4 | 2 | 3.62 | 211.5 | 7tJTnM...pump, 2LXBtf...pump, CJCPDU...pump, HFMxeP...pump |
| WLHv2U...JVVh | 6 | 2 | 4 | -5.81 | 4.7 | 54L2bc...bonk, uuKtEf...birb, EBPhMR...bonk, Bdp5x2...bonk |
| Hqu85s...cVEa | 5 | 4 | 1 | 15.53 | 14.8 | DRRR82...pump, Bm81CJ...pump, 7tJTnM...pump, 2LXBtf...pump |
| C4JRuZ...xxAf | 5 | 4 | 1 | 14.00 | 10.8 | DRRR82...pump, Bm81CJ...pump, 2LXBtf...pump, BAedLY...kfUi |
| 5Y7L4P...QFxa | 5 | 3 | 2 | 1.48 | 254.8 | 7tJTnM...pump, CJCPDU...pump, HFMxeP...pump, Ejti7z...birb |
| ARu4n5...5SZn | 5 | 2 | 3 | -2.01 | 112.8 | 54L2bc...bonk, Bm81CJ...pump, 7tJTnM...pump, CZCddw...pump |

### Repeated Possible Creators

| creator | campaigns | wins | losses | winRate | net | examples |
| --- | --- | --- | --- | --- | --- | --- |
| odinqZ...JLaR | 4 | 2 | 2 | 50.0% | -0.10 | 7dL4P6...pump, BSQtQy...pump, HwLyiZ...RQ33, CqwXQ3...pump |
| GhiJGK...DHzM | 3 | 0 | 3 | 0.0% | -2.85 | EHumjH...pump, 5ngfrg...pump, BAedLY...kfUi |

### Entry-Age Snapshot

| mint | net | entryAgeSec | buyerRank | preActors | closestActors |
| --- | --- | --- | --- | --- | --- |
| oHSWp1...pump | 29.87 | 0 | 1 | 0 |  |
| EJn4RN...pump | 26.40 | 0 | 1 | 0 |  |
| 2UPV97...o6Dw | 19.13 | 0 | 1 | 0 |  |
| HudM42...pump | 17.95 | 0 | 1 | 0 |  |
| 2KnytD...pump | 16.44 | 24 | 43 | 42 | DULTc1...tpmM, 8p9KLm...smyk, M6iwwa...VR1v, 4ySfsX...dWfS, BJscDF...6Vzx |
| FUTK4c...pump | 10.85 | 7 | 15 | 14 | Gygj9Q...BUpA, BwWK17...de6s, 2VTtis...Y6Fa, 7tbKxU...1ZHn, FZz6is...2WW4 |
| DRRR82...pump | 9.06 | 25 | 123 | 122 | EmZVHr...Zejq, GfXUFa...GCcW, 3N8zvL...edu6, EhFVSU...5N9o, 6gVu5N...DJNj |
| ErfTQc...pump | 6.89 | 0 | 1 | 0 |  |
| 8UP6Aj...pump | -6.56 | 4 | 19 | 18 | AqmyUa...a4Zj, EEAag8...Ai88, 2xzNPf...9abJ, 5KcqJ7...kyp3, 99i9uV...pDYQ |
| 54L2bc...bonk | -6.04 | 7 | 22 | 21 | BZSi16...BJVZ, WLHv2U...JVVh, 2xgaam...GAuH, PCsv7S...wa8R, 6JM6UQ...gdB5 |
| Hvd3iu...pump | 4.94 | 0 | 1 | 0 |  |
| 7FPotR...VQbr | 4.59 | 0 | 1 | 0 |  |
| Fh3wcU...dSXL | 4.50 | 0 | 1 | 0 |  |
| 6VQGP2...pump | 4.35 | 0 | 1 | 0 |  |
| EnrsEd...pump | 4.15 | 0 | 1 | 0 |  |
| 5suCP7...pump | 3.54 | 0 | 1 | 0 |  |
| 2QqbUz...4TM9 | 3.45 | 0 | 1 | 0 |  |
| AmSN2u...pump | 3.40 | 3 | 11 | 10 | GEqnay...bb1y, BsqPqH...vqjv, Gq1RQQ...gyRU, Fkemk4...Ya9d, 9oieEB...ortJ |
| gBWb8Y...ZtAP | -3.31 | 0 | 1 | 0 |  |
| FPUMvk...pump | -3.11 | 0 | 1 | 0 |  |

## Next Tests

1. Run deep mode over all 98 mints and inspect recurring pre-source actors.
2. Add a live shadow mode that detects his first probe and records what happened in the preceding 60 seconds.
3. Backtest copy latency: 1s, 3s, 5s, 10s after his buy, with your intended SOL size.
4. Backtest a safer strategy that copies only conviction adds after a successful probe instead of copying every tiny entry.
