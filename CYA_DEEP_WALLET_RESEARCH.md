# CyA Deep Wallet Research

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Last updated: 2026-06-15

The working conclusions below are based on the larger `5000` transaction cache and `217` candle-enriched entries.

## Current Verdict

CyA is an exit-led wallet, not a simple fixed-TP scalp wallet. The larger sample strongly favors following CyA exits or taking partial profit and then following CyA exits. Simple TP/SL and simple trailing tested poorly.

Best current approach:

- Copy once per mint.
- Use `0.25 SOL` fixed-size entries.
- Cap live exposure to `0.75 SOL` per session and `2` open positions.
- Keep the fast local Pump.fun market-cap filter on.
- Use `$5k` as the starting min market cap.
- Keep impact/drift protection.
- Mirror CyA sells.
- Treat Moralis volume/liquidity/momentum filters as research-only for now because they cost hot-path time.
- Rebuild lead-wallet/creator research against the same larger sample before using those as live filters.

## Data Basis

Quant pass:

- Wallet tx cache: `5000` txs
- Swaps reconstructed: `3709`
- Buys/sells: `2738 / 971`
- Campaigns: `511`
- Closed campaigns: `511`
- Candle-enriched entries: `217`
- Window: `2026-06-12T21:11:58Z` to `2026-06-15T00:07:03Z`

Wallet-level stats:

- Realized PnL: `+509.55 SOL`
- Closed-campaign win rate: `56.2%`
- Average source ROI: `+15.7%`
- Median source ROI: `+4.4%`
- Profit factor: `2.62`
- Median hold time: `0.62 min`
- Median entry size: `3.007 SOL`
- Average entry size: `3.484 SOL`
- Top 5 winners contributed `45.3%` of total realized SOL

Entry environment:

- Median pre-5m volume: `$6.8k`
- P75 pre-5m volume: `$14.1k`
- Approx entry MC median: `$3.4k`
- Approx entry MC p25/p75: `$2.1k / $8.6k`
- Median MFE: `+1.2%`
- Median MAE: `-40.1%`

Important caveat: "approx entry MC" is `entry candle USD price * 1B token supply`. This is most useful for Pump.fun-style 1B supply tokens. It is better for historical bucket research than live/current DexScreener market cap, but still approximate.

## Strategy Backtest

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Sell when CyA sells | 217 | 58.5% | +17.5% | +5.1% | 3.13 | -201.3% |
| 50% at +30, 50% CyA exit | 217 | 63.1% | +14.3% | +10.1% | 3.04 | -151.0% |
| 20% trailing stop | 217 | 25.8% | -2.1% | -5.4% | 0.74 | -671.8% |
| TP30 SL20 | 217 | 12.0% | -10.7% | -20.0% | 0.15 | -2368.2% |
| TP50 SL25 | 217 | 12.4% | -11.8% | -25.0% | 0.22 | -2712.3% |

Read:

- The strongest tested behavior is `MIRROR_SELLS=true`.
- The best risk-adjusted variant is probably partial TP at `+30%`, then CyA exit.
- Fixed TP/SL and simple trailing are not good CyA fits because many winners draw down hard before CyA exits profitably.

## Target And Downside Probabilities

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 36.4% | 659s |
| `+20%` | 24.4% | 624s |
| `+30%` | 20.3% | 912s |
| `+50%` | 13.4% | 1487s |
| `+100%` | 5.1% | 3355s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 66.8% |
| `-20%` | 59.4% |
| `-30%` | 51.2% |

Read:

- CyA entries frequently go red before resolving.
- A tight stop will cut many trades that CyA later exits well.
- The bot still needs a hard emergency stop, but CyA's edge is not captured by a standard tight TP/SL box.

## Market Cap Buckets

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 129 | 51.9% | +2.1% | +0.3% | 9.2% | 6.7% | 4.2% | 0.0% | -7.9% |
| `$5k-10k` | 42 | 73.8% | +18.6% | +13.9% | 42.9% | 40.5% | 26.2% | +14.3% | -70.6% |
| `$10k-20k` | 11 | 72.7% | +53.4% | +30.5% | 72.7% | 45.5% | 27.3% | +29.4% | -81.5% |
| `$20k-50k` | 32 | 56.3% | +47.3% | +6.4% | 43.8% | 37.5% | 25.0% | +18.6% | -92.7% |
| `$50k-100k` | 3 | 100.0% | +210.5% | +199.7% | 66.7% | 66.7% | 66.7% | +72.2% | -60.6% |
| `$100k+` | 0 | - | - | - | - | - | - | - | - |

Read:

- `$0-5k` is only slightly positive if we follow CyA exits well.
- `$0-5k` is weak for bot scalping: only `6.7%` hit `+30%`.
- `$5k-10k` is the best balanced bucket.
- `10k+` improves upside, but sample size falls and drawdowns stay deep.

Current live filter:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=150
PREBUY_MIN_MARKET_CAP_USD=5000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false
```

Stricter variants to test later:

```env
PREBUY_MIN_MARKET_CAP_USD=10000
```

```env
PREBUY_MIN_MARKET_CAP_USD=15000
```

## Volume Buckets

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 21 | 66.7% | +71.8% | +24.3% | 38.1% | 38.1% | 33.3% | +15.2% | -88.5% |
| `$0-2.5k` | 21 | 71.4% | +7.3% | +4.4% | 22.2% | 22.2% | 5.6% | 0.0% | -27.1% |
| `$2.5k-5k` | 41 | 43.9% | +2.4% | -2.6% | 16.7% | 11.1% | 8.3% | +0.1% | -6.2% |
| `$5k-10k` | 53 | 54.7% | +7.2% | +0.8% | 21.6% | 17.6% | 11.8% | +1.0% | -22.9% |
| `$10k-20k` | 52 | 69.2% | +19.7% | +14.7% | 23.1% | 17.3% | 7.7% | +1.4% | -46.1% |
| `$20k-50k` | 22 | 50.0% | +23.3% | +1.0% | 36.4% | 27.3% | 22.7% | +15.5% | -63.9% |
| `$50k+` | 7 | 57.1% | +15.9% | +10.8% | 57.1% | 57.1% | 42.9% | +48.9% | -50.3% |

Read:

- Volume is noisy.
- `$10k-20k` has the best stable median source ROI.
- `$50k+` has the best MFE/scalp behavior but small sample.
- Keep Moralis volume as research-only for now because same-slot needs speed.

Research-only filter candidate:

```env
PREBUY_MORALIS_FILTER_ENABLED=true
PREBUY_MIN_VOLUME_USD=5000
PREBUY_MIN_LIQUIDITY_USD=10000
PREBUY_MIN_MOMENTUM_PCT=5
PREBUY_MORALIS_TIMEOUT_MS=750
PREBUY_FILTER_FAIL_OPEN=false
```

## Momentum Buckets

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 48 | 52.1% | +2.1% | +0.7% | 10.4% | 10.4% | 8.3% | 0.0% | -2.6% |
| `-20% to -5%` | 36 | 41.7% | +0.8% | -4.7% | 13.9% | 11.1% | 8.3% | 0.0% | -5.5% |
| `-5% to 0%` | 7 | 85.7% | +59.2% | +16.4% | 28.6% | 28.6% | 14.3% | 0.0% | -13.3% |
| `0% to +10%` | 26 | 42.3% | -2.8% | -4.2% | 25.0% | 6.3% | 0.0% | +8.0% | -42.3% |
| `+10% to +30%` | 12 | 58.3% | +9.3% | +3.5% | 33.3% | 25.0% | 0.0% | 0.0% | -30.8% |
| `+30%+` | 67 | 73.1% | +25.4% | +12.3% | 37.3% | 31.3% | 20.9% | +13.2% | -67.1% |

Read:

- `+30%+` pre-entry momentum is a meaningful signal.
- It still has deep median MAE, so it should not replace mirror-sell logic.

## Source Buy Size Buckets

| CyA source buy size | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 1 | 100.0% | +11.3% | +11.3% | 0.0% | 0.0% | 0.0% | 0.0% | -6.8% |
| `1-3 SOL` | 0 | - | - | - | - | - | - | - | - |
| `3-6 SOL` | 215 | 58.1% | +17.3% | +4.9% | 25.9% | 21.5% | 14.1% | +1.4% | -40.1% |
| `6-12 SOL` | 1 | 100.0% | +49.0% | +49.0% | 0.0% | 0.0% | 0.0% | 0.0% | -57.3% |
| `12+ SOL` | 0 | - | - | - | - | - | - | - | - |

Read:

- Almost every measured CyA entry is `3-6 SOL`.
- A `0.25 SOL` copy is still small relative to CyA's own size.
- Sizing is not the core issue. Selection and exit behavior are.

## Current Liquidity Buckets

Current liquidity had low coverage in the quant pass, so it is weaker than entry MC/volume.

| Current liquidity | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-2.5k` | 3 | 100.0% | +133.9% | +67.8% | 33.3% | 33.3% | 33.3% | 0.0% | -78.8% |
| `$2.5k-5k` | 27 | 66.7% | +48.3% | +19.6% | 44.4% | 37.0% | 18.5% | +17.8% | -93.5% |
| `$5k-10k` | 6 | 16.7% | -7.3% | -12.7% | 33.3% | 33.3% | 33.3% | +17.4% | -73.2% |
| `$10k-25k` | 5 | 80.0% | +153.0% | +56.0% | 100.0% | 100.0% | 100.0% | +224.5% | -45.5% |
| `$25k+` | 1 | 100.0% | +199.7% | +199.7% | 100.0% | 100.0% | 100.0% | +133.3% | -60.6% |

