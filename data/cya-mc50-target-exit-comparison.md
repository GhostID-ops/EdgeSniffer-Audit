# CyA MC >= $50k Target vs Source Exit Analysis

Source wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Assumptions:
- One row per token campaign, deduped by mint.
- Entry MC filter is source-fill implied MC: first-buy SOL spent x cached SOL/USD / first-buy tokens x 1B supply.
- Source exit is the first CyA sell after the first buy.
- TP strategies sell 100% at the target only if the target is hit before that first CyA sell; otherwise they fall back to the same first-sell source exit.

## Coverage

- Wallet tx cache: 10,000 txs
- Swaps reconstructed: 7,329
- Token campaigns: 1,023
- Source-fill MC >= $50,000 candidates: 27
- Measured rows: 15
- Skipped candidates: 12
- SOL/USD used: $72.54
- Entry MC median: $136,967
- Entry MC p25/p75: $84,941 / $208,568

## Target Hit Before Source Exit

| Target | Hits | Hit Rate | Median Hit Minutes |
|---|---:|---:|---:|
| +30% | 6/15 | 40.0% | 0.4 |
| +50% | 4/15 | 26.7% | 3.9 |
| +75% | 2/15 | 13.3% | 1.8 |
| +100% | 1/15 | 6.7% | 3.5 |

## Exit Comparison

| Strategy | Trades | Avg | Median | Win Rate | PF | Total | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| mirror CyA first sell | 15 | -0.6% | -12.8% | 26.7% | 0.95 | -9.0% | -117.2% |
| TP30 sell 100% | 15 | 11.1% | 0.1% | 53.3% | 2.94 | 166.6% | -50.3% |
| TP50 sell 100% | 15 | 9.5% | -0.7% | 40.0% | 2.10 | 142.7% | -94.8% |
| TP75 sell 100% | 15 | 2.8% | -12.8% | 26.7% | 1.23 | 42.2% | -117.2% |
| TP100 sell 100% | 15 | 5.7% | -12.8% | 26.7% | 1.47 | 85.0% | -117.2% |

## Trades

| Token | Source-Fill Entry MC | Entry SOL | Source Exit | Hit +30/+50/+75/+100 | Hold Min |
|---|---:|---:|---:|---|---:|
| RIZZMAS (`ADA9mt...pump`) | $511,343 | 5.00 | 0.1% | N / N / N / N | 2 |
| CHUD (`21rKrt...pump`) | $460,029 | 5.00 | -0.6% | N / N / N / N | 1 |
| Chaton (`7kkQU6...i7i3`) | $305,538 | 5.00 | -14.1% | N / N / N / N | 1 |
| GAYLON (`pu4obk...pump`) | $266,817 | 5.00 | -2.5% | N / N / N / N | 0 |
| INDEX (`3V3h9a...BAGS`) | $208,568 | 5.03 | -17.9% | Y / N / N / N | 2 |
| OGFLOKI (`4gZ8BD...pump`) | $197,338 | 5.00 | -28.1% | Y / Y / N / N | 12 |
| BAG (`H2wiDu...BAGS`) | $173,645 | 10.03 | 5.9% | Y / Y / Y / Y | 37 |
| H4STE (`2vx5rq...pump`) | $136,967 | 5.00 | 92.8% | Y / Y / Y / N | 0 |
| MBGA (`dFybjf...pump`) | $110,375 | 5.00 | -22.4% | Y / Y / N / N | 141 |
| Berrun (`Yc3WKp...pump`) | $105,198 | 5.00 | -17.7% | N / N / N / N | 12 |
| Prince (`BFM8az...pump`) | $94,037 | 5.00 | -0.7% | N / N / N / N | 5 |
| MUTUMBO (`4StuLH...pump`) | $84,941 | 5.00 | -26.1% | Y / N / N / N | 2 |
| 1B (`2Mcwcc...pump`) | $81,194 | 5.00 | -12.8% | N / N / N / N | 3 |
| JITO (`DuamrF...pump`) | $60,936 | 5.00 | -37.6% | N / N / N / N | 3 |
| bagwork (`FWmq7B...pump`) | $50,451 | 5.00 | 72.5% | N / N / N / N | 0 |

## Skips

- HH3Bt7...BAGS: missing entry/source-exit candle data
- 5HDE96...BAGS: missing entry/source-exit candle data
- BW7dRJ...BAGS: missing entry/source-exit candle data
- 8J69rb...pump: missing entry/source-exit candle data
- 9cRCn9...pump: missing entry/source-exit candle data
- EUwXuw...pump: missing entry/source-exit candle data
- AvxFBj...pump: missing entry/source-exit candle data
- 8MifqH...pump: missing entry/source-exit candle data
- CN5C83...BAGS: missing entry/source-exit candle data
- 9bUTao...hFMZ: missing entry/source-exit candle data
- 2rYbGE...pump: missing entry/source-exit candle data
- 3x6apz...pump: missing entry/source-exit candle data
