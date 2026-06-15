# 4BdK Deep Wallet Research

Wallet: `4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk`

Last updated: 2026-06-15

The working conclusions below are based on a larger `5000` transaction cache and `51` candle-enriched entries. The wallet-level sample is large; the candle/path sample is smaller because Moralis quota was consumed while expanding beyond the cached rows.

## Current Verdict

4BdK is an exit-led high-MFE wallet. The wallet often enters tokens that can run hard, but the path usually includes brutal drawdown. Tight fixed stops will cut many trades before the edge resolves.

Best current approach:

- Copy once per mint.
- Mirror sells proportionally, not full-exit on tiny trims.
- Use a wide emergency stop.
- Use a fast local Pump.fun market-cap filter.
- Start the local MC filter at `$20k`.
- Keep Moralis volume/liquidity/momentum filters research-only for now.
- Keep position size controlled even though the session cap can be larger.

## Data Basis

Quant pass:

- Wallet tx cache: `5000` txs
- Swaps reconstructed: `2202`
- Buys/sells: `1593 / 609`
- Campaigns: `360`
- Closed campaigns: `355`
- Candle-enriched entries: `51`
- Window: `2026-05-12T16:52:10Z` to `2026-06-15T01:54:46Z`

Wallet-level stats:

- Realized PnL: `+858.94 SOL`
- Closed-campaign win rate: `70.7%`
- Average source ROI: `+68.0%`
- Median source ROI: `+30.2%`
- Profit factor: `5.94`
- Median hold time: `2.12 min`
- Average hold time: `34.04 min`
- Largest win: `+76.35 SOL`
- Largest loss: `-18.04 SOL`
- Top 5 winners contributed `28.8%` of total realized SOL

Entry environment:

- Median source buy: `5.068 SOL`
- Average source buy: `13.310 SOL`
- Median pre-5m volume: `$32.8k`
- P75 pre-5m volume: `$72.7k`
- Median current liquidity: `$4.6k`
- Median MFE: `+99.0%`
- Median MAE: `-81.8%`

Important caveat: "approx entry MC" is `entry candle USD price * 1B token supply`. This is most useful for Pump.fun-style 1B supply tokens. It is better for bucket research than live/current DexScreener market cap, but still approximate.

## Strategy Backtest

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Sell when wallet sells | 51 | 70.6% | +159.6% | +30.6% | 25.11 | -54.8% |
| 50% at +30, 50% source exit | 51 | 82.4% | +100.9% | +30.3% | 53.14 | -27.8% |
| 20% trailing stop | 51 | 66.7% | +37.4% | +15.9% | 10.18 | -36.7% |
| Time exit 30m | 51 | 47.1% | +13.7% | -3.6% | 1.52 | -868.7% |
| TP60 SL30 | 51 | 49.0% | +13.0% | -30.0% | 1.85 | -210.0% |
| TP50 SL25 | 51 | 51.0% | +12.3% | +3.4% | 2.01 | -125.0% |
| TP40 SL25 | 51 | 52.9% | +8.7% | +40.0% | 1.74 | -140.0% |
| TP30 SL20 | 51 | 54.9% | +6.9% | +30.0% | 1.77 | -120.0% |
| Time exit 1h | 51 | 43.1% | +8.9% | -28.7% | 1.26 | -1110.8% |
| Time exit 4h | 51 | 35.3% | -11.5% | -61.5% | 0.75 | -1598.9% |
| Time exit 24h | 51 | 17.6% | -18.3% | -75.5% | 0.66 | -1273.2% |

Read:

- The best tested behavior is sell when 4BdK sells.
- The best risk-adjusted behavior is likely partial profit at `+30%`, then source exit.
- The closest env-only behavior today is `MIRROR_SELLS=true` with `MIRROR_SELL_MODE=proportional`.
- Fixed TP is positive, but it gives up most of 4BdK's edge.

## Target And Downside Probabilities

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 88.2% | 5504s |
| `+20%` | 80.4% | 5889s |
| `+30%` | 72.5% | 6033s |
| `+50%` | 62.7% | 6825s |
| `+100%` | 49.0% | 8153s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 94.1% |
| `-20%` | 82.4% |
| `-30%` | 72.5% |

Read:

- 4BdK entries frequently go deeply red.
- `STOP_LOSS_PCT=20-30` does not match the source-exit edge.
- A wide emergency stop is still needed, but it should protect against disasters, not normal 4BdK noise.

## Market Cap Buckets

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Hit +40 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 2 | 50.0% | +8.9% | +8.9% | 50.0% | 50.0% | 50.0% | 50.0% | +150.6% | -38.5% |
| `$5k-10k` | 2 | 50.0% | -3.1% | -3.1% | 50.0% | 50.0% | 50.0% | 50.0% | +112.9% | -46.3% |
| `$10k-20k` | 7 | 57.1% | +13.8% | +11.5% | 28.6% | 28.6% | 14.3% | 14.3% | +22.2% | -83.5% |
| `$20k-50k` | 22 | 81.8% | +205.5% | +58.0% | 81.8% | 77.3% | 77.3% | 63.6% | +174.8% | -82.9% |
| `$50k-100k` | 10 | 70.0% | +110.8% | +135.6% | 80.0% | 80.0% | 80.0% | 50.0% | +111.9% | -86.6% |
| `$100k+` | 8 | 62.5% | +300.1% | +1.8% | 87.5% | 62.5% | 50.0% | 37.5% | +61.8% | -77.6% |

