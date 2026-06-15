# Corrected Wallet Ledger Audit

Wallet: `omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq`
Generated: 2026-06-01T03:46:10.618Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 10000
- Time range: 2026-05-29T00:23:01.000Z to 2026-06-01T03:42:58.000Z
- Classified buy/sell token-flow txs: 9794
- Unclassified token-flow txs: 29
- Positions touched: 800
- SOL/USD used: $82.61

## PnL

- Realized PnL: +49.3011 SOL (+$4,073)
- Realized ROI on sold cost basis: +1.5%
- Unrealized PnL, priced open lots: -31.3420 SOL ($-2,589)
- Open mark value priced: +24.3736 SOL (+$2,014)
- Open cost basis priced: +55.7156 SOL
- Combined realized + priced unrealized: +17.9591 SOL (+$1,484)

## Trading Statistics

- Realized trades: 4809
- Win rate: +45.6%
- Average ROI/trade: +1.8%
- Median ROI/trade: -1.3%
- Profit factor: 1.22
- Sharpe proxy: 0.08
- Max drawdown, realized sequence: -5.0564 SOL
- Average holding duration: 184.2m
- Median holding duration: 19.5m
- Largest win: +1.7008 SOL (+$141)
- Largest loss: -1.2236 SOL ($-101)

## Net Flow Diagnostics

- Native SOL net over fetched txs: -3.4177 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: 0.00 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| IBM (3Hvxn5...SdPv) | 3392055610464 | +3.8918 SOL | +$321 | DexScreener current |
| TAPE (7QXDpK...pump) | 7105113741390 | +1.9249 SOL | +$159 | DexScreener current |
| Graphify (BdntCw...pump) | 7032457148322 | +1.7349 SOL | +$143 | DexScreener current |
| FCSRL (AMc5zM...pump) | 5138271205009 | +0.9529 SOL | +$79 | DexScreener current |
| DATA (AyLn7Y...pump) | 7635632361626 | +0.8907 SOL | +$74 | DexScreener current |
| FLUKI (GJF4KM...pump) | 6748169074065 | +0.7593 SOL | +$63 | DexScreener current |
| TripleK (FboMqy...pump) | 6072672339890 | +0.7351 SOL | +$61 | DexScreener current |
| PokeBurn (Cgv7iy...pump) | 8746839186513 | +0.5632 SOL | +$47 | DexScreener current |
| opsec (214uC4...pump) | 5975462930230 | +0.5400 SOL | +$45 | DexScreener current |
| Juan (Anr6gk...pump) | 7193394176712 | +0.4582 SOL | +$38 | DexScreener current |

