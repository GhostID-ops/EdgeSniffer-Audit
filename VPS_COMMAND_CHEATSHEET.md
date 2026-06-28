# VPS Command Cheatsheet

## SSH Into My VPS

IPv4:

```bash
ssh root@87.99.145.246
```

IPv6:

```bash
ssh root@2a01:4ff:f4:1f2a::1
```

If SSH says the host key changed:

```bash
ssh-keygen -R 87.99.145.246
ssh root@87.99.145.246
```

## Main Bot Folder

```bash 
cd /root/bot/FinalBot-cya
```

Edit main env:

```bash
nano /root/bot/FinalBot/.env
```

## 4BdK Bot Folder

```bash
cd /root/bot/FinalBot-cya
```

Edit 4BdK env:

```bash
nano /root/bot/FinalBot-4bdk/.env
```

## 5qn Bot Folder

```bash
cd /root/bot/FinalBot-cya
```

Edit 5qn env:

```bash
nano /root/bot/FinalBot-5qn/.env
```

## Start Services

Main bot:

```bash
systemctl start finalbot
```

4BdK bot:

```bash
systemctl start finalbot-4bdk
```

5qn bot:

```bash
systemctl start finalbot-5qn
```

Start 4BdK and 5qn together:

```bash
systemctl start finalbot-4bdk finalbot-5qn
```

## Stop/Pause Services

Main bot:

```bash
systemctl stop finalbot
```

4BdK bot:

```bash
systemctl stop finalbot-4bdk
```

5qn bot:

```bash
systemctl stop finalbot-5qn
```

Stop 4BdK and 5qn together:

```bash
systemctl stop finalbot-4bdk finalbot-5qn
```

## Restart Services After Env Changes

Main bot:

```bash
systemctl restart finalbot
```

4BdK bot:

```bash
systemctl restart finalbot-4bdk
```

5qn bot:

```bash
systemctl restart finalbot-cya
```

Restart 4BdK and 5qn together:

```bash
systemctl restart finalbot-4bdk finalbot-5qn
```

## Check Service Status

Main bot:

```bash
systemctl status finalbot
```

4BdK bot:

```bash
systemctl status finalbot-4bdk
```

5qn bot:

```bash
systemctl status finalbot-5qn
```

## Watch Logs

Main bot:

```bash
journalctl -u finalbot -f
```

4BdK bot:

```bash
journalctl -u finalbot-cya -f
```

5qn bot:

```bash
journalctl -u finalbot-5qn -f
```

Recent logs only:

```bash
journalctl -u finalbot-4bdk -n 100 --no-pager
journalctl -u finalbot-5qn -n 100 --no-pager
```

## 4BdK Same-Slot Tiny Live Test

This applies a 4BdK-only same-slot validation preset to `/root/bot/FinalBot-4bdk`.

It sends real tiny live buys:

- wallet: `4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk`
- buy size: `0.04 SOL`
- session cap: `0.16 SOL`
- exits: proportional mirror sells from 4BdK
- hot path: Shyft gRPC/RabbitStream, ShredStream, Jito race, Rust hot sniper primary

Install/update code and apply the preset:

```bash
cd /root/bot/FinalBot
git pull origin main
npm install
npm run build
npm run rust-sidecar:jito-build

cd /root/bot/FinalBot-4bdk
git pull origin main
npm install
npm run build
npm run rust-sidecar:jito-build
npm run env:4bdk-same-slot
systemctl restart finalbot-4bdk
```

Watch the test:

```bash
journalctl -u finalbot-4bdk -f
```

After one or more copied buys, scan same-slot quality:

```bash
cd /root/bot/FinalBot-4bdk
npm run telemetry:scan-today
```

Stop the test:

```bash
systemctl stop finalbot-4bdk
```

## 4BdK Max Same-Slot Performance

This applies an aggressive real-trade 4BdK profile with high Jito tips, high priority fees, Rust hot sniper primary, Shyft gRPC/RabbitStream, ShredStream, and multi-region Jito race.

Default exposure stays small:

- buy size: `0.04 SOL`
- session cap: `0.16 SOL`
- default Jito tip: `0.01 SOL`
- default priority fee: `20,000,000` micro-lamports/CU

Apply from the VPS:

```bash
cd /root/bot/FinalBot
git pull origin main
bash scripts/apply4bdkMaxSameSlotPerformance.sh
```

Go harder for a short window:

```bash
cd /root/bot/FinalBot
JITO_TIP_LAMPORTS=25000000 \
RUST_HOT_SNIPER_JITO_TIP_LAMPORTS=25000000 \
PRIORITY_FEE_MICRO_LAMPORTS=30000000 \
RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS=30000000 \
bash scripts/apply4bdkMaxSameSlotPerformance.sh
```

Watch and score:

