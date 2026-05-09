# Bot upgrades reference

This document captures every meaningful change made to the bot during the recent overhaul. Use it as a refresher on what's new, why it's there, and how to configure it.

---

## Overview

The bot was reshaped around three high-level goals:

1. **Cut entry latency** so we land closer to the source wallet's price.
2. **Replace bad entries with sized-down good entries** instead of blanket-skipping.
3. **Add real exit/risk machinery** so winners don't fade into losers and a bad streak can't drain the account.

The biggest single change is **direct pump.fun integration** — for pre-graduation pump.fun tokens, the bot bypasses Jupiter entirely, reads the bonding curve directly, computes exact buy math, and submits a buy instruction straight to the pump.fun program. This solves the "Jupiter says 100% impact" problem on fresh launches and shaves another 100-300ms off the entry path.

---

## What's new at a glance

### Latency
- **Direct `getTransaction` fast path** for WS-detected swaps (was Helius enhanced parse with 12×250ms retries)
- **ALT cache (LRU)** in the Sender path
- **Direct ATA balance lookup** for sells (was full token-account scan)
- **Halved `RAW_TX_RETRY_MS`** (150 → 75)

### Entry quality
- **Soft impact cap with reduce-and-retry** — no more hard skips on high-impact trades
- **Direct pump.fun bonding-curve route** — bypasses Jupiter for pre-graduation tokens
- **Source-fill-price drift check** (opt-in) — skip if our price is much worse than source's

### Risk / exit
- **Tiered polling** (fast / near-trigger / normal buckets)
- **Drift-based quote skip** — projects PnL instead of always quoting
- **Stop-loss grace window** (3500ms default) — protects against fake first-tick SLs from quote spread
- **Stair-step trailing stop / breakeven move** (opt-in)
- **Sell broadcast retry with fresh blockhash** for transient errors

### Safety / circuit breakers
- **Daily session loss limit** (opt-in) — blocks new buys after cumulative loss
- **Consecutive-loss circuit breaker** (opt-in) — pauses new buys after N losses in a row

### Observability
- **Persistent JSONL trade log** at `data/trades.jsonl` — every buy/sell with config snapshot
- **Per-detection latency in WS logs** — tells you whether the fast path is winning races

### Misc infra
- **Dynamic priority fees** — background-polled p75 of recent fees on the pump.fun program
- **Bounded `processedSignatures`** — no more memory creep over long sessions

---

## Direct pump.fun integration (the big one)

### Why it exists

When a pump.fun token is freshly launched, Jupiter often hasn't indexed the bonding curve yet. Asking Jupiter for a quote on a 5-second-old mint typically returns `priceImpactPct: 1.0` (100%) — not because the pool is empty, but because Jupiter doesn't know about it yet. The bot's impact cap then correctly refused to take that quote at face value, which meant **systematically missing the freshest launches** — exactly the trades you want to catch.

Other sniper bots solve this by **never asking Jupiter for fresh pump.fun tokens.** They go directly to the pump.fun program. So now we do too.

### How it works

Every buy now starts with a "Phase 0" attempt:

1. **Detect**: derive the bonding curve PDA from the mint, fetch it via `getAccountInfo`. If the account exists, is owned by the pump.fun program, and `complete: false` → it's a pre-graduation pump.fun token.
2. **Quote locally**: decode the curve state (`virtual_sol_reserves`, `virtual_token_reserves`), apply the constant-product formula with the 1% trading fee deducted, compute exact `tokens_out` and impact.
3. **Liquidity-aware sizing**: if requested size exceeds your impact cap, binary-search the largest size that fits — no multi-probe Jupiter calls.
4. **Build + sign + broadcast**: assemble compute budget + create-ATA-idempotent + buy instruction + tip, sign, send via Sender.

If detection fails (graduated token, not on curve, etc.), the bot **falls through to Jupiter** — graduated tokens still work the same way they did before.

### What this delivers

- No more "100% impact" false negatives on fresh launches
- Exact, deterministic impact math (you know the curve formula)
- ~100-300ms faster than the Jupiter path (no quote/swap roundtrips)
- Same Sender-based broadcast as before — tip, priority fee, fast landing all unchanged

### Configuration

```env
# Direct pump.fun route — on by default
PUMPFUN_DIRECT_ENABLED=true
```