Read:

- `$20k-100k` is the strongest balanced range.
- `$20k-50k` has the best combination of sample size, median source ROI, and hit rates.
- `$50k-100k` is also strong, especially for fixed TP or trailing behavior.
- `$100k+` has big average ROI but weak median source ROI, so it is more outlier-driven.

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

Stricter variant:

```env
PREBUY_MIN_MARKET_CAP_USD=20000
PREBUY_MAX_MARKET_CAP_USD=100000
```

## Volume Buckets

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Hit +40 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 12 | 83.3% | +157.8% | +126.6% | 83.3% | 75.0% | 75.0% | 58.3% | +174.8% | -84.3% |
| `$0-2.5k` | 2 | 100.0% | +9.0% | +9.0% | 0.0% | 0.0% | 0.0% | 0.0% | +8.7% | -51.6% |
| `$2.5k-5k` | 0 | - | - | - | - | - | - | - | - | - |
| `$5k-10k` | 2 | 50.0% | +9.4% | +9.4% | 100.0% | 100.0% | 50.0% | 50.0% | +134.9% | -49.3% |
| `$10k-20k` | 1 | 0.0% | -0.5% | -0.5% | 100.0% | 100.0% | 100.0% | 100.0% | +294.6% | -28.3% |
| `$20k-50k` | 14 | 71.4% | +177.5% | +20.5% | 64.3% | 57.1% | 57.1% | 35.7% | +68.1% | -74.3% |
| `$50k+` | 20 | 65.0% | +186.2% | +33.5% | 75.0% | 70.0% | 65.0% | 55.0% | +126.6% | -85.5% |

Read:

- The best usable volume threshold is probably `$20k+`.
- `$50k+` has cleaner hit rates but would skip too many otherwise good trades.
- Keep Moralis volume disabled for same-slot speed; use it only for research or slower validation.

Research-only filter candidate:

```env
PREBUY_MORALIS_FILTER_ENABLED=true
PREBUY_MIN_VOLUME_USD=20000
PREBUY_MIN_LIQUIDITY_USD=
PREBUY_MIN_MOMENTUM_PCT=
PREBUY_MORALIS_TIMEOUT_MS=750
PREBUY_FILTER_FAIL_OPEN=false
```

## Momentum Buckets

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Hit +40 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 6 | 50.0% | +23.5% | +9.9% | 66.7% | 66.7% | 66.7% | 66.7% | +220.6% | -25.4% |
| `-20% to -5%` | 5 | 80.0% | -0.6% | +11.5% | 80.0% | 80.0% | 80.0% | 80.0% | +144.9% | -29.3% |
| `-5% to 0%` | 1 | 100.0% | +49.3% | +49.3% | 100.0% | 100.0% | 100.0% | 100.0% | +289.6% | +48.7% |
| `0% to +10%` | 4 | 50.0% | +98.4% | -0.8% | 75.0% | 50.0% | 25.0% | 0.0% | +40.7% | -87.6% |
| `+10% to +30%` | 6 | 83.3% | +279.2% | +93.5% | 66.7% | 66.7% | 66.7% | 50.0% | +105.5% | -92.1% |
| `+30%+` | 17 | 64.7% | +234.6% | +45.2% | 64.7% | 58.8% | 52.9% | 35.3% | +78.7% | -82.6% |
| `unknown` | 12 | 83.3% | +157.8% | +126.6% | 83.3% | 75.0% | 75.0% | 58.3% | +174.8% | -84.3% |

Read:

- Momentum is useful but messy.
- `+10%+` momentum is strong enough to watch.
- Negative momentum buckets also worked, so momentum should not be the main live filter.

## Source Buy Size Buckets

| 4BdK source buy size | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Hit +40 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 0 | - | - | - | - | - | - | - | - | - |
| `1-3 SOL` | 18 | 83.3% | +260.3% | +50.1% | 61.1% | 55.6% | 50.0% | 27.8% | +47.8% | -84.8% |
| `3-6 SOL` | 8 | 62.5% | +72.4% | +17.7% | 37.5% | 37.5% | 37.5% | 37.5% | +19.6% | -84.8% |
| `6-12 SOL` | 20 | 65.0% | +135.6% | +36.2% | 95.0% | 90.0% | 85.0% | 70.0% | +150.1% | -49.2% |
| `12+ SOL` | 5 | 60.0% | +32.5% | +3.4% | 80.0% | 60.0% | 60.0% | 60.0% | +215.3% | -8.9% |

Read:

