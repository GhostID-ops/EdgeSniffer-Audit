# CyA Bucket Audit - 2026-06-22

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Artifacts:

- `data/cya-ledger-buckets-30000.json` / `.md`: full ledger-first MC/source-exit study.
- `data/cya-quant-20260622.json`: candle-enriched path/exit study.

## Coverage

Expanded ledger-first pass:

- 30,000 transactions from 2026-05-29 through 2026-06-22.
- 21,292 reconstructed fills, 3,046 unique-mint campaigns, and 3,045/3,045 closed campaigns measured.
- 2,498 Pump-route, 62 BAGS, and 485 other-route campaigns.
- Entry MC comes from CyA's own first-fill price and historical SOL/USD, matching the live inferred-MC gate. It does not require a surviving pool or candles.

Candle path pass:

- Fresh 12,001-transaction snapshot, 1,260 campaigns, and 1,259 closed campaigns.
- 223 entries had external candle paths. Provider quota and historical pool availability limit this subset.
- Use this subset for MFE/MAE, target-hit, and exit-policy comparisons, not for live inferred-MC thresholds.

## Exit Strategy

| Exit | n | Win | Avg ROI | Median ROI | PF | Max cumulative drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Mirror CyA sell | 223 | 54.3% | +29.2% | +2.3% | 4.42 | -128.7 pts |
| 50% at +30%, 50% CyA sell | 223 | 63.7% | +23.0% | +11.6% | 4.47 | -127.8 pts |
| 20% trailing | 223 | 30.9% | +4.8% | -5.5% | 1.62 | -364.4 pts |
| TP30 / SL20 | 223 | 17.5% | -8.5% | -20.0% | 0.35 | -1,910.0 pts |
| TP50 / SL25 | 223 | 17.0% | -8.7% | -25.0% | 0.46 | -1,968.2 pts |
| 30-minute exit | 223 | 19.3% | -23.1% | -22.7% | 0.28 | -5,161.8 pts |

CyA remains exit-led. Full mirror has the highest mean; the hybrid has the better win rate, median, profit factor, and essentially the same drawdown. Fixed TP/SL and time exits are rejected.

## Ledger-First Entry Market-Cap Buckets

Pump-route results, which are the relevant Rust Pump.fun/PumpSwap cohort:

| Source-fill entry MC | n | Win | Avg ROI | Median ROI | Equal-size PF | Source PnL |
|---|---:|---:|---:|---:|---:|---:|
| $0-5k | 1,542 | 62.8% | +32.6% | +7.8% | 7.47 | +1,561.77 SOL |
| $5k-10k | 560 | 48.6% | +10.2% | -1.2% | 1.96 | +294.24 SOL |
| $10k-20k | 242 | 47.9% | +9.2% | -2.6% | 1.76 | +227.49 SOL |
| $20k-50k | 96 | 35.4% | -1.2% | -8.8% | 0.93 | +25.89 SOL |
| $50k-100k | 30 | 26.7% | -9.2% | -11.8% | 0.50 | -22.24 SOL |
| $100k+ | 28 | 17.9% | -8.5% | -7.7% | 0.38 | -55.02 SOL |

Live inferred-MC recommendation: no minimum and a `$10k` maximum. The `<$5k` cohort is strongest by a wide margin. `$5k-10k` remains positive but has a slightly negative median. `$20k+` should be rejected.

## Why The Earlier 223-Trade MC Result Differed

The smaller audit used the first available external candle price and only retained mints with usable historical candles. The live inferred filter instead calculates MC from CyA's source fill. Candle availability is non-random, and candle-open MC is not equivalent to source-fill MC.

The earlier apparent `$20k-50k` strength does not survive the 3,045-campaign source-fill study. The ledger-first result is the correct one for configuring `PREBUY_INFERRED_MC_FILTER_ENABLED`.

## Finalized CyA Hybrid Behavior

- Sell 50% of current bot inventory once quoted PnL reaches +30%.
- Execute the partial sell once only.
- Suppress ordinary TP, SL, and trailing exits when `HYBRID_EXIT_SOURCE_EXIT_REMAINING=true`.
- Stop price polling after the partial TP lands.
- Close all remaining confirmed inventory on CyA's first sell signal.
- Queue the source sell if the partial TP is awaiting confirmation, then drain the remainder afterward.
- Use the existing local Rust sell route under `RUST_ONLY_SELLS=true`; no Jupiter fallback is required.

## Recommended CyA Configuration

```env
TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o

RUST_ONLY_BUYS=true
RUST_ONLY_SELLS=true
JUPITER_FALLBACK_ENABLED=false

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MIRROR_SELL_MODE=full

HYBRID_EXIT_ENABLED=true
HYBRID_EXIT_TP_PCT=30
HYBRID_EXIT_TP_SELL_PCT=50
HYBRID_EXIT_SOURCE_EXIT_REMAINING=true
STOP_LOSS_PCT=
TAKE_PROFIT_PCT=
TRAILING_STEPS=

PREBUY_INFERRED_MC_FILTER_ENABLED=true
PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false
LOCAL_PUMPFUN_SOL_PRICE_USD=72.80
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
PREBUY_MIN_MARKET_CAP_USD=
PREBUY_MAX_MARKET_CAP_USD=10000
PREBUY_FILTER_FAIL_OPEN=false
```

The inferred-MC calculation uses data already present in the detected source swap plus the background-refreshed SOL price. It adds no HTTP request to the buy hot path.

## Caveats

- Ledger entry MC is source first-fill price x historical SOL/USD x assumed 1B supply. It aligns with the live inferred filter but includes CyA's fill impact.
- The MC table measures source-exit ROI. Hybrid MFE/MAE and target-hit behavior still comes from the smaller candle subset.
- Campaigns are unique-mint aggregates, matching the intended `BUY_ONCE_PER_TOKEN=true` policy.
- BAGS remains weak and thin; Rust-only routing naturally keeps the strategy focused on accepted local routes.
- `.env` was not changed. `.env.cya-same-slot` was updated with the Rust-only hybrid preset.
- This has not been live-tested on the VPS.
