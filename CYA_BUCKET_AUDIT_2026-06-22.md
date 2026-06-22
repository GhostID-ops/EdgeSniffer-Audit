# CyA Bucket Audit — 2026-06-22

Wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Artifact: `data/cya-quant-20260622.json`

## Coverage

- Fresh Helius snapshot: 12,001 transactions, 8,854 reconstructed swaps, 1,260 campaigns, 1,259 closed campaigns.
- Window: 2026-06-14 01:35 UTC through 2026-06-22 18:39 UTC.
- Candle-enriched bucket sample: 223 entries. The requested ceiling was 600; provider quota/data availability limited enrichment.
- Wallet realized PnL: +1,008.53 SOL; win rate 51.8%; median ROI +1.3%; profit factor 2.06.
- Median hold: 0.62 minutes. Median source entry: 3.008 SOL.

## Strategy Result

| Exit | n | Win | Avg ROI | Median ROI | PF | Max cumulative drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Mirror CyA sell | 223 | 54.3% | +29.2% | +2.3% | 4.42 | -128.7 pts |
| 50% at +30%, 50% CyA sell | 223 | 63.7% | +23.0% | +11.6% | 4.47 | -127.8 pts |
| 20% trailing | 223 | 30.9% | +4.8% | -5.5% | 1.62 | -364.4 pts |
| TP30 / SL20 | 223 | 17.5% | -8.5% | -20.0% | 0.35 | -1,910.0 pts |
| TP50 / SL25 | 223 | 17.0% | -8.7% | -25.0% | 0.46 | -1,968.2 pts |
| 30-minute exit | 223 | 19.3% | -23.1% | -22.7% | 0.28 | -5,161.8 pts |

CyA remains exit-led. Full mirror has the highest mean; the hybrid has the better win rate, median, profit factor, and essentially the same drawdown. Fixed TP/SL and time exits are rejected.

## Entry Market-Cap Buckets

| Approx entry MC | n | Source win | Avg source ROI | Median source ROI | PF | Hit +30 | Median MAE | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| $0–5k | 100 | 49.0% | +5.3% | -0.5% | 1.57 | 15.0% | -12.3% | Skip |
| $5–10k | 38 | 55.3% | +23.6% | +1.9% | 3.93 | 34.2% | -64.1% | Copy |
| $10–20k | 21 | 52.4% | +13.2% | +5.5% | 2.36 | 57.1% | -80.3% | Copy |
| $20–50k | 50 | 64.0% | +77.7% | +8.7% | 14.32 | 62.0% | -92.9% | Best supported bucket |
| $50–100k | 7 | 71.4% | +100.7% | +11.6% | 10.74 | 28.6% | -59.2% | Too thin |
| $100k+ | 7 | 42.9% | +29.7% | -1.9% | 3.41 | 57.1% | -59.9% | Too thin / hybrid only |

Recommended initial live range: `$5k–$50k`. Shadow `$50k+` separately until it has at least 30–50 measured trades.

## Other Buckets

- Pre-5m momentum `+30%+`: n=76, 63.2% wins, +16.6% median ROI, PF 7.39. This is a strong research signal, but Moralis must stay off the hot path.
- Pre-5m volume `$20k–$50k`: n=38, 76.3% wins, +25.5% median ROI, PF 19.33. Also research/shadow only because an HTTP gate would cost latency.
- Source buy size is not selective: 219 of 223 measured trades are in the 3–6 SOL bucket.
- Drawdowns are violent in the winning MC buckets. A tight stop destroys the source-exit edge.

## Recommended CyA Shadow Configuration

```env
TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o

SIMULATION_MODE=true
SHADOW_EXECUTION_MODE=true

RUST_ONLY_BUYS=true
RUST_ONLY_SELLS=true
JUPITER_FALLBACK_ENABLED=false

BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MIRROR_SELL_MODE=full

MIN_BUY_SOL=0.25
MAX_BUY_SOL=0.25
MAX_SOL_PER_TOKEN=0.25
MAX_SESSION_DEPLOYED_SOL=0.75
MAX_OPEN_POSITIONS=2

LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
PREBUY_MIN_MARKET_CAP_USD=5000
PREBUY_MAX_MARKET_CAP_USD=50000
PREBUY_FILTER_FAIL_OPEN=false
PREBUY_MORALIS_FILTER_ENABLED=false

MAX_ENTRY_PRICE_IMPACT_PCT=60
MAX_SOURCE_DRIFT_PCT=25

PUMPSWAP_DIRECT_BUY_ENABLED=true
MIRROR_GRADUATED_BUYS=false
```

The last two flags do **not** currently form a strict graduated-buy exclusion: the direct PumpSwap attempt occurs before the `MIRROR_GRADUATED_BUYS` fallback guard. Keep PumpSwap direct enabled because it is also needed for local sells. A dedicated pre-buy `SKIP_GRADUATED_BUYS` gate is still needed if the strategy must exclude all graduated entries without compromising exits.

## Caveats

- Entry MC is an approximation from historical USD price and an assumed 1B supply. It is useful for Pump.fun-style buckets, not a precise cross-route valuation.
- The enriched sample mixes routes. Prior BAGS-only research (57 closed campaigns through June 19) was negative: -24.09 SOL, -10.2% median ROI, PF 0.80.
- Candle availability is not random, so bucket results can be selection-biased.
- Average ROI is winner-sensitive. Use median, PF, sample size, and drawdown together.
- No `.env` values were changed. This audit was not live-tested on the VPS.
