# Corrected Wallet Ledger Audit

Wallet: `omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq`
Generated: 2026-06-21T23:01:01.073Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 3000
- Time range: 2026-06-21T11:34:38.000Z to 2026-06-21T23:00:00.000Z
- Classified buy/sell token-flow txs: 2984
- Unclassified token-flow txs: 0
- Positions touched: 208
- SOL/USD used: $73.03

## PnL

- Realized PnL: +7.8132 SOL (+$571)
- Realized ROI on sold cost basis: +0.8%
- Unrealized PnL, priced open lots: -10.7002 SOL ($-781)
- Open mark value priced: +16.3919 SOL (+$1,197)
- Open cost basis priced: +27.0921 SOL
- Combined realized + priced unrealized: -2.8870 SOL ($-211)

## Trading Statistics

- Realized trades: 1453
- Win rate: +39.6%
- Average ROI/trade: -2.1%
- Median ROI/trade: -2.6%
- Profit factor: 1.13
- Sharpe proxy: -0.08
- Max drawdown, realized sequence: -5.1319 SOL
- Average holding duration: 75.8m
- Median holding duration: 18.7m
- Largest win: +1.1194 SOL (+$82)
- Largest loss: -1.1813 SOL ($-86)

## Net Flow Diagnostics

- Native SOL net over fetched txs: +6.0355 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: 215.70 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| JINS (7AZPFM...pump) | 3488432837797 | +1.8572 SOL | +$136 | DexScreener current |
| RYXON (2eT7LU...pump) | 5577519924758 | +0.9768 SOL | +$71 | DexScreener current |
| n0dex (ARmShX...pump) | 3396417762814 | +0.8925 SOL | +$65 | DexScreener current |
| BOTCHUA (4pAv4h...pump) | 7042457880473 | +0.8146 SOL | +$59 | DexScreener current |
| MINORI (C6pX9e...pump) | 5517650432593 | +0.8092 SOL | +$59 | DexScreener current |
| Martha (H82sDB...pump) | 7454612054696 | +0.5752 SOL | +$42 | DexScreener current |
| Vertebrae (9QKFPn...pump) | 7401248089344 | +0.5719 SOL | +$42 | DexScreener current |
| BANKRAM (tqbtUb...pump) | 9421929934372 | +0.5657 SOL | +$41 | DexScreener current |
| ML (JBm8zt...pump) | 8727845348799 | +0.5244 SOL | +$38 | DexScreener current |
| BULLCEMBER (DTRmPL...pump) | 8567936287636 | +0.4850 SOL | +$35 | DexScreener current |

