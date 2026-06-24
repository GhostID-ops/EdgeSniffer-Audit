# 4vw54 Deep Wallet Research

Wallet: `4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9`

Last updated: 2026-06-24

Fresh runs used:

- High-coverage ledger bucket audit: `npx tsx src/walletLedgerBucketAudit.ts 4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9 12001 wallet-ledger-buckets-4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9-12001`
- Ledger bucket artifacts: `data/wallet-ledger-buckets-4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9-12001.json`, `data/wallet-ledger-buckets-4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9-12001.md`
- Candle-enriched quant audit: `npx tsx src/walletQuantAudit.ts 4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9 5000 160`
- Quant artifact: `data/wallet-quant-audit-4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9-5000-160.json`
- Older path audit reference: `data/path-wallet-audit-4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9.md`
- Older ledger audit reference: `data/wallet-ledger-audit-4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9.md`

## Current Verdict

4vw54 is a strong low-market-cap speed wallet. The high-coverage ledger sample is the main source of truth here: `1,946` closed measured campaigns across `12,001` fetched transactions, covering `2026-06-03T16:56:05Z` to `2026-06-24T19:25:24Z`.

Best current approach:

- Copy once per mint.
- Use copy percentage rather than fixed-size entries because this wallet's sizing is unusually consistent.
- Start around `10%` to `25%` copy size for testing, then scale only if our entry drift is acceptable.
- Prefer entries below `$20k` inferred/source-fill market cap.
- More conservative variant: pump route below `$10k`.
- Avoid `$50k-$100k` entries. This is the clearest bad bucket in the high-coverage sample.
- Keep source exits enabled for the remainder.
- Keep the hybrid exit: `50% at +30%, 50% source exit`.
- Do not use pure trailing stop or fixed TP/SL as the primary exit model for this wallet.
- Do not use long time exits. Median hold time is under one minute and time exits performed badly.

This wallet is closer to CyA than Omego in exit behavior: the source exit is valuable, and the hybrid partial-profit model improves the risk profile. It is not an Omego-style wallet where we should ignore source exits and rely mainly on bot-managed trailing.

## Data Basis

High-coverage ledger bucket audit:

- Transactions: `12,001`
- Reconstructed fills: `8,279`
- Campaigns: `1,948`
- Closed measured campaigns: `1,946`
- Oldest transaction: `2026-06-03T16:56:05Z`
- Newest transaction: `2026-06-24T19:25:24Z`
- Pump rows: `1,635`
- BAGS rows: `21`
- Other rows: `290`
- SOL/USD source: CoinGecko historical range
- Entry MC basis: source first-fill price times assumed `1,000,000,000` supply

Quant/candle pass:

- Transactions fetched: `5,000`
- Swaps reconstructed: `3,713`
- Buys/sells: `2,536 / 1,177`
- Campaigns: `845`
- Closed campaigns: `842`
- Candle-enriched entries: `92`
- Window: `2026-06-15T14:41:15Z` to `2026-06-24T19:29:25Z`

Quant caveat:

Moralis returned free-plan daily usage errors during the candle run, so the `92` measured entries should be treated as directional. The ledger bucket sample is much larger and should carry more weight for entry filters.

## Wallet-Level Stats

From the 5,000-tx quant pass:

- Realized PnL: `+424.23 SOL`
- Closed-campaign win rate: `50.8%`
- Average source ROI: `+16.4%`
- Median source ROI: `+0.9%`
- Profit factor: `3.09`
- Average holding duration: `15.79m`
- Median holding duration: `0.63m`
- Largest win/loss: `+12.82 SOL / -5.98 SOL`
- Median entry size: `2.017 SOL`
- Average entry size: `2.391 SOL`

Entry environment from the candle slice:

- Median pre-5m volume: `$9.9k`
- P75 pre-5m volume: `$18.9k`
- Median current liquidity: `$3.1k`
- Median current market cap: `$1.9k`
- Median MFE: `+6.8%`
- Median MAE: `-56.2%`

