# CyA Deep Wallet Research

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Last updated: 2026-06-17

Fresh runs used:

- Quant audit: `npx tsx src/walletQuantAudit.ts CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o 6000 260`
- Quant artifact: `data/cya-quant.json`
- Expanded quant audit: `npx tsx src/walletQuantAudit.ts CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o 10000 600`
- Expanded quant artifact: `data/cya-quant-large.json`
- 100k+ last-7d audit: `node scripts/analyzeCya100kLast7d.mjs 100000 7 24`
- 100k+ last-7d artifacts: `data/cya-100k-last7d.json`, `data/cya-100k-last7d.md`
- Recent path audit: `PATH_AUDIT_QUIET=true npx tsx src/pathBasedWalletAudit.ts CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o 6000 60 24 20`
- Generated recent-trade detail: `data/path-wallet-audit-CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o.md`

## Current Verdict

CyA is still an exit-led wallet. The fresh 6,000-tx sample continues to reject simple fixed TP/SL and time exits. The best tested behavior is still to stay close to CyA's exit, with the best current backtest variant being `50% at +30%, 50% source exit`.

Important update: the low market-cap bucket weakened materially in the fresh sample. The old `$0-5k` bucket was slightly positive; the new `$0-5k` bucket is negative on source exit. That makes the `$5k` minimum market-cap filter more important, not less.

Best current approach:

- Copy once per mint.
- Use `0.25 SOL` fixed-size entries.
- Cap live exposure to `0.75 SOL` per session and `2` open positions.
- Keep the fast local Pump.fun market-cap filter on.
- Keep `PREBUY_MIN_MARKET_CAP_USD=5000` as the minimum; test `10000` or `20000` in shadow before tightening live.
- Keep impact/drift protection.
- Mirror CyA sells until the bot has a native hybrid exit rule for `50% at +30%, 50% source exit`.
- Treat Moralis volume/liquidity/momentum filters as research-only for same-slot copy because they add hot-path latency.
- Do not use the latest 3-entry path audit as a broad strategy sample; use it only as a last-trades sanity check.

## Expanded 10k-Tx Check

This bigger pass widens wallet-level context to `10,000` txs and `1,022` closed campaigns, covering `2026-06-08T19:58:41Z` to `2026-06-17T13:39:38Z`. Candle enrichment increased from `212` to `268` measured entries, but Moralis hit the daily free-plan usage limit during the run, so this is still not a full 600-entry path sample.

Expanded wallet-level stats:

- Wallet tx cache: `10000` txs
- Swaps reconstructed: `7329`
- Buys/sells: `5386 / 1943`
- Campaigns: `1023`
- Closed campaigns: `1022`
- Candle-enriched entries: `268`
- Realized PnL: `+911.87 SOL`
- Closed-campaign win rate: `56.0%`
- Average source ROI: `+32.8%`
- Median source ROI: `+4.1%`
- Profit factor: `2.33`
- Median hold time: `0.62 min`
- Median entry size: `3.008 SOL`
- Top 5 winners contributed `31.8%` of total realized SOL

Expanded strategy read:

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Sell when CyA sells | 268 | 52.2% | +15.6% | +1.8% | 2.56 | -214.6% |
| 50% at +30, 50% CyA exit | 268 | 59.3% | +13.3% | +7.8% | 2.63 | -180.0% |
| 20% trailing stop | 268 | 25.4% | -0.3% | -4.6% | 0.96 | -581.0% |
| TP30 SL20 | 268 | 14.2% | -9.3% | -20.0% | 0.25 | -2491.9% |
| TP50 SL25 | 268 | 14.2% | -9.9% | -25.0% | 0.35 | -2683.0% |
| Time exit 30m | 268 | 12.3% | -28.0% | -24.9% | 0.18 | -7497.6% |
| Time exit 1h | 268 | 10.4% | -31.2% | -34.2% | 0.18 | -8361.8% |
| Time exit 4h | 268 | 9.3% | -37.2% | -39.3% | 0.10 | -9975.9% |
| Time exit 24h | 268 | 7.5% | -40.1% | -42.9% | 0.07 | -10735.6% |

