# Jito Latency Upgrade Guide

This branch adds three latency upgrades:

1. Jito direct `sendTransaction` support.
2. Jito tip-floor oracle.
3. Slot-aware execution telemetry.

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

## Speed mode preset

Apply the speed preset on the VPS:

```bash
cd /root/bot/FinalBot
git pull origin feature/latency-roadmap
npm install
npm run build
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
