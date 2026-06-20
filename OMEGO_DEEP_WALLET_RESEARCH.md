# Omego Deep Wallet Research

Wallet: `omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq`

Last updated: 2026-06-20

## 2026-06-20 Fresh Audit Update

Two fresh audits were run after the missed-sell concern:

- Path audit: latest `3000` txs, covering `2026-06-19T17:17:41Z` to `2026-06-20T05:57:20Z`.
- Ledger audit: latest `10000` txs, covering `2026-06-18T12:04:04Z` to `2026-06-20T06:01:27Z`.

Fresh path audit:

- Swaps reconstructed: `2976`
- Campaigns: `201`
- Candle-measured entries: `74`
- Realized PnL over this short path window: `+15.798 SOL`
- Closed campaigns: `198`
- Source-exit win rate: `42.9%`
- Avg / median source exit: `+0.2% / -0.8%`
- Median MFE / MAE: `+30.9% / -48.6%`
- Hit rates: `+30% = 38/74`, `+50% = 25/74`, `+100% = 10/74`, `+200% = 4/74`
- Median approximate entry MC: `$6.9k`

Fresh ledger audit:

- Transactions fetched: `10000`
- Classified buy/sell token-flow txs: `9928`
- Positions touched: `600`
- Realized trades: `4854`
- Realized PnL: `+72.790 SOL`
- Realized ROI on sold cost basis: `+2.0%`
- Win rate: `45.8%`
- Median ROI/trade: `-1.2%`
- Profit factor: `1.33`
- Priced unrealized PnL: `-54.042 SOL`
- Combined realized + priced unrealized: `+18.748 SOL`

Fresh strategy result:

| Strategy | Trades | Avg ROI | Median ROI | Win Rate | Profit Factor |
|---|---:|---:|---:|---:|---:|
| Mirror source exit | 74 | `+0.2%` | `-0.8%` | `43.2%` | `1.07` |
| TP30 SL25 | 74 | `-1.4%` | `-12.1%` | `43.2%` | `0.89` |
| Trail arm45 give30 SL35 | 74 | `-4.1%` | `-24.0%` | `39.2%` | `0.78` |
| TP50 SL30 | 74 | `-5.1%` | `-30.0%` | `32.4%` | `0.72` |

Read:

- Omego is profitable in aggregate because the volume is enormous and winners pay for many tiny scratches/losses.
- Source exits are still only barely positive on this short sample, and median source exit remains negative.
- The main live risk is not just strategy quality; it is operational drift. Missing a small percentage of Omego sells matters because he trades constantly and often exits quickly.
- The new `SOURCE_HOLD_EXIT_*` guard should be enabled for Omego live testing so the bot can self-close if it holds a token that the tracked source wallet no longer holds.

Recommended source-hold guard for Omego:

```env
SOURCE_HOLD_EXIT_ENABLED=true
SOURCE_HOLD_EXIT_INTERVAL_MS=10000
SOURCE_HOLD_EXIT_CONSECUTIVE_MISSES=2
SOURCE_HOLD_EXIT_MIN_POSITION_AGE_MS=15000
```

This is not a hot-path entry filter. It is a post-entry safety loop using RPC token-balance checks. It should reduce stale positions when a sell signal is missed, without adding latency to buys.

The working conclusions below are based mainly on a fresh `5000` transaction quant pass with `214` candle-enriched entries. I also reference the older path/lead-order audit with `6000` transactions and `119` measured entries because it has earlier-buyer/source-order clues that the newer quant pass does not include.

## Current Verdict

Omego is not a good blind source-exit mirror. The wallet made realized SOL, but source exits are weak on a per-token ROI basis. The edge is the entry path: a meaningful number of entries produce tradable MFE, but many later fade hard.

Best current approach:

- Copy once per mint.
- Do not mirror Omego sells for live validation.
- Use bot-managed exits: tight-ish emergency stop plus early profit-locking trailing steps.
- Use small fixed-size entries.
- Use the fast local Pump.fun market-cap filter.
- Start with `$20k-$50k` entry market cap.
- If trade frequency is too low, test a looser `$12k+` min market cap.
- Keep Moralis volume/liquidity/momentum filters research-only for now because same-slot needs speed.
- Treat earlier-buyer count as a strong future lead, but it is not currently a live hot-path filter.