```bash
journalctl -u finalbot-4bdk -f

cd /root/bot/FinalBot-4bdk
npm run telemetry:scan-today
```

## CyA Shadow TP Simulations

These run three CyA-only shadow services side by side:

- `finalbot-cya-shadow-tp30`: MC >= `$50k`, sell 100% at `+30%`
- `finalbot-cya-shadow-tp50`: MC >= `$50k`, sell 100% at `+50%`
- `finalbot-cya-shadow-tp75`: MC >= `$50k`, sell 100% at `+75%`

The MC gate uses the generic prebuy market-cap filter with DexScreener pair metadata. It does not consume Moralis OHLCV quota unless volume/momentum filters are added.

Install/update the services from the VPS:

```bash
cd /root/bot/FinalBot
git pull origin main
bash scripts/setupCyaShadowTpSimulations.sh
```

Start all three:

```bash
systemctl start finalbot-cya-shadow-tp30 finalbot-cya-shadow-tp50 finalbot-cya-shadow-tp75
```

Stop all three:

```bash
systemctl stop finalbot-cya-shadow-tp30 finalbot-cya-shadow-tp50 finalbot-cya-shadow-tp75
```

Watch logs:

```bash
journalctl -u finalbot-cya-shadow-tp30 -f
journalctl -u finalbot-cya-shadow-tp50 -f
journalctl -u finalbot-cya-shadow-tp75 -f
```

Trade logs:

```bash
/root/bot/FinalBot-cya-shadow-tp30/data/trades.jsonl
/root/bot/FinalBot-cya-shadow-tp50/data/trades.jsonl
/root/bot/FinalBot-cya-shadow-tp75/data/trades.jsonl
```

## Pull Latest Bot Update On VPS

Main bot:

```bash
cd /root/bot/FinalBot
git pull origin main
npm install
npm run build
```

If using ShredStream/Rust sidecar:

```bash
npm run rust-sidecar:jito-build
```

Then restart:

```bash
systemctl restart finalbot
```

## Copy Local Env To VPS

Run from Windows PowerShell on my PC:

```powershell
scp "C:\Users\Cheet\OneDrive\Documents\FinalBot\.env" root@87.99.145.246:/root/bot/FinalBot/.env
```

For IPv6 scp, use brackets:

```powershell
scp "C:\Users\Cheet\OneDrive\Documents\FinalBot\.env" root@[2a01:4ff:f4:1f2a::1]:/root/bot/FinalBot/.env
```

Then SSH into the VPS and restart:

```bash
systemctl restart finalbot
```

## Telemetry Report

```bash
cd /root/bot/FinalBot
npm run telemetry:execution
```

For 4BdK:

```bash
cd /root/bot/FinalBot-4bdk
npm run telemetry:execution
```

For 5qn:

```bash
cd /root/bot/FinalBot-5qn
npm run telemetry:execution
```

## CyA Same-Slot Main Bot

This applies the live CyA same-slot validation preset to the main/root bot. It removes the local
market-cap filter for this session, enables Rust hot sniper, races Jito transaction + Jito bundle +
Helius Sender + RPC fallback, and uses small live exposure.

```bash
cd /root/bot/FinalBot

sudo apt update
sudo apt install -y build-essential perl pkg-config libssl-dev

cp .env ".env.backup-before-cya-same-slot-$(date +%Y%m%d-%H%M%S)"

npm run env:cya-same-slot
npm run build
npm run rust-sidecar:jito-build

systemctl restart finalbot
journalctl -u finalbot -f
```

Starting exposure:

```env
MIN_BUY_SOL=0.04
MAX_BUY_SOL=0.04
MAX_SOL_PER_TOKEN=0.04
MAX_SESSION_DEPLOYED_SOL=0.20
MAX_OPEN_POSITIONS=2
```

Scale-up after clean telemetry:

```env
MIN_BUY_SOL=0.06
MAX_BUY_SOL=0.06
MAX_SOL_PER_TOKEN=0.06
MAX_SESSION_DEPLOYED_SOL=0.30
MAX_OPEN_POSITIONS=2
```

## CyA MC 50k-100k No-Fee VPS Instance

This creates a separate live CyA bot instance:

