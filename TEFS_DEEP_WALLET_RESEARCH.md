# TEFS Deep Wallet Research

Wallet: `tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf`

Last updated: 2026-06-24

Fresh runs used:

- Ledger-bucket audit: `npm run audit:wallet-ledger-buckets -- tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf 12001`
- Bucket artifacts: `data/wallet-ledger-buckets-tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf-12001.json` / `.md`
- Quant audit: `PATH_AUDIT_QUIET=true npx tsx src/walletQuantAudit.ts tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf 6000 220`
- Path audit (candle-enriched): `npx tsx src/pathBasedWalletAudit.ts tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf 5000 80 24 20`
- Path artifact: `data/path-wallet-audit-tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf.md`
- Env strategy sim: `node scripts/simulateOmegoEnvStrategies.mjs --input=data/path-wallet-audit-tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf.json`
- Copy-trade viability: custom net-of-cost full-mirror model over the bucket JSON (per-campaign `realizedPnlSol` / `sourceExitRoiPct`, fee-in-source, follower slippage + tip haircut)
- Live execution reality check: `node scripts/scanTodayTelemetry.mjs` plus `priceImpactPct` distribution from local `data/trades.jsonl`

## Current Verdict

`tefs` is a **genuinely profitable, ultra-fast Pump.fun scalper** and is a much cleaner copy candidate than CyA or the `3XSY` wallet checked alongside it. Over the last 6 days it realized `+89.8 SOL` (425 closed campaigns) at a `64.5%` win rate and a SOL-weighted profit factor of `3.27`. The 12-day bucket sample confirms it: `+65.5 SOL` over `808` closed campaigns.

Unlike CyA, **pure copytrading is viable on this wallet** — but only because of two execution facts that the user's live setup already satisfies:

1. **You match his fill on both legs.** Break-even is `~0.90%` all-in slippage+tip per leg (`~1.8%` round trip). Below that, full mirror nets positive; above it, it bleeds. The user runs **no tips / no priority fees** and snipes both buys and sells fast, and the bot's own realized price-impact on comparable wallets is `~0.40%` median per leg — comfortably inside the budget.
2. **You mirror his scale-ins.** `71%` of his campaigns add to the position (avg `4.4` buys / `4.3` sells per campaign), and his **top 5 winners alone are `59%` of total realized profit**. Those winners come from positions he scales into. A `BUY_ONCE_PER_TOKEN=true` copy would skip exactly the trades that carry the wallet. **This is the single most important setting and it is the opposite of the CyA recommendation.**

Best current approach:

- Mirror every buy (scale-ins ON) and every sell, proportionally.
- Keep tips/priority fees off and execution fast — your edge over break-even is the execution, not the selection.
- Filter market cap to `$10k-$100k`. The `$100k+` bucket is the only losing one.
- Do **not** use a tight stop. `91.8%` of entries touch `-30%` before resolving; a tight SL gets chopped out before his exit works.
- Pure mirror is the proven-safe baseline. The tested *best* exit is the hybrid `50% at +30%, 50% source exit`, because the wallet chronically under-exits (sells at `+3.6%` median while median MFE is `+48.8%`). Shadow-test the hybrid before running it live.

## Copy-Trade Viability (the core question)

His realized P&L is computed from actual wallet SOL deltas, so the **Pump.fun protocol fee is already inside his ROI**. A follower doing the same trades pays their own fee and lands in the same place. The only *extra* drag a follower carries is **slippage vs his fill on each leg, plus tips**.

Full-mirror, net of follower slippage + tips (fee already in source ROI), `$10k-$50k` core entries:

| Your execution per leg | Net SOL | Net / deployed | Median trade | Win % |
|---|---:|---:|---:|---:|
| His exact fills (ceiling) | +62.7 | +1.97% | +3.30% | 63.4% |
| Very fast: 0.3% slip, no tip | +43.4 | +1.36% | +2.69% | 61.1% |
| Fast: 0.5% slip + tiny tip | +27.2 | +0.85% | +2.17% | 59.5% |
| Average: 0.9% slip + tip | -1.9 | -0.06% | +1.26% | 56.6% |
| Slow sell: 1.5% slip + tip | -40.5 | -1.27% | +0.04% | 50.2% |

- **Break-even is ~0.90% all-in cost per leg** (gross edge `+65.5 SOL` spread over `~7,276 SOL` of traded notional = `0.90%` per side).
- Below `~0.9%/leg` → profitable. Above it → negative, and it degrades fast because the wallet is high-churn (every leg is a fresh slippage roll).
- **The sell leg is the risk.** He holds `2.1-2.6 min` median; you only act after you see his sell, so a late sell into a dumping scalp is where copy bleeds. Fast buy-sniping alone is not enough — the sell mirror must be equally tight.