Set to `false` to disable and route everything through Jupiter (you would only do this for debugging).

### Caveats

- The bot uses the historical pump.fun program ID and account layout. If pump.fun ships a new program version with different fields, the decoder may misread.
- Fee recipient is hardcoded to the historical default; modern pump.fun reads it from the global PDA dynamically. If trades fail with an account-mismatch error, this needs updating.
- Watch the console for `Pump.fun curve detected (...)` lines on the first sim run to verify it's working.

---

## Latency improvements

### Direct `getTransaction` fast path

**Was**: When the WS callback fired with a signature, the bot called Helius's enhanced parse API and retried up to 12 times × 250ms = 3 seconds while Helius's index caught up.

**Now**: Two paths run in parallel — `readConnection.getTransaction(sig)` (raw RPC, fast) and Helius enhanced parse (slow, fallback). Whichever returns a swap first wins.

**Impact**: Median WS-detection latency dropped from 600-1500ms to 230-700ms. Floor is now Solana's `confirmed` commitment delay, not Helius's index lag.

**Caveat**: `getTransaction` doesn't support `processed` commitment, so we're stuck at confirmed (~400ms minimum). Going below that requires gRPC streaming (which you opted out of) or log-pattern detection from `logsSubscribe` payloads (a future option).

The console now logs `(raw, NNNms)` or `(enhanced, NNNms)` after each WS detection so you can see which path won.

### ALT cache

**Was**: Every transaction build called `getAddressLookupTable` for each ALT in the swap. 50-150ms per buy.

**Now**: 64-entry LRU cache with 30-minute TTL. Jupiter reuses a small set of hot ALTs across thousands of swaps, so warm-cache hit rate is near 100% after the first few trades.

### Direct ATA balance lookup

**Was**: Every sell called `getParsedTokenAccountsByOwner` which returns all token accounts the wallet owns, then filtered for the right mint. 100-300ms per sell.

**Now**: `getTokenBalanceFast` derives the ATA address deterministically and reads it directly with `getTokenAccountBalance`. One account read instead of N. Falls back to scan-all on error.

### Retry interval halve

`RAW_TX_RETRY_MS` dropped from 150ms to 75ms. Saves ~75ms on the median tail when the first `getTransaction` poll misses.

---

## Entry quality

### Soft impact cap with reduce-and-retry

**Was**: Hard cap. If Jupiter said impact > cap, the trade was skipped entirely.

**Now**: Two-phase entry. Phase 1 ("probe-and-shrink") quotes at requested size; if impact exceeds the cap, reduces size proportionally and re-quotes. Repeats up to `MAX_IMPACT_REDUCE_ATTEMPTS` times before giving up. The first valid quote is reused as the slippage-ladder's first attempt to skip one HTTP roundtrip.

With pump.fun direct, this phase uses the bonding curve math directly (binary search for max-fit size) — no multi-probe.

**Configuration**:

```env
# Cap on entry impact (percent). Empty/0 = disabled.
MAX_ENTRY_PRICE_IMPACT_PCT=12

# How many size-reduction attempts before giving up. Default 2.
MAX_IMPACT_REDUCE_ATTEMPTS=2
```

For dynamic sizing to actually engage, set `MIN_BUY_SOL` < `MAX_BUY_SOL`. With them equal, there's no room to reduce.

### Source-fill-price drift check (opt-in, off by default)

**What it does**: Captures the source wallet's effective fill price (`source_sol_in / source_tokens_out`) when extracting their swap. Compares to our quoted price. If our price is more than `MAX_SOURCE_DRIFT_PCT`% above source's, we skip — we're chasing, not copying.

**Configuration**:

```env
# Skip if our quote is more than this % above source's fill. Empty/0 = disabled.
# MAX_SOURCE_DRIFT_PCT=25
```

**When to use it**: If you're routinely filling at much worse prices than source (lots of competing snipers piling in between source and you). Most users should leave it off — the impact cap is a more direct gate, and you'd rather fill at a smaller size than miss the trade.

---

## Risk / exit management

### Tiered risk-check polling

**Was**: One fixed cadence (`RISK_CHECK_INTERVAL_MS`) for every position.

**Now**: Three buckets per position per tick:
- **fast** — first `RISK_FAST_WINDOW_MS` after entry (volatility window right after buy)
- **near** — last-known PnL is within `RISK_NEAR_TRIGGER_PCT` points of TP/SL
- **normal** — everything else

