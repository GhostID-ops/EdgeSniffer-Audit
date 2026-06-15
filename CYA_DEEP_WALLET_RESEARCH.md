# CyA Deep Wallet Research

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Last updated: 2026-06-14

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
- Candle-enriched entries: `77`
- Window covered by wallet cache: `2026-06-12T21:11:58Z` to `2026-06-15T00:07:03Z`
- CyA wallet realized PnL in this cache: `+509.55 SOL`
- CyA closed-campaign win rate in this cache: `56.2%`
- Median CyA source ROI: `+4.4%`
- Average CyA source ROI: `+15.7%`
- Median hold time: `0.62 min`
- Median entry size: `3.007 SOL`
- Median pre-5m volume: `$7.1k`
- P75 pre-5m volume: `$19.8k`
- Approx entry MC median: `$3.5k`
- Approx entry MC p25/p75: `$2.2k / $9.1k`

Important caveat: "approx entry MC" is `entry candle USD price * 1B token supply`. This is most useful for Pump.fun-style 1B supply tokens. It is better for historical bucket research than live/current DexScreener market cap, but still approximate.

### Approx Entry Market Cap Buckets

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 44 | 47.7% | -0.2% | -2.1% | 10.3% | 10.3% | 7.7% | 0.0% | -10.3% |
| `$5k-10k` | 17 | 76.5% | +13.6% | +10.5% | 47.1% | 41.2% | 35.3% | +15.7% | -68.7% |
| `$10k-20k` | 3 | 66.7% | +3.6% | +1.9% | 100.0% | 100.0% | 66.7% | +70.4% | -61.0% |
| `$20k-50k` | 11 | 27.3% | +23.7% | -15.1% | 27.3% | 18.2% | 18.2% | +17.2% | -92.2% |
| `$50k-100k` | 2 | 100.0% | +215.9% | +215.9% | 50.0% | 50.0% | 50.0% | +36.1% | -60.7% |
| `$100k+` | 0 | - | - | - | - | - | - | - | - |

Read: the clearest avoid bucket is `$0-5k`. The `$5k-10k` bucket has the best stable sample. `$20k+` is high variance: average can look good because of large winners, but median and drawdown are ugly.

### Pre-5m Volume Buckets

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 5 | 20.0% | +43.8% | -28.8% | 20.0% | 20.0% | 20.0% | +0.6% | -86.3% |
| `$0-2.5k` | 6 | 66.7% | +0.9% | +5.5% | 20.0% | 20.0% | 20.0% | +0.9% | -41.0% |
| `$2.5k-5k` | 14 | 35.7% | -1.4% | -3.4% | 18.2% | 18.2% | 18.2% | +0.1% | -5.5% |
| `$5k-10k` | 19 | 52.6% | +5.5% | +0.1% | 22.2% | 16.7% | 16.7% | +1.4% | -26.1% |
| `$10k-20k` | 14 | 64.3% | +1.3% | +3.7% | 21.4% | 21.4% | 7.1% | +12.1% | -53.8% |
| `$20k-50k` | 12 | 66.7% | +40.7% | +5.1% | 33.3% | 25.0% | 25.0% | +15.9% | -61.6% |
| `$50k+` | 7 | 57.1% | +15.9% | +10.8% | 57.1% | 57.1% | 42.9% | +48.9% | -50.3% |

Read: if we ever use a slower Moralis prebuy volume filter, `$5k` is the minimum defensible volume floor, while `$20k+` is the cleaner momentum/liquidity-confirmed variant. For same-slot live, this remains research-only because the Moralis call costs time.

### Pre-5m Momentum Buckets

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 15 | 46.7% | +9.0% | -3.3% | 20.0% | 20.0% | 13.3% | 0.0% | -2.5% |
| `-20% to -5%` | 14 | 35.7% | -4.6% | -7.9% | 28.6% | 28.6% | 21.4% | +0.4% | -9.0% |
| `-5% to 0%` | 2 | 100.0% | +12.8% | +12.8% | 50.0% | 50.0% | 0.0% | +19.3% | -39.2% |
| `0% to +10%` | 14 | 42.9% | -2.5% | -4.2% | 22.2% | 0.0% | 0.0% | +13.1% | -48.3% |
| `+10% to +30%` | 2 | 50.0% | +13.2% | +13.2% | 0.0% | 0.0% | 0.0% | 0.0% | -23.4% |
| `+30%+` | 25 | 76.0% | +24.8% | +4.9% | 32.0% | 32.0% | 32.0% | +14.3% | -61.5% |

Read: strong pre-entry momentum, especially `+30%+`, is a real lead. It is not clean enough to replace exits because median MAE is still very deep.

### CyA Source Buy Size Buckets

In the latest 77 candle-enriched entries, every measured entry was a `3-6 SOL` CyA source buy.

| Bucket | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `3-6 SOL` | 77 | 53.2% | +12.0% | +1.3% | 26.4% | 23.6% | 19.4% | +1.9% | -44.7% |

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
| `$2.5k-5k` | 10 | 60.0% | +56.4% | +37.6% | 50.0% | 40.0% | 30.0% | +20.3% | -93.6% |
| `$5k-10k` | 5 | 20.0% | +44.8% | -28.8% | 20.0% | 20.0% | 20.0% | +0.9% | -70.4% |
| `$10k-25k` | 2 | 50.0% | -7.9% | -7.9% | 100.0% | 100.0% | 100.0% | +195.4% | -38.8% |

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

- The latest 77-entry quant pass says `$0-5k` is the weak bucket: `44` trades, `47.7%` source-exit win rate, `-2.1%` median source ROI, only `10.3%` hit `+30%`.
- `$5k-10k` is the best stable bucket: `17` trades, `76.5%` source-exit win rate, `+10.5%` median source ROI, `41.2%` hit `+30%`.
- `15k+` is stricter, but it skips the strongest stable bucket in the current data.

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

Why `5000` volume is a candidate: the 77-entry quant pass shows the `$0`, `$2.5k-5k`, and very low-volume buckets are unreliable. The `$20k+` buckets look cleaner, but are stricter and slower to validate in live same-slot mode.

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

- Need a refreshed wallet quant audit with candle enrichment completed; the local 5,000-tx wallet cache exists, but the candle-enriched audit timed out during this pass.
- Need to shadow-test `0.25 SOL` sizing specifically. Tiny `0.04 SOL` shadow fills exaggerate fee drag and are no longer representative.
- Need to verify whether `PUMPSWAP_DIRECT_BUY_ENABLED=false` plus `MIRROR_GRADUATED_BUYS=false` fully prevents graduated buys or whether we should add a dedicated `SKIP_GRADUATED_BUYS=true` env gate.
- Need to compare `MIRROR_SELLS=true/full` versus `MIRROR_SELLS=true/proportional` for CyA.
