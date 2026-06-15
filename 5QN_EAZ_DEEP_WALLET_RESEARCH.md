# 5qn + EAz Deep Wallet Research

Wallets:

- `5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF`
- `EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5`

Last updated: 2026-06-15

The working conclusions below are based on fresh `5000` transaction quant passes for both wallets. The wallet-level sample is large, but the candle/path sample is small: `30` measured 5qn entries and `28` measured EAz entries. Treat the exact percentages carefully, but the shape is very clear.

## Current Verdict

5qn and EAz are high-MFE leader wallets. They often enter tokens that run hard very quickly, but the source exits are not clean enough to blindly mirror. EAz is especially poor as a source-exit mirror in this sample.

Best current approach:

- Track both wallets in one instance.
- Copy once per mint.
- Do not mirror source sells by default.
- Use same-slot/next-slot infra and aggressive landing fees.
- Use bot-managed exits, not source exits.
- Use a fast local Pump.fun market-cap filter.
- Start with `$20k+` entry market cap.
- If you want stricter quality and fewer trades, test `$50k+`.
- Keep Moralis volume/liquidity/momentum filters research-only because same-slot needs speed.

## Data Basis

### 5qn Quant Pass

- Wallet tx cache: `5000` txs
- Swaps reconstructed: `1391`
- Buys/sells: `677 / 714`
- Campaigns: `422`
- Closed campaigns: `316`
- Candle-enriched entries: `30`
- Window: `2026-02-10T19:00:27Z` to `2026-06-15T14:44:30Z`

Wallet-level stats:

- Realized PnL: `+678.10 SOL`
- Closed-campaign win rate: `43.7%`
- Average source ROI: `+12.9%`
- Median source ROI: `-6.8%`
- Profit factor: `3.17`
- Median hold time: `3.53 min`
- Average hold time: `59.31 min`
- Median entry size: `9.726 SOL`
- Average entry size: `6.869 SOL`
- Top 5 winners contributed `50.7%` of total realized SOL

Entry environment:

- Median pre-5m volume: `$4.7k`
- P75 pre-5m volume: `$11.0k`
- Median current liquidity: `$2.9k`
- Median current market cap: `$1.9k`
- Median MFE: `+138.8%`
- Median MAE: `-94.7%`

### EAz Quant Pass

- Wallet tx cache: `5000` txs
- Swaps reconstructed: `1272`
- Buys/sells: `713 / 559`
- Campaigns: `397`
- Closed campaigns: `293`
- Candle-enriched entries: `28`
- Window: `2026-02-24T04:39:58Z` to `2026-06-15T14:44:31Z`

Wallet-level stats:

- Realized PnL: `-59.10 SOL`
- Closed-campaign win rate: `39.6%`
- Average source ROI: `+34.7%`
- Median source ROI: `-12.6%`
- Profit factor: `0.85`
- Median hold time: `4.17 min`
- Average hold time: `67.73 min`
- Median entry size: `9.507 SOL`
- Average entry size: `6.893 SOL`
- Median MFE: `+138.8%`
- Median MAE: `-94.8%`

Important caveat: "approx entry MC" is `entry candle USD price * 1B token supply`. This is most useful for Pump.fun-style 1B supply tokens. It is better for historical bucket research than live/current DexScreener market cap, but still approximate.

## Strategy Backtest

