# FinalBot Copytrading Bot

Solana copytrading bot that watches tracked wallets, detects SOL/token swaps, and mirrors buys or exits with your wallet. The live bot entrypoint is `src/copytrade.ts`.

## Run

```bash
npm run bot
```

For a beginner-friendly daily use guide after install, read `BEGINNER_BOT_GUIDE.md`.

These commands all run the same live source entrypoint:

```bash
npm run bot
npm run dev
npm start
```

`dist/` is build output only. Normal bot runs should not depend on it.

## Required `.env`

| Variable | Description |
| --- | --- |
| `HELIUS_API_KEY` | Helius API key for RPC, websocket, and transaction parsing. |
| `PRIVATE_KEY_BASE58` | Wallet private key used for mirrored trades. |
| `TRACKED_WALLETS` | Comma-separated wallets to copy. |

Common live/simulation controls:

| Variable | Description |
| --- | --- |
| `SIMULATION_MODE` | `true` for dry runs, `false` for live trading. |
| `MIN_BUY_SOL` | Minimum mirrored buy size. |
| `MAX_BUY_SOL` | Maximum mirrored buy size. |
| `SOL_RESERVE` | SOL kept aside for fees. |
| `MIRROR_SELLS` | Whether to mirror source-wallet sells. |
| `BUY_ONCE_PER_TOKEN` | Whether to skip duplicate buys of the same token per session. |

Risk exits:

| Variable | Description |
| --- | --- |
| `STOP_LOSS_PCT` | Base stop-loss percent below remaining cost basis. |
| `TAKE_PROFIT_PCT` | Take-profit percent above remaining cost basis. |
| `TRAILING_STEPS` | Stair-step trailing stop, e.g. `10:-8,13:3`. |
| `RISK_FAST_INTERVAL_MS` | Quote cadence during the first fast window after a buy. |
| `RISK_FAST_WINDOW_MS` | Duration of the fast risk window. |
| `RISK_NEAR_TRIGGER_INTERVAL_MS` | Quote cadence when near TP/SL. |
| `RISK_NORMAL_INTERVAL_MS` | Normal quote cadence. |
| `MAX_RISK_QUOTES_PER_CHECK` | Max open positions quoted per risk tick. |
| `MIN_HOLD_BEFORE_STOP_LOSS_MS` | Stop-loss grace period after entry. |

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run bot` | Run the copytrading bot from source. |
| `npm run dev` | Same as `npm run bot`. |
| `npm start` | Same as `npm run bot`. |
| `npm run build` | Type-check and compile to `dist/`. |
| `npm run web` | Run the optional web dashboard. |
| `npm run swap` | Run the one-off manual swap helper. |

## Cleanup Notes

The bot needs `.env`, `package.json`, `package-lock.json`, `tsconfig.json`, `src/`, and `node_modules/`.

Generated or disposable items:

| Path | Safe to remove? | Notes |
| --- | --- | --- |
| `dist/` | Yes | Recreated by `npm run build`. |
| `.cache/` | Usually | Old investigation/cache data, not used by normal bot runs. |
| `web-server-*.log` | Yes | Old local server logs. |
| `.restore-backups/` | Maybe | Keep only if you still want old backup files. |
