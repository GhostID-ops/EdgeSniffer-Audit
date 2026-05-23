# FinalBot Windsurf Setup Guide

This guide sets up FinalBot from scratch on Windows using the Windsurf terminal. Each friend needs their own wallet private key, Helius API key, RPC URL, and license key.

## 1. Open Windsurf Terminal

1. Install Windsurf from https://windsurf.com/ if it is not installed yet.
2. Open Windsurf.
3. Click `Terminal`.
4. Click `New Terminal`.

All commands below go into that Windsurf terminal.

## 2. Install Git, Node, and npm from Terminal

Node.js includes npm. On Windows, install both Git and Node LTS with `winget`:

```powershell
winget install --id Git.Git -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
```

Close Windsurf completely, reopen it, then open a new terminal and verify:

```powershell
git --version
node --version
npm --version
```

If `winget` asks you to accept source agreements, type `Y` and press Enter.

If `npm` is not recognized after installation, fully close Windsurf and open it again. If it still fails, restart Windows.

## 3. Clone FinalBot

Create a clean folder and clone the bot:

```powershell
mkdir C:\Trading
cd C:\Trading
git clone --branch restore-version https://github.com/frankykas/TradingBot.git FinalBot
cd C:\Trading\FinalBot
```

Open the cloned folder in Windsurf:

1. Click `File`.
2. Click `Open Folder`.
3. Select `C:\Trading\FinalBot`.
4. Open a new Windsurf terminal.

If the terminal is not already in the project folder, run:

```powershell
cd C:\Trading\FinalBot
```

## 4. Install Project Dependencies

```powershell
npm install
```

This creates `node_modules` on your computer. Do not commit or share that folder.

## 5. Create Your `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` in Windsurf and fill in your private values:

```env
HELIUS_API_KEY=your_helius_api_key
SECURE_RPC_URL=your_helius_secure_rpc_url
WEBSOCKET_URL=wss://mainnet.helius-rpc.com/?api-key=your_helius_api_key
PRIVATE_KEY_BASE58=your_wallet_private_key
LICENSE_KEY=your_license_key
```

Keep `.env` private. Never send it to anyone and never commit it to GitHub.

## 6. Helius Setup

1. Log in to Helius.
2. Create or open a mainnet project.
3. Copy your API key into `HELIUS_API_KEY`.
4. Copy your Secure RPC URL into `SECURE_RPC_URL`.
5. Set `WEBSOCKET_URL` like this:

```env
WEBSOCKET_URL=wss://mainnet.helius-rpc.com/?api-key=YOUR_HELIUS_API_KEY
```

Keep this sender endpoint unless told otherwise:

```env
SENDER_ENDPOINT=http://ewr-sender.helius-rpc.com/fast
```

## 7. Wallet Setup

Use a dedicated trading wallet. Do not use your main wallet.

Start in simulation:

```env
SIMULATION_MODE=true
DRY_RUN=false
```

Only switch live after the bot runs correctly:

```env
SIMULATION_MODE=false
DRY_RUN=false
```

Before live trading, fund the wallet with SOL and keep extra SOL for fees.

## 8. Build the Bot

```powershell
npm run build
```

If the build finishes without errors, the code compiles.

## 9. Run the Bot

Run the main bot:

```powershell
npm run bot
```

Optional web UI:

```powershell
npm run web
```

Stop any running command with `Ctrl+C`.

## 10. Pull Future Updates

Stop the bot first, then run:

```powershell
git pull origin restore-version
npm install
npm run build
```

## 11. Common Fixes

If `npm` is not recognized:

```powershell
node --version
npm --version
```

Then close and reopen Windsurf, or restart Windows.

If dependencies are missing:

```powershell
npm install
```

If the build fails after an update:

```powershell
npm install
npm run build
```

If no trades appear, check these `.env` values:

```env
TRACKED_WALLETS=
HELIUS_API_KEY=
SECURE_RPC_URL=
WEBSOCKET_URL=
SIMULATION_MODE=true
```

Do not commit `.env`, `.license-cache.json`, `node_modules`, `dist`, logs, or `data/*.jsonl`.