Read:

- The wallet is profitable while holding for very short windows.
- The median source exit is only slightly positive, but the profit factor is strong because winners pay well.
- Deep MAE is common, so tight fixed stops are likely to cut many trades that the source wallet still exits profitably.

## Strategy Backtest

Candle-enriched `92` entry sample:

| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 50% at +30, 50% source exit | 92 | 67.4% | +18.4% | +12.0% | 5.03 | -68.1pp |
| Sell when wallet sells | 92 | 55.4% | +15.6% | +5.5% | 3.06 | -151.3pp |
| 20% trailing stop | 92 | 27.2% | -0.7% | -9.9% | 0.93 | -360.7pp |
| TP30 SL20 | 92 | 17.4% | -8.8% | -20.0% | 0.36 | -904.2pp |
| TP50 SL25 | 92 | 16.3% | -9.6% | -25.0% | 0.45 | -1038.5pp |
| TP40 SL25 | 92 | 17.4% | -10.4% | -25.0% | 0.39 | -1074.1pp |
| TP60 SL30 | 92 | 14.1% | -12.9% | -30.0% | 0.38 | -1304.6pp |
| Time exit 4h | 92 | 6.5% | -15.3% | -45.4% | 0.66 | -2976.1pp |
| Time exit 24h | 92 | 6.5% | -28.6% | -49.4% | 0.38 | -3120.0pp |
| Time exit 30m | 92 | 10.9% | -36.5% | -42.0% | 0.10 | -3355.7pp |
| Time exit 1h | 92 | 8.7% | -37.7% | -42.0% | 0.11 | -3572.4pp |

Read:

- The best tested behavior is the hybrid `50% at +30%, 50% source exit`.
- Source exit alone is good, but hybrid improves win rate, median ROI, profit factor, and drawdown.
- Pure trailing is not supported by the current data.
- Fixed TP/SL is very weak.
- Time exits are bad for this wallet.

Recommended exit posture:

```env
HYBRID_EXIT_ENABLED=true
HYBRID_EXIT_TP_PCT=30
HYBRID_EXIT_TP_SELL_PCT=50
HYBRID_EXIT_SOURCE_EXIT_REMAINING=true
MIRROR_SELLS=true
```

## Target And Downside Probabilities

From the `92` candle-measured entries:

| Target | Hit probability | Avg time to hit |
|---|---:|---:|
| `+10%` | 42.4% | 394s |
| `+20%` | 33.7% | 571s |
| `+30%` | 29.3% | 655s |
| `+50%` | 21.7% | 882s |
| `+100%` | 10.9% | 1049s |

| Downside | Hit probability |
|---|---:|
| `-10%` | 82.6% |
| `-20%` | 77.2% |
| `-30%` | 72.8% |

Read:

- This wallet's entries commonly go red before resolving.
- A simple `-20%` or `-30%` stop will fire on most entries.
- If using a stop loss, it should be an emergency protection rule with a hold delay, not the primary alpha model.

## High-Coverage Entry Market-Cap Buckets

All routes, ledger source-exit basis:

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 723 | 58.8% | +18.6% | +7.0% | 4.19 | +349.06 SOL | 0.45m |
| `$5k-10k` | 773 | 52.3% | +13.8% | +1.3% | 2.59 | +378.40 SOL | 0.80m |
| `$10k-20k` | 357 | 49.0% | +15.8% | -0.6% | 2.67 | +208.22 SOL | 1.17m |
| `$20k-50k` | 73 | 43.8% | +28.7% | -5.0% | 3.02 | +12.72 SOL | 1.50m |
| `$50k-100k` | 12 | 25.0% | -9.8% | -6.2% | 0.31 | -6.36 SOL | 0.37m |
| `$100k+` | 8 | 25.0% | +47.0% | -13.4% | 4.63 | +8.48 SOL | 0.13m |

