# Corrected Wallet Ledger Audit

Wallet: `GaYjpVLZ1ViQy9fK2PFVJu9t9jXLUF8YEJ71cNRTaXNo`
Generated: 2026-06-22T00:12:17.511Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 2330
- Time range: 2026-06-16T01:01:24.000Z to 2026-06-22T00:11:36.000Z
- Classified buy/sell token-flow txs: 2224
- Unclassified token-flow txs: 14
- Positions touched: 248
- SOL/USD used: $72.67

## PnL

- Realized PnL: -2.3072 SOL ($-168)
- Realized ROI on sold cost basis: -2.0%
- Unrealized PnL, priced open lots: -0.6449 SOL ($-47)
- Open mark value priced: +1.6615 SOL (+$121)
- Open cost basis priced: +2.3064 SOL
- Combined realized + priced unrealized: -2.9521 SOL ($-215)

## Trading Statistics

- Realized trades: 1011
- Win rate: +31.6%
- Average ROI/trade: -3.1%
- Median ROI/trade: -4.5%
- Profit factor: 0.75
- Sharpe proxy: -0.15
- Max drawdown, realized sequence: -3.3167 SOL
- Average holding duration: 104.7m
- Median holding duration: 7.4m
- Largest win: +0.6779 SOL (+$49)
- Largest loss: -0.7812 SOL ($-57)

## Net Flow Diagnostics

- Native SOL net over fetched txs: +13.4438 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: 0.00 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| CROFT (7GjyNr...pump) | 1499483515134 | +0.2623 SOL | +$19 | DexScreener current |
| GHOST (8ESs6W...pump) | 599797292371 | +0.1681 SOL | +$12 | DexScreener current |
| Martha (H82sDB...pump) | 619850504451 | +0.1570 SOL | +$11 | DexScreener current |
| SimVerse (HKBFkm...pump) | 599531218250 | +0.1451 SOL | +$11 | DexScreener current |
| MINORI (C6pX9e...pump) | 799277338239 | +0.1345 SOL | +$10 | DexScreener current |
| SP (8HTgYx...pump) | 940276159486 | +0.1223 SOL | +$9 | DexScreener current |
| GOAL (3vVxL1...pump) | 897483469222 | +0.0773 SOL | +$6 | DexScreener current |
| BROOKE (FkQnbE...pump) | 753937629417 | +0.0726 SOL | +$5 | DexScreener current |
| 滑る猫 (GB7mAJ...E1ob) | 1387552068236 | +0.0700 SOL | +$5 | DexScreener current |
| GymShow (3tjZY8...pump) | 1559532531732 | +0.0682 SOL | +$5 | DexScreener current |