## Live Execution Reality Check

The user asked to replace the slippage *assumptions* above with real measured numbers. Status:

- `scanTodayTelemetry.mjs` returned **0 rows for today** locally. The live `tefs` copy test runs on the VPS (`/root/bot/FinalBot-cya` family); its telemetry has not been exported into this repo.
- Local `data/execution-telemetry.jsonl` (06-05 → 06-12) and `data/trades.jsonl`, plus the `cya-live-export-20260619` bundle (06-12 → 06-16), contain **no `tefs` rows** — they are CyA / omego instances only.
- The execution-telemetry `details` payload is latency-only (`providerToProcessMs` etc.), not fill-vs-source price. The usable slippage proxy is `priceImpactPct` in `trades.jsonl`.

What the existing same-bot data does tell us (CyA / omego copies, same infra, no-tip fast config):

- Realized per-leg `priceImpactPct`: **p50 `0.40%`, p90 `1.14%`**.
- Against the `0.90%/leg` break-even: the **median leg is comfortably profitable**; only roughly the worst `~10%` of legs exceed break-even. That lands the user squarely in the "Very fast / Fast" rows above (`+0.85%` to `+1.36%` on deployed).

To replace this with `tefs`-specific truth, export the live telemetry from the VPS (see "Open Questions" for the exact commands), and we will compute real entry-leg and exit-leg drift per `sourceSignature`.

## Fresh Data Basis

Quant pass (`data/_quant` run):

- Wallet tx cache: `6000` txs
- Swaps reconstructed: `3968` (buys/sells `1988 / 1980`)
- Campaigns: `432`; closed: `425`; candle-enriched: `219`
- Window: `2026-06-18T06:03:51Z` → `2026-06-24T13:28:58Z`

Wallet-level stats (quant):

- Realized PnL: `+89.80 SOL`
- Win rate: `64.5%`
- Average source ROI: `+0.38%`
- Median source ROI: `+3.59%`
- Profit factor (SOL): `3.27`
- Median hold: `2.55 min` (average `42.0 min` — a few long bags pull the mean)
- Median entry size: `0.841 SOL` (avg `0.939 SOL`)
- Largest win / loss: `+30.42 SOL / -2.00 SOL`
- Top 5 winners contributed `59.1%` of total realized SOL

Bucket pass (high-coverage, 12 days):

- Transactions `12001` → reconstructed fills `7138` (`59%`; ~110 "other"-route txs only partially parsed)
- Campaigns `823`; closed `808`
- Window: `2026-06-12T19:01:00Z` → `2026-06-24T06:00:57Z`
- Realized PnL `+65.52 SOL`; win `62.4%`; profit factor (SOL) `1.86`
- Scale-in campaigns (>1 buy): `575 / 808` (`71%`); avg `4.40` buys / `4.33` sells per campaign
- Median hold `2.13 min`; hold distribution `<1m: 251 | 1-3m: 192 | 3-10m: 170 | 10-60m: 137 | >60m: 58`

Approximation caveat: entry market cap uses entry fill price × 1B assumed supply. Use it as a range, not exact DexScreener MC. The path audit mis-scaled this wallet's entry MC (reported `$31`); **trust the bucket/quant MC, not the path-audit MC.**

## Strategy Backtest

Quant `219`-entry candle-enriched sample:

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit Factor | Cumulative | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| D: 50% at +30, 50% source exit | 219 | 80.8% | +8.6% | +14.5% | 4.36 | +1883pp | -91pp |
| E: 20% trailing stop | 219 | 51.1% | +12.7% | +0.7% | 3.03 | +2785pp | -121pp |
| C: Sell when wallet sells (full mirror) | 219 | 65.3% | -0.4% | +3.5% | 0.90 | -92pp | -211pp |
| B2: TP40 SL25 | 219 | 30.1% | -5.0% | -25.0% | 0.71 | -1091pp | -1206pp |
| B: TP50 SL25 | 219 | 26.0% | -5.1% | -25.0% | 0.72 | -1106pp | -1256pp |
| A: TP30 SL20 | 219 | 28.3% | -5.6% | -20.0% | 0.60 | -1216pp | -1286pp |
| B3: TP60 SL30 | 219 | 25.6% | -6.6% | -30.0% | 0.70 | -1435pp | -1615pp |

Read:

- **The hybrid `D` is the best tested behavior** (PF `4.36`, `80.8%` win, `+14.5%` median). It works because the wallet hits `+30%` on `62.6%` of entries but sells at `+3.6%` median — there is a large, frequently-realized gap between his MFE and his exit that a partial-profit rule captures.
- **`E` 20% trailing has the highest raw cumulative return** but is lumpy (high avg, near-zero median) — it lives off the big MFE runners and will feel like a long string of small losses punctuated by rare large wins.
- **`C` full mirror is a near-breakeven `+3.5%` median on a per-trade % basis**, but on his *actual sizing* it is the `+65.5 / +89.8 SOL` the wallet really made (the `% ` PF understates it because he sizes/scales winners). A faithful full-mirror replicates that, minus your slippage — see the viability table.
- **All fixed TP/SL are bad**, same as every other wallet studied. Do not box this wallet in a scalp TP/SL.

## Target And Downside Probabilities

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 81.3% | 1260s |
| `+20%` | 68.9% | 1223s |
| `+30%` | 62.6% | 1612s |
| `+50%` | 48.4% | 3679s |
| `+100%` | 30.6% | 4222s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 95.4% |
| `-20%` | 92.7% |
| `-30%` | 91.8% |

Read:

- Upside frequency is **high** — `62.6%` of entries reach `+30%`, `30.6%` reach `+100%`. This is what makes the hybrid/trailing exits work.
- Downside is **near-universal** — `91.8%` touch `-30%` first. These are violent paths. A tight stop will be chopped out before either his exit or a target resolves. Use only a wide emergency stop.

## Market Cap Buckets

Trustworthy source-exit buckets (ledger audit, 808 campaigns):

| Approx entry MC | n | Win % | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| `$5k-10k` | 2 | 100.0% | +8.2% | +8.2% | - | +0.10 | 0.4m |
| `$10k-20k` | 56 | 60.7% | -2.9% | +3.0% | 0.61 | +2.54 | 1.0m |
| `$20k-50k` | 677 | 63.7% | -1.0% | +3.4% | 0.78 | +60.14 | 2.6m |
| `$50k-100k` | 51 | 52.9% | -3.2% | +1.4% | 0.51 | +4.04 | 1.6m |
| `$100k+` | 22 | 45.5% | -4.1% | -0.5% | 0.37 | -1.30 | 1.0m |

Quant cross-check, `$20k-50k` bucket (candle-enriched, n=152): win `70.4%`, avg `+0.56%`, median `+3.80%`, hit `+30%` `60.5%`, hit `+50%` `47.4%`, hit `+100%` `30.3%`.

Read:

- **`$20k-50k` is the wallet.** `677` of `808` campaigns (`84%`) and `+60.14` of `+65.5 SOL`.
- `$10k-20k` is thin but green; `$50k-100k` is marginal; **`$100k+` is the only outright loser** (PF `0.37`).
- Live filter: `PREBUY_MIN_MARKET_CAP_USD=10000`, `PREBUY_MAX_MARKET_CAP_USD=100000`. Shadow-test tightening the min to `20000`.

## Source Buy Size Buckets

Quant `sourceBuySol` buckets:

| Source first-buy size | n | Win % source exit | Avg source ROI | Median source ROI |
|---|---:|---:|---:|---:|
| `0-1 SOL` | 186 | 67.7% | -0.1% | +3.8% |
| `1-3 SOL` | 29 | - | - | - |
| `3-6 SOL` | 3 | - | - | - |
| `6+ SOL` | 1 | - | - | - |

Read:

- He is a **consistent small-size entrant** (`~0.85 SOL` first buy, `186` of `219` in the `0-1 SOL` bucket), then **scales** the ones that work. The first-buy size is not the signal; the scale-in is.
- For copy sizing, mirror proportionally so your adds track his adds. Set `MAX_SOL_PER_TOKEN` high enough that scale-ins are not blocked.

## Entry Path Stats

- Median MFE: `+48.8%`; Median MAE: `-91.2%`
- Hit `+30 / +50 / +100`: `62.6% / 48.4% / 30.6%` (quant); path-audit 80-entry subset `50/80 / 36/80 / 23/80`
- Median time to 2x: `15m`
- Median current liquidity at entry: `$3.5k`; median pre-5m volume `$21k`

Read: huge upside frequency paired with brutal drawdowns. The realized edge is in *exit discipline*, which for a follower means either mirroring his (fast) sells or running the hybrid partial-profit rule.

## Lead Wallet / Sniping Clues

- Profiled lead entries: `20`
- Median source order: `98.5` (he is, on average, the ~99th buyer into a mint)
- Median source delay: `0s`; median earlier buys: `0`
- Repeated top earlier buyers: none

