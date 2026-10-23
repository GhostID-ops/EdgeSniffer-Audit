# Jito Latency Upgrade Guide

This branch adds six latency upgrades:

1. Jito direct `sendTransaction` support.
2. Jito tip-floor oracle.
3. Multi-send sender racing.
4. Rust Pump.fun buy executor.
5. Rust Pump.fun hot sniper.
6. Slot-aware execution telemetry.

## What changed

### Jito send mode

`JITO_SEND_MODE` controls which Jito direct path the bot uses when `USE_JITO_DIRECT=true`.

```env
USE_JITO_DIRECT=true
USE_JITO_FALLBACK=true
JITO_SEND_MODE=race
```

Modes:

- `bundle`: current behavior; sends a 1-transaction bundle to Jito `/api/v1/bundles`.
- `transaction`: sends directly to Jito `/api/v1/transactions`.
- `race`: sends both Jito transaction and Jito bundle paths, and uses the first accepted result.

For max speed, use:

```env
JITO_SEND_MODE=race
BROADCAST_RACE_ENABLED=true
EXECUTION_MULTI_SEND_ENABLED=true
SENDER_RPC_FALLBACK_ENABLED=true
```

This can race Jito transaction, Jito bundle, Helius Sender, and RPC fallback. Use it only on the wallets where speed matters, because too many simultaneous instances can hit sender/rate limits.

## Jito connection warmup

Enable periodic lightweight Jito warmup calls so quiet wallets do not pay a cold connection penalty when they wake up:

```env
JITO_CONNECTION_WARMUP_ENABLED=true
JITO_CONNECTION_WARMUP_MS=15000
```

## Jito tip oracle

The bot can now poll Jito's public tip-floor endpoint in the background:

```env
JITO_TIP_ORACLE_ENABLED=true
JITO_TIP_ORACLE_PERCENTILE=p75
JITO_TIP_ORACLE_REFRESH_MS=5000
JITO_TIP_FLOOR_URL=https://bundles.jito.wtf/api/v1/bundles/tip_floor
```

The hot path never waits for this API. It uses the last cached tip value.

Useful optional caps:

```env
JITO_TIP_ORACLE_MIN_LAMPORTS=200000
JITO_TIP_ORACLE_MAX_LAMPORTS=2000000
```

If the oracle fails, the bot keeps using your static `TIP_LAMPORTS`.

## Rust Pump.fun buy executor

The Rust sidecar can now execute Pump.fun buys after TypeScript has made the strategy decision:

```env
RUST_EXECUTOR_ENABLED=true
RUST_EXECUTOR_TIMEOUT_MS=1500
```

Current Rust executor scope:

- Pump.fun direct buys.
- Pump.fun source-replay buys.
- Rust build/sign/send using Jito transaction, Jito bundle, Helius Sender, and RPC fallback settings.
- TypeScript still owns strategy gates, PnL, risk exits, sells, Telegram, and UI.

If Rust execution fails or times out, the bot falls back to the existing TypeScript execution path.

## Rust Pump.fun hot sniper

The Rust sidecar can also execute the earliest eligible Pump.fun buy before the normal TypeScript mirror path runs:

```env
RUST_HOT_SNIPER_ENABLED=true
```

Scope:

- Pump.fun source-replay buys only.
- Requires source instruction accounts/data from ShredStream/Yellowstone.
- Uses the same env sizing basics: `COPY_BUY_PERCENTAGE`, `MIN_BUY_SOL`, `MAX_BUY_SOL`, `MIN_BUY_BEHAVIOR`, `BUY_ONCE_PER_TOKEN`, `MAX_SOL_PER_TOKEN`, and `MAX_SESSION_DEPLOYED_SOL`.
- Uses the same sender settings: `USE_JITO_DIRECT`, `JITO_SEND_MODE`, `EXECUTION_MULTI_SEND_ENABLED`, `USE_JITO_FALLBACK`, and `SENDER_RPC_FALLBACK_ENABLED`.
- Disabled automatically when `SIMULATION_MODE=true` or `SHADOW_EXECUTION_MODE=true`.

Optional hot-path fee overrides:

```env
RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS=
RUST_HOT_SNIPER_JITO_TIP_LAMPORTS=
RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS=
```

Leave those blank to reuse normal env fee/tip values. After a hot buy broadcasts, Node records the position, sends Telegram/UI updates, confirms in the background, and cleans up the position if the transaction fails on chain.

Expected startup/log lines:

```text
Rust sidecar config: ... Rust executor=ON, Rust hot sniper=ON
rust-sidecar: hot sniper ON for Pump.fun source-replay buys
Rust hot sniper buy ABCD1234... 5sigHere... (18ms)
Rust hot sniper recorded buy for ABCD1234...
```

## Speed mode preset

Apply the speed preset on the VPS:

```bash
cd /root/bot/FinalBot
git pull origin feature/latency-roadmap
npm install
npm run build
npm run rust-sidecar:jito-build
npm run env:speed-mode
```

Then restart however you run the bot:

```bash
npm run bot
```

or, if using service mode:

```bash
systemctl restart finalbot
```

The speed preset does **not** change private keys, tracked wallets, buy sizing, TP/SL, trailing steps, or mirror-sell strategy.

## Local Pump.fun market-cap filter

For fast Pump.fun instances, prefer the local curve filter over Moralis/DexScreener prebuy checks. The bot already reads the bonding curve before a direct buy, so it can estimate current market cap from virtual reserves without an extra HTTP lookup.

```env
PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=150
PREBUY_MIN_MARKET_CAP_USD=15000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false
```

If you do not want to maintain a SOL/USD value, use SOL-denominated thresholds:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_MIN_MARKET_CAP_SOL=100
LOCAL_PUMPFUN_MAX_MARKET_CAP_SOL=
```

The log will say `local Pump.fun MC passed` when a token clears the filter, or `local Pump.fun MC ... < ...` / `> ...` when it is blocked. This filter only applies to Pump.fun bonding-curve direct buys; non-Pump routes still need their own route-specific filters.

Use `PREBUY_FILTER_FAIL_OPEN=false` when the filter is meant to protect real buys. If it is `true`, the bot is allowed to buy when the curve/MC cannot be checked fast enough, including source-replay entries.

## VPS control UI

The VPS control panel now has a `Speed Mode` button for each instance. It applies the same low-latency env values as `.env.speed-mode`.

It does not restart automatically. Use `Save + Restart`, `Restart`, or manually restart after applying it.

## Telemetry

Run:

```bash
cd /root/bot/FinalBot
npm run telemetry:execution
```

Look for:

```text
sourceSlot
txIdx
landingSlot
delta
```

The key metric is:

```text
source slot -> landing slot
```

Best case is `0 slot(s)` or `1 slot(s)`. If `process -> broadcast` is low but slot delta is high, the bottleneck is landing/order competition, not bot parsing speed.

## Sources

- Jito low-latency transaction send: https://docs.jito.wtf/lowlatencytxnsend/
- Jito ShredStream: https://docs.jito.wtf/lowlatencytxnfeed/
- Solana priority fees: https://solana.com/docs/core/fees
