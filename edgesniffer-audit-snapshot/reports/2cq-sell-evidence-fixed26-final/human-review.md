# 2CQ Exact Sell-Evidence Recovery - Fixed 26

Classification: `2CQ_SELL_JOIN_BUG_FOUND_AND_FIXED_OFFLINE`

## Result

- Sold/fully closed: **26/26** through **42 Pump bonding-curve sell transactions**.
- Median first sell: **1724.0ms**; median full exit: **1897.5ms**.
- First sell was full-size for **15** mints and partial for **11**.
- The old join found zero because raw sell rows lacked the two metadata hints it required; finalized signature matching recovers them without changing the official ledger.

## Economics

| Policy | Size | N | WR | PF | Expectancy | PnL SOL | Median ROI | Worst ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TP20/SL20/120s | 0.5 | 26 | 42.31% | 2.14 | 7.17% | +0.9324 | -2.41% | -28.61% |
| TP20/SL20/120s | 1.0 | 26 | 42.31% | 2.06 | 6.66% | +1.7305 | -2.24% | -29.06% |
| Observed 2CQ schedule | 0.5 | 26 | 80.77% | 4.50 | 10.10% | +1.3127 | +8.71% | -28.16% |
| Observed 2CQ schedule | 1.0 | 25 | 80.00% | 3.81 | 9.46% | +2.3661 | +8.42% | -28.70% |
| Actual 2CQ realized | actual sizing | 26 | 26.92% | 0.57 | n/a | -2.1067 | -3.45% | -32.99% |

## Coverage And Exit Style

The ledger is event-driven rather than a polling tape. `120s covered` below means the source partition is marked exact-replay complete and supports causal replay through T+120s; observed span is the interval to the latest retained exact event.

