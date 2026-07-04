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

## EdgeSniffer Launch Sniffer

EdgeSniffer is paper-only research unless you intentionally change it. The normal VPS folder is:

```bash
cd /root/bot/EdgeSniffer
```

### Start A Fresh Paper Scan

Use this when changing profiles or after a cleanup:

```bash
cd /root/bot/EdgeSniffer
npm run session:reset
npm run paper:launch
```

Current preferred research posture:

```env
score=85-94
entry mc=1500-5000
source=0.25-1.45
TP50 / SL20 / 120s
maxOpen=3
maxPerHour=20
maxSession=60
```

If a session produces no opens for a long time, do not blindly loosen everything. Run the reports first and loosen only the gate named by the evidence.

### Quick Health Check

```bash
cd /root/bot/EdgeSniffer
npm run report:launch
```

Healthy scan signs:

```text
status=signals_active
lastSignal is recent
lastStream is recent
localDecoded increasing
pollErrors=0
helius429=0
```

### Full Report Bundle

Run after at least 1-2 hours of scanning, or sooner if you already have closes:

```bash
cd /root/bot/EdgeSniffer
npm run report:open-quality -- 12 5
npm run report:launch
npm run report:missed-runners -- 12 85
npm run report:reality-gap -- 12 85
npm run report:entry-telemetry -- 12 25
npm run report:viable-missed-runners -- 12 25
npm run report:exposure-allocation -- 12 25
npm run report:param-sweep -- 12
npm run report:aggregate-fingerprint
npm run ui:build
```

If actual opens are losing but scanner hypothetical paths are positive, do not increase exposure. First inspect `open-quality`, `entry-telemetry`, and `reality-gap`.

### Confirmation / Entry Timing Reports

Use these when scanner runners exist but paper opens are missing:

```bash
cd /root/bot/EdgeSniffer
npm run report:confirmation-sweep -- 12
npm run report:entry-timing -- 12
```

If `confirmation-sweep` says `viable=0` and `entry-timing` says first-entry beats confirmed-entry, confirmation gates are probably starving the lane.

### Wallet / CyA Label Reports

Use this to inspect CyA/lookalike wallet performance and turn winners/losers into labels:

```bash
cd /root/bot/EdgeSniffer
WALLET_PERF_ROOTS=/root/bot/FinalBot-cya WALLET_PERF_TOP_N=50 npm run report:wallet-performance -- 24
npm run report:wallet-fingerprint -- 24
```

Clean actual CyA winner-only list:

```bash
cd /root/bot/EdgeSniffer
CYA_WINNER_ROOTS=/root/bot/FinalBot-cya CYA_WINNER_FILE_MODE=actual CYA_WINNER_APPEND=false CYA_WINNER_TOP_N=50 npm run report:cya-winners -- 24
cat data/cya-winner-mints.txt
```

Target one known mint:

```bash
cd /root/bot/EdgeSniffer
BURST_CLUSTER_TARGET_MINT=8DGnPt9FxLYsy8ynCj2WQ1LePAqhvprudxX7JLVepump npm run report:burst-clusters -- 24
```

Do not run a full `report:burst-clusters -- 24` if it is slow. Use a target mint or a short window first:

```bash
cd /root/bot/EdgeSniffer
npm run report:burst-clusters -- 6
```

### Disk Cleanup

Standard cleanup after a normal session:

```bash
cd /root/bot/EdgeSniffer
npm run maintenance:cleanup
df -h
du -sh data data/* 2>/dev/null | sort -h
```

Aggressive cleanup after reports are exported or if disk is getting tight:

```bash
cd /root/bot/EdgeSniffer
npm run maintenance:cleanup -- --aggressive
df -h
du -sh data data/* 2>/dev/null | sort -h
```

Aggressive cleanup is meant to protect the VPS from huge firehose files. Export the reports you care about first. Keep `data/reports` when possible because that is the useful research history.

If the disk is already full and commands are failing, stop the scanner first:

```bash
pkill -f 'tsx src/commands/launchPaperTrader.ts'
cd /root/bot/EdgeSniffer
npm run maintenance:cleanup -- --aggressive
df -h
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

## Friend Demo UI / SSH Tunnel

Open this tunnel from your own computer terminal, not from inside the VPS:

```bash
ssh -L 3001:127.0.0.1:3001 root@87.99.145.246
```

Keep that terminal open, then open the simple UI locally:

```text
http://127.0.0.1:3001/friend-simple
```

Advanced friend cockpit:

```text
http://127.0.0.1:3001/friend
```

Direct VPS link, only works if port 3001 is reachable from the internet:

```text
http://87.99.145.246:3001/friend-simple
```

Check friend UI login:

```bash
cd /root/bot/FinalBot-cya
grep '^UI_BASIC_AUTH_' friend-demo/.env
```

Update friend UI code on VPS:

```bash
cd /root/bot/FinalBot-cya
git pull --ff-only origin main
npm install
npm run build
```

Restart friend demo UI:

```bash
cd /root/bot/FinalBot-cya
pkill -f "demoInstance.ts friend-demo" || true
nohup npm run friend:demo -- friend-demo 3001 > friend-demo.log 2>&1 &
```

Check if friend UI is running:

```bash
cd /root/bot/FinalBot-cya
ss -ltnp | grep ':3001' || true
tail -80 friend-demo.log
```

Test friend UI locally on the VPS:

```bash
cd /root/bot/FinalBot-cya
USER=$(grep '^UI_BASIC_AUTH_USER=' friend-demo/.env | cut -d= -f2-)
PASS=$(grep '^UI_BASIC_AUTH_PASSWORD=' friend-demo/.env | cut -d= -f2-)
curl -i -u "$USER:$PASS" http://127.0.0.1:3001/friend-simple | head
```

Force wallet/cashflow PnL basis for the friend demo:

```bash
cd /root/bot/FinalBot-cya
sed -i 's/^PNL_ACCOUNTING_BASIS=.*/PNL_ACCOUNTING_BASIS=wallet/' friend-demo/.env
grep -q '^PNL_ACCOUNTING_BASIS=' friend-demo/.env || echo 'PNL_ACCOUNTING_BASIS=wallet' >> friend-demo/.env
grep '^PNL_ACCOUNTING_BASIS=' friend-demo/.env
```
## Wallet Cohort Background Audit

Full methodology: `WALLET_COHORT_RESEARCH_GUIDE.md`

```bash
cd /root/bot/FinalBot-cya
npm run build
bash scripts/startWalletAuditBatch.sh data/wallet-audit-wallets.json
```

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
tail -f "$RUN_DIR/runner.log"
cat "$RUN_DIR/state.json"
```

After completion:

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
cat "$RUN_DIR/cohort-summary.md"
npm run audit:wallet-latency -- "$RUN_DIR" \
  /root/bot/EdgeSniffer/data/launch-paper-signals.jsonl \
  /root/bot/EdgeSniffer/data/launch-paper-events.jsonl
```
