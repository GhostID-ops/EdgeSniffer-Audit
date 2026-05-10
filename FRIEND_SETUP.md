# FinalBot Windsurf Setup Guide

This guide is for setting up FinalBot on a Windows laptop using Windsurf. Your friend will use the same Helius setup style, but with his own Helius API key, RPC URL, and wallet private key.

## 1. Install the required apps

Install these first:

1. Windsurf: https://windsurf.com/
2. Git for Windows: https://git-scm.com/download/win
3. Node.js LTS: https://nodejs.org/

Node.js includes `npm`, so there is no separate npm installer. After installing Node, close and reopen Windsurf so its terminal can see the new `node` and `npm` commands.

## 2. Open a Windsurf terminal

1. Open Windsurf.
2. Click `Terminal` in the top menu.
3. Click `New Terminal`.
4. The terminal should open at the bottom of Windsurf.

Check that Git, Node, and npm are installed:

```powershell
git --version
node --version
npm --version
```

If `npm` is not recognized, restart Windsurf. If it still fails, reinstall Node.js LTS and make sure `Add to PATH` is enabled.

## 3. Clone the bot from GitHub

In the Windsurf terminal, run:

```powershell
mkdir C:\Trading
cd C:\Trading
git clone https://github.com/frankykas/TradingBot.git FinalBot
cd C:\Trading\FinalBot
```

Then open the project folder in Windsurf:

1. Click `File`.
2. Click `Open Folder`.
3. Select `C:\Trading\FinalBot`.

After opening the folder, open a new Windsurf terminal again. It should start in `C:\Trading\FinalBot`. If not, run:

```powershell
cd C:\Trading\FinalBot
```

## 4. Install npm dependencies

In the Windsurf terminal:

```powershell
npm install
```

This creates `node_modules` locally. It may take a minute. `node_modules` is ignored by Git and should not be committed.

## 5. Create the `.env` file

In the Windsurf terminal:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` inside Windsurf:

1. In the left file explorer, click `.env`.
2. Fill in the required values.
3. Save the file with `Ctrl+S`.

Required values:

```env
HELIUS_API_KEY=your_helius_api_key
SECURE_RPC_URL=your_helius_secure_rpc_url
WEBSOCKET_URL=wss://mainnet.helius-rpc.com/?api-key=your_helius_api_key
PRIVATE_KEY_BASE58=your_wallet_private_key
```

Keep `.env` private. It is ignored by Git and must never be pushed or shared.

## 6. Helius setup

1. Log in to Helius.
2. Create or open a mainnet project.
3. Copy the API key into `HELIUS_API_KEY`.
4. Copy the Secure RPC URL into `SECURE_RPC_URL`.
5. Set `WEBSOCKET_URL` like this:

```env
WEBSOCKET_URL=wss://mainnet.helius-rpc.com/?api-key=YOUR_HELIUS_API_KEY
```

Keep this sender endpoint:

```env
SENDER_ENDPOINT=http://ewr-sender.helius-rpc.com/fast
```

## 7. Wallet setup

Use a dedicated trading wallet. Do not use a main wallet.

Start in simulation:

```env
SIMULATION_MODE=true
DRY_RUN=false
```

Only switch live after simulation works:

```env
SIMULATION_MODE=false
DRY_RUN=false
```

Before going live, fund the trading wallet with SOL and keep enough reserve for fees.

## 8. Current strategy settings

The shared template uses this current scalp profile:

```env
STOP_LOSS_PCT=14
TAKE_PROFIT_PCT=15
TRAILING_STEPS=10:-8,13:3
MIN_HOLD_BEFORE_STOP_LOSS_MS=3000
```

This aims to take 15% style scalps and cut fast fades earlier.

## 9. Build the bot

In the Windsurf terminal:

```powershell
npm run build
```

If this finishes without errors, the code compiles.

## 10. Run the bot

Run in simulation mode first:

```powershell
npm run bot
```

Optional web UI:

```powershell
npm run web
```

Stop the bot with `Ctrl+C` in the Windsurf terminal.

## 11. Pull future updates

Stop the bot first, then run this in the Windsurf terminal:

```powershell
git pull origin main
npm install
npm run build
```

Do not commit `.env`, `node_modules`, `dist`, logs, or `data/trades.jsonl`.

## 12. Common fixes

If `npm` is not recognized:

1. Install Node.js LTS.
2. Restart Windsurf.
3. Open a new Windsurf terminal.
4. Run `npm --version`.

If dependencies are missing:

```powershell
npm install
```

If the build fails after pulling updates:

```powershell
npm install
npm run build
```

If Git says local files would be overwritten, check that `.env` and `data/trades.jsonl` were not accidentally added:

```powershell
git status
```

If no trades appear, check these `.env` values:

```env
TRACKED_WALLETS=
WEBSOCKET_URL=
HELIUS_API_KEY=
SIMULATION_MODE=true
```
