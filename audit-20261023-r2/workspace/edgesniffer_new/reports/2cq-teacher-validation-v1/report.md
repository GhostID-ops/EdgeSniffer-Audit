# 2CQ Teacher Validation V1

Generated: `2026-09-08T17:37:02.290730Z`
Wallet: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`
Frozen window: `2026-09-01T00:00:00Z` to `2026-09-08T00:00:00Z`
Window choice: **7 complete UTC days** (7-day window retained because 4685 >= 500 closed reconcilable Pump episodes)

## Verdict

- Profitability: **2CQ_TEACHER_PROFITABILITY_PROMISING_BUT_ACCOUNTING_INCOMPLETE**
- Data: **2CQ_ADDRESS_PARTIALLY_CONTAMINATED_BUT_USABLE**
- Closed, reconcilable Pump episodes: **4,685**

## Primary Economics

| Metric | Result |
|---|---:|
| Episodes | 4685 |
| Unique mints | 4439 |
| SOL deployed | 12169.92995633 |
| SOL returned | 13074.488927093 |
| Closed-episode net before failed fees SOL | 904.558970763 |
| Failed-attempt fee drag SOL | 310.050979854 |
| Net after failed fees SOL | 594.507990909 |
| Win rate % | 48.644610458911416 |
| Episode PF before failed fees | 2.880089358927179 |
| Period PF after failed fees | 1.7514227114907088 |
| Expectancy after failed fees / episode SOL | 0.126896049286873 |
| Median return % | -0.3216404641013201 |
| Average winner SOL | 0.6080230324370338 |
| Average loser SOL | -0.1999690441234414 |
| Max drawdown SOL | 7.498599860000013 |

## Activity By Venue

| Category | Tx | Outflow SOL | Inflow SOL | Absolute volume SOL | Fees SOL |
|---|---:|---:|---:|---:|---:|
| OTHER_DEX_OR_AGGREGATOR:OKX_DEX_ROUTER | 13 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| PLAIN_SPL_TRANSFER | 88 | 0.007040 | 0.000000 | 0.007040 | 0.000340 |
| PUMPSWAP | 1877 | 985.247847 | 1224.222676 | 2209.470523 | 18.279931 |
| PUMP_BONDING_CURVE | 34349 | 14535.232165 | 15361.651580 | 29896.883744 | 509.286982 |
| SOL_TRANSFER | 60607 | 927.124049 | 0.486477 | 927.610526 | 8.878114 |
| UNRELATED | 830 | 141.141843 | 0.000000 | 141.141843 | 141.107321 |

## Chronology

| UTC day | N | WR % | PF after failed fees | Failed attempts | Failed fees SOL | Net after fees SOL |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-01 | 1025 | 47.3170731707317 | 1.7478219101111214 | 14343 | 90.45434142 | 157.36176630299997 |
| 2026-09-02 | 942 | 48.40764331210191 | 1.8763753332085147 | 13013 | 65.104386528 | 141.87653244400002 |
| 2026-09-03 | 833 | 50.06002400960384 | 1.7564196254226356 | 10733 | 47.833366698 | 98.61795389100001 |
| 2026-09-04 | 588 | 44.89795918367347 | 1.4401391316147631 | 6817 | 32.413614369 | 40.939050017 |
| 2026-09-05 | 320 | 42.8125 | 1.618662874527749 | 4043 | 16.08717873 | 30.642260270999994 |
| 2026-09-06 | 284 | 42.95774647887324 | 1.2070158108579032 | 3028 | 19.554512926 | 9.201884913 |
| 2026-09-07 | 693 | 57.43145743145743 | 2.1416628075134305 | 8364 | 38.603579183 | 115.86854307000002 |

## Concentration

- best1: `25.074515 SOL`, `2.7720155000894544%` of net PnL
- top5: `86.825056 SOL`, `9.598606528302144%` of net PnL
- top10: `121.405175 SOL`, `13.421477115593042%` of net PnL
- top1Pct: `304.635327 SOL`, `33.67777410068008%` of net PnL

## Accounting Notes

- Primary unit is a zero-to-positive-to-effectively-zero mint position episode, not a transaction.
- Effectively zero means at most one raw unit or one part per billion of known acquired inventory, whichever is larger.
- Transferred-in inventory is never assigned a purchase basis; any episode containing unexplained token transfers is excluded from primary economics.
- Actual wallet quote delta includes base/priority transaction fee, explicit delivery transfers, token-account rent changes, Pump/creator fees, and swap proceeds as they affected this address.
- Failed transactions mutate zero inventory. When 2CQ was the fee payer, their exact transaction fee is deducted separately from period economics and period-level adjusted PF.
- Transactions with more than one non-WSOL wallet token delta are fail-closed as unsupported instead of allocating one SOL delta to multiple mints.
- No prior alpha model, ranker, threshold, lifecycle anchor, or shadow outcome was read.

## Next Decomposition

NONE. The teacher did not pass both Phase 0A gates strongly enough to justify behavioral decomposition.