Read: **`tefs` is not a sniper and is not lead-wallet-driven.** He buys mid-pack into already-moving `$20-50k` names and wins on speed of exit and scaling. There is no upstream wallet worth copying instead.

## Recommended Live Validation

```env
TRACKED_WALLETS=tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf

SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false

# CRITICAL: mirror his scale-ins. Buy-once would skip the trades that carry the wallet.
BUY_ONCE_PER_TOKEN=false
MIRROR_SELLS=true
MIRROR_SELL_MODE=full

# Proportional-style sizing; keep per-token cap high enough to allow adds.
MIN_BUY_SOL=0.15
MAX_BUY_SOL=0.20
MAX_SOL_PER_TOKEN=0.80
MAX_SESSION_DEPLOYED_SOL=2.00
MAX_OPEN_POSITIONS=3

# Wide emergency stop only. 91.8% of entries touch -30% before resolving; a tight stop chops.
STOP_LOSS_PCT=50
MIN_HOLD_BEFORE_STOP_LOSS_MS=10000
TAKE_PROFIT_PCT=
TRAILING_STEPS=

# Skip the only losing bucket; keep the core.
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=69
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=10000
PREBUY_MAX_MARKET_CAP_USD=100000
PREBUY_FILTER_FAIL_OPEN=false

MAX_ENTRY_PRICE_IMPACT_PCT=60
MAX_SOURCE_DRIFT_PCT=25

# Keep tips/priority OFF — your sub-break-even slippage is the whole edge.
EXECUTION_MULTI_SEND_ENABLED=true
RUST_HOT_SNIPER_ENABLED=true
RUST_HOT_SNIPER_PRIMARY=true
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=60
```

Why pure mirror first:

- It is the behavior your fast, no-tip live execution already proves you can run inside the `~0.9%/leg` break-even.
- The hybrid `50% at +30, 50% source exit` tested better (PF `4.36`) but requires the bot to hold past his exit and manage a partial — more execution surface and live trailing-slippage risk. Validate pure mirror first, then shadow the hybrid.

## Shadow Research Variant

Use this to collect live-like exit-rule data without sending transactions, and to compare pure mirror vs the hybrid:

```env
SIMULATION_MODE=true
SHADOW_EXECUTION_MODE=true

TRACKED_WALLETS=tefsDGYz1qc3F52ckPPxaVqxwbCuJkheoEhzyFDumyf

BUY_ONCE_PER_TOKEN=false
MIRROR_SELLS=true
MIRROR_SELL_MODE=full

MIN_BUY_SOL=0.15
MAX_BUY_SOL=0.20
MAX_SOL_PER_TOKEN=0.80
MAX_SESSION_DEPLOYED_SOL=3.00
MAX_OPEN_POSITIONS=3

PREBUY_MIN_MARKET_CAP_USD=10000
PREBUY_MAX_MARKET_CAP_USD=100000
PREBUY_FILTER_FAIL_OPEN=false

SHADOW_LATENCY_MS=80
SHADOW_ENTRY_SLIPPAGE_PCT=0.5
SHADOW_EXIT_SLIPPAGE_PCT=0.6
SHADOW_TX_FEE_SOL=0
SHADOW_FAILURE_RATE_PCT=0
```

Set `SHADOW_ENTRY_SLIPPAGE_PCT` / `SHADOW_EXIT_SLIPPAGE_PCT` to the real per-leg numbers once VPS telemetry is measured.

## Open Questions

- **Measure real `tefs` slippage.** Export VPS telemetry and re-run the viability model with measured entry/exit drift:
  - `scp` or pull `data/execution-telemetry.jsonl` and `data/trades.jsonl` from the live instance (mirror the `cya-live-export` bundle), then `node scripts/scanTodayTelemetry.mjs --trades=<path> --telemetry=<path>` and a per-`sourceSignature` fill-vs-source diff.
- **Confirm scale-in mirroring works as configured.** Verify `BUY_ONCE_PER_TOKEN=false` + a high `MAX_SOL_PER_TOKEN` actually lets the bot add to a position the way `tefs` does, rather than capping at the first buy.
- **Implement and shadow-test the hybrid `50% at +30%, 50% source exit`** — it is the best tested exit but is not cleanly expressible in the current env-only exit rules.
- **Raise bucket coverage.** Only `59%` of fills reconstructed; investigate the ~110 "other"-route txs before trusting the absolute SOL totals as complete.
- **Decide the min-MC floor** by shadow-comparing `PREBUY_MIN_MARKET_CAP_USD=10000` vs `20000` on live-like data.