## Data Basis

Fresh quant pass:

- Wallet tx cache: `5000` txs
- Swaps reconstructed: `4963`
- Buys/sells: `2483 / 2480`
- Campaigns: `332`
- Closed campaigns: `326`
- Candle-enriched entries: `214`
- Window: `2026-06-14T17:36:06Z` to `2026-06-15T13:44:35Z`

Wallet-level stats:

- Realized PnL: `+26.60 SOL`
- Closed-campaign win rate: `43.6%`
- Average source ROI: `-3.9%`
- Median source ROI: `-1.3%`
- Profit factor: `1.73`
- Median hold time: `19.38 min`
- Average hold time: `114.80 min`
- Median entry size: `0.623 SOL`
- Average entry size: `0.665 SOL`
- Top 5 winners contributed `40.9%` of total realized SOL

Entry environment:

- Median pre-5m volume: `$1.6k`
- P75 pre-5m volume: `$4.7k`
- Median current liquidity: `$4.8k`
- Median current market cap: `$2.3k`
- Median MFE: `+18.2%`
- Median MAE: `-43.6%`

Important caveat: "approx entry MC" is `entry candle USD price * 1B token supply`. This is most useful for Pump.fun-style 1B supply tokens. It is better for historical bucket research than live/current DexScreener market cap, but still approximate.

## Strategy Backtest

Important accounting note: the old `Max drawdown` column below was a cumulative per-trade ROI drawdown, not bankroll drawdown. It can go below `-100%` because it is summing repeated trade ROI losses. Treat it as percentage points (`pp`) and use it only for strategy ordering, not capital loss.

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Cumulative DD |
|---|---:|---:|---:|---:|---:|---:|
| 20% trailing stop | 214 | 36.4% | +9.4% | -2.2% | 2.26 | -160.1pp |
| 50% at +30, 50% source exit | 214 | 57.0% | +1.5% | +3.7% | 1.25 | -265.8pp |
| TP60 SL30 | 214 | 29.0% | -1.0% | -23.5% | 0.94 | -525.2pp |
| TP50 SL25 | 214 | 28.5% | -1.3% | -25.0% | 0.91 | -458.1pp |
| TP30 SL20 | 214 | 31.3% | -2.3% | -20.0% | 0.79 | -602.4pp |
| Sell when Omego sells | 214 | 42.5% | -4.4% | -1.8% | 0.41 | -989.0pp |
| Time exit 30m | 214 | 29.0% | -12.1% | -16.6% | 0.51 | -2607.4pp |
| Time exit 1h | 214 | 23.4% | -17.3% | -24.2% | 0.39 | -3715.7pp |
| Time exit 4h | 214 | 15.9% | -26.8% | -32.4% | 0.20 | -5749.8pp |

Read:

- The best tested behavior is a path trailing stop, not source exits.
- `MIRROR_SELLS=true` is a bad default for Omego.
- Fixed TP/SL boxes are still weak, even though many entries hit `+30%`.
- The live bot's `TRAILING_STEPS` are stair-step profit floors, not a true percent-below-peak trail. The recommended env below approximates the tested behavior with a `25%` stop and early profit locks.

## Target And Downside Probabilities

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 57.0% | 1422s |
| `+20%` | 46.3% | 2817s |
| `+30%` | 41.1% | 2901s |
| `+50%` | 31.8% | 3556s |
| `+100%` | 18.2% | 5109s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 79.0% |
| `-20%` | 70.1% |
| `-30%` | 60.7% |

Read:

- Omego entries commonly produce tradable upside, but also commonly go red.
- A pure fixed take-profit strategy misses too much because the downside arrives often.
- Bot-managed exits need to react fast once the position has any profit.

## Market Cap Buckets

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 120 | 27.5% | -8.3% | -4.8% | 40.8% | 36.7% | 30.0% | +14.4% | -26.4% |
| `$5k-10k` | 45 | 51.1% | -1.9% | +0.5% | 46.7% | 44.4% | 33.3% | +17.0% | -60.0% |
| `$10k-20k` | 20 | 45.0% | -2.6% | -0.8% | 45.0% | 40.0% | 20.0% | +17.5% | -79.8% |
| `$20k-50k` | 28 | 89.3% | +6.1% | +6.2% | 71.4% | 57.1% | 46.4% | +44.3% | -78.3% |
| `$50k-100k` | 1 | 100.0% | +24.7% | +24.7% | 0.0% | 0.0% | 0.0% | +2.5% | -97.0% |
| `$100k+` | 0 | - | - | - | - | - | - | - | - |