| Mint | Ord | Entry UTC | Age ms | Observed span s | 120s covered | Sells | First/full ms | First fraction | Actual ROI |
|---|---:|---|---:|---:|:---:|---:|---:|---:|---:|
| `5ic7ZWXt1p17BgyKXcenmsQBkCiyciBYtz76jRAKpump` | 227 | 2026-09-06T14:30:43.708000+00:00 | 26 | 11461.2 | yes | 3 | 2507/12359 | 40% | +110.51% |
| `Tfb3QDyxBUf267XciPwWLhRFkSUDNc618pj6Lbzpump` | 460 | 2026-09-06T17:01:26.183000+00:00 | 5 | 4060.7 | yes | 3 | 1551/3515 | 40% | -3.33% |
| `54UwkZPkJEru2UYSxAsRJrwcTyJKFU9pf6tcYJjTpump` | 593 | 2026-09-06T17:16:23.456000+00:00 | 153 | 186.4 | yes | 2 | 153/3370 | 40% | -10.03% |
| `AVSnwYouHroqzbRgHJQ9qoiftFQwr5FBsdR2JEwepump` | 685 | 2026-09-06T17:28:29.392000+00:00 | 76 | 5232.1 | yes | 1 | 1962/1962 | 100% | -3.58% |
| `4aSpLDCDf42BgKbj7BNeVDY5ABGsKKaQT5pND5yJpump` | 798 | 2026-09-06T17:41:13.362000+00:00 | 71 | 7267.4 | yes | 2 | 1582/3350 | 40% | -32.99% |
| `5JZdrDGyMpADK9nSjtDifm8DqWN3tk1eWau9a4qxpump` | 835 | 2026-09-06T17:46:28.273000+00:00 | 11 | 536.4 | yes | 1 | 1723/1723 | 100% | -4.24% |
| `DrJXyxno9KaxYjLhUvGu2uFxa9hypivLyEwoVh9Zpump` | 905 | 2026-09-06T17:54:52.499000+00:00 | 51 | 2364.2 | yes | 1 | 921/921 | 100% | -2.82% |
| `4MFS9yTqNRQmr9zuvEaeW4ynMRW6H8a9DrUaaMaPpump` | 933 | 2026-09-06T17:59:21.025000+00:00 | 41 | 952.0 | yes | 2 | 27/3405 | 40% | -7.64% |
| `HSpxb5WiTWQQJu5nAFYpFtTDNc2Mwy2kydLDPqpNpump` | 1523 | 2026-09-06T20:09:15.151000+00:00 | 36 | 50.9 | yes | 1 | 788/788 | 100% | -4.40% |
| `2BcRcpdC5ycSQVmtBPgVUf3JBeHtozeBQdawuqsYpump` | 1586 | 2026-09-06T20:15:14.215000+00:00 | 44 | 730.8 | yes | 1 | 1805/1805 | 100% | +7.95% |
| `ErRHebgrC4uLpgB1WfJDkdRHY2rTAViSDZMp61bhpump` | 1642 | 2026-09-06T20:21:58.364000+00:00 | 23 | 1713.0 | yes | 1 | 1792/1792 | 100% | -1.61% |
| `ckch5uXQMHwQmYa9AdYiCcGNiGZes44ZHCQKUM8pump` | 1713 | 2026-09-06T20:31:53.267000+00:00 | 20 | 665.8 | yes | 2 | 25/3357 | 35% | +2.14% |
| `6oXTTCX9o8cy1YHi2XzMEGYGCuyc6qnfKd6whSNMpump` | 1792 | 2026-09-06T20:54:20.607000+00:00 | 43 | 1782.7 | yes | 2 | 99/24568 | 35% | -23.89% |
| `CaoQRKeXnmgsAZTy9pZq67rxWeqvSBBSAg5VFDabpump` | 1818 | 2026-09-06T21:00:26.005000+00:00 | 27 | 258.5 | yes | 1 | 1887/1887 | 100% | -2.36% |
| `ACJ7BjFg19dUxMJRzp59RZuFnAFDTMRPK2SFZb9Hpump` | 2041 | 2026-09-06T21:28:07.773000+00:00 | 194 | 1004.9 | yes | 1 | 837/837 | 100% | -32.55% |
| `2QbdFYSVpksupj1BnAwgn7xs5RQi5AHm3Wk1kFn2yqvK` | 2355 | 2026-09-06T22:04:48.527000+00:00 | 61 | 10468.2 | yes | 2 | 757/3348 | 40% | +1.60% |
| `25XgZUL1je77yz9ZTsBurGJaKTxXJvTLP1ycgQixpump` | 2441 | 2026-09-06T22:12:14.754000+00:00 | 26 | 65.0 | yes | 1 | 1959/1959 | 100% | -2.21% |
| `BQXj5YhF2h4rWGAuV6VuxRzGebLzLcVKVjYDkvZ7pump` | 2475 | 2026-09-06T22:14:32.953000+00:00 | 34 | 1935.3 | yes | 4 | 2215/17563 | 40% | +9.64% |
| `75Pme4A9hGeoMdhP6F7qbjLYJFpukJfmkBcicVQ9pump` | 2559 | 2026-09-06T22:21:49.952000+00:00 | 45 | 7390.6 | yes | 1 | 44/44 | 100% | -4.65% |
| `fnNDAyAGcwZVxsR7eSxAV3yd5KZ3ebibGvuuLKBpump` | 2598 | 2026-09-06T22:25:24.218000+00:00 | 20 | 5862.7 | yes | 1 | 1908/1908 | 100% | +9.77% |
| `AombERGouZfEbAcpnYuMBCd1Sq12TReezNGZBLozpump` | 2680 | 2026-09-06T22:32:55.284000+00:00 | 81 | 36.3 | yes | 1 | 1834/1834 | 100% | -4.38% |
| `6ZiTcpmXkuNaZdKUeaAX27mFe1JGnpmt6mXqnWpWpump` | 2681 | 2026-09-06T22:32:55.359000+00:00 | 123 | 36.3 | yes | 1 | 1759/1759 | 100% | -4.38% |
| `GtZU865x8JMwFbsWSua32XRBHK7HutY6rJqW81chpump` | 3033 | 2026-09-06T23:12:03.426000+00:00 | 33 | 762.3 | yes | 1 | 1725/1725 | 100% | -16.69% |
| `89679zE7HGNb1zvrsQK5Kxmy9KfFs6Vxn1UCELyepump` | 3203 | 2026-09-06T23:31:36.823000+00:00 | 45 | 34.9 | yes | 2 | 58/1562 | 40% | +1.54% |
| `ESayFbuTYr4Vy477jksGAr4ibj3W7fJcxWpaFm57QReu` | 3584 | 2026-09-07T00:13:18.521000+00:00 | 183 | 517.9 | yes | 3 | 2330/6363 | 40% | -2.33% |
| `7qXvFs1KXWnnfgwyLvoNwmoqdEwgbFu9KCdSdK9dpump` | 3744 | 2026-09-07T00:32:34.837000+00:00 | 30 | 90.8 | yes | 1 | 1785/1785 | 100% | -4.81% |