### 5qn

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Time exit 1h | 30 | 33.3% | +34.6% | -46.7% | 1.73 | -404.5% |
| 20% trailing stop | 30 | 76.7% | +28.5% | +25.9% | 16.26 | -20.0% |
| Time exit 30m | 30 | 43.3% | +25.8% | 0.0% | 1.66 | -316.6% |
| 50% at +30, 50% source exit | 30 | 43.3% | +19.5% | -30.5% | 1.89 | -371.3% |
| Sell when 5qn sells | 30 | 43.3% | +18.0% | -73.9% | 1.36 | -933.4% |
| TP60 SL30 | 30 | 53.3% | +17.1% | +2.1% | 2.56 | -85.8% |
| TP50 SL25 | 30 | 56.7% | +16.8% | +26.5% | 3.02 | -75.0% |
| TP40 SL25 | 30 | 56.7% | +11.8% | +21.5% | 2.42 | -75.0% |
| TP30 SL20 | 30 | 50.0% | +5.1% | +0.6% | 1.64 | -60.0% |
| Time exit 24h | 30 | 13.3% | -60.5% | -94.4% | 0.14 | -2056.9% |

### EAz

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 20% trailing stop | 28 | 78.6% | +30.2% | +27.1% | 16.10 | -20.0% |
| Time exit 1h | 28 | 32.1% | +29.1% | -81.8% | 1.57 | -404.5% |
| Time exit 30m | 28 | 42.9% | +21.8% | -15.6% | 1.52 | -316.6% |
| TP50 SL25 | 28 | 57.1% | +16.2% | +26.5% | 2.82 | -75.0% |
| TP60 SL30 | 28 | 53.6% | +16.2% | +2.1% | 2.38 | -85.8% |
| TP40 SL25 | 28 | 57.1% | +11.2% | +21.5% | 2.26 | -75.0% |
| TP30 SL20 | 28 | 50.0% | +4.4% | +0.6% | 1.52 | -60.0% |
| 50% at +30, 50% source exit | 28 | 35.7% | +3.5% | -32.0% | 1.15 | -336.0% |
| Sell when EAz sells | 28 | 32.1% | -9.6% | -79.5% | 0.82 | -955.8% |
| Time exit 24h | 28 | 10.7% | -65.3% | -94.6% | 0.14 | -2056.9% |

Read:

- The best tested behavior for both wallets is a path trailing stop.
- Source exits are not the best fit, especially for EAz.
- A simple `TP50 SL25` is positive, but it gives up runner upside compared with trailing.
- The live bot's `TRAILING_STEPS` are stair-step profit floors, not a true percent-below-peak trail. The recommended env below approximates the tested `20% trailing stop`.

## Target And Downside Probabilities

### 5qn

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 80.0% | 398s |
| `+20%` | 76.7% | 414s |
| `+30%` | 76.7% | 466s |
| `+50%` | 76.7% | 570s |
| `+100%` | 50.0% | 1261s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 80.0% |
| `-20%` | 80.0% |
| `-30%` | 80.0% |

### EAz

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 82.1% | 398s |
| `+20%` | 78.6% | 415s |
| `+30%` | 78.6% | 469s |
| `+50%` | 78.6% | 565s |
| `+100%` | 50.0% | 1281s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 85.7% |
| `-20%` | 85.7% |
| `-30%` | 85.7% |

Read:

- These wallets produce huge upside, but the same tokens often later collapse.
- Same-slot or next-slot entry matters. Late entry converts runner edge into bag risk.
- Exits should be automated. Waiting for source sells is too slow and too noisy.

## Market Cap Buckets

### 5qn

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 5 | 60.0% | -26.7% | +1.3% | 0.0% | 0.0% | 0.0% | 0.0% | +0.7% | 0.0% |
| `$5k-10k` | 0 | - | - | - | - | - | - | - | - | - |
| `$10k-20k` | 2 | 50.0% | +35.3% | +35.3% | 0.0% | 0.0% | 0.0% | 0.0% | +10.9% | -81.8% |
| `$20k-50k` | 16 | 18.8% | -38.9% | -94.6% | 100.0% | 100.0% | 100.0% | 56.3% | +225.6% | -94.8% |
| `$50k-100k` | 4 | 100.0% | +257.5% | +308.2% | 100.0% | 100.0% | 100.0% | 75.0% | +140.7% | -97.4% |
| `$100k+` | 3 | 66.7% | +64.9% | +46.6% | 100.0% | 100.0% | 100.0% | 100.0% | +714.1% | -47.4% |