Read:

- `$20k-50k` is the cleanest fresh-sample bucket.
- `$0-5k` has enough MFE to look tempting, but source-exit quality is terrible.
- `$5k-10k` is tradable but noisier than the higher-MC range.
- `$50k+` is too thin to trust.

Older path audit check:

| Bucket | n | Hit +30 | Hit +50 | Source win | Median source exit | Median MFE |
|---|---:|---:|---:|---:|---:|---:|
| `MC < 5k` | 48 | 12.5% | 6.3% | 35.4% | -1.9% | +7.0% |
| `MC 5k-8k` | 41 | 43.9% | 26.8% | 51.2% | +1.3% | +22.9% |
| `MC 8k-12k` | 16 | 43.8% | 37.5% | 68.8% | +2.3% | +25.3% |
| `MC 12k-20k` | 11 | 72.7% | 45.5% | 81.8% | +3.4% | +45.9% |
| `MC >=20k` | 3 | 66.7% | 33.3% | 100.0% | +2.3% | +33.1% |

Current live filter:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=20000
PREBUY_MAX_MARKET_CAP_USD=50000
PREBUY_FILTER_FAIL_OPEN=false
```

Looser variant if trade count is too low:

```env
PREBUY_MIN_MARKET_CAP_USD=12000
PREBUY_MAX_MARKET_CAP_USD=50000
```

## Volume Buckets

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 30 | 80.0% | +2.3% | +4.6% | 56.7% | 46.7% | 36.7% | +28.8% | -67.2% |
| `$0-2.5k` | 98 | 34.7% | -6.6% | -4.1% | 44.9% | 40.8% | 30.6% | +16.4% | -31.4% |
| `$2.5k-5k` | 34 | 29.4% | -6.3% | -4.8% | 32.4% | 29.4% | 20.6% | +1.0% | -38.4% |
| `$5k-10k` | 25 | 36.0% | -4.3% | -2.0% | 36.0% | 36.0% | 32.0% | +15.4% | -57.2% |
| `$10k-20k` | 18 | 50.0% | +0.7% | +0.1% | 72.2% | 66.7% | 55.6% | +82.7% | -63.4% |
| `$20k-50k` | 9 | 55.6% | -5.4% | +0.4% | 55.6% | 33.3% | 22.2% | +22.3% | -82.6% |
| `$50k+` | 0 | - | - | - | - | - | - | - | - |

Read:

- `$10k-20k` volume has the best path behavior.
- Volume is not enough by itself. Some no-prevolume rows look good because they overlap with higher MC and missing pre-candle coverage.
- Keep Moralis volume disabled for same-slot speed.

Research-only volume filter:

```env
PREBUY_MORALIS_FILTER_ENABLED=true
PREBUY_MIN_VOLUME_USD=10000
PREBUY_MIN_LIQUIDITY_USD=
PREBUY_MIN_MOMENTUM_PCT=
PREBUY_MORALIS_TIMEOUT_MS=750
PREBUY_FILTER_FAIL_OPEN=false
```

## Momentum Buckets

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 35 | 31.4% | -7.6% | -3.2% | 45.7% | 45.7% | 42.9% | +14.3% | -15.2% |
| `-20% to -5%` | 18 | 16.7% | -8.6% | -6.7% | 38.9% | 33.3% | 33.3% | +7.4% | -6.9% |
| `-5% to 0%` | 14 | 35.7% | -10.3% | -2.2% | 35.7% | 28.6% | 7.1% | +29.2% | -38.5% |
| `0% to +10%` | 23 | 30.4% | -6.9% | -4.8% | 30.4% | 30.4% | 21.7% | +7.7% | -26.4% |
| `+10% to +30%` | 31 | 29.0% | -6.0% | -3.0% | 54.8% | 41.9% | 25.8% | +23.5% | -45.4% |
| `+30%+` | 63 | 50.8% | -1.5% | +0.3% | 47.6% | 44.4% | 34.9% | +18.2% | -54.5% |
| `unknown` | 30 | 80.0% | +2.3% | +4.6% | 56.7% | 46.7% | 36.7% | +28.8% | -67.2% |

Read:

- `+30%+` momentum is the least bad known-momentum bucket, but not good enough to be a main live filter.
- Negative momentum can still produce MFE but source exits are bad.
- Momentum should stay research-only until we can combine it cheaply with MC and source-order signals.

## Source Buy Size Buckets

| Omego source buy size | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 195 | 41.0% | -4.8% | -2.0% | 46.2% | 42.1% | 32.8% | +18.2% | -40.4% |
| `1-3 SOL` | 19 | 57.9% | +0.0% | +0.4% | 47.4% | 31.6% | 21.1% | +19.4% | -74.3% |
| `3-6 SOL` | 0 | - | - | - | - | - | - | - | - |
| `6-12 SOL` | 0 | - | - | - | - | - | - | - | - |
| `12+ SOL` | 0 | - | - | - | - | - | - | - | - |

Read:

- Most Omego buys are under `1 SOL`.
- Bigger source buys are slightly better for source exits, but not better for `+30/+50` hit rates.
- The bot does not currently have a source-buy-size prefilter, and this is not strong enough to prioritize building one.

## Current Liquidity Buckets

Current liquidity is from pair lookup at audit time, not guaranteed entry-time liquidity. Coverage is poor because `178/214` rows are unknown.

| Current liquidity | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-2.5k` | 4 | 50.0% | -9.1% | -1.0% | 0.0% | 0.0% | 0.0% | 0.0% | -51.8% |
| `$2.5k-5k` | 15 | 93.3% | +11.6% | +7.9% | 46.7% | 46.7% | 26.7% | +19.9% | -92.9% |
| `$5k-10k` | 5 | 100.0% | +8.1% | +4.5% | 100.0% | 40.0% | 40.0% | +28.8% | -81.6% |
| `$10k-25k` | 11 | 81.8% | +5.9% | +5.9% | 81.8% | 72.7% | 72.7% | +83.9% | -44.1% |
| `$25k+` | 1 | 100.0% | +6.6% | +6.6% | 100.0% | 100.0% | 0.0% | +38.4% | -23.1% |
| `unknown` | 178 | 33.7% | -6.7% | -3.1% | 43.3% | 39.3% | 30.3% | +15.7% | -38.4% |