Expanded conclusion:

- The bigger run strengthens the main conclusion: CyA is not a fixed-TP scalp wallet.
- `Sell when CyA sells` has the highest average ROI in the expanded sample.
- The hybrid `50% at +30%, 50% CyA exit` has the better win rate, median ROI, profit factor, and drawdown profile.
- Simple trailing is close to breakeven but still negative.
- Fixed TP/SL and time exits remain clearly bad.
- If we implement one new strategy rule for CyA, it should be the hybrid partial-profit plus mirror-exit rule.

## 100k+ Market-Cap Trades, Last 7d

This focused pass avoids Moralis entirely. It reads the local 10,000-tx Helius cache, prefilters last-7d CyA campaigns by source-fill implied market cap, fetches entry/path candles from GeckoTerminal with 429 backoff, then keeps only entries with candle-based approximate entry MC above `$100k`.

Coverage:

- Window: `2026-06-10T13:39:38Z` to `2026-06-17T13:39:38Z`
- Last-7d campaigns checked: `1021`
- Source-fill 100k+ candidates: `17`
- Measured candle-confirmed 100k+ entries: `17`
- Skipped candidates: `0`
- Median entry MC: `$359.5k`
- Median MFE / MAE: `+47.9% / -70.1%`
- Hit `+30% / +50% / +100%`: `70.6% / 41.2% / 17.6%`
- Source-exit win rate: `29.4%`
- Source-exit median ROI: `-7.2%`
- Realized PnL on this slice: `-33.69 SOL`

100k+ strategy backtest:

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 50% at +30, 50% CyA exit | 17 | 64.7% | +9.6% | +5.1% | 4.50 | -26.4% |
| Sell when CyA sells | 17 | 29.4% | +0.5% | -7.2% | 1.05 | -91.5% |
| 20% trailing stop | 17 | 35.3% | -1.3% | -5.8% | 0.83 | -87.5% |
| TP50 SL25 | 17 | 23.5% | -4.9% | -25.0% | 0.71 | -142.4% |
| TP30 SL20 | 17 | 23.5% | -7.1% | -20.0% | 0.50 | -131.1% |
| Time exit 30m | 17 | 17.6% | -21.2% | -20.9% | 0.35 | -431.1% |
| Time exit 24h | 17 | 29.4% | -33.0% | -31.0% | 0.21 | -571.6% |
| Time exit 1h | 17 | 17.6% | -37.4% | -33.5% | 0.05 | -662.8% |
| Time exit 4h | 17 | 11.8% | -46.4% | -41.5% | 0.04 | -815.6% |

100k+ read:

- This is the strongest argument yet for a CyA-specific hybrid exit.
- 100k+ entries often give a tradable move, but CyA's own final exits were weak in this slice.
- `70.6%` of the 100k+ entries hit `+30%`, but only `29.4%` were source-exit winners.
- Full mirror sells are still better than fixed TP/SL across the broader samples, but for 100k+ entries specifically, partial profit at `+30%` is doing the heavy lifting.
- Do not use time exits for this wallet. Even high-MC entries decay violently after the initial opportunity.
- 100k+ filtering alone is not enough. It needs the hybrid exit rule to turn the MFE into realized edge.

## Fresh Data Basis

Quant pass:

- Wallet tx cache: `6000` txs
- Swaps reconstructed: `4621`
- Buys/sells: `3440 / 1181`
- Campaigns: `619`
- Closed campaigns: `618`
- Candle-enriched entries: `212`
- Window: `2026-06-14T01:13:01Z` to `2026-06-17T12:42:25Z`

Wallet-level stats:

- Realized PnL: `+539.28 SOL`
- Closed-campaign win rate: `55.5%`
- Average source ROI: `+12.5%`
- Median source ROI: `+4.4%`
- Profit factor: `2.14`
- Median hold time: `0.60 min`
- Median entry size: `3.008 SOL`
- Average entry size: `3.721 SOL`
- Largest win/loss: `+82.14 SOL / -22.17 SOL`
- Top 5 winners contributed `45.0%` of total realized SOL

Entry environment:

