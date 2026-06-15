# Corrected Wallet Ledger Audit

Wallet: `EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5`
Generated: 2026-06-09T14:26:16.511Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 3000
- Time range: 2026-03-12T07:47:02.000Z to 2026-06-09T14:21:16.000Z
- Classified buy/sell token-flow txs: 838
- Unclassified token-flow txs: 86
- Positions touched: 274
- SOL/USD used: $65.03

## PnL

- Realized PnL: -12.8309 SOL ($-834)
- Realized ROI on sold cost basis: -1.1%
- Unrealized PnL, priced open lots: -766.9117 SOL ($-49,872)
- Open mark value priced: +83.5463 SOL (+$5,433)
- Open cost basis priced: +850.4580 SOL
- Combined realized + priced unrealized: -779.7426 SOL ($-50,707)

## Trading Statistics

- Realized trades: 369
- Win rate: +53.7%
- Average ROI/trade: +122.9%
- Median ROI/trade: +3.9%
- Profit factor: 0.96
- Sharpe proxy: 0.13
- Max drawdown, realized sequence: -154.7029 SOL
- Average holding duration: 77.6m
- Median holding duration: 4.5m
- Largest win: +9.7150 SOL (+$632)
- Largest loss: -58.8777 SOL ($-3,829)

## Net Flow Diagnostics

- Native SOL net over fetched txs: -3.0001 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: 0.00 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| WORLDFROG (6QM4yL...pump) | 298096226074332 | +8.6362 SOL | +$562 | DexScreener current |
| WORLDGUY (6N1EGf...pump) | 291216879866363 | +8.4280 SOL | +$548 | DexScreener current |
| RETARDFEST (CsUQjj...pump) | 253638817586297 | +7.2897 SOL | +$474 | DexScreener current |
| NP67C (61Er3D...pump) | 248154468052523 | +7.1741 SOL | +$467 | DexScreener current |
| TROLLOPOOP (AFaycn...pump) | 143940148446289 | +6.3614 SOL | +$414 | DexScreener current |
| WORLDLGBT (AHDBgQ...pump) | 159913473032386 | +4.6452 SOL | +$302 | DexScreener current |
| TOXGUY (3QFtTe...pump) | 157263183642056 | +4.5755 SOL | +$298 | DexScreener current |
| CHILL (384MNr...pump) | 151881169696446 | +4.3862 SOL | +$285 | DexScreener current |
| cumtire (BQDmZJ...pump) | 147242286722666 | +3.2627 SOL | +$212 | DexScreener current |
| WORLDTROLL (EKqZ9Q...pump) | 102479028535012 | +2.9737 SOL | +$193 | DexScreener current |

