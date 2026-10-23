# Teacher Impulse Live Promotion Candidate

Generated: 2026-08-28

## Active Hypothesis

Early organic pump.fun impulse tokens with entry market cap at or below 5000 USD can support a short-runner exit profile. The current best production candidate is not the old 8% scalp. It is:

- Entry: `entry_mc <= 5000`
- TP: `20%`
- SL: `-20%`
- Position sizing for first live test: dust only
- Execution posture: `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, `JUPITER_FALLBACK_ENABLED=false`

## Evidence

Fresh reset data shows the edge survives delayed-entry simulation.

| Lane | TP | Delay | Resolved | W / L | Win% | PF | Fee-Aware SOL |
|---|---:|---:|---:|---:|---:|---:|---:|
| `mc5000` | 20% | 0ms | 143 | 112 / 31 | 78.32% | 3.61 | +12.63 |
| `mc5000` | 20% | 250ms target | 141 | 108 / 33 | 76.60% | 3.27 | +11.47 |
| `mc5000` | 20% | 500ms target | 137 | 107 / 30 | 78.10% | 3.57 | +11.98 |
| `mc5000` | 20% | 1000ms target | 137 | 105 / 32 | 76.64% | 3.28 | +11.17 |
| `mc<=5000` derived | 20% | 0ms | 176 | 140 / 36 | 79.55% | 3.89 | +16.40 |
| `mc<=5000` derived | 20% | 250ms target | 175 | 134 / 41 | 76.57% | 3.27 | +14.23 |
| `mc<=5000` derived | 20% | 500ms target | 171 | 132 / 39 | 77.19% | 3.38 | +14.33 |
| `mc<=5000` derived | 20% | 1000ms target | 170 | 131 / 39 | 77.06% | 3.36 | +14.15 |

The delayed-entry simulation uses the first valid forward market-cap mark at or after the target delay, then evaluates TP/SL from that delayed fill. This is intentionally more conservative than the perfect shadow entry.

## Promotion Choice

Preferred first dust candidate:

```text
mc5000
TP 20%
SL -20%
```

Reason: it is a real prospective child service currently running, while `mc<=5000 derived` is mined from the parent receipts. The semantics match, but live calibration should use the service lane that is already prospectively collecting.

Alternative if we want cleaner concentration:

```text
mc5000-dom55
TP 20%
SL -20%
```

Reason: still positive, but lower volume and slightly lower fee-aware edge.

## Required Before Dust

- Keep the repeatable report command as the source of truth:
  `/root/bot/FinalBot-cya/scripts/runTeacherImpulseLiveReadiness.sh`
- Use only corrected SL semantics: `--sl-pct -20`
- Do not use old pre-reset PF numbers.
- Do not promote max-forward ROI or top-wick lists as PnL.
- Confirm disk space before running dust; active reset data is large and growing.
- Keep live dust capped and isolated from old services.

## Invalidation

Reject or demote the lane if any of these happen:

- Fee-aware delayed-entry PnL turns negative at 500ms or 1000ms.
- Dust fills drift materially beyond the delayed-entry model.
- Real exits cannot realize the 20% TP despite shadow marks.
- More than one submit-capable sidecar is active.
- Mayhem unavailable or hard-block tokens enter the live candidate stream.
