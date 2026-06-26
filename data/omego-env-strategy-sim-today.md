# Omego Env Strategy Simulation

Generated: 2026-06-22T02:28:59.695Z

Input: `data/path-wallet-audit-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq.json`

## Caveats

- Uses cached candle-enriched path rows only.
- Source sell timing is not present in the path rows; mirror-source exits are applied at the candle horizon if no trailing/stop exit triggers first.
- Open-position and exposure caps are therefore most reliable for bot-managed stop/trailing exits, not source-exit timing.
- Fees/tips/slippage are not deducted.

## Summary

| Scenario | Trades | Filter Skips | Open/Exposure Skips | Deployed | PnL | PnL / Deployed | Win % | Median ROI | PF | Max DD | Max Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Recommended 15% MC12-50k cap3 exp2.5 | 14 | 155 | 20/0 | +2.158 SOL | -0.202 SOL | -9.3% | +35.7% | -25.0% | 0.36 | -0.202 SOL | +0.525 SOL |
| Loose 15% MC8-50k cap3 exp2.5 | 36 | 117 | 36/0 | +4.741 SOL | -0.359 SOL | -7.6% | +44.4% | -25.0% | 0.47 | -0.509 SOL | +0.453 SOL |
| Tight 15% MC20-50k cap3 exp2.5 | 10 | 179 | 0/0 | +1.757 SOL | -0.254 SOL | -14.5% | +20.0% | -25.0% | 0.18 | -0.254 SOL | +0.572 SOL |
| More exposure 20% MC12-50k cap5 exp5 | 31 | 155 | 3/0 | +6.190 SOL | -0.642 SOL | -10.4% | +25.8% | -25.0% | 0.43 | -0.642 SOL | +1.168 SOL |
| Conservative 10% MC12-50k cap3 exp1.5 | 14 | 155 | 20/0 | +1.438 SOL | -0.150 SOL | -10.4% | +35.7% | -25.0% | 0.28 | -0.150 SOL | +0.350 SOL |
| All MC 15% cap3 exp2.5 | 38 | 0 | 151/0 | +3.391 SOL | -0.276 SOL | -8.1% | +34.2% | -25.0% | 0.46 | -0.332 SOL | +0.362 SOL |
| Early buyers <=5 15% cap3 exp2.5 | 0 | 189 | 0/0 | +0.000 SOL | +0.000 SOL | - | +0.0% | - | 0.00 | +0.000 SOL | +0.000 SOL |

## Details

### Recommended 15% MC12-50k cap3 exp2.5

- Trades: 14; skipped filter/open/exposure: 155/20/0
- Total PnL: -0.202 SOL on +2.158 SOL deployed (-9.3%)
- Win rate / median ROI / PF: +35.7% / -25.0% / 0.36
- Max exposure / max DD: +0.525 SOL / -0.202 SOL
- Exit reasons: SL25:8, trail-floor-25:2, trail-floor-5:2, trail-floor-15:1
- Best token: 100x coin +0.048 SOL at $26,970
- Worst token: ATM -0.048 SOL at $26,591

### Loose 15% MC8-50k cap3 exp2.5

- Trades: 36; skipped filter/open/exposure: 117/36/0
- Total PnL: -0.359 SOL on +4.741 SOL deployed (-7.6%)
- Win rate / median ROI / PF: +44.4% / -25.0% / 0.47
- Max exposure / max DD: +0.453 SOL / -0.509 SOL
- Exit reasons: SL25:19, trail-floor-5:6, trail-floor-25:4, trail-floor-15:4
- Best token: MEMEFATHER +0.096 SOL at $10,085
- Worst token: ATM -0.048 SOL at $26,591

### Tight 15% MC20-50k cap3 exp2.5

- Trades: 10; skipped filter/open/exposure: 179/0/0
- Total PnL: -0.254 SOL on +1.757 SOL deployed (-14.5%)
- Win rate / median ROI / PF: +20.0% / -25.0% / 0.18
- Max exposure / max DD: +0.572 SOL / -0.254 SOL
- Exit reasons: SL25:7, trail-floor-25:1, trail-floor-5:1, source-exit:1
- Best token: 100x coin +0.048 SOL at $26,970
- Worst token: ATM -0.048 SOL at $26,591

### More exposure 20% MC12-50k cap5 exp5

- Trades: 31; skipped filter/open/exposure: 155/3/0
- Total PnL: -0.642 SOL on +6.190 SOL deployed (-10.4%)
- Win rate / median ROI / PF: +25.8% / -25.0% / 0.43
- Max exposure / max DD: +1.168 SOL / -0.642 SOL
- Exit reasons: SL25:22, trail-floor-25:2, trail-floor-15:2, trail-floor-5:2
- Best token: JINS +0.216 SOL at $16,104
- Worst token: ATM -0.064 SOL at $26,591

### Conservative 10% MC12-50k cap3 exp1.5

- Trades: 14; skipped filter/open/exposure: 155/20/0
- Total PnL: -0.150 SOL on +1.438 SOL deployed (-10.4%)
- Win rate / median ROI / PF: +35.7% / -25.0% / 0.28
- Max exposure / max DD: +0.350 SOL / -0.150 SOL
- Exit reasons: SL25:8, trail-floor-3:2, trail-floor-25:1, trail-floor-15:1
- Best token: 100x coin +0.032 SOL at $26,970
- Worst token: ATM -0.032 SOL at $26,591

### All MC 15% cap3 exp2.5

- Trades: 38; skipped filter/open/exposure: 0/151/0
- Total PnL: -0.276 SOL on +3.391 SOL deployed (-8.1%)
- Win rate / median ROI / PF: +34.2% / -25.0% / 0.46
- Max exposure / max DD: +0.362 SOL / -0.332 SOL
- Exit reasons: SL25:20, source-exit:7, trail-floor-5:5, trail-floor-15:3
- Best token: ANSHUA +0.076 SOL at $12,033
- Worst token: SORIO -0.045 SOL at $23,441

### Early buyers <=5 15% cap3 exp2.5

- Trades: 0; skipped filter/open/exposure: 189/0/0
- Total PnL: +0.000 SOL on +0.000 SOL deployed (-)
- Win rate / median ROI / PF: +0.0% / - / 0.00
- Max exposure / max DD: +0.000 SOL / +0.000 SOL
- Exit reasons: -
- Best token: -
- Worst token: -


## Read

- Use this to compare buckets and caps, not as a perfect replay of live exits.
- The stronger live test candidates are the scenarios with positive PnL/deployed, low drawdown, and enough trades to matter.
- Rerun this against the weekly path audit output when the VPS background crawl finishes.
