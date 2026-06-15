# Corrected Wallet Ledger Audit

Wallet: `GUgMNi2tJcjYpDSFs2VZQpjryeWRG4Xgmi1JPDEoe1TH`
Generated: 2026-06-09T14:20:08.399Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 3000
- Time range: 2026-04-14T11:15:14.000Z to 2026-06-09T14:05:50.000Z
- Classified buy/sell token-flow txs: 1902
- Unclassified token-flow txs: 33
- Positions touched: 285
- SOL/USD used: $65.60

## PnL

- Realized PnL: +110.1840 SOL (+$7,228)
- Realized ROI on sold cost basis: +55.0%
- Unrealized PnL, priced open lots: +0.0000 SOL (+$0)
- Open mark value priced: +0.0000 SOL (+$0)
- Open cost basis priced: +0.0000 SOL
- Combined realized + priced unrealized: +110.1840 SOL (+$7,228)

## Trading Statistics

- Realized trades: 347
- Win rate: +62.5%
- Average ROI/trade: +58.1%
- Median ROI/trade: +10.1%
- Profit factor: 4.04
- Sharpe proxy: 0.41
- Max drawdown, realized sequence: -7.7392 SOL
- Average holding duration: 12.4m
- Median holding duration: 2.3m
- Largest win: +7.9227 SOL (+$520)
- Largest loss: -6.6670 SOL ($-437)

## Net Flow Diagnostics

- Native SOL net over fetched txs: -28.3302 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: 0.00 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|

