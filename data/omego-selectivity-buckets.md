# Omego Selectivity Buckets — which cycles clear the fee

Source: data/wallet-ledger-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-10000.json
Closed cycles: 4229 · SOL=$72.67 · copy=20% · drag = fee 1.0%/leg + slip 0.4%/leg + tip 0.0000/leg (no tips)
Baseline (copy ALL closed cycles, buy-once): **-5.508 SOL net**

Net ROI is AFTER drag. A bucket clears the pump.fun fee when **Avg NET ROI > 0**. "MC" = entry market cap estimate (entry price x 1B supply x SOL price).

### By entry market cap (most actionable — bot has entry price / virtSol)

| Bucket | Cycles | Deployed | Avg gross ROI | Avg NET ROI | Win % | Net SOL | Verdict |
|---|---:|---:|---:|---:|---:|---:|:--|
| 1) <$8k | 1994 | 215.0 | +168.5% | +163.3% | 31% | -0.977 | ❌ SKIP |
| 2) $8-15k | 1405 | 229.9 | +2.2% | -0.7% | 40% | -1.481 | ❌ SKIP |
| 3) $15-25k | 665 | 142.4 | +1.9% | -0.9% | 39% | -1.264 | ❌ SKIP |
| 4) $25-40k | 165 | 42.4 | -1.4% | -4.2% | 32% | -1.785 | ❌ SKIP |

### By Omego's first-buy size (bot sees source amount)

| Bucket | Cycles | Deployed | Avg gross ROI | Avg NET ROI | Win % | Net SOL | Verdict |
|---|---:|---:|---:|---:|---:|---:|:--|
| 1) <0.4 SOL | 138 | 0.2 | +2414.0% | +2377.4% | 1% | +1.753 | ✅ COPY |
| 2) 0.4-0.6 | 1051 | 110.4 | +1.0% | -1.8% | 30% | -2.034 | ❌ SKIP |
| 3) 0.6-0.9 | 1951 | 286.3 | +2.2% | -0.6% | 39% | -1.759 | ❌ SKIP |
| 4) 0.9-1.3 | 1034 | 218.3 | +1.6% | -1.2% | 38% | -2.781 | ❌ SKIP |
| 5) >1.3 SOL | 55 | 14.5 | -2.0% | -4.7% | 16% | -0.687 | ❌ SKIP |

### By hour of day (UTC)

| Bucket | Cycles | Deployed | Avg gross ROI | Avg NET ROI | Win % | Net SOL | Verdict |
|---|---:|---:|---:|---:|---:|---:|:--|
| 1) 00-06 UTC | 1063 | 160.1 | +1.0% | -1.8% | 37% | -0.137 | ❌ SKIP |
| 2) 06-12 UTC | 487 | 75.1 | +1.9% | -0.9% | 37% | -0.721 | ❌ SKIP |
| 3) 12-18 UTC | 1601 | 235.0 | +210.8% | +205.1% | 34% | -2.601 | ❌ SKIP |
| 4) 18-24 UTC | 1078 | 159.6 | +0.4% | -2.4% | 33% | -2.048 | ❌ SKIP |

## How to turn this into bot filters
- Keep only **✅ COPY** buckets; the **❌ SKIP** buckets are where you pay fee on a net-losing cohort.
- Entry-MC gate maps directly to a virtSol / entry-price filter in the buy path.
- Buy-size gate maps to a min/max source-amount filter.
- Re-run with your real --slip to harden the cutoffs.
