# Omego Env Strategy Simulation

Generated: 2026-06-24T04:56:11.832Z

Input: `data/path-wallet-audit-3XSYLa8VqkTV4RxKGG92nQb3gPziFFFEVLCYkEaEtZ5k.json`

## Caveats

- Uses cached candle-enriched path rows only.
- Source sell timing is not present in the path rows; mirror-source exits are applied at the candle horizon if no trailing/stop exit triggers first.
- Open-position and exposure caps are therefore most reliable for bot-managed stop/trailing exits, not source-exit timing.
- Fees/tips/slippage are not deducted.

## Summary

| Scenario | Trades | Filter Skips | Open/Exposure Skips | Deployed | PnL | PnL / Deployed | Win % | Median ROI | PF | Max DD | Max Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Recommended 15% MC12-50k cap3 exp2.5 | 62 | 18 | 0/0 | +6.698 SOL | -0.287 SOL | -4.3% | +43.5% | -25.0% | 0.70 | -0.412 SOL | +0.271 SOL |
| Loose 15% MC8-50k cap3 exp2.5 | 62 | 18 | 0/0 | +6.698 SOL | -0.287 SOL | -4.3% | +43.5% | -25.0% | 0.70 | -0.412 SOL | +0.271 SOL |
| Tight 15% MC20-50k cap3 exp2.5 | 52 | 28 | 0/0 | +5.868 SOL | -0.194 SOL | -3.3% | +44.2% | -25.0% | 0.76 | -0.407 SOL | +0.235 SOL |
| More exposure 20% MC12-50k cap5 exp5 | 62 | 18 | 0/0 | +8.931 SOL | -0.383 SOL | -4.3% | +43.5% | -25.0% | 0.70 | -0.550 SOL | +0.361 SOL |
| Conservative 10% MC12-50k cap3 exp1.5 | 62 | 18 | 0/0 | +4.465 SOL | -0.154 SOL | -3.5% | +46.8% | -25.0% | 0.74 | -0.250 SOL | +0.181 SOL |
| All MC 15% cap3 exp2.5 | 63 | 17 | 0/0 | +6.716 SOL | -0.292 SOL | -4.3% | +42.9% | -25.0% | 0.69 | -0.417 SOL | +0.271 SOL |
| Early buyers <=5 15% cap3 exp2.5 | 16 | 64 | 0/0 | +1.845 SOL | -0.161 SOL | -8.7% | +43.8% | -25.0% | 0.40 | -0.161 SOL | +0.191 SOL |

## Details

### Recommended 15% MC12-50k cap3 exp2.5

- Trades: 62; skipped filter/open/exposure: 18/0/0
- Total PnL: -0.287 SOL on +6.698 SOL deployed (-4.3%)
- Win rate / median ROI / PF: +43.5% / -25.0% / 0.70
- Max exposure / max DD: +0.271 SOL / -0.412 SOL
- Exit reasons: SL25:35, trail-floor-15:9, trail-floor-5:7, trail-floor-25:4
- Best token: TRENCHES +0.082 SOL at $42,340
- Worst token: SIDELINED -0.044 SOL at $34,853

### Loose 15% MC8-50k cap3 exp2.5

- Trades: 62; skipped filter/open/exposure: 18/0/0
- Total PnL: -0.287 SOL on +6.698 SOL deployed (-4.3%)
- Win rate / median ROI / PF: +43.5% / -25.0% / 0.70
- Max exposure / max DD: +0.271 SOL / -0.412 SOL
- Exit reasons: SL25:35, trail-floor-15:9, trail-floor-5:7, trail-floor-25:4
- Best token: TRENCHES +0.082 SOL at $42,340
- Worst token: SIDELINED -0.044 SOL at $34,853

### Tight 15% MC20-50k cap3 exp2.5

- Trades: 52; skipped filter/open/exposure: 28/0/0
- Total PnL: -0.194 SOL on +5.868 SOL deployed (-3.3%)
- Win rate / median ROI / PF: +44.2% / -25.0% / 0.76
- Max exposure / max DD: +0.235 SOL / -0.407 SOL
- Exit reasons: SL25:29, trail-floor-15:9, trail-floor-5:5, trail-floor-45:4
- Best token: TRENCHES +0.082 SOL at $42,340
- Worst token: SIDELINED -0.044 SOL at $34,853

### More exposure 20% MC12-50k cap5 exp5

- Trades: 62; skipped filter/open/exposure: 18/0/0
- Total PnL: -0.383 SOL on +8.931 SOL deployed (-4.3%)
- Win rate / median ROI / PF: +43.5% / -25.0% / 0.70
- Max exposure / max DD: +0.361 SOL / -0.550 SOL
- Exit reasons: SL25:35, trail-floor-15:9, trail-floor-5:7, trail-floor-25:4
- Best token: TRENCHES +0.110 SOL at $42,340
- Worst token: SIDELINED -0.058 SOL at $34,853

### Conservative 10% MC12-50k cap3 exp1.5

- Trades: 62; skipped filter/open/exposure: 18/0/0
- Total PnL: -0.154 SOL on +4.465 SOL deployed (-3.5%)
- Win rate / median ROI / PF: +46.8% / -25.0% / 0.74
- Max exposure / max DD: +0.181 SOL / -0.250 SOL
- Exit reasons: SL25:33, trail-floor-15:8, trail-floor-3:6, trail-floor-25:4
- Best token: TRENCHES +0.055 SOL at $42,340
- Worst token: SIDELINED -0.029 SOL at $34,853

### All MC 15% cap3 exp2.5

- Trades: 63; skipped filter/open/exposure: 17/0/0
- Total PnL: -0.292 SOL on +6.716 SOL deployed (-4.3%)
- Win rate / median ROI / PF: +42.9% / -25.0% / 0.69
- Max exposure / max DD: +0.271 SOL / -0.417 SOL
- Exit reasons: SL25:36, trail-floor-15:9, trail-floor-5:7, trail-floor-25:4
- Best token: TRENCHES +0.082 SOL at $42,340
- Worst token: SIDELINED -0.044 SOL at $34,853

### Early buyers <=5 15% cap3 exp2.5

- Trades: 16; skipped filter/open/exposure: 64/0/0
- Total PnL: -0.161 SOL on +1.845 SOL deployed (-8.7%)
- Win rate / median ROI / PF: +43.8% / -25.0% / 0.40
- Max exposure / max DD: +0.191 SOL / -0.161 SOL
- Exit reasons: SL25:9, trail-floor-5:5, trail-floor-35:2
- Best token: Gronk +0.042 SOL at $37,901
- Worst token: $CWIF -0.038 SOL at $37,371


## Read

- Use this to compare buckets and caps, not as a perfect replay of live exits.
- The stronger live test candidates are the scenarios with positive PnL/deployed, low drawdown, and enough trades to matter.
- Rerun this against the weekly path audit output when the VPS background crawl finishes.
