# GUg + 5qn + EAz Cluster Research

Wallets:

- GUg main wallet: `GUgMNi2tJcjYpDSFs2VZQpjryeWRG4Xgmi1JPDEoe1TH`
- 5qn lead wallet: `5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF`
- EAz lead wallet: `EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5`

Last updated: 2026-06-15

## Current Verdict

GUg is not just another wallet to copy. It looks like the main/settlement wallet for a 5qn + EAz cluster.

The cleanest actionable signal is not "GUg buys." The cleanest signal is:

1. 5qn and EAz both buy the same mint.
2. They buy in the same slot or near the same slot.
3. Both source buys are large, especially `>=8 SOL` each.
4. GUg usually appears about 9 seconds later with a tiny marker buy around `0.102 SOL`, then often sells quickly.
5. Sometimes 5qn or EAz also transfers token inventory directly into GUg.

That means GUg is best used as a confirmation/selector signal, not as the first entry source. If we wait for GUg to buy, we are usually 6-37 seconds behind the real entry. For same-slot or next-slot attempts, the first actionable event is still 5qn/EAz.

## Data Basis

### Cached Sample

Fresh `5000` transaction pulls were used for each wallet.

| Wallet | Swaps reconstructed | Unique buy mints | Unique sell mints |
|---|---:|---:|---:|
| GUg | 2981 | 407 | 417 |
| 5qn | 1387 | 421 | 315 |
| EAz | 1276 | 399 | 295 |

Source mint overlap:

| Group | Mints |
|---|---:|
| Any 5qn/EAz source mint | 454 |
| Bought by both 5qn and EAz | 366 |
| 5qn only | 55 |
| EAz only | 33 |

### Direct Transfers Into GUg

The cluster map found `37` direct token transfers into GUg:

| Edge | Transfers |
|---|---:|
| EAz -> GUg | 19 |
| 5qn -> GUg | 18 |

This matters because GUg's sells often include inventory that was sent to him. His sell/PnL profile is therefore not directly copyable by simply buying his tiny marker size.

### Lead Order

On tokens bought by at least two of these three wallets:

| Wallet | Buys | Shared | Sole first | Tied first | Lead rate | Avg rank |
|---|---:|---:|---:|---:|---:|---:|
| GUg | 514 | 271 | 161 | 0 | 59.4% | 1.41 |
| 5qn | 675 | 376 | 88 | 126 | 56.9% | 1.44 |
| EAz | 715 | 387 | 22 | 126 | 38.2% | 1.95 |

Read this carefully: the overall lead table is mixed because the wallet behavior changes by setup. The recent/high-conviction pattern is much cleaner: 5qn and EAz enter together first with large SOL, then GUg marks/receives/sells.

## Selector Statistics

### Main Signal Buckets

| Signal bucket | n | GUg bought | GUg buy rate | Transfer to GUg | Median GUg delay | Median GUg buy | Median GUg buy -> first sell |
|---|---:|---:|---:|---:|---:|---:|---:|
| Any 5qn/EAz source | 454 | 271 | 59.7% | 37 | 9s | 0.102 SOL | 55s |
| Both sources | 366 | 240 | 65.6% | 37 | 9s | 0.102 SOL | 55s |
| Both same-slot | 126 | 109 | 86.5% | 36 | 9s | 0.102 SOL | 55s |
| Both same-slot and each >=8 SOL | 122 | 106 | 86.9% | 36 | 9s | 0.102 SOL | 56s |
| Both each >=9 SOL | 112 | 95 | 84.8% | 36 | 9s | 0.102 SOL | 59s |
| Both total >=20 SOL | 59 | 48 | 81.4% | 15 | 9s | 0.102 SOL | 66s |

Best read: the strongest filter is `both wallets same-slot + each spends >=8 SOL`. It catches the exact pattern you noticed and selects GUg follow-through almost 87% of the time.

### Source Size Buckets

Only mints bought by both 5qn and EAz:

| Combined source size | n | GUg bought | GUg buy rate | Transfer to GUg | Same-slot count | Median GUg delay |
|---|---:|---:|---:|---:|---:|---:|
| 0-2 SOL | 122 | 80 | 65.6% | 0 | 0 | - |
| 2-6 SOL | 93 | 39 | 41.9% | 0 | 0 | - |
| 6-12 SOL | 19 | 11 | 57.9% | 1 | 1 | 11s |
| 12-18 SOL | 14 | 13 | 92.9% | 0 | 14 | 10s |
| 18-21 SOL | 68 | 57 | 83.8% | 22 | 66 | 9s |
| 21+ SOL | 50 | 40 | 80.0% | 14 | 45 | 9s |

The `12-18 SOL` bucket is very strong, but small. The main practical rule is still: both wallets fire together, and combined source size is roughly `18+ SOL`.

## Recent High-Conviction Examples

