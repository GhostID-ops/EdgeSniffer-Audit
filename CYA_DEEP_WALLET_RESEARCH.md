# CyA Deep Wallet Research

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Last updated: 2026-06-15

## Current Verdict

CyA is not a tiny-test wallet. His historical buy size clusters around `3-6 SOL`, so if `BUY_ONCE_PER_TOKEN=true`, a `0.25 SOL` copy is a reasonable single-bullet test size. The earlier `0.04 SOL` setting was useful for shadow/same-slot plumbing, but it is too small for serious live expectancy because priority fees, tips, and exit drag are too large relative to the position.

Best current approach:

- Copy once per mint.
- Use `0.25 SOL` fixed-size entries.
- Keep exposure capped to 2-3 bullets per session.
- Re-enable the fast local Pump.fun market-cap filter.
- Use `$5k` as the evidence-based starting min market cap; `10k/15k` are stricter variants, not the default.
- Keep impact/drift protection.
- Test mirror-sells for CyA, because the path audit favored source exits and the latest shadow log showed HM could likely have exited earlier if source sells were mirrored.
- Avoid or separately test PumpSwap/graduated entries; the latest shadow session's two worst losses were PumpSwap/graduated routes.

## Evidence Snapshot

Historical CyA lead sample:

- Profiled campaigns: `68`
- Winners/losers: `45/23`
- Win rate: `66.2%`
- Median CyA ROI: `+17.4%`
- Average CyA ROI: `+26.8%`
- P25/P75 ROI: `-7.8% / +47.9%`
- Median source buy: `3.007 SOL`
- Most profiled source buys: `3-6 SOL`

Buy-size buckets from the lead sample:

| Source buy size | Trades | Win rate | Median ROI | Avg ROI |
|---|---:|---:|---:|---:|
| `0-1 SOL` | 3 | 33.3% | -9.2% | +24.0% |
| `3-6 SOL` | 60 | 65.0% | +16.4% | +26.0% |
| `6-12 SOL` | 2 | 100.0% | +69.2% | +58.2% |
| `12+ SOL` | 3 | 100.0% | +26.6% | +25.9% |

Latest shadow session:

- Trades: `4 buys / 4 sells`
- Result: `-0.0547 SOL`
- Deployed/spent: `0.1713 SOL`
- Approx return on deployed: `-32%`
- Winners: `1`
- Losers: `3`
- Biggest losses: `$NUMB -61.5%`, `$HM -60.8%`
- Pump.fun direct buys: 2
- PumpSwap direct buys: 2
- PumpSwap direct buys were the two large losers.
- gRPC source detection was excellent: average age about `9ms`, max about `42ms`.
- Session cap was too low for research: `MAX_SESSION_DEPLOYED_SOL=0.2` caused hundreds of later buys to be skipped.

Important read: latency was not the main problem in the shadow session. Trade selection, route filtering, and exit logic were.

## Data Basis

The stronger bucket data comes from a fresh quant pass over the local CyA cache:

- Wallet tx cache: `5000` txs
- Swaps reconstructed: `3709`
- Buys/sells: `2738 / 971`
- Campaigns: `511`
- Candle-enriched entries: `217`
- Window covered by wallet cache: `2026-06-12T21:11:58Z` to `2026-06-15T00:07:03Z`
- CyA wallet realized PnL in this cache: `+509.55 SOL`
- CyA closed-campaign win rate in this cache: `56.2%`
- Median CyA source ROI: `+4.4%`
- Average CyA source ROI: `+15.7%`
- Median hold time: `0.62 min`
- Median entry size: `3.007 SOL`
- Median pre-5m volume: `$6.8k`
- P75 pre-5m volume: `$14.1k`
- Approx entry MC median: `$3.4k`
- Approx entry MC p25/p75: `$2.1k / $8.6k`

Important caveat: "approx entry MC" is `entry candle USD price * 1B token supply`. This is most useful for Pump.fun-style 1B supply tokens. It is better for historical bucket research than live/current DexScreener market cap, but still approximate.

### Strategy Backtest On 217 Entries

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Sell when CyA sells | 217 | 58.5% | +17.5% | +5.1% | 3.13 | -201.3% |
| 50% at +30, 50% CyA exit | 217 | 63.1% | +14.3% | +10.1% | 3.04 | -151.0% |
| 20% trailing stop | 217 | 25.8% | -2.1% | -5.4% | 0.74 | -671.8% |
| TP30 SL20 | 217 | 12.0% | -10.7% | -20.0% | 0.15 | -2368.2% |
| TP50 SL25 | 217 | 12.4% | -11.8% | -25.0% | 0.22 | -2712.3% |

