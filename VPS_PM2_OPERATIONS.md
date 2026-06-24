# VPS PM2 Operations

## Update VPS

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm install
npm run build
```

## Start With PM2

Install PM2 once:

```bash
npm install -g pm2
```

Start or replace the CyA process:

```bash
cd /root/bot/FinalBot-cya
pm2 delete finalbot-cya 2>/dev/null || true
pm2 start ecosystem.config.cjs --only finalbot-cya
pm2 save
pm2 status
pm2 logs finalbot-cya --lines 100
```

Make PM2 survive VPS reboots:

```bash
pm2 startup
```

Then run the command PM2 prints.

## PM2 Controls

```bash
pm2 status
pm2 logs finalbot-cya --lines 100
pm2 restart finalbot-cya
pm2 stop finalbot-cya
pm2 delete finalbot-cya
```

## Env Presets

Buy-only tip and priority fees:

```bash
cd /root/bot/FinalBot-cya
npm run env:buy-only-fees
```

No tip and no priority fees:

```bash
cd /root/bot/FinalBot-cya
npm run env:no-execution-fees
```

Set one-off values while deduping the touched keys:

```bash
cd /root/bot/FinalBot-cya
npm run env:upsert -- HYBRID_EXIT_ENABLED=true STOP_LOSS_PCT=35 MIN_HOLD_BEFORE_STOP_LOSS_MS=15000
```

Check duplicate env keys, fee posture, and PM2 status:

```bash
cd /root/bot/FinalBot-cya
npm run vps:doctor
```