Pump route only:

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 621 | 59.1% | +18.8% | +7.2% | 4.24 | +293.06 SOL | 0.45m |
| `$5k-10k` | 659 | 51.7% | +14.3% | +1.1% | 2.64 | +338.38 SOL | 0.80m |
| `$10k-20k` | 285 | 51.2% | +18.9% | +1.3% | 3.13 | +202.69 SOL | 1.35m |
| `$20k-50k` | 65 | 40.0% | +28.6% | -7.2% | 2.96 | +0.34 SOL | 1.38m |
| `$50k-100k` | 4 | 25.0% | -23.5% | -13.5% | 0.06 | -4.67 SOL | 3.02m |
| `$100k+` | 1 | 0.0% | -20.9% | -20.9% | 0.00 | -1.05 SOL | 0.50m |

Other route:

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 96 | 58.3% | +18.4% | +5.0% | 4.23 | +56.06 SOL | 0.41m |
| `$5k-10k` | 112 | 56.3% | +11.8% | +2.9% | 2.44 | +41.39 SOL | 0.72m |
| `$10k-20k` | 71 | 39.4% | +3.1% | -4.0% | 1.25 | +4.63 SOL | 0.90m |
| `$20k-50k` | 8 | 75.0% | +29.7% | +20.2% | 3.64 | +12.38 SOL | 2.20m |
| `$50k-100k` | 1 | 0.0% | -35.3% | -35.3% | 0.00 | -1.78 SOL | 0.90m |
| `$100k+` | 2 | 0.0% | -21.1% | -21.1% | 0.00 | -0.85 SOL | 0.91m |

Read:

- The main edge lives under `$20k`.
- `$0-5k` is not a dead zone for this wallet; it is actually one of the strongest buckets.
- `$5k-10k` has the largest total PnL in the all-route sample.
- `$10k-20k` is still good on average, especially pump route.
- `$20k-50k` is profitable but thinner and less reliable.
- `$50k-$100k` should be avoided.
- `$100k+` is too thin to trust despite positive total PnL in the all-route sample.

## Strategy Cuts From Ledger Rows

These are source-exit ledger simulations, not candle path simulations.

| Strategy Cut | n | Win | Avg ROI | Median ROI | Source PnL | 25% Copy PnL Approx |
|---|---:|---:|---:|---:|---:|---:|
| All trades | 1,946 | 53.5% | +16.5% | +2.4% | +950.54 SOL | +237.63 SOL |
| All under `$20k` | 1,853 | 54.2% | +16.1% | +2.9% | +935.69 SOL | +233.92 SOL |
| All under `$50k` | 1,926 | 53.8% | +16.5% | +2.6% | +948.41 SOL | +237.10 SOL |
| Pump under `$20k` | 1,565 | 54.6% | +16.9% | +3.0% | +834.12 SOL | +208.53 SOL |
| Pump under `$10k` | 1,280 | 55.3% | +16.5% | +3.2% | +631.44 SOL | +157.86 SOL |
| Skip `$50k-$100k` only | 1,934 | 53.7% | +16.7% | +2.5% | +956.89 SOL | +239.22 SOL |
| Source size `0.1-5 SOL` | 1,886 | 53.8% | +15.8% | +2.6% | +913.06 SOL | +228.26 SOL |
| Under `$20k` and source size `0.1-5 SOL` | 1,821 | 54.4% | +16.2% | +2.9% | +912.79 SOL | +228.20 SOL |

Read:

- The broadest practical filter is `entry MC < $20k`.
- A tighter `pump < $10k` filter improves median and win rate a bit, but cuts too much opportunity.
- Simply avoiding `$50k-$100k` preserves almost all flow and slightly improves PnL.
- Source-size filtering adds little because the wallet already self-sizes consistently.

## Source Buy Size Buckets

Ledger source-exit basis:

| Source first buy | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median MC | Median hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `1-2 SOL` | 214 | 64.0% | +20.2% | +8.3% | 5.80 | +85.78 SOL | `$2.9k` | 0.45m |
| `2-5 SOL` | 1,671 | 52.4% | +15.2% | +1.7% | 2.84 | +825.56 SOL | `$6.6k` | 0.72m |
| `5+ SOL` | 58 | 44.8% | +9.1% | -0.8% | 1.61 | +31.69 SOL | `$19.6k` | 2.42m |

Read:

- His `1-2 SOL` buys are the cleanest.
- His dominant `2-5 SOL` buy size is still strongly profitable and carries most of the total edge.
- `5+ SOL` buys are not an automatic reject, but they are weaker and tend to enter higher.
- This supports copy percentage. Fixed-size entries would flatten useful source sizing information.

## Candle Market-Cap Buckets

Lower-coverage candle slice, source-exit basis:

| Approx entry MC | n | Win % source exit | Avg source ROI | Median source ROI | Hit +20 | Hit +30 | Hit +50 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `$0-5k` | 39 | 53.8% | +7.6% | +4.1% | 25.6% | 23.1% | 15.4% | +0.0% | -24.9% |
| `$5k-10k` | 25 | 52.0% | +12.3% | +3.6% | 40.0% | 28.0% | 20.0% | +17.5% | -65.0% |
| `$10k-20k` | 16 | 50.0% | +7.1% | +1.4% | 37.5% | 37.5% | 25.0% | +5.0% | -82.4% |
| `$20k-50k` | 12 | 75.0% | +59.8% | +55.2% | 41.7% | 41.7% | 41.7% | +13.7% | -92.6% |
| `$50k-100k` | 0 | - | - | - | - | - | - | - | - |
| `$100k+` | 0 | - | - | - | - | - | - | - | - |

Read:

- The candle slice likes `$20k-$50k`, but the sample is only `12` trades and conflicts with the much larger ledger view.
- Use this as support for allowing `$20k-$50k` in shadow, not as a reason to make it the main live bucket.
- The candle slice still supports the core idea that this wallet can source-exit profitably in low MC ranges.

## Volume Buckets

Lower-coverage candle slice:

| Pre-5m volume | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| `$0` | 12 | 75.0% | +60.0% | +55.2% | 33.3% | +5.7% | -63.4% |
| `$0-2.5k` | 2 | 50.0% | -4.7% | -4.7% | 0.0% | +0.0% | -61.2% |
| `$2.5k-5k` | 9 | 33.3% | -6.5% | -10.8% | 33.3% | +6.8% | -5.7% |
| `$5k-10k` | 25 | 52.0% | +8.0% | +4.6% | 16.0% | +0.0% | -37.9% |
| `$10k-20k` | 22 | 63.6% | +14.8% | +8.4% | 27.3% | +16.4% | -63.9% |
| `$20k-50k` | 21 | 52.4% | +14.1% | +3.6% | 42.9% | +17.6% | -63.1% |
| `$50k+` | 1 | 0.0% | -36.8% | -36.8% | 100.0% | +67.3% | -93.0% |

Read:

- Volume is research-useful but not clean enough for a hot-path filter.
- The best practical read is that `$10k-$50k` pre-5m volume looks healthier than `$2.5k-$5k`.
- Do not add Moralis/HTTP volume checks to same-slot buys.

## Momentum Buckets

Lower-coverage candle slice:

| Pre-5m momentum | n | Win % source exit | Avg source ROI | Median source ROI | Hit +30 | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| `<= -20%` | 19 | 42.1% | -2.6% | -8.6% | 31.6% | +2.6% | -35.4% |
| `-20% to -5%` | 12 | 58.3% | +7.5% | +7.1% | 25.0% | +0.0% | -14.7% |
| `-5% to 0%` | 4 | 75.0% | +14.3% | +22.0% | 0.0% | +0.0% | -5.7% |
| `0% to +10%` | 2 | 50.0% | +7.9% | +7.9% | 0.0% | +0.0% | -14.7% |
| `+10% to +30%` | 9 | 66.7% | +14.6% | +10.2% | 33.3% | +0.0% | -42.0% |
| `+30%+` | 34 | 50.0% | +13.9% | +1.5% | 32.4% | +17.5% | -66.4% |
| `unknown` | 12 | 75.0% | +60.0% | +55.2% | 33.3% | +5.7% | -63.4% |