- Median pre-5m volume: `$8.0k`
- P75 pre-5m volume: `$17.7k`
- Median current liquidity: `$3.6k`
- Median current market cap: `$2.1k`
- Median MFE: `+0.9%`
- Median MAE: `-46.3%`

Approximation caveat: entry market cap uses entry candle USD price and assumes 1B supply. It is useful for Pump.fun/Bags-style historical bucket research, but it is not a precise live DexScreener market cap.

## Strategy Backtest

Fresh `212` candle-enriched entry sample:

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 50% at +30, 50% CyA exit | 212 | 57.5% | +9.9% | +5.5% | 2.18 | -179.9% |
| Sell when CyA sells | 212 | 50.9% | +9.5% | +0.7% | 1.93 | -214.4% |
| 20% trailing stop | 212 | 22.2% | -1.9% | -4.1% | 0.76 | -581.0% |
| TP30 SL20 | 212 | 12.7% | -9.1% | -20.0% | 0.22 | -1923.8% |
| TP50 SL25 | 212 | 12.3% | -10.2% | -24.7% | 0.28 | -2176.5% |
| Time exit 30m | 212 | 12.7% | -25.6% | -17.0% | 0.15 | -5443.4% |
| Time exit 1h | 212 | 9.4% | -28.5% | -25.3% | 0.16 | -6082.1% |
| Time exit 4h | 212 | 8.0% | -35.3% | -32.0% | 0.06 | -7482.2% |
| Time exit 24h | 212 | 7.1% | -37.2% | -39.6% | 0.07 | -7880.2% |

Read:

- The strongest tested live-compatible behavior remains `MIRROR_SELLS=true`.
- The best tested behavior overall is the hybrid `50% at +30%, 50% CyA exit`.
- Fixed TP/SL remains a bad CyA fit.
- Time exits are very poor because the wallet often enters violent paths where the realized edge comes from CyA's exit timing, not passive holding.

## Target And Downside Probabilities

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 34.4% | 2265s |
| `+20%` | 27.8% | 2723s |
| `+30%` | 23.1% | 2880s |
| `+50%` | 18.4% | 3990s |
| `+100%` | 7.5% | 7259s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 62.7% |
| `-20%` | 57.1% |
| `-30%` | 53.8% |

Read:

- CyA entries still go deeply red very often.
- A tight stop will cut many trades before the source exit has a chance to work.
- Keep an emergency stop, but do not expect a normal scalp TP/SL box to capture CyA's edge.

## Market Cap Buckets

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 112 | 43.8% | -2.6% | -4.2% | 10.7% | 8.0% | 6.3% | 0.0% | -3.5% |
| `$5k-10k` | 34 | 61.8% | +14.6% | +10.4% | 41.2% | 38.2% | 35.3% | +18.1% | -70.2% |
| `$10k-20k` | 12 | 41.7% | +7.5% | -7.7% | 33.3% | 16.7% | 16.7% | +6.7% | -83.0% |
| `$20k-50k` | 38 | 63.2% | +33.4% | +12.6% | 52.6% | 44.7% | 28.9% | +20.7% | -92.1% |
| `$50k-100k` | 5 | 80.0% | +63.9% | +36.9% | 40.0% | 40.0% | 40.0% | +4.9% | -49.0% |
| `$100k+` | 11 | 45.5% | +12.3% | -1.9% | 63.6% | 54.5% | 45.5% | +40.3% | -72.3% |

Read:

- `$0-5k` is no longer attractive in the fresh sample.
- `$5k-10k` is still the best balanced low-cap bucket.
- `$20k-50k` has the best larger-sample ROI in this pass, but the median drawdown is extreme.
- The live filter should keep at least `$5k`; shadow testing should compare `$10k` and `$20k` minimums.

