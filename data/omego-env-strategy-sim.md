# Omego Env Strategy Simulation

Generated: 2026-06-20T14:45:38.713Z

Input: `data/path-wallet-audit-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq.json`

## Caveats

- Uses cached candle-enriched path rows only.
- Source sell timing is not present in the path rows; mirror-source exits are applied at the candle horizon if no trailing/stop exit triggers first.
- Open-position and exposure caps are therefore most reliable for bot-managed stop/trailing exits, not source-exit timing.
- Fees/tips/slippage are not deducted.

## Summary

| Scenario | Trades | Filter Skips | Open/Exposure Skips | Deployed | PnL | PnL / Deployed | Win % | Median ROI | PF | Max DD | Max Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Recommended 15% MC12-50k cap3 exp2.5 | 17 | 57 | 0/0 | +2.229 SOL | -0.007 SOL | -0.3% | +64.7% | +5.0% | 0.96 | -0.127 SOL | +0.395 SOL |
| Loose 15% MC8-50k cap3 exp2.5 | 21 | 43 | 10/0 | +2.620 SOL | -0.047 SOL | -1.8% | +47.6% | -13.2% | 0.85 | -0.198 SOL | +0.410 SOL |
| Tight 15% MC20-50k cap3 exp2.5 | 2 | 72 | 0/0 | +0.307 SOL | -0.026 SOL | -8.5% | +50.0% | -10.0% | 0.24 | -0.035 SOL | +0.169 SOL |
| More exposure 20% MC12-50k cap5 exp5 | 17 | 57 | 0/0 | +2.972 SOL | -0.010 SOL | -0.3% | +64.7% | +5.0% | 0.96 | -0.169 SOL | +0.526 SOL |
| Conservative 10% MC12-50k cap3 exp1.5 | 17 | 57 | 0/0 | +1.486 SOL | -0.004 SOL | -0.3% | +64.7% | +8.0% | 0.97 | -0.087 SOL | +0.263 SOL |
| All MC 15% cap3 exp2.5 | 29 | 0 | 45/0 | +3.141 SOL | -0.195 SOL | -6.2% | +31.0% | -25.0% | 0.58 | -0.320 SOL | +0.351 SOL |
| Early buyers <=5 15% cap3 exp2.5 | 12 | 62 | 0/0 | +1.374 SOL | +0.169 SOL | +12.3% | +58.3% | +5.0% | 2.15 | -0.077 SOL | +0.289 SOL |

## Details

### Recommended 15% MC12-50k cap3 exp2.5

- Trades: 17; skipped filter/open/exposure: 57/0/0
- Total PnL: -0.007 SOL on +2.229 SOL deployed (-0.3%)
- Win rate / median ROI / PF: +64.7% / +5.0% / 0.96
- Max exposure / max DD: +0.395 SOL / -0.127 SOL
- Exit reasons: SL25:6, trail-floor-15:5, trail-floor-5:2, source-exit:2
- Best token: ORBRYN +0.049 SOL at $17,858
- Worst token: Mike -0.039 SOL at $12,565

### Loose 15% MC8-50k cap3 exp2.5

- Trades: 21; skipped filter/open/exposure: 43/10/0
- Total PnL: -0.047 SOL on +2.620 SOL deployed (-1.8%)
- Win rate / median ROI / PF: +47.6% / -13.2% / 0.85
- Max exposure / max DD: +0.410 SOL / -0.198 SOL
- Exit reasons: SL25:9, trail-floor-15:4, source-exit:3, trail-floor-5:3
- Best token: Euphoria +0.150 SOL at $9,321
- Worst token: Comrades -0.038 SOL at $14,214

### Tight 15% MC20-50k cap3 exp2.5

- Trades: 2; skipped filter/open/exposure: 72/0/0
- Total PnL: -0.026 SOL on +0.307 SOL deployed (-8.5%)
- Win rate / median ROI / PF: +50.0% / -10.0% / 0.24
- Max exposure / max DD: +0.169 SOL / -0.035 SOL
- Exit reasons: SL25:1, trail-floor-5:1
- Best token: LOE +0.008 SOL at $23,260
- Worst token: EMM -0.035 SOL at $20,204

### More exposure 20% MC12-50k cap5 exp5

- Trades: 17; skipped filter/open/exposure: 57/0/0
- Total PnL: -0.010 SOL on +2.972 SOL deployed (-0.3%)
- Win rate / median ROI / PF: +64.7% / +5.0% / 0.96
- Max exposure / max DD: +0.526 SOL / -0.169 SOL
- Exit reasons: SL25:6, trail-floor-15:5, trail-floor-5:2, source-exit:2
- Best token: ORBRYN +0.065 SOL at $17,858
- Worst token: Mike -0.052 SOL at $12,565

### Conservative 10% MC12-50k cap3 exp1.5

- Trades: 17; skipped filter/open/exposure: 57/0/0
- Total PnL: -0.004 SOL on +1.486 SOL deployed (-0.3%)
- Win rate / median ROI / PF: +64.7% / +8.0% / 0.97
- Max exposure / max DD: +0.263 SOL / -0.087 SOL
- Exit reasons: SL25:6, trail-floor-15:5, source-exit:2, trail-floor-25:1
- Best token: ORBRYN +0.032 SOL at $17,858
- Worst token: Mike -0.026 SOL at $12,565

### All MC 15% cap3 exp2.5

- Trades: 29; skipped filter/open/exposure: 0/45/0
- Total PnL: -0.195 SOL on +3.141 SOL deployed (-6.2%)
- Win rate / median ROI / PF: +31.0% / -25.0% / 0.58
- Max exposure / max DD: +0.351 SOL / -0.320 SOL
- Exit reasons: SL25:15, source-exit:6, trail-floor-15:3, trail-floor-25:2
- Best token: Euphoria +0.150 SOL at $9,321
- Worst token: Comrades -0.038 SOL at $14,214

### Early buyers <=5 15% cap3 exp2.5

- Trades: 12; skipped filter/open/exposure: 62/0/0
- Total PnL: +0.169 SOL on +1.374 SOL deployed (+12.3%)
- Win rate / median ROI / PF: +58.3% / +5.0% / 2.15
- Max exposure / max DD: +0.289 SOL / -0.077 SOL
- Exit reasons: SL25:5, trail-floor-5:2, trail-floor-20:2, trail-floor-35:1
- Best token: Euphoria +0.136 SOL at $9,321
- Worst token: DROOL -0.034 SOL at $11,265


## Read

- Use this to compare buckets and caps, not as a perfect replay of live exits.
- The stronger live test candidates are the scenarios with positive PnL/deployed, low drawdown, and enough trades to matter.
- Rerun this against the weekly path audit output when the VPS background crawl finishes.