Read: the bigger sample strongly favors source-exit/mirror-sell behavior. Fixed TP/SL and simple trailing do poorly because many entries draw down hard before CyA exits profitably.

### Approx Entry Market Cap Buckets

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 129 | 51.9% | +2.1% | +0.3% | 9.2% | 6.7% | 4.2% | 0.0% | -7.9% |
| `$5k-10k` | 42 | 73.8% | +18.6% | +13.9% | 42.9% | 40.5% | 26.2% | +14.3% | -70.6% |
| `$10k-20k` | 11 | 72.7% | +53.4% | +30.5% | 72.7% | 45.5% | 27.3% | +29.4% | -81.5% |
| `$20k-50k` | 32 | 56.3% | +47.3% | +6.4% | 43.8% | 37.5% | 25.0% | +18.6% | -92.7% |
| `$50k-100k` | 3 | 100.0% | +210.5% | +199.7% | 66.7% | 66.7% | 66.7% | +72.2% | -60.6% |
| `$100k+` | 0 | - | - | - | - | - | - | - | - |

Read: `$0-5k` is only slightly positive if we perfectly follow CyA exits, but it has poor scalp potential: only `6.7%` hit `+30%`. `$5k-10k` is the best balanced bucket. `$10k+` improves upside, but sample size falls and drawdowns stay deep.

### Pre-5m Volume Buckets

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 21 | 66.7% | +71.8% | +24.3% | 38.1% | 38.1% | 33.3% | +15.2% | -88.5% |
| `$0-2.5k` | 21 | 71.4% | +7.3% | +4.4% | 22.2% | 22.2% | 5.6% | 0.0% | -27.1% |
| `$2.5k-5k` | 41 | 43.9% | +2.4% | -2.6% | 16.7% | 11.1% | 8.3% | +0.1% | -6.2% |
| `$5k-10k` | 53 | 54.7% | +7.2% | +0.8% | 21.6% | 17.6% | 11.8% | +1.0% | -22.9% |
| `$10k-20k` | 52 | 69.2% | +19.7% | +14.7% | 23.1% | 17.3% | 7.7% | +1.4% | -46.1% |
| `$20k-50k` | 22 | 50.0% | +23.3% | +1.0% | 36.4% | 27.3% | 22.7% | +15.5% | -63.9% |
| `$50k+` | 7 | 57.1% | +15.9% | +10.8% | 57.1% | 57.1% | 42.9% | +48.9% | -50.3% |

Read: volume is messy. `$10k-20k` has the best stable median source ROI, while `$50k+` has the best MFE/scalp behavior but small sample. Moralis volume remains research-only for same-slot because it costs time.

### Pre-5m Momentum Buckets

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 48 | 52.1% | +2.1% | +0.7% | 10.4% | 10.4% | 8.3% | 0.0% | -2.6% |
| `-20% to -5%` | 36 | 41.7% | +0.8% | -4.7% | 13.9% | 11.1% | 8.3% | 0.0% | -5.5% |
| `-5% to 0%` | 7 | 85.7% | +59.2% | +16.4% | 28.6% | 28.6% | 14.3% | 0.0% | -13.3% |
| `0% to +10%` | 26 | 42.3% | -2.8% | -4.2% | 25.0% | 6.3% | 0.0% | +8.0% | -42.3% |
| `+10% to +30%` | 12 | 58.3% | +9.3% | +3.5% | 33.3% | 25.0% | 0.0% | 0.0% | -30.8% |
| `+30%+` | 67 | 73.1% | +25.4% | +12.3% | 37.3% | 31.3% | 20.9% | +13.2% | -67.1% |

Read: strong pre-entry momentum, especially `+30%+`, is a real lead. It is not clean enough to replace exits because median MAE is still very deep.

### CyA Source Buy Size Buckets