### EAz

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 4 | 50.0% | -18.2% | +0.6% | 0.0% | 0.0% | 0.0% | 0.0% | +1.5% | 0.0% |
| `$5k-10k` | 0 | - | - | - | - | - | - | - | - | - |
| `$10k-20k` | 2 | 0.0% | -61.8% | -61.8% | 0.0% | 0.0% | 0.0% | 0.0% | +10.9% | -81.8% |
| `$20k-50k` | 16 | 12.5% | -43.5% | -94.6% | 100.0% | 100.0% | 100.0% | 56.3% | +225.6% | -94.8% |
| `$50k-100k` | 4 | 100.0% | +149.4% | +150.8% | 100.0% | 100.0% | 100.0% | 75.0% | +140.7% | -97.4% |
| `$100k+` | 2 | 50.0% | +13.3% | +13.3% | 100.0% | 100.0% | 100.0% | 100.0% | +578.2% | -72.1% |

Read:

- Below `$20k` is weak for runner/scalp behavior in this sample.
- `$20k-$50k` has very high MFE but terrible source exits. This is exactly why bot-managed exits matter.
- `$50k+` is the cleanest quality bucket but has a small sample.

Current live filter:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=20000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false
```

Stricter quality variant:

```env
PREBUY_MIN_MARKET_CAP_USD=50000
PREBUY_MAX_MARKET_CAP_USD=
```

## Volume Buckets

Volume is useful, but it requires Moralis or another slower external call if used live. Keep this research-only for same-slot.

### 5qn

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 14 | 14.3% | -39.9% | -94.7% | 100.0% | 100.0% | 100.0% | 50.0% | +122.4% | -94.9% |
| `$2.5k-5k` | 3 | 66.7% | +22.1% | +1.4% | 33.3% | 33.3% | 33.3% | 33.3% | +561.2% | -85.8% |
| `$5k-10k` | 4 | 25.0% | -49.2% | -69.5% | 25.0% | 25.0% | 25.0% | 25.0% | +7.6% | -41.1% |
| `$10k-20k` | 2 | 100.0% | +152.8% | +152.8% | 50.0% | 50.0% | 50.0% | 0.0% | +37.3% | -48.8% |
| `$50k+` | 6 | 83.3% | +153.7% | +131.7% | 100.0% | 100.0% | 100.0% | 100.0% | +328.4% | -96.4% |

### EAz

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 14 | 14.3% | -42.8% | -94.7% | 100.0% | 100.0% | 100.0% | 50.0% | +122.4% | -94.9% |
| `$2.5k-5k` | 3 | 0.0% | -43.1% | -41.4% | 33.3% | 33.3% | 33.3% | 33.3% | +561.2% | -85.8% |
| `$5k-10k` | 3 | 0.0% | -55.9% | -76.8% | 33.3% | 33.3% | 33.3% | 33.3% | +15.2% | -82.1% |
| `$10k-20k` | 2 | 100.0% | +147.6% | +147.6% | 50.0% | 50.0% | 50.0% | 0.0% | +37.3% | -48.8% |
| `$50k+` | 5 | 80.0% | +66.4% | +65.8% | 100.0% | 100.0% | 100.0% | 100.0% | +214.5% | -96.8% |

Research-only volume filter:

```env
PREBUY_MORALIS_FILTER_ENABLED=true
PREBUY_MIN_VOLUME_USD=50000
PREBUY_MIN_LIQUIDITY_USD=
PREBUY_MIN_MOMENTUM_PCT=
PREBUY_MORALIS_TIMEOUT_MS=750
PREBUY_FILTER_FAIL_OPEN=false
```

## Momentum Buckets

### 5qn

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 4 | 50.0% | -33.7% | -29.3% | 0.0% | 0.0% | 0.0% | 0.0% | +0.7% | 0.0% |
| `0% to +10%` | 1 | 100.0% | +24.6% | +24.6% | 100.0% | 100.0% | 100.0% | 100.0% | +8465.0% | -87.3% |
| `+10% to +30%` | 3 | 33.3% | -36.6% | -68.6% | 100.0% | 100.0% | 100.0% | 100.0% | +1115.9% | -47.4% |
| `+30%+` | 7 | 85.7% | +188.2% | +216.8% | 71.4% | 71.4% | 71.4% | 57.1% | +138.8% | -96.8% |
| `unknown` | 14 | 14.3% | -39.9% | -94.7% | 100.0% | 100.0% | 100.0% | 50.0% | +122.4% | -94.9% |

### EAz

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 3 | 66.7% | -24.2% | +1.3% | 0.0% | 0.0% | 0.0% | 0.0% | +1.5% | 0.0% |
| `0% to +10%` | 1 | 0.0% | -8.9% | -8.9% | 100.0% | 100.0% | 100.0% | 100.0% | +8465.0% | -87.3% |
| `+10% to +30%` | 2 | 0.0% | -77.9% | -77.9% | 100.0% | 100.0% | 100.0% | 100.0% | +779.1% | -68.8% |
| `+30%+` | 7 | 71.4% | +81.2% | +65.8% | 71.4% | 71.4% | 71.4% | 57.1% | +138.8% | -96.8% |
| `unknown` | 14 | 14.3% | -42.8% | -94.7% | 100.0% | 100.0% | 100.0% | 50.0% | +122.4% | -94.9% |

Read:

- `+30%+` momentum is the cleanest known momentum bucket.
- But momentum coverage is thin and fetching it live costs time.
- Do not let Moralis momentum slow down a same-slot instance yet.

## Source Buy Size Buckets

### 5qn

| 5qn source buy size | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 6 | 100.0% | +268.3% | +308.2% | 100.0% | 100.0% | 100.0% | 83.3% | +178.5% | -96.6% |
| `1-3 SOL` | 5 | 60.0% | +29.8% | +24.6% | 60.0% | 60.0% | 60.0% | 60.0% | +714.1% | -6.8% |
| `3-6 SOL` | 1 | 0.0% | -68.6% | -68.6% | 100.0% | 100.0% | 100.0% | 100.0% | +442.2% | -47.4% |
| `6-12 SOL` | 18 | 22.2% | -63.9% | -94.4% | 72.2% | 72.2% | 72.2% | 33.3% | +75.7% | -94.7% |

### EAz

| EAz source buy size | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 7 | 100.0% | +175.8% | +232.4% | 100.0% | 100.0% | 100.0% | 85.7% | +214.5% | -96.8% |
| `1-3 SOL` | 2 | 0.0% | -42.9% | -42.9% | 50.0% | 50.0% | 50.0% | 50.0% | +4232.5% | -43.7% |
| `3-6 SOL` | 1 | 0.0% | -67.8% | -67.8% | 100.0% | 100.0% | 100.0% | 100.0% | +442.2% | -47.4% |
| `6-12 SOL` | 18 | 11.1% | -74.7% | -94.4% | 72.2% | 72.2% | 72.2% | 33.3% | +75.7% | -94.7% |

Read:

- The small source buys are much cleaner.
- The big `6-12 SOL` buys still produce MFE, but source exits are ugly.
- We do not currently have a source-buy-size prefilter. If we build one, `MAX_SOURCE_BUY_SOL=3` is a strong candidate for this cluster.

## Current Liquidity Buckets

Current liquidity is from pair lookup at audit time, not guaranteed entry-time liquidity.

### 5qn

| Current liquidity | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-2.5k` | 1 | 0.0% | -95.3% | -95.3% | 100.0% | 100.0% | 100.0% | 0.0% | +52.2% | -95.3% |
| `$2.5k-5k` | 18 | 33.3% | +26.6% | -94.2% | 100.0% | 100.0% | 100.0% | 61.1% | +147.1% | -95.1% |
| `$5k-10k` | 4 | 75.0% | +54.9% | +35.6% | 100.0% | 100.0% | 100.0% | 100.0% | +1067.2% | -67.4% |
| `unknown` | 7 | 57.1% | -9.0% | +1.3% | 0.0% | 0.0% | 0.0% | 0.0% | +2.2% | -0.6% |