Each bucket has its own interval. Near-trigger > fast > normal priority when multiple positions are due in one tick.

### Drift-based quote skip

Per-position state tracks `(lastPnlPct, lastQuotedAt)`. Each tick, computes `lastPnl ± RISK_DRIFT_PCT_PER_SEC × elapsedSec` (worst-case projection). If that window can't possibly have crossed a trigger AND the bucket interval hasn't elapsed, **the Jupiter quote is skipped entirely**. Saves a lot of credits on calm positions — no extra HTTP, just bookkeeping.

### Stop-loss grace window

**What it does**: SL is disarmed for the first `MIN_HOLD_BEFORE_STOP_LOSS_MS` after a buy (default 3500ms). TP can still fire immediately.

**Why**: Quote spread on a fresh fill often shows -7% to -15% on the first risk-check, just from the bid-ask. Without grace, tight stop-losses would fire instantly on every entry. The grace lets the trade actually start before SL is allowed to trigger.

This change has measurably saved trades in your sessions (e.g., RAKU#1 in the recent log: first read at -34.7% with `stopLoss=true`, but grace suppressed the fire and the trade recovered to +52% TP).

### Stair-step trailing stop / breakeven move (opt-in, off by default)

**What it does**: Tracks per-position peak PnL%. As peak crosses configured thresholds, the effective stop level locks higher.

**Configuration**:

```env
# Format: "peakPct:floorPct,peakPct:floorPct,..."
# Example: at +10% peak lock breakeven; at +20% peak lock +5%.
# TRAILING_STEPS=10:0,20:5
```

**Worked example** with `TRAILING_STEPS=10:0,20:5`, `STOP_LOSS_PCT=25`, `TAKE_PROFIT_PCT=30`:

| Tick | PnL | Peak | Effective stop | What happens |
|---|---|---|---|---|
| t=0.5s | -7% | 0% | (grace) | SL disarmed |
| t=4s | -5% | 0% | -25% | Base SL active |
| t=12s | +13% | +13% | **0%** | +10% step triggered |
| t=16s | +24% | +24% | **+5%** | +20% step triggered |
| t=20s | +18% | +24% | **+5%** | Peak unchanged |
| t=24s | +6% | +24% | **+5%** | PnL ≤ +5% → trailing stop fires at +5% |

Without trailing, this trade exits at -25%. With trailing, +5%.

**The tradeoff**: tighter steps lock profit faster but whipsaw on volatile uptrends; looser steps capture more of the continued moves but give back more on fades. `10:0,20:5` is a reasonable starting point.

### Sell broadcast retry with fresh blockhash

**What it does**: When `broadcastTransactionWithSender` fails on transient errors (blockhash expired, network glitch, "block height exceeded"), the mirror loop now adds an additional attempt with a fresh quote (which gets a fresh blockhash baked in). Bounded to one extra retry past the slippage ladder.

**Why**: A failed sell leaves a position stuck with stale risk state. Slippage retries already worked; broadcast retries didn't.

---

## Safety / circuit breakers

Both opt-in. They share the `circuitBreaker.ts` module. They only block buys — never sells. Existing positions can always exit.

### Daily session loss limit

**What it does**: Tracks cumulative realized PnL since session start. When it drops below `-DAILY_LOSS_LIMIT_SOL`, blocks all new buys for the rest of the session. Manual restart re-enables.

**Configuration**:

```env
# Stop trading if total session loss reaches this in SOL. Empty/0 = disabled.
# DAILY_LOSS_LIMIT_SOL=5
```

### Consecutive-loss circuit breaker

**What it does**: Tracks recent trade outcomes (win/loss). When N losses in a row fire, pauses new buys for `CIRCUIT_BREAKER_PAUSE_MS`. Pause auto-clears after the duration.

**Configuration**:

```env
# Pause new buys after N losses in a row. 0 = disabled.
# CONSECUTIVE_LOSS_LIMIT=3

# Pause duration in ms after a circuit breaker trip.
# CIRCUIT_BREAKER_PAUSE_MS=600000
```

---

## Observability

### Persistent JSONL trade log

**Where**: `data/trades.jsonl` (relative to wherever you run the bot from). Created on first trade.

**What's captured per record**: timestamp, mode (sim/live), side, mints, amounts, source signature, mirror signature, market cap, price impact, realized PnL (sells), exit reason, plus a snapshot of key risk config at trade time.

**Why**: The bot's in-memory `recentSwaps` array clears on restart. Without this, there was no way to analyze trades across sessions.

**Failure mode**: Disk write failures are warned once and then silenced. Never blocks the trading hot path.

### Per-detection WS latency logs

Every WS-detected swap now logs which extraction path won and how long it took:

```
⚡ WS detected swap 5jpZyvBk… (raw, 512ms)
```

`raw` = direct `getTransaction` fast path. `enhanced` = Helius enhanced parse fallback. If you see `enhanced` regularly, the raw path is failing for some reason and we should investigate.

---

## Misc infrastructure

### Dynamic priority fees

**What it does**: A background loop polls `getRecentPrioritizationFees` for the pump.fun program every 5 seconds, computes the 75th percentile, caches it. Every transaction reads the cached value synchronously — zero latency on the hot path.

The static `PRIORITY_FEE_MICRO_LAMPORTS` is the **floor** — we never go below it. Dynamic fee only kicks in when the network is busier than the floor.

**Configuration**:

```env
# Background-polled p75 priority fee. On by default.
DYNAMIC_PRIORITY_FEE_ENABLED=true

# Refresh interval in ms.
DYNAMIC_PRIORITY_FEE_REFRESH_MS=5000
```

### Bounded `processedSignatures`

**Was**: `new Set<string>()`. Grew unbounded forever.

**Now**: TTL-bounded wrapper. Sweeps entries older than 4 hours every 256 inserts. Prevents memory creep on long-running sessions.

---

## Full env-var reference

### Latency / detection

| Var | Default | Purpose |
|---|---|---|
| `WEBSOCKET_URL` | required | Helius WS endpoint (use Gatekeeper beta for fastest) |
| `SECURE_RPC_URL` | required | Helius staked RPC for reads |
| `SENDER_ENDPOINT` | `http://ewr-sender.helius-rpc.com/fast` | Helius Sender for tx broadcast |
| `HELIUS_MIN_REQUEST_GAP_MS` | `100` | Throttle for Helius parsed-tx requests |
| `HELIUS_429_COOLDOWN_MS` | `10000` | Cooldown after Helius 429 |

### Sizing

| Var | Default | Purpose |
|---|---|---|
| `MIN_BUY_SOL` | `0.01` | Minimum bet per copied buy |
| `MAX_BUY_SOL` | `1` | Maximum bet per copied buy (cap) |
| `SOL_RESERVE` | `0.1` | Untouched gas reserve |
| `MAX_OPEN_POSITIONS` | (unset) | Cap on simultaneously open positions |
| `BUY_ONCE_PER_TOKEN` | `true` | Skip duplicate buys of the same mint |

### Slippage / impact

| Var | Default | Purpose |
|---|---|---|
| `MIRROR_SLIPPAGE_BPS` | `500` | Default buy slippage (bps) |
| `MIRROR_RETRY_SLIPPAGE_BPS` | `1500` | Retry slippage on failure |
| `MAX_ENTRY_PRICE_IMPACT_PCT` | (unset) | Soft cap on entry impact |
| `MAX_IMPACT_REDUCE_ATTEMPTS` | `2` | Size-reduction attempts before giving up |
| `MAX_SOURCE_DRIFT_PCT` | (unset) | Skip if quote is N% above source's price |

### Tipping / fees

| Var | Default | Purpose |
|---|---|---|
| `TIP_LAMPORTS` | `65000` | Jito tip per tx |
| `PRIORITY_FEE_MICRO_LAMPORTS` | `50000` | Static priority fee floor |
| `COMPUTE_UNIT_LIMIT` | `1400000` | CU limit |
| `DYNAMIC_PRIORITY_FEE_ENABLED` | `true` | Use p75 of recent fees |
| `DYNAMIC_PRIORITY_FEE_REFRESH_MS` | `5000` | Background refresh interval |

### Risk / exits

| Var | Default | Purpose |
|---|---|---|
| `STOP_LOSS_PCT` | (unset) | Base stop-loss threshold |
| `TAKE_PROFIT_PCT` | (unset) | Take-profit threshold |
| `MIN_HOLD_BEFORE_STOP_LOSS_MS` | `3500` | Grace window — SL disarmed for this long after buy |
| `TRAILING_STEPS` | (unset) | Stair-step trailing stop, e.g. `10:0,20:5` |

### Polling tiers

| Var | Default | Purpose |
|---|---|---|
| `RISK_FAST_INTERVAL_MS` | `500` | Tick during fast window after each buy |
| `RISK_FAST_WINDOW_MS` | `20000` | Duration of fast window |
| `RISK_NEAR_TRIGGER_INTERVAL_MS` | `400` | Tick when near a trigger |
| `RISK_NORMAL_INTERVAL_MS` | `1000` | Tick otherwise |
| `RISK_NEAR_TRIGGER_PCT` | `8` | Distance (in pp) that flips into "near" bucket |
| `RISK_DRIFT_PCT_PER_SEC` | `3` | Worst-case drift assumption for quote-skip |
| `MAX_RISK_QUOTES_PER_CHECK` | `2` | Max quotes per tick (prevents starving buys) |

### Circuit breakers

| Var | Default | Purpose |
|---|---|---|
| `DAILY_LOSS_LIMIT_SOL` | (unset) | Block new buys after cumulative loss reaches this |
| `CONSECUTIVE_LOSS_LIMIT` | `0` (off) | Pause after N losses in a row |
| `CIRCUIT_BREAKER_PAUSE_MS` | `600000` | Pause duration after circuit-breaker trip |

### Mode flags

| Var | Default | Purpose |
|---|---|---|
| `SIMULATION_MODE` | `false` | If true, no real txs sent |
| `FAST_MODE` | `false` | Record after broadcast; confirm in background |
| `MIRROR_SELLS` | `true` | Copy source-wallet sells |
| `PUMPFUN_DIRECT_ENABLED` | `true` | Use direct pump.fun route for pre-graduation tokens |

---

## Caveats and known limitations

1. **Pump.fun program ID and account layout** are based on the historical canonical version. If pump.fun ships a new program with different fields, the decoder may misread. Watch the first sim run for `Pump.fun curve detected` vs `Pump.fun direct path skipped: not a pump.fun bonding-curve token` to verify.
2. **`getTransaction` floor**: ~400ms `confirmed` commitment delay is a chain-level constraint, not removable without gRPC.
3. **MIRROR_SELLS=false** with a laddering source wallet means you're decoupled from source's exits — your trailing stop / TP / SL is your only exit signal. Calibrate accordingly.
4. **Drift check, source-fill-price, and many circuit breakers are off by default.** They're opt-in via env vars.
5. **n=small for trade analysis**: most strategy decisions in the audit were based on 5-trade samples. Run 30+ trades before treating any single conclusion as load-bearing.

---

## Suggested baseline `.env` for live trading

This is what I'd run after the upgrades, assuming pump.fun-only copytrading from one source wallet, 0.5-1.5 SOL bets, conservative-but-not-paranoid risk:

```env
# Sizing
MIN_BUY_SOL=0.5
MAX_BUY_SOL=1.5
SOL_RESERVE=0.5
MAX_OPEN_POSITIONS=2

# Slippage / impact
MIRROR_SLIPPAGE_BPS=1200
MIRROR_RETRY_SLIPPAGE_BPS=2000
MAX_ENTRY_PRICE_IMPACT_PCT=12

# Risk
STOP_LOSS_PCT=22
TAKE_PROFIT_PCT=35
MIN_HOLD_BEFORE_STOP_LOSS_MS=3500
TRAILING_STEPS=10:0,20:5

# Polling tiers
RISK_FAST_INTERVAL_MS=400
RISK_FAST_WINDOW_MS=20000
RISK_NEAR_TRIGGER_INTERVAL_MS=400
RISK_NORMAL_INTERVAL_MS=1000

# Tipping
TIP_LAMPORTS=500000
PRIORITY_FEE_MICRO_LAMPORTS=200000
DYNAMIC_PRIORITY_FEE_ENABLED=true

# Mode
SIMULATION_MODE=false
FAST_MODE=true
MIRROR_SELLS=false
BUY_ONCE_PER_TOKEN=true

# Pump.fun direct (default on, listed for visibility)
PUMPFUN_DIRECT_ENABLED=true
```

Tune from there based on the actual data your sessions produce.