Current live filter:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=72
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=5000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false
```

Shadow-test variants:

```env
PREBUY_MIN_MARKET_CAP_USD=10000
```

```env
PREBUY_MIN_MARKET_CAP_USD=20000
```

## Volume Buckets

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 18 | 77.8% | +38.9% | +21.4% | 61.1% | 55.6% | 22.2% | +32.8% | -92.1% |
| `$0-2.5k` | 14 | 57.1% | +11.8% | +9.4% | 0.0% | 0.0% | 0.0% | +0.3% | -19.2% |
| `$2.5k-5k` | 37 | 40.5% | +2.8% | -2.1% | 8.1% | 8.1% | 8.1% | 0.0% | -1.1% |
| `$5k-10k` | 57 | 50.9% | +7.9% | +0.1% | 24.6% | 21.1% | 17.5% | 0.0% | -11.6% |
| `$10k-20k` | 39 | 51.3% | +4.3% | +1.8% | 28.2% | 25.6% | 25.6% | +5.2% | -58.0% |
| `$20k-50k` | 38 | 47.4% | +8.7% | -1.6% | 44.7% | 31.6% | 28.9% | +14.5% | -66.9% |
| `$50k+` | 9 | 44.4% | +11.2% | -2.5% | 33.3% | 22.2% | 11.1% | +3.7% | -74.7% |

Read:

- Volume remains noisy and not clean enough to justify a hot-path Moralis gate.
- Higher volume improves hit-rate behavior in places, but not enough to beat source-exit discipline.

## Momentum Buckets

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 68 | 41.2% | -7.3% | -6.3% | 14.7% | 13.2% | 11.8% | 0.0% | -1.8% |
| `-20% to -5%` | 28 | 42.9% | +12.6% | -0.8% | 14.3% | 14.3% | 14.3% | 0.0% | -5.8% |
| `-5% to 0%` | 11 | 27.3% | -4.7% | -8.1% | 9.1% | 9.1% | 9.1% | +1.3% | -5.0% |
| `0% to +10%` | 16 | 50.0% | +0.5% | -3.6% | 31.3% | 18.8% | 18.8% | +0.8% | -32.1% |
| `+10% to +30%` | 12 | 50.0% | +5.3% | +5.7% | 58.3% | 41.7% | 33.3% | +20.8% | -36.7% |
| `+30%+` | 59 | 62.7% | +24.5% | +13.6% | 35.6% | 28.8% | 25.4% | +9.7% | -72.4% |
| `unknown` | 18 | 77.8% | +38.9% | +21.4% | 61.1% | 55.6% | 22.2% | +32.8% | -92.1% |

Read:

- `+30%+` pre-entry momentum remains a meaningful positive signal.
- Negative momentum, especially `<= -20%`, is weak in the fresh pass.
- Momentum could be a shadow-only filter candidate, but not worth slowing same-slot execution yet.

## Source Buy Size Buckets

| CyA source buy size | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 2 | 0.0% | -22.5% | -22.5% | 0.0% | 0.0% | 0.0% | 0.0% | -0.6% |
| `1-3 SOL` | 0 | - | - | - | - | - | - | - | - |
| `3-6 SOL` | 206 | 51.0% | +9.6% | +0.7% | 28.6% | 23.8% | 18.9% | +1.0% | -48.3% |
| `6-12 SOL` | 3 | 66.7% | +18.9% | +5.9% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| `12+ SOL` | 1 | 100.0% | +25.3% | +25.3% | 0.0% | 0.0% | 0.0% | 0.0% | -0.6% |

Read:

- Most measured entries are still in the `3-6 SOL` source-buy range.
- `0.25 SOL` copy sizing is still conservative relative to CyA's own size.
- Selection and exit behavior matter much more than scaling up size.

## Recent On-Chain Path Audit

The latest path audit covered `6000` txs through `2026-06-17T12:42:25Z`, but only `3` of the freshest `60` campaigns had enough path data to measure. Treat this as a latest-trades check, not a statistically strong sample.

Recent measured entries:

| Token | Buy SOL | Approx Entry MC | MFE | MAE | Source Exit | Earlier Buys | Top Earlier Buyer |
|---|---:|---:|---:|---:|---:|---:|---|
| BAGWORK (`Aa9Ajz...BAGS`) | 5.0320 | $52,897 | +94.3% | -75.9% | +123.5% | 11 | `J8Uow4...hCfx` |
| 10M (`HH3Bt7...BAGS`) | 3.0320 | $93,390 | +11.2% | -46.3% | -1.9% | 14 | `J3x7iG...wk9H` |
| INDEX (`3V3h9a...BAGS`) | 5.0320 | $226,880 | +49.5% | -87.8% | -17.9% | 0 | - |

Recent path read:

- The latest measured trades were higher market-cap Bags entries, not tiny Pump.fun starts.
- Drawdowns were still severe even when MFE was strong.
- Two of three had many earlier buys before CyA; the sample is too small to promote any lead wallet yet.
- The latest on-chain behavior still supports mirror/source-exit logic over fixed TP/SL.

## Recommended Live Validation

```env
TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o

SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MIRROR_SELL_MODE=full

MIN_BUY_SOL=0.25
MAX_BUY_SOL=0.25
MAX_SOL_PER_TOKEN=0.25
MAX_SESSION_DEPLOYED_SOL=0.75
MAX_OPEN_POSITIONS=2

STOP_LOSS_PCT=30
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TAKE_PROFIT_PCT=40
TRAILING_STEPS=20:12,35:25,60:45,100:80,180:140

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=72
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=5000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false

MAX_ENTRY_PRICE_IMPACT_PCT=60
MAX_SOURCE_DRIFT_PCT=25

PUMPSWAP_DIRECT_BUY_ENABLED=true
MIRROR_GRADUATED_BUYS=false

EXECUTION_MULTI_SEND_ENABLED=true
RUST_HOT_SNIPER_ENABLED=true
RUST_HOT_SNIPER_PRIMARY=true
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=60
```

Why full mirror sells:

- The fresh `212`-entry backtest says source exit remains one of the only positive tested behaviors.
- `50% at +30, 50% source exit` tested better, but the current env-only setup does not express that exact hybrid rule cleanly.
- Until the hybrid exit is implemented, full mirror sells are the closest live behavior to the tested edge.

## Shadow Research Variant

Use this to collect live-like sample without sending transactions:

```env
SIMULATION_MODE=true
SHADOW_EXECUTION_MODE=true

MIN_BUY_SOL=0.25
MAX_BUY_SOL=0.25
MAX_SOL_PER_TOKEN=0.25
MAX_SESSION_DEPLOYED_SOL=2.00
MAX_OPEN_POSITIONS=2

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MIRROR_SELL_MODE=full

STOP_LOSS_PCT=30
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TAKE_PROFIT_PCT=40
TRAILING_STEPS=20:12,35:25,60:45,100:80,180:140

LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=72
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=10000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false

MAX_ENTRY_PRICE_IMPACT_PCT=60
MAX_SOURCE_DRIFT_PCT=25

SHADOW_LATENCY_MS=80
SHADOW_ENTRY_SLIPPAGE_PCT=2
SHADOW_EXIT_SLIPPAGE_PCT=3
SHADOW_TX_FEE_SOL=0
SHADOW_FAILURE_RATE_PCT=0
```

## Lead Signals

Lead-wallet and creator filters are not ready for live gating.

Fresh lead/path notes:

- Latest path audit profiled only `3` recent lead entries.
- Median source order: `11`
- Median source delay: `5s`
- Median earlier buys: `11`
- Repeated top earlier buyers: none

Next lead-signal work:

- Re-run `scripts/findCyaLeadSignals.mjs` against the same fresh 6,000-tx window with a larger closed-campaign limit.
- Compare lead wallets/creators against winners and losers, not just appearances.
- Only add a fast lead-wallet prefilter if repeated wallets or creators show enough support across a larger sample.

## Open Questions

- Need to implement and test the hybrid exit rule: `50% at +30%, 50% CyA exit`.
- Need to shadow compare `PREBUY_MIN_MARKET_CAP_USD=5000`, `10000`, and `20000`.
- Need to decide whether Bags/graduated entries should be copied differently from early Pump.fun entries.
- Need to verify whether `PUMPSWAP_DIRECT_BUY_ENABLED=false` plus `MIRROR_GRADUATED_BUYS=false` fully prevents graduated buys or whether a dedicated `SKIP_GRADUATED_BUYS=true` gate is needed.