- folder: `/root/bot/FinalBot-cya-mc50-100-nofee`
- service: `finalbot-cya-mc50-100-nofee`
- wallet: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`
- buy size: `0.20 SOL`
- open positions: no cap (`MAX_OPEN_POSITIONS=` blank)
- market cap gate: local inferred `$50k-$100k`
- fees: no Jito tip, no Helius tip, no priority fee, no dynamic priority fee
- execution: Rust-only buys/sells, no Jupiter fallback

Install/update from the VPS:

```bash
cd /root/bot/FinalBot
git pull origin main
bash scripts/setupCyaMc50NoFeeInstance.sh
```

Start it:

```bash
systemctl start finalbot-cya-mc50-100-nofee
```

Watch it:

```bash
journalctl -u finalbot-cya-mc50-100-nofee -f
```

Stop it:

```bash
systemctl stop finalbot-cya-mc50-100-nofee
```

Verify the env:

```bash
cd /root/bot/FinalBot-cya-mc50-100-nofee
npm run vps:doctor
grep -nE '^(TRACKED_WALLETS|MIN_BUY_SOL|MAX_BUY_SOL|MAX_OPEN_POSITIONS|PREBUY_MIN_MARKET_CAP_USD|PREBUY_MAX_MARKET_CAP_USD|SIMULATION_MODE|DRY_RUN|SHADOW_EXECUTION_MODE|FAILED_BUY_RECOVERY|TIP_LAMPORTS|PRIORITY_FEE_MICRO_LAMPORTS|DYNAMIC_PRIORITY_FEE_ENABLED|JITO_TIP_ORACLE_ENABLED|JUPITER_FALLBACK_ENABLED|RUST_EXECUTOR_ENABLED|RUST_ONLY_BUYS|RUST_ONLY_SELLS|PUMPSWAP_DIRECT_BUY_ENABLED|PUMPSWAP_DIRECT_PREFLIGHT)=' .env
```

Telegram `getUpdates` conflict check:

```bash
systemctl list-units --type=service --state=running 'finalbot*'
pm2 status
journalctl -u finalbot -n 100 --no-pager | grep -i 'getUpdates\|Telegram command polling'
```
## Jito/ShredStream Sanity Check

Bot logs should show:

```text
Rust sidecar config: Yellowstone=ON, Jito ShredStream=ON
Jito ShredStream: CONNECTED and streaming entries into the bot.
```

Check Jito proxy logs:

```bash
docker logs -f jito-shredstream-proxy
```

## Infra Env Values

```env
SHYFT_GRPC_URL=https://grpc.va.shyft.to
SHYFT_RABBITSTREAM_URL=https://rabbitstream.va.shyft.to/
JITO_REGION=ny
JITO_REGIONS=ny
DESIRED_REGIONS=ny
JITO_SHREDSTREAM_ENABLED=true
JITO_SHREDSTREAM_GRPC_URL=http://127.0.0.1:9999
```

## Useful Reminder

If a bot is running through `systemd`, terminal hotkeys inside `npm run bot` will not work.

To use hotkeys manually:

```bash
systemctl stop finalbot
cd /root/bot/FinalBot
npm run bot
```

Stop manual mode with `Ctrl+C`, then restart background mode:

```bash
systemctl start finalbot
```

## PM2 Bot Mode

Use this when you want PM2 to keep the bot alive instead of running `npm run bot` manually.

Install PM2 once:

```bash
npm install -g pm2
```

Start CyA with the committed PM2 config:

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm install
npm run build
pm2 delete finalbot-cya 2>/dev/null || true
pm2 start ecosystem.config.cjs --only finalbot-cya
pm2 save
pm2 status
pm2 logs finalbot-cya --lines 100
```

Useful PM2 commands:

```bash
pm2 status
pm2 logs finalbot-cya --lines 100
pm2 restart finalbot-cya
pm2 stop finalbot-cya
pm2 delete finalbot-cya
```

Make PM2 survive VPS reboots:

```bash
pm2 startup
```

Then run the command PM2 prints.

## Env Update/Dedupe Commands

These commands update `.env` and remove duplicate entries for the keys they touch.

Buy-only tip and priority fees:

```bash
cd /root/bot/FinalBot-cya
npm run env:buy-only-fees
grep -nE '^(TIP_LAMPORTS|PRIORITY_FEE_MICRO_LAMPORTS|DYNAMIC_PRIORITY_FEE_ENABLED|EXECUTION_TIP_MULTIPLIERS|EXECUTION_PRIORITY_FEE_MULTIPLIERS)=' .env
```

No tip and no priority fees:

```bash
cd /root/bot/FinalBot-cya
npm run env:no-execution-fees
grep -nE '^(TIP_LAMPORTS|PRIORITY_FEE_MICRO_LAMPORTS|DYNAMIC_PRIORITY_FEE_ENABLED|DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS|EXECUTION_TIP_MULTIPLIERS|EXECUTION_PRIORITY_FEE_MULTIPLIERS)=' .env
```

Set one-off env values:

```bash
cd /root/bot/FinalBot-cya
npm run env:upsert -- HYBRID_EXIT_ENABLED=true STOP_LOSS_PCT=35 MIN_HOLD_BEFORE_STOP_LOSS_MS=15000
```

Check duplicates, fee posture, and PM2 process status:

```bash
cd /root/bot/FinalBot-cya
npm run vps:doctor
```