Read:

- Do not use current liquidity as the main CyA filter yet.
- The sample is too thin and too sensitive to current DexScreener state.

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
LOCAL_PUMPFUN_SOL_PRICE_USD=150
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

- The 217-entry backtest says source exit is the best tested behavior.
- `50% at +30, 50% source exit` has the best median ROI and win rate, but the bot does not yet have this exact hybrid exit rule as a simple env-only strategy.
- Until the hybrid exit is implemented, full mirror-sells are the closest live behavior to the best tested edge.

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
LOCAL_PUMPFUN_SOL_PRICE_USD=150
PREBUY_MIN_MARKET_CAP_USD=5000
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

## Main Bot VPS Update Script

This updates only env values and does not restart the service.

```bash
cd /root/bot/FinalBot

cp .env ".env.backup-before-cya-large-sample-$(date +%Y%m%d-%H%M%S)"

node -e "const fs=require('fs'); const p='.env'; const u={
TRACKED_WALLETS:'CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o',

SIMULATION_MODE:'false',
SHADOW_EXECUTION_MODE:'false',

BUY_ONCE_PER_TOKEN:'true',
MIRROR_SELLS:'true',
MIRROR_SELL_MODE:'full',

MIN_BUY_SOL:'0.25',
MAX_BUY_SOL:'0.25',
MAX_SOL_PER_TOKEN:'0.25',
MAX_SESSION_DEPLOYED_SOL:'0.75',
MAX_OPEN_POSITIONS:'2',

STOP_LOSS_PCT:'30',
MIN_HOLD_BEFORE_STOP_LOSS_MS:'8000',
TAKE_PROFIT_PCT:'40',
TRAILING_STEPS:'20:12,35:25,60:45,100:80,180:140',

PREBUY_MORALIS_FILTER_ENABLED:'false',
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED:'true',
LOCAL_PUMPFUN_SOL_PRICE_USD:'150',
PREBUY_MIN_MARKET_CAP_USD:'5000',
PREBUY_MAX_MARKET_CAP_USD:'',
PREBUY_FILTER_FAIL_OPEN:'false',

MAX_ENTRY_PRICE_IMPACT_PCT:'60',
MAX_SOURCE_DRIFT_PCT:'25',

PUMPSWAP_DIRECT_BUY_ENABLED:'true',
MIRROR_GRADUATED_BUYS:'false',

EXECUTION_MULTI_SEND_ENABLED:'true',
RUST_HOT_SNIPER_ENABLED:'true',
RUST_HOT_SNIPER_PRIMARY:'true',
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS:'60'
}; let s=fs.readFileSync(p,'utf8'); for (const [k,v] of Object.entries(u)){ const re=new RegExp('^'+k+'=.*$','m'); s=re.test(s)?s.replace(re,k+'='+v):s.trimEnd()+'\n'+k+'='+v+'\n'; } fs.writeFileSync(p,s); console.log('CyA large-sample env updated; service not restarted');"
```

## Lead Signals

Lead-wallet and creator tables should be rebuilt against the same `5000` tx / `217` candle-enriched context before using them as live filters.

Next lead-signal work:

- Re-run earlier-wallet detection on the current 5,000-tx CyA cache.
- Rebuild creator/deployer buckets from the same sample window.
- Add a fast lead-wallet prefilter only if the refreshed sample confirms repeated wallets or creators with enough appearances.
- Prefer lead-wallet/creator confirmation over slow generic Moralis checks if the goal is same-slot or near-slot entry.

## Open Questions

- Need to shadow-test `0.25 SOL` sizing specifically. Tiny `0.04 SOL` shadow fills exaggerate fee drag and are no longer representative.
- Need to verify whether `PUMPSWAP_DIRECT_BUY_ENABLED=false` plus `MIRROR_GRADUATED_BUYS=false` fully prevents graduated buys or whether we should add a dedicated `SKIP_GRADUATED_BUYS=true` env gate.
- Need to compare `MIRROR_SELLS=true/full` versus a future hybrid rule: `50% at +30%, 50% CyA exit`.
- Next larger audit step would be expanding beyond the current `5000` tx cache or increasing candle coverage past the most recent `220` campaigns.
