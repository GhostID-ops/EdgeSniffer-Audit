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

## Balance

Your SOL balance is printed at startup and again every 60 seconds while the bot runs.

## Simulation mode

Set `SIMULATION_MODE=true` in `.env` to paper trade without sending transactions. The bot **still tracks every tracked wallet's transactions** (same polling and swap detection as live), detects Jupiter swaps, gets quotes, builds the mirror tx (quote + swap + Sender tx), records simulated positions/PnL, but skips broadcast and confirmation. Use this to test strategy behavior before going live.

In simulation mode you can set a **starting SOL balance** for display: set `SIMULATION_SOL_BALANCE=10` or `STARTING_SOL_BALANCE=10` (e.g. 10 SOL). The bot will show that amount as your balance instead of fetching from chain, so you can test as if you had more or less SOL.

## Dry run mode

Set `DRY_RUN=true` in `.env` for a lighter, safer quote-only run. Dry run still watches wallets and detects swaps, but it stops after the Jupiter quote: it does **not** request a swap transaction, build/sign a Sender transaction, broadcast, confirm, or mutate paper positions/PnL. If both `DRY_RUN=true` and `SIMULATION_MODE=true` are set, dry run takes precedence.

## Scripts

- **`npm run bot`** / **`npm run dev`** – Run the copytrading bot (tsx).
- **`npm run start`** – Run the bot (node, after `npm run build`).
- **`npm run swap`** – One-off manual Jupiter swap (for testing).