### EAz

| Current liquidity | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-2.5k` | 1 | 0.0% | -95.3% | -95.3% | 100.0% | 100.0% | 100.0% | 0.0% | +52.2% | -95.3% |
| `$2.5k-5k` | 18 | 33.3% | +0.3% | -94.2% | 100.0% | 100.0% | 100.0% | 61.1% | +147.1% | -95.1% |
| `$5k-10k` | 3 | 33.3% | +5.9% | -8.9% | 100.0% | 100.0% | 100.0% | 100.0% | +714.1% | -87.3% |
| `unknown` | 6 | 33.3% | -32.7% | -20.7% | 0.0% | 0.0% | 0.0% | 0.0% | +2.9% | -1.2% |

Read:

- Liquidity is less useful than entry MC and source buy size.
- Keep liquidity as research-only unless we accept slower hot-path checks.

## Recommended Live Validation

```env
TRACKED_WALLETS=5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF,EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5

SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=false

MIN_BUY_SOL=0.15
MAX_BUY_SOL=0.15
MAX_SOL_PER_TOKEN=0.15
MAX_SESSION_DEPLOYED_SOL=1.20
MAX_OPEN_POSITIONS=6

STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TAKE_PROFIT_PCT=
TRAILING_STEPS=10:-8,20:0,30:10,50:30,80:55,120:90,200:150,350:260,600:450

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=20000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false