In the 217 candle-enriched entries, almost every measured entry was a `3-6 SOL` CyA source buy.

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0-1 SOL` | 1 | 100.0% | +11.3% | +11.3% | 0.0% | 0.0% | 0.0% | 0.0% | -6.8% |
| `1-3 SOL` | 0 | - | - | - | - | - | - | - | - |
| `3-6 SOL` | 215 | 58.1% | +17.3% | +4.9% | 25.9% | 21.5% | 14.1% | +1.4% | -40.1% |
| `6-12 SOL` | 1 | 100.0% | +49.0% | +49.0% | 0.0% | 0.0% | 0.0% | 0.0% | -57.3% |
| `12+ SOL` | 0 | - | - | - | - | - | - | - | - |

Read: a `0.25 SOL` copy is still small relative to CyA's source size. The issue is not relative sizing; it is selecting the right bucket and exiting correctly.

### CyA Source Order Buckets

From the lead-signal file:

| Bucket | n | Win % | Avg ROI | Median ROI |
|---|---:|---:|---:|---:|
| Known order `1-5` | 1 | 100.0% | +26.6% | +26.6% |
| Known order `6-15` | 10 | 50.0% | +11.9% | -1.6% |
| Known order `16-30` | 6 | 33.3% | +4.1% | -2.1% |
| Known order `31+` | 8 | 75.0% | +17.3% | +20.4% |
| Unknown order | 43 | 72.1% | +35.2% | +18.7% |

Read: source order is noisy. The better lead is repeated wallet/creator overlap, not just "CyA was early."

### Current Liquidity Buckets

Current liquidity had low coverage in the quant pass, so it is weaker than entry MC/volume.

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-2.5k` | 3 | 100.0% | +133.9% | +67.8% | 33.3% | 33.3% | 33.3% | 0.0% | -78.8% |
| `$2.5k-5k` | 27 | 66.7% | +48.3% | +19.6% | 44.4% | 37.0% | 18.5% | +17.8% | -93.5% |
| `$5k-10k` | 6 | 16.7% | -7.3% | -12.7% | 33.3% | 33.3% | 33.3% | +17.4% | -73.2% |
| `$10k-25k` | 5 | 80.0% | +153.0% | +56.0% | 100.0% | 100.0% | 100.0% | +224.5% | -45.5% |
| `$25k+` | 1 | 100.0% | +199.7% | +199.7% | 100.0% | 100.0% | 100.0% | +133.3% | -60.6% |

Read: do not use current liquidity as the main CyA filter yet.

## Best Filter Stack

### Keep

```env
BUY_ONCE_PER_TOKEN=true
MAX_ENTRY_PRICE_IMPACT_PCT=60
PREBUY_FILTER_FAIL_OPEN=false
```

`MAX_ENTRY_PRICE_IMPACT_PCT=60` already blocked 100% impact junk in the latest shadow log. For `0.25 SOL` live entries, consider tightening to `35-45` after one more shadow run.

### Market Cap

Use the local Pump.fun market-cap filter for CyA live sizing:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=150
PREBUY_MIN_MARKET_CAP_USD=5000
PREBUY_MAX_MARKET_CAP_USD=
```

Reasoning:

- The 217-entry quant pass says `$0-5k` is acceptable only if we mirror CyA exits well: `129` trades, `51.9%` source-exit win rate, `+0.3%` median source ROI.
- `$0-5k` is still weak for bot scalping: only `6.7%` hit `+30%` and `4.2%` hit `+50%`.
- `$5k-10k` is the best balanced bucket: `42` trades, `73.8%` source-exit win rate, `+13.9%` median source ROI, `40.5%` hit `+30%`.
- `10k+` improves upside, but samples get smaller and drawdowns remain deep.

Open research item: test `PREBUY_MIN_MARKET_CAP_USD=10000` and `15000` as stricter variants if `$5k` lets through too many thin rugs.

### Volume / Liquidity / Momentum

The bot supports these:

```env
PREBUY_MORALIS_FILTER_ENABLED=true
PREBUY_MIN_VOLUME_USD=
PREBUY_MIN_LIQUIDITY_USD=
PREBUY_MIN_MOMENTUM_PCT=
PREBUY_MORALIS_WINDOW_MS=300000
PREBUY_MORALIS_TIMEOUT_MS=1500
```

But for same-slot CyA, do not make Moralis mandatory yet. It adds network latency and can break the hot path.

Best current use:

```env
PREBUY_MORALIS_FILTER_ENABLED=false
```

Research-only candidate, not same-slot live:

```env
PREBUY_MORALIS_FILTER_ENABLED=true
PREBUY_MIN_VOLUME_USD=5000
PREBUY_MIN_LIQUIDITY_USD=10000
PREBUY_MIN_MOMENTUM_PCT=5
PREBUY_MORALIS_TIMEOUT_MS=750
PREBUY_FILTER_FAIL_OPEN=false
```

Why `5000` volume is a candidate: the 217-entry quant pass shows `$2.5k-5k` volume has weak median ROI and weak hit rates. `$10k-20k` has the best stable median source ROI, while `$50k+` has the best MFE/scalp behavior but small sample.

### Graduated / PumpSwap Filter

Latest shadow session warning:

- `$NUMB` was PumpSwap/graduated and lost `-61.5%`.
- `$HM` was PumpSwap/graduated and lost `-60.8%`.

Candidate filter to test, not the recommended live default yet:

```env
PUMPSWAP_DIRECT_BUY_ENABLED=false
MIRROR_GRADUATED_BUYS=false
```

Caution: route code can still fall through toward Jupiter in some cases. If this filter matters, build/verify an explicit `SKIP_GRADUATED_BUYS=true` gate later.

Current live default keeps `PUMPSWAP_DIRECT_BUY_ENABLED=true` so PumpSwap buys use the fastest known direct route instead of a slower/fuzzier fallback. The actual best filter is a future explicit graduated-token skip gate, not a half-disable that may still route elsewhere.

## Best CyA Strategy

### Recommended Live Validation

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

EXECUTION_MULTI_SEND_ENABLED=true
RUST_HOT_SNIPER_ENABLED=true
RUST_HOT_SNIPER_PRIMARY=true
RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=60
```