- `6-12 SOL` source buys look best for bot scalpability.
- The bot does not currently have a source-buy-size prefilter.
- If we build one later, a candidate is `MIN_SOURCE_BUY_SOL=6` with no max or a soft max around `12`.

## Current Liquidity Buckets

Current liquidity is from the pair lookup at audit time, not guaranteed entry-time liquidity.

| Current liquidity | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Hit +40 | Hit +50 | Hit +100 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-2.5k` | 1 | 100.0% | +20.2% | +20.2% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | -76.5% |
| `$2.5k-5k` | 26 | 65.4% | +190.4% | +25.6% | 76.9% | 73.1% | 69.2% | 50.0% | +110.6% | -88.0% |
| `$5k-10k` | 11 | 81.8% | +182.2% | +189.7% | 90.9% | 72.7% | 72.7% | 63.6% | +130.1% | -46.8% |
| `$10k-25k` | 3 | 66.7% | +282.2% | +123.2% | 100.0% | 100.0% | 100.0% | 66.7% | +221.5% | -14.9% |
| `$25k+` | 4 | 100.0% | +68.7% | +64.3% | 75.0% | 75.0% | 75.0% | 75.0% | +210.4% | -10.8% |
| `unknown` | 6 | 50.0% | +7.0% | +5.3% | 16.7% | 16.7% | 0.0% | 0.0% | +15.6% | -83.1% |

Read:

- Liquidity looked useful, but this is current liquidity, not historical entry liquidity.
- Do not make liquidity a live same-slot requirement unless we accept a slower Moralis/DexScreener path.

## Recommended Live Validation

```env
TRACKED_WALLETS=4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk

SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MIRROR_SELL_MODE=proportional
MIRROR_SELL_PROPORTIONAL_MIN_PCT=20
MIRROR_SELL_PROPORTIONAL_MAX_PCT=100
MIRROR_SELL_PROPORTIONAL_FALLBACK=full

MIN_BUY_SOL=0.25
MAX_BUY_SOL=0.30
MAX_SOL_PER_TOKEN=0.30
MAX_SESSION_DEPLOYED_SOL=3.00
MAX_OPEN_POSITIONS=4

STOP_LOSS_PCT=65
MIN_HOLD_BEFORE_STOP_LOSS_MS=10000
TAKE_PROFIT_PCT=
TRAILING_STEPS=50:20,100:50,180:110,300:200,500:360

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

EXECUTION_MULTI_SEND_ENABLED=false
```

Why `STOP_LOSS_PCT=65`:

- The measured median MAE is `-81.8%`.
- A `20-30%` stop is mathematically fighting the wallet's normal path.
- The stop should be a disaster guard, while proportional mirror sells and trailing handle normal exits.

## VPS Update Script

This updates only the `4bdk` instance env and restarts the service.

```bash
cd /root/bot/FinalBot-4bdk

cp .env ".env.backup-before-4bdk-deep-research-$(date +%Y%m%d-%H%M%S)"

node -e "const fs=require('fs'); const p='.env'; const u={
TRACKED_WALLETS:'4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk',

SIMULATION_MODE:'false',
SHADOW_EXECUTION_MODE:'false',

BUY_ONCE_PER_TOKEN:'true',
MIRROR_SELLS:'true',
MIRROR_SELL_MODE:'proportional',
MIRROR_SELL_PROPORTIONAL_MIN_PCT:'20',
MIRROR_SELL_PROPORTIONAL_MAX_PCT:'100',
MIRROR_SELL_PROPORTIONAL_FALLBACK:'full',

MIN_BUY_SOL:'0.25',
MAX_BUY_SOL:'0.30',
MAX_SOL_PER_TOKEN:'0.30',
MAX_SESSION_DEPLOYED_SOL:'3.00',
MAX_OPEN_POSITIONS:'4',

STOP_LOSS_PCT:'65',
MIN_HOLD_BEFORE_STOP_LOSS_MS:'10000',
TAKE_PROFIT_PCT:'',
TRAILING_STEPS:'50:20,100:50,180:110,300:200,500:360',

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

EXECUTION_MULTI_SEND_ENABLED:'false'
}; let s=fs.readFileSync(p,'utf8'); for (const [k,v] of Object.entries(u)){ const re=new RegExp('^'+k+'=.*$','m'); s=re.test(s)?s.replace(re,k+'='+v):s.trimEnd()+'\n'+k+'='+v+'\n'; } fs.writeFileSync(p,s); console.log('4BdK deep-research env updated');"

systemctl restart finalbot-4bdk
journalctl -u finalbot-4bdk -f
```

## Open Questions

- Expand candle-enriched sample after Moralis quota resets; wallet-level sample is large, path sample is only `51`.
- Compare proportional mirror sells against a future hybrid rule: `50% at +30%, 50% source exit`.
- Consider building a source-buy-size filter, because `6-12 SOL` source buys look meaningfully better for bot scalpability.
- Test `PREBUY_MIN_MARKET_CAP_USD=20000` against no MC filter for live missed-trade rate.
