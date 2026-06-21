# Wallet PnL Windows - omegoM...XAfq

Generated: 2026-06-21T00:41:10.414Z

## Method

FIFO per mint from Helius account deltas. Gross excludes fees; net includes wallet-paid tx fees in buy cost and sell proceeds.

Important: this is the comparable definition we control. GMGN/Padre/Axiom may differ by open-position marks, USD conversion time, fees, failed tx handling, transfers, and whether they count only closed positions.

## Coverage

- Transactions loaded: 25997
- Swaps extracted: 25390
- Realized costed sells: 12556
- Uncosted sells: 148
- Oldest swap: 2026-05-30T18:05:45.000Z
- Newest swap: 2026-06-20T06:01:27.000Z
- Anchor: newest (2026-06-20T06:01:27.000Z)

Sources:
- data\wallet-ledger-cache\wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-10000.json: 10000 txs
- data\wallet-quant-audit-cache\wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-5000.json: 5000 txs
- data\path-audit-cache\wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-1200.json: 1200 txs
- data\path-audit-cache\wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-3000.json: 3000 txs
- data\path-audit-cache\wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-5000.json: 5000 txs
- data\path-audit-cache\wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-6000.json: 6000 txs

## PnL Windows

| Window | Buys | Sells | Buy mints | Deployed | Gross PnL | Net PnL | Net ROI | Win % | PF | Fees | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1d | 2840 | 2841 | 340 | +2221.0700 SOL | +25.8647 SOL | +23.5837 SOL | +1.1% | +46.0% | 1.19 | +2.3019 SOL | 2026-06-19T06:01:27.000Z -> 2026-06-20T06:01:27.000Z |
| 7d | 7263 | 7264 | 897 | +5582.1337 SOL | +79.1125 SOL | +73.2988 SOL | +1.3% | +45.8% | 1.24 | +5.8835 SOL | 2026-06-13T06:01:27.000Z -> 2026-06-20T06:01:27.000Z |
| 30d | 12692 | 12698 | 1685 | +9690.2338 SOL | +154.4607 SOL | +144.2934 SOL | +1.5% | +45.5% | 1.27 | +10.2832 SOL | 2026-05-21T06:01:27.000Z -> 2026-06-20T06:01:27.000Z |

## Notes

- Net PnL is the number to compare against platforms that include fees.
- Gross PnL is better for evaluating strategy edge before execution costs.
- If coverage does not span the full 30d window, the 30d row is incomplete by definition.
- Uncosted sells usually mean the cache did not include the buy-side history for that token or the wallet received/transferred tokens outside a normal swap.