| Time UTC | Mint | 5qn SOL | EAz SOL | GUg? | GUg delay | GUg buy | First GUg sell |
|---|---|---:|---:|---|---:|---:|---:|
| 2026-06-15 14:09:15 | `3NpNAR...pump` | 9.888 | 9.664 | No | - | - | - |
| 2026-06-15 11:20:50 | `CR3pPp...pump` | 10.603 | 10.573 | Yes | 18s | 0.1021 | 4.443 SOL |
| 2026-06-15 10:59:27 | `FLc3iW...pump` | 10.917 | 10.279 | Yes | 7s | 0.1021 | 2.779 SOL |
| 2026-06-15 10:14:14 | `J4kR1b...pump` | 10.684 | 10.471 | Yes | 7s | 0.1021 | 3.844 SOL |
| 2026-06-15 10:00:28 | `9mWSoG...pump` | 10.218 | 11.028 | Yes | 8s | 0.1027 | 3.852 SOL |
| 2026-06-15 09:46:43 | `9vsTKk...pump` | 10.350 | 10.471 | Yes | 6s | 0.1021 | 2.897 SOL |
| 2026-06-15 09:20:06 | `7mf5fE...pump` | 10.674 | 10.380 | Yes | 16s | 0.1023 | 4.890 SOL |
| 2026-06-15 08:58:52 | `6CHy8U...pump` | 10.957 | 8.649 | Yes | 37s | 0.1021 | 0.098 SOL |

The miss cases matter. Same-slot + high size is not guaranteed, but it is much cleaner than blindly copying every 5qn or EAz buy.

## GUg Wallet Behavior

The corrected older GUg ledger audit covered `3000` txs and is better for wallet PnL because it handles transfers more carefully:

- Realized PnL: `+110.1840 SOL`
- Realized ROI on sold cost basis: `+55.0%`
- Realized trades: `347`
- Win rate: `62.5%`
- Profit factor: `4.04`
- Median hold: `2.3m`
- Average hold: `12.4m`

The fresh `5000` tx quant pass confirms the behavior shape, but source-exit ROI is inflated by transferred inventory:

- Buys/sells: `514 / 2467`
- Campaigns: `407`
- Closed campaigns: `405`
- Median entry size in candle sample: `0.102 SOL`
- Median MFE in candle sample: `+122.4%`
- Median MAE in candle sample: `-94.5%`
- Candle-enriched entries: `19`

The small candle sample hit Moralis free-plan limits, so use the path stats as directional only. Still, it matches the visual pattern: GUg enters tiny, exits very fast, and many tokens have large but brief upside followed by deep collapse.

## Filters And Strategy

### Best Current Filter Leads

1. Best filter: both 5qn and EAz buy the same mint in the same slot.
2. Best size filter: both source buys are `>=8 SOL`, or combined source buy is `>=18 SOL`.
3. Best live market-cap filter from the earlier 5qn/EAz research: `PREBUY_MIN_MARKET_CAP_USD=20000`.
4. Stricter market-cap variant: `PREBUY_MIN_MARKET_CAP_USD=50000`, but this will miss many of the GUg-selected 20k-50k runners.
5. Do not use Moralis volume/liquidity/momentum filters for same-slot. They are too slow and, in the GUg sample, many winners show `0` pre-5m volume because they are brand-new candles.
6. Do not mirror GUg sells mechanically unless the bot can understand transferred inventory. GUg's sell stream is not a normal copy target.

### How I Would Play It

If the bot only supports current env filters:

- Track 5qn + EAz together.
- Do not track GUg as the primary entry wallet.
- Do not mirror sells.
- Use bot-managed exits.
- Keep market cap `>=20k`.
- Use aggressive same-slot infra and fees.
- Use fixed size, not copy percentage, because source wallets are often spending 9-11 SOL each.

If we build one more feature, this gets much better:

- Add a cluster trigger: buy only when 5qn and EAz both buy the same mint within `0-1 slots` or within a very short wall-clock window.
- Add a source-size trigger: require both source buys `>=8 SOL`, or combined source size `>=18 SOL`.
- Treat GUg as a confirmation after entry:
  - If GUg appears within `20-30s`, keep normal trailing.
  - If GUg does not appear, tighten the trail or exit faster.
  - If a direct transfer into GUg appears, treat it as stronger hold/runner confirmation.

That feature would match the data better than a plain env-only filter.

## Starting Env Shape

Use this as the baseline if running the current bot without a dedicated cluster-confirmation feature:

```env
TRACKED_WALLETS=5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF,EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5

SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
MIRROR_SELLS=false
BUY_ONCE_PER_TOKEN=true

MIN_BUY_SOL=0.20
MAX_BUY_SOL=0.20
MAX_SOL_PER_TOKEN=0.20
MAX_SESSION_DEPLOYED_SOL=1.20
MAX_OPEN_POSITIONS=4

STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TAKE_PROFIT_PCT=
TRAILING_STEPS=10:-8,20:0,30:10,50:30,80:55,120:90,200:150,350:260,600:450

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
PREBUY_MIN_MARKET_CAP_USD=20000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false
```

For higher conviction sizing, use `0.25 SOL`, but only after the instance is actually landing next-slot or better. If fills are late, the same token can flip from edge to bag very fast.