MAX_ENTRY_PRICE_IMPACT_PCT=60
MAX_SOURCE_DRIFT_PCT=25

SKIP_SOURCE_PREEXISTING_MINTS=true
SOURCE_PREEXISTING_SCAN_TX_LIMIT=300

EXECUTION_MULTI_SEND_ENABLED=true
RUST_HOT_SNIPER_ENABLED=true
RUST_HOT_SNIPER_PRIMARY=true
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=20

TIP_LAMPORTS=2500000
RUST_HOT_SNIPER_JITO_TIP_LAMPORTS=3000000
RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS=500000
JITO_TIP_ORACLE_ENABLED=true
JITO_TIP_ORACLE_PERCENTILE=p95
JITO_TIP_ORACLE_MIN_LAMPORTS=1000000
JITO_TIP_ORACLE_MAX_LAMPORTS=10000000
PRIORITY_FEE_MICRO_LAMPORTS=2000000
DYNAMIC_PRIORITY_FEE_ENABLED=true
DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS=10000000
RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS=10000000
```

Why these settings:

- `MIRROR_SELLS=false` because source exits are noisy.
- `STOP_LOSS_PCT=25` and early trailing floors approximate the tested `20% trailing stop`.
- `$20k+` MC keeps the cluster focused on the buckets where MFE actually appears.
- `MAX_SESSION_DEPLOYED_SOL=1.20` lets the bot take up to eight `0.15 SOL` signals in a session while avoiding full-wallet exposure.
- Aggressive fee settings are included because these wallets require same-slot or next-slot landing to capture the MFE.

Scalp-only variant:

```env
TAKE_PROFIT_PCT=50
TRAILING_STEPS=20:0,30:10,40:20
```

Higher-quality, fewer-trades variant:

```env
PREBUY_MIN_MARKET_CAP_USD=50000
```

## VPS Update Script

This updates only the `5qn` instance env and restarts the service.

```bash
cd /root/bot/FinalBot-5qn

