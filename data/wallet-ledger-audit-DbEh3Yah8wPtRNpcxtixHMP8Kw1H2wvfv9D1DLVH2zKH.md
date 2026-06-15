# Corrected Wallet Ledger Audit

Wallet: `DbEh3Yah8wPtRNpcxtixHMP8Kw1H2wvfv9D1DLVH2zKH`
Generated: 2026-06-01T03:15:11.515Z

## Methodology

This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.
It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.

## Coverage

- Transactions fetched: 10000
- Time range: 2026-05-30T17:56:48.000Z to 2026-06-01T03:12:16.000Z
- Classified buy/sell token-flow txs: 9600
- Unclassified token-flow txs: 111
- Positions touched: 617
- SOL/USD used: $82.72

## PnL

- Realized PnL: +30.6923 SOL (+$2,539)
- Realized ROI on sold cost basis: +1.3%
- Unrealized PnL, priced open lots: +188.2420 SOL (+$15,571)
- Open mark value priced: +192.8901 SOL (+$15,956)
- Open cost basis priced: +4.6482 SOL
- Combined realized + priced unrealized: +218.9342 SOL (+$18,110)

## Trading Statistics

- Realized trades: 4735
- Win rate: +50.7%
- Average ROI/trade: +1.2%
- Median ROI/trade: +0.2%
- Profit factor: 1.22
- Sharpe proxy: 0.06
- Max drawdown, realized sequence: -3.5415 SOL
- Average holding duration: 110.8m
- Median holding duration: 16.9m
- Largest win: +0.6934 SOL (+$57)
- Largest loss: -0.4667 SOL ($-39)

## Net Flow Diagnostics

- Native SOL net over fetched txs: -72.2415 SOL
- WSOL token net over fetched txs: +0.0000 SOL
- USDC raw net over fetched txs: -187.35 USDC

## Largest Open Lots By Current Mark

| Token | Held Raw | Current Value | Current Value USD | Source |
|---|---:|---:|---:|---|
| SPCX (BhTPX3...pump) | 25667880580989 | +144.9714 SOL | +$11,992 | DexScreener current |
| USDC (72rNQZ...pump) | 63371782819164 | +17.6739 SOL | +$1,462 | DexScreener current |
| NDAQ (ANM5za...pump) | 57980489039884 | +11.6424 SOL | +$963 | DexScreener current |
| STRAIGHT (J7C3Zs...pump) | 84033078768635 | +4.1925 SOL | +$347 | DexScreener current |
| USDC (76UZDg...pump) | 46375102719189 | +3.3783 SOL | +$279 | DexScreener current |
| Trumpcoin (8o6BZj...pump) | 30288033580056 | +3.1504 SOL | +$261 | DexScreener current |
| Liquititty (8x6nSK...pump) | 79366264598954 | +2.8304 SOL | +$234 | DexScreener current |
| DATBIHGAH (5p4U8y...pump) | 4233016595218 | +0.6289 SOL | +$52 | DexScreener current |
| MMCP (2Vkmp5...pump) | 6170533060409 | +0.6287 SOL | +$52 | DexScreener current |
| PIKACHU (YGiyoi...pump) | 4803404335960 | +0.6057 SOL | +$50 | DexScreener current |