Why full mirror sells:

- CyA appears to actively exit.
- Historical path audit favored `Mirror source exit`.
- In the latest shadow session, HM had a source sell long before the bot's stop-loss finally closed it.
- If full mirror exits prove too twitchy, switch to proportional later.

### Safer Shadow Research Variant

Use this if the goal is more sample before live:

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
```

## Main Bot VPS Update Script

This updates only env values and does not restart the service.

```bash
cd /root/bot/FinalBot

cp .env ".env.backup-before-cya-deep-research-$(date +%Y%m%d-%H%M%S)"

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
}; let s=fs.readFileSync(p,'utf8'); for (const [k,v] of Object.entries(u)){ const re=new RegExp('^'+k+'=.*$','m'); s=re.test(s)?s.replace(re,k+'='+v):s.trimEnd()+'\n'+k+'='+v+'\n'; } fs.writeFileSync(p,s); console.log('CyA deep-research live env updated; service not restarted');"
```

## Research Leads

Earlier-wallet candidates to investigate or eventually add as pre-CyA lead signals:

| Wallet | Seen | Win rate | Median ROI | Lead |
|---|---:|---:|---:|---:|
| `DxVLq8oEizx7Sz5WcbVUiQX8fLL3y1Jj8rMGEXEDi5oH` | 10 | 80% | +22.1% | 0s |
| `J3sGCaN7fAGrpZrfhKVqmtAccxtwuWpKMhurqdarV6Dn` | 3 | 100% | +24.7% | 0s |
| `5opAR6Z3NCPEgXchUvox5nvqTKhFzsykooiR67xxkj6e` | 3 | 100% | +31.3% | 0s |
| `4LtF24A86sQ92JdUMVaCyxeJ9BuoUzJczK1zPwbBMzJ8` | 5 | 80% | +24.7% | 0s |
| `8QFT4Dx84gRGajj3jzJv4BHL8dU8d7DpjCUR3HgnX7gW` | 3 | 100% | +12.4% | 0s |

Creator candidates:

| Creator | Seen | Win rate | Avg ROI |
|---|---:|---:|---:|
| `JC1irmi7bWUEjPvqCbZbAoJ9JXh6htDC9uP5tAC2w4k` | 2 | 100% | +29.5% |
| `7qvLrGK7iL3LZLgv8EVKgNr3QSahgivkQ6QkCMNh9pWA` | 2 | 100% | +29.6% |
| `8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ` | 3 | 0% | -17.6% |

Next build idea: a fast lead-wallet prefilter that only takes CyA buys when one of the top earlier wallets or positive creators is also present in the fresh-token history. This is more promising than a slow generic volume filter if we want same-slot/near-slot performance.

## Open Questions

- Completed larger quant audit: `5000` wallet txs, `511` closed campaigns, `217` candle-enriched entries. Next larger step would be expanding beyond the current 5,000-tx cache or increasing candle coverage past the most recent 220 campaigns.
- Need to shadow-test `0.25 SOL` sizing specifically. Tiny `0.04 SOL` shadow fills exaggerate fee drag and are no longer representative.
- Need to verify whether `PUMPSWAP_DIRECT_BUY_ENABLED=false` plus `MIRROR_GRADUATED_BUYS=false` fully prevents graduated buys or whether we should add a dedicated `SKIP_GRADUATED_BUYS=true` env gate.
- Need to compare `MIRROR_SELLS=true/full` versus `MIRROR_SELLS=true/proportional` for CyA.