cp .env ".env.backup-before-5qn-eaz-deep-research-$(date +%Y%m%d-%H%M%S)"

node -e "const fs=require('fs'); const p='.env'; const u={
TRACKED_WALLETS:'5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF,EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5',

SIMULATION_MODE:'false',
SHADOW_EXECUTION_MODE:'false',

BUY_ONCE_PER_TOKEN:'true',
MIRROR_SELLS:'false',

MIN_BUY_SOL:'0.15',
MAX_BUY_SOL:'0.15',
MAX_SOL_PER_TOKEN:'0.15',
MAX_SESSION_DEPLOYED_SOL:'1.20',
MAX_OPEN_POSITIONS:'6',

STOP_LOSS_PCT:'25',
MIN_HOLD_BEFORE_STOP_LOSS_MS:'8000',
TAKE_PROFIT_PCT:'',
TRAILING_STEPS:'10:-8,20:0,30:10,50:30,80:55,120:90,200:150,350:260,600:450',

PREBUY_MORALIS_FILTER_ENABLED:'false',
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED:'true',
LOCAL_PUMPFUN_SOL_PRICE_USD:'66',
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED:'true',
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS:'300000',
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS:'2500',
PREBUY_MIN_MARKET_CAP_USD:'20000',
PREBUY_MAX_MARKET_CAP_USD:'',
PREBUY_FILTER_FAIL_OPEN:'false',

MAX_ENTRY_PRICE_IMPACT_PCT:'60',
MAX_SOURCE_DRIFT_PCT:'25',

SKIP_SOURCE_PREEXISTING_MINTS:'true',
SOURCE_PREEXISTING_SCAN_TX_LIMIT:'300',

USE_JITO_DIRECT:'true',
USE_JITO_FALLBACK:'true',
JITO_SEND_MODE:'race',
BROADCAST_RACE_ENABLED:'true',
EXECUTION_MULTI_SEND_ENABLED:'true',
SENDER_RPC_FALLBACK_ENABLED:'true',

RUST_HOT_SNIPER_ENABLED:'true',
RUST_HOT_SNIPER_PRIMARY:'true',
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS:'20',

TIP_LAMPORTS:'2500000',
RUST_HOT_SNIPER_JITO_TIP_LAMPORTS:'3000000',
RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS:'500000',
JITO_TIP_ORACLE_ENABLED:'true',
JITO_TIP_ORACLE_PERCENTILE:'p95',
JITO_TIP_ORACLE_REFRESH_MS:'5000',
JITO_TIP_ORACLE_MIN_LAMPORTS:'1000000',
JITO_TIP_ORACLE_MAX_LAMPORTS:'10000000',
PRIORITY_FEE_MICRO_LAMPORTS:'2000000',
DYNAMIC_PRIORITY_FEE_ENABLED:'true',
DYNAMIC_PRIORITY_FEE_REFRESH_MS:'5000',
DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS:'10000000',
RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS:'10000000'
}; let s=fs.readFileSync(p,'utf8'); for (const [k,v] of Object.entries(u)){ const re=new RegExp('^'+k+'=.*$','m'); s=re.test(s)?s.replace(re,k+'='+v):s.trimEnd()+'\n'+k+'='+v+'\n'; } fs.writeFileSync(p,s); console.log('5qn + EAz deep-research env updated');"

npm run build
npm run rust-sidecar:jito-build

systemctl restart finalbot-5qn
journalctl -u finalbot-5qn -f
```

## Follow-Up Research

- Build a source-buy-size filter. Candidate: skip source buys above `3 SOL` for this cluster, or at least compare live results by source buy size.
- Add true percent-below-peak trailing mode. The quant-tested `20% trailing stop` is not identical to the current stair-step trailing floors.
- Re-run this after another 10-20 live detected entries because the candle-enriched sample is small.
- Compare `$20k+` versus `$50k+` MC filters once we have same-slot/next-slot landing telemetry.