Read:

- Liquidity looks promising when known, especially `$10k-25k`.
- Do not use it as a live same-slot requirement yet because coverage is low and fetching it in hot path costs time.

## Lead / Sniping Clues

Older path audit:

- Lead-profiled entries: `80`
- Median source order: `40.5`
- Median source delay: `0s`
- Median earlier buys: `2.5`
- Repeated top earlier buyers: none

Earlier-buy buckets:

| Lead bucket | n | Hit +30 | Hit +50 | Hit +100 | Median MFE | Median MAE | Source win | Median source exit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `Earlier buys <= 5` | 41 | 63.4% | 39.0% | 26.8% | +37.5% | -74.3% | 68.3% | +1.9% |
| `Earlier buys > 40` | 18 | 16.7% | 11.1% | 5.6% | +12.8% | -51.4% | 33.3% | -1.7% |
| `Source order <= 10` | 8 | 37.5% | 25.0% | 12.5% | +4.8% | -48.6% | 25.0% | -2.6% |
| `Source order > 50` | 21 | 28.6% | 14.3% | 14.3% | +18.3% | -55.8% | 42.9% | -0.7% |

Read:

- `Earlier buys <= 5` is the strongest non-MC lead in the older audit.
- Crowded entries are much worse.
- We do not currently have a cheap same-slot earlier-buy-count filter. This is worth building later if Omego becomes a priority.

## Recommended Live Validation

