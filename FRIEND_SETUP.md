# FinalBot Friend Setup Guide

This guide takes a fresh Windows laptop from zero to running FinalBot with your own Helius key and wallet.

## 1. Install prerequisites

1. Install Git for Windows: https://git-scm.com/download/win
2. Install Node.js LTS: https://nodejs.org/
3. Open PowerShell and confirm both work:

```powershell
git --version
node --version
npm --version
```

## 2. Clone the bot

Pick a folder, then clone the repository:

```powershell
cd C:\Trading
git clone https://github.com/frankykas/TradingBot.git FinalBot
cd C:\Trading\FinalBot
```

If the folder does not exist yet, create it first:

```powershell
mkdir C:\Trading
```

## 3. Install dependencies

```powershell
npm install
```

## 4. Create the local `.env`

Copy the example file:

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill these values:

```env
HELIUS_API_KEY=your_helius_api_key
SECURE_RPC_URL=your_helius_secure_rpc_url
WEBSOCKET_URL=wss://mainnet.helius-rpc.com/?api-key=your_helius_api_key
PRIVATE_KEY_BASE58=your_wallet_private_key
```

Keep `.env` private. It is ignored by Git and must never be pushed.

## 5. Helius setup

1. Log in to Helius.
2. Create or open a mainnet project.
3. Copy the API key into `HELIUS_API_KEY`.
4. Copy the Secure RPC URL into `SECURE_RPC_URL`.
5. Use the WebSocket URL format from `.env.example`, replacing the API key.
6. Keep the sender endpoint as:

```env
SENDER_ENDPOINT=http://ewr-sender.helius-rpc.com/fast
```

## 6. Wallet setup

Use a dedicated trading wallet. Fund it with SOL only after simulation works.

For safety, start with:

```env
SIMULATION_MODE=true
DRY_RUN=false
```

Only switch to live mode when ready:

```env
SIMULATION_MODE=false
DRY_RUN=false
```

## 7. Current strategy settings

The shared template uses the current scalp profile:

```env
STOP_LOSS_PCT=14
TAKE_PROFIT_PCT=15
TRAILING_STEPS=10:-8,13:3
MIN_HOLD_BEFORE_STOP_LOSS_MS=3000
```

This aims to take 15% style scalps and cut fast fades earlier.

## 8. Build and run

Check TypeScript:

```powershell
npm run build
```

Run the bot in simulation:

```powershell
npm run bot
```

Optional web UI:

```powershell
npm run web
```

## 9. Pull future updates

Before pulling, stop the bot. Then:

```powershell
git pull origin main
npm install
npm run build
```

If Git complains about `.env`, do not add it. `.env` should stay local only.

## 10. Common fixes

If dependencies are missing:

```powershell
npm install
```

If TypeScript build fails after an update:

```powershell
npm install
npm run build
```

If Helius or Jupiter rate limits happen, wait a few minutes, then restart the bot.

If no trades are appearing, check:

```env
TRACKED_WALLETS=
WEBSOCKET_URL=
HELIUS_API_KEY=
SIMULATION_MODE=true
```
