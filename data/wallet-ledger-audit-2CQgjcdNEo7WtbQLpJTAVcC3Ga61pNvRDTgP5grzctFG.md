# Corrected Wallet Ledger Audit

Wallet: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`
Generated: 2026-06-01T13:03:01.102Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 10000
- Time range: 2026-05-30T13:31:21.000Z to 2026-06-01T12:57:05.000Z
- Classified buy/sell token-flow txs: 8964
- Unclassified token-flow txs: 110
- Positions touched: 2142
- SOL/USD used: $80.77

## PnL

- Realized PnL: +364.6853 SOL (+$29,456)
- Realized ROI on sold cost basis: +6.8%
- Unrealized PnL, priced open lots: -61.5468 SOL ($-4,971)
- Open mark value priced: +396.8012 SOL (+$32,050)
- Open cost basis priced: +458.3480 SOL
- Combined realized + priced unrealized: +303.1386 SOL (+$24,485)

## Trading Statistics

- Realized trades: 6254
- Win rate: +52.5%
- Average ROI/trade: +8.8%
- Median ROI/trade: +0.9%
- Profit factor: 2.28
- Sharpe proxy: 0.21
- Max drawdown, realized sequence: -4.8634 SOL
- Average holding duration: 6.5m
- Median holding duration: 0.1m
- Largest win: +6.5701 SOL (+$531)
- Largest loss: -4.5714 SOL ($-369)

## Net Flow Diagnostics

- Native SOL net over fetched txs: +110.9820 SOL
- WSOL token net over fetched txs: -6.1187 SOL
- USDC raw net over fetched txs: 466.86 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| DATBIHGAH (5vAExw...pump) | 23671902209040 | +186.8079 SOL | +$15,088 | DexScreener current |
| SPCX (BhTPX3...pump) | 18264122462586 | +89.4777 SOL | +$7,227 | DexScreener current |
| 1 (9CLsBq...pump) | 111432929006013 | +6.3863 SOL | +$516 | DexScreener current |
| ANTH (5ar4uH...pump) | 39367159461537 | +5.3468 SOL | +$432 | DexScreener current |
| OAI (9HTEQT...pump) | 49010896036881 | +5.2646 SOL | +$425 | DexScreener current |
| USDUT (5AGCYM...pump) | 57594705977595 | +2.8523 SOL | +$230 | DexScreener current |
| POO (2Y528W...pump) | 94962792385392 | +2.7194 SOL | +$220 | DexScreener current |
| $200 (6aoYgr...pump) | 48910090599865 | +2.4295 SOL | +$196 | DexScreener current |
| test (9XfChp...pump) | 82845577931100 | +2.3704 SOL | +$191 | DexScreener current |
| 35 (4bNZ5G...pump) | 77374872564218 | +2.2225 SOL | +$180 | DexScreener current |