Read:

- Momentum does not give a clean enough same-slot rule.
- Very negative momentum `<= -20%` is weak on source exits.
- Positive momentum can work, but the better action is still to preserve entry speed and rely on local MC/route/sizing.

## Live Filter Recommendation

The best live-compatible filter for this wallet is local inferred market cap only. No HTTP/API filter belongs in the hot path.

Balanced live test:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=
PREBUY_MAX_MARKET_CAP_USD=20000
PREBUY_FILTER_FAIL_OPEN=false
```

Conservative live test:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
PREBUY_MIN_MARKET_CAP_USD=
PREBUY_MAX_MARKET_CAP_USD=10000
PREBUY_FILTER_FAIL_OPEN=false
```

Broad live test:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
PREBUY_MIN_MARKET_CAP_USD=
PREBUY_MAX_MARKET_CAP_USD=50000
PREBUY_FILTER_FAIL_OPEN=false
```

Hard avoid if route/MC gating supports it:

```env
PREBUY_SKIP_MARKET_CAP_RANGE_USD=50000:100000
```

Note: only use the skip-range env if the bot actually supports it. If not, implement this as a local inferred-MC condition before enabling it live.

## Copy Size Recommendation

Because his first buys cluster tightly around `2.017 SOL` and `3.017 SOL`, copy percentage is more natural than fixed buys.

Practical copy-size ladder:

- Probe: `5%` copy, roughly `0.10-0.15 SOL` on normal entries.
- Serious test: `10%` copy, roughly `0.20-0.30 SOL`.
- Aggressive test: `25%` copy, roughly `0.50-0.75 SOL`.

Recommendation:

- Start with `10%` until we confirm entry drift and sell reliability.
- Move to `25%` only if same-slot/next-slot entries are close enough and source sells mirror cleanly.
- Avoid fixed `0.2 SOL` as the main sizing model for this wallet unless bankroll stability matters more than source-signal fidelity.

## Suggested Bot Posture

```env
RUST_ONLY_BUYS=true
RUST_ONLY_SELLS=true
JUPITER_FALLBACK_ENABLED=false
MIRROR_SELLS=true

COPY_PERCENTAGE=10

HYBRID_EXIT_ENABLED=true
HYBRID_EXIT_TP_PCT=30
HYBRID_EXIT_TP_SELL_PCT=50
HYBRID_EXIT_SOURCE_EXIT_REMAINING=true

LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
PREBUY_MIN_MARKET_CAP_USD=
PREBUY_MAX_MARKET_CAP_USD=20000
PREBUY_FILTER_FAIL_OPEN=false
```

Optional protection:

```env
STOP_LOSS_PCT=35
MIN_HOLD_BEFORE_STOP_LOSS_MS=15000
```

Read:

- The stop loss is only a damage guard, not the primary strategy.
- A tight stop is not supported because the wallet's entries hit deep downside very often.
- The most important operational requirement is still fast local execution and clean mirror sells.

## Final Read

This is a good candidate wallet.

The cleanest version is:

- Copy percentage, not fixed bet.
- No HTTP filters in the buy path.
- Local inferred MC cap around `$20k`.
- Hybrid exit: 50% at `+30%`, rest source exit.
- Avoid `$50k-$100k` entries.
- Scale only after live drift is measured.

Compared with CyA:

- 4vw54 has cleaner low-MC buckets and more consistent source sizing.
- CyA's best research often needs a minimum MC floor; 4vw54 does not. His `$0-5k` bucket is strong.
- Both favor the hybrid exit over pure trailing/fixed TP/SL.

Compared with Omego:

- 4vw54 is much better for source-exit mirroring.
- Omego is more of a bot-managed path/MFE wallet.
- Do not apply Omego's trailing-first posture here.

