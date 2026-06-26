# Omego Env Strategy Simulation

Generated: 2026-06-24T06:14:51.686Z

Input: `data/path-wallet-audit-tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf.json`

## Caveats

- Uses cached candle-enriched path rows only.
- Source sell timing is not present in the path rows; mirror-source exits are applied at the candle horizon if no trailing/stop exit triggers first.
- Open-position and exposure caps are therefore most reliable for bot-managed stop/trailing exits, not source-exit timing.
- Fees/tips/slippage are not deducted.

## Summary

| Scenario | Trades | Filter Skips | Open/Exposure Skips | Deployed | PnL | PnL / Deployed | Win % | Median ROI | PF | Max DD | Max Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Recommended 15% MC12-50k cap3 exp2.5 | 0 | 80 | 0/0 | +0.000 SOL | +0.000 SOL | - | +0.0% | - | 0.00 | +0.000 SOL | +0.000 SOL |
| Loose 15% MC8-50k cap3 exp2.5 | 0 | 80 | 0/0 | +0.000 SOL | +0.000 SOL | - | +0.0% | - | 0.00 | +0.000 SOL | +0.000 SOL |
| Tight 15% MC20-50k cap3 exp2.5 | 0 | 80 | 0/0 | +0.000 SOL | +0.000 SOL | - | +0.0% | - | 0.00 | +0.000 SOL | +0.000 SOL |
| More exposure 20% MC12-50k cap5 exp5 | 0 | 80 | 0/0 | +0.000 SOL | +0.000 SOL | - | +0.0% | - | 0.00 | +0.000 SOL | +0.000 SOL |
| Conservative 10% MC12-50k cap3 exp1.5 | 0 | 80 | 0/0 | +0.000 SOL | +0.000 SOL | - | +0.0% | - | 0.00 | +0.000 SOL | +0.000 SOL |
| All MC 15% cap3 exp2.5 | 65 | 0 | 15/0 | +0.009 SOL | -0.000 SOL | -2.7% | +40.0% | -25.0% | 0.79 | -0.000 SOL | +0.001 SOL |
| Early buyers <=5 15% cap3 exp2.5 | 20 | 60 | 0/0 | +0.003 SOL | -0.000 SOL | -6.0% | +40.0% | -25.0% | 0.64 | -0.000 SOL | +0.001 SOL |

## Details

### Recommended 15% MC12-50k cap3 exp2.5

- Trades: 0; skipped filter/open/exposure: 80/0/0
- Total PnL: +0.000 SOL on +0.000 SOL deployed (-)
- Win rate / median ROI / PF: +0.0% / - / 0.00
- Max exposure / max DD: +0.000 SOL / +0.000 SOL
- Exit reasons: -
- Best token: -
- Worst token: -

### Loose 15% MC8-50k cap3 exp2.5

- Trades: 0; skipped filter/open/exposure: 80/0/0
- Total PnL: +0.000 SOL on +0.000 SOL deployed (-)
- Win rate / median ROI / PF: +0.0% / - / 0.00
- Max exposure / max DD: +0.000 SOL / +0.000 SOL
- Exit reasons: -
- Best token: -
- Worst token: -

### Tight 15% MC20-50k cap3 exp2.5

- Trades: 0; skipped filter/open/exposure: 80/0/0
- Total PnL: +0.000 SOL on +0.000 SOL deployed (-)
- Win rate / median ROI / PF: +0.0% / - / 0.00
- Max exposure / max DD: +0.000 SOL / +0.000 SOL
- Exit reasons: -
- Best token: -
- Worst token: -

### More exposure 20% MC12-50k cap5 exp5

- Trades: 0; skipped filter/open/exposure: 80/0/0
- Total PnL: +0.000 SOL on +0.000 SOL deployed (-)
- Win rate / median ROI / PF: +0.0% / - / 0.00
- Max exposure / max DD: +0.000 SOL / +0.000 SOL
- Exit reasons: -
- Best token: -
- Worst token: -

### Conservative 10% MC12-50k cap3 exp1.5

- Trades: 0; skipped filter/open/exposure: 80/0/0
- Total PnL: +0.000 SOL on +0.000 SOL deployed (-)
- Win rate / median ROI / PF: +0.0% / - / 0.00
- Max exposure / max DD: +0.000 SOL / +0.000 SOL
- Exit reasons: -
- Best token: -
- Worst token: -

### All MC 15% cap3 exp2.5

- Trades: 65; skipped filter/open/exposure: 0/15/0
- Total PnL: -0.000 SOL on +0.009 SOL deployed (-2.7%)
- Win rate / median ROI / PF: +40.0% / -25.0% / 0.79
- Max exposure / max DD: +0.001 SOL / -0.000 SOL
- Exit reasons: SL25:36, trail-floor-5:8, trail-floor-15:7, trail-floor-25:5
- Best token: USCON +0.000 SOL at $27
- Worst token: Gronk -0.000 SOL at $46

### Early buyers <=5 15% cap3 exp2.5

- Trades: 20; skipped filter/open/exposure: 60/0/0
- Total PnL: -0.000 SOL on +0.003 SOL deployed (-6.0%)
- Win rate / median ROI / PF: +40.0% / -25.0% / 0.64
- Max exposure / max DD: +0.001 SOL / -0.000 SOL
- Exit reasons: SL25:12, trail-floor-60:3, trail-floor-5:2, trail-floor-20:2
- Best token: EC43 +0.000 SOL at $62
- Worst token: ATELIER -0.000 SOL at $604


## Read

- Use this to compare buckets and caps, not as a perfect replay of live exits.
- The stronger live test candidates are the scenarios with positive PnL/deployed, low drawdown, and enough trades to matter.
- Rerun this against the weekly path audit output when the VPS background crawl finishes.
