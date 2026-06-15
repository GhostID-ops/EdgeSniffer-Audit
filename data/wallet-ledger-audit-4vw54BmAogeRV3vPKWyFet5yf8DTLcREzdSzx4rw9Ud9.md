# Corrected Wallet Ledger Audit

Wallet: `4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9`
Generated: 2026-06-01T15:33:14.692Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 300
- Time range: 2026-05-31T18:28:56.000Z to 2026-06-01T15:29:37.000Z
- Classified buy/sell token-flow txs: 222
- Unclassified token-flow txs: 18
- Positions touched: 55
- SOL/USD used: $79.36

## PnL

- Realized PnL: +15.0835 SOL (+$1,197)
- Realized ROI on sold cost basis: +11.1%
- Unrealized PnL, priced open lots: +2.0759 SOL (+$165)
- Open mark value priced: +7.4585 SOL (+$592)
- Open cost basis priced: +5.3826 SOL
- Combined realized + priced unrealized: +17.1594 SOL (+$1,362)

## Trading Statistics

- Realized trades: 67
- Win rate: +64.2%
- Average ROI/trade: +20.5%
- Median ROI/trade: +10.4%
- Profit factor: 2.33
- Sharpe proxy: 0.48
- Max drawdown, realized sequence: -2.5917 SOL
- Average holding duration: 1.2m
- Median holding duration: 0.7m
- Largest win: +1.8240 SOL (+$145)
- Largest loss: -2.2723 SOL ($-180)

## Net Flow Diagnostics

- Native SOL net over fetched txs: +14.2622 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: 6.66 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| Diddy (5xU43k...pump) | 15478729734999 | +7.4585 SOL | +$592 | DexScreener current |