```env
TRACKED_WALLETS=omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq

SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=false

MIN_BUY_SOL=0.05
MAX_BUY_SOL=0.05
MAX_SOL_PER_TOKEN=0.05
MAX_SESSION_DEPLOYED_SOL=0.30
MAX_OPEN_POSITIONS=3

STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TAKE_PROFIT_PCT=
TRAILING_STEPS=10:-8,20:0,30:10,50:30,80:55,120:90

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=20000
PREBUY_MAX_MARKET_CAP_USD=50000
PREBUY_FILTER_FAIL_OPEN=false

MAX_ENTRY_PRICE_IMPACT_PCT=60
MAX_SOURCE_DRIFT_PCT=25

SKIP_SOURCE_PREEXISTING_MINTS=true
SOURCE_PREEXISTING_SCAN_TX_LIMIT=300

EXECUTION_MULTI_SEND_ENABLED=true
RUST_HOT_SNIPER_ENABLED=true
RUST_HOT_SNIPER_PRIMARY=true
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=60
```

Why these settings:

- `MIRROR_SELLS=false` because source exits are weak.
- `STOP_LOSS_PCT=25` because the best tested strategy was a true `20%` trailing stop, but live execution needs a little room.
- `TRAILING_STEPS=10:-8,20:0,30:10,50:30,80:55,120:90` tries to catch the MFE behavior without waiting for Omego's own exits.
- `$20k-$50k` entry MC is the best fresh bucket.
- `0.05 SOL` entries are a step up from the old tiny preset while keeping this noisy wallet contained.

## VPS Update Script

This updates only the `omego` instance env and restarts the service.

```bash
cd /root/bot/FinalBot-omego

cp .env ".env.backup-before-omego-deep-research-$(date +%Y%m%d-%H%M%S)"

node -e "const fs=require('fs'); const p='.env'; const u={
TRACKED_WALLETS:'omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq',

SIMULATION_MODE:'false',
SHADOW_EXECUTION_MODE:'false',

BUY_ONCE_PER_TOKEN:'true',
MIRROR_SELLS:'false',

MIN_BUY_SOL:'0.05',
MAX_BUY_SOL:'0.05',
MAX_SOL_PER_TOKEN:'0.05',
MAX_SESSION_DEPLOYED_SOL:'0.30',
MAX_OPEN_POSITIONS:'3',

STOP_LOSS_PCT:'25',
MIN_HOLD_BEFORE_STOP_LOSS_MS:'8000',
TAKE_PROFIT_PCT:'',
TRAILING_STEPS:'10:-8,20:0,30:10,50:30,80:55,120:90',

PREBUY_MORALIS_FILTER_ENABLED:'false',
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED:'true',
LOCAL_PUMPFUN_SOL_PRICE_USD:'66',
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED:'true',
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS:'300000',
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS:'2500',
PREBUY_MIN_MARKET_CAP_USD:'20000',
PREBUY_MAX_MARKET_CAP_USD:'50000',
PREBUY_FILTER_FAIL_OPEN:'false',

MAX_ENTRY_PRICE_IMPACT_PCT:'60',
MAX_SOURCE_DRIFT_PCT:'25',

SKIP_SOURCE_PREEXISTING_MINTS:'true',
SOURCE_PREEXISTING_SCAN_TX_LIMIT:'300',

EXECUTION_MULTI_SEND_ENABLED:'true',
RUST_HOT_SNIPER_ENABLED:'true',
RUST_HOT_SNIPER_PRIMARY:'true',
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS:'60'
}; let s=fs.readFileSync(p,'utf8'); for (const [k,v] of Object.entries(u)){ const re=new RegExp('^'+k+'=.*$','m'); s=re.test(s)?s.replace(re,k+'='+v):s.trimEnd()+'\n'+k+'='+v+'\n'; } fs.writeFileSync(p,s); console.log('Omego deep-research env updated');"

npm run build
npm run rust-sidecar:jito-build

systemctl restart finalbot-omego
journalctl -u finalbot-omego -f
```

## Follow-Up Research

- Build a same-slot-safe source-order or earlier-buy-count filter. The older path audit says `earlier buys <= 5` is a strong signal.
- Compare `$20k-$50k` live results against the looser `$12k-$50k` variant.
- Add a true percent-below-peak trailing mode if Omego remains worth running. The quant-tested `20% trailing stop` is not identical to current `TRAILING_STEPS`.
- Track whether current same-slot infra lands before Omego's second/third entries, since late entries are less attractive for this wallet.
