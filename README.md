# FinalBot – Copytrading Bot

Copytrading bot for Solana: watches tracked wallets for Jupiter swaps and mirrors them with your wallet. Uses **Helius Sender** for submission and **Jupiter** for quotes/swaps.

## How it works

1. **Watch** – Polls Helius for recent transactions of each tracked wallet and parses them (Enhanced Transactions API).
2. **Detect** – Identifies Jupiter swaps (SOL→token, token→token, token→SOL).
3. **Mirror** – Clamps size to your min/max buy (SOL terms), gets a Jupiter quote, builds a Sender-ready tx (tip + priority fee), broadcasts via Helius Sender, confirms on the send path.

## Run the bot

```bash
# Copy .env.example to .env and set your keys + TRACKED_WALLETS
npm run bot
# or after build:
npm run start
```

## Required env (in `.env`)

| Variable | Description |
|----------|-------------|
| `HELIUS_API_KEY` | Helius API key (read RPC + parse transactions) |
| `PRIVATE_KEY_BASE58` | Your wallet private key (base58, e.g. from Phantom) |
| `TRACKED_WALLETS` | Comma-separated Solana addresses to copy |

Optional: `SENDER_ENDPOINT`, `TIP_LAMPORTS`, `PRIORITY_FEE_MICRO_LAMPORTS`, `MIN_BUY_SOL`, `MAX_BUY_SOL`.

`MIN_BUY_SOL` is your minimum position size per copied token. If the source wallet buys less than this, the bot raises the mirrored buy to `MIN_BUY_SOL`; it does not skip the trade. `MAX_BUY_SOL` still caps oversized buys.

## Pump.fun Mayhem Filter

Set `BLOCK_PUMPFUN_MAYHEM=true` to skip pump.fun tokens detected as Mayhem Mode before buying. The bot checks pump.fun metadata/page data and falls back to mint supply/Token-2022 heuristics. Set `PUMPFUN_MAYHEM_STRICT=true` if you prefer skipping pump.fun buys when Mayhem status cannot be verified.

## Risk exits

Stop loss and take profit are optional. Leave them empty or `0` to disable.

| Variable | Description |
|----------|-------------|
| `STOP_LOSS_PCT` | Auto-sell an open position when its quoted exit is this percent below remaining cost basis, e.g. `25` for -25%. |
| `TAKE_PROFIT_PCT` | Auto-sell an open position when its quoted exit is this percent above remaining cost basis, e.g. `100` for +100%. |
| `RISK_CHECK_INTERVAL_MS` | How often open positions are quoted for risk exits. Default `5000`. |

## Balance

Your SOL balance is printed at startup and again every 60 seconds while the bot runs.

## Simulation mode (dry run)

Set `SIMULATION_MODE=true` or `DRY_RUN=true` in `.env` to run without sending any transactions. The bot **still tracks every tracked wallet's transactions** (same polling and swap detection as live), detects Jupiter swaps, and builds the mirror tx (quote + swap + Sender tx) but skips broadcast and confirmation. Use this to test before going live.

In simulation mode you can set a **starting SOL balance** for display: set `SIMULATION_SOL_BALANCE=10` or `STARTING_SOL_BALANCE=10` (e.g. 10 SOL). The bot will show that amount as your balance instead of fetching from chain, so you can test as if you had more or less SOL.

## Scripts

- **`npm run bot`** / **`npm run dev`** – Run the copytrading bot (tsx).
- **`npm run start`** – Run the bot (node, after `npm run build`).
- **`npm run swap`** – One-off manual Jupiter swap (for testing).
