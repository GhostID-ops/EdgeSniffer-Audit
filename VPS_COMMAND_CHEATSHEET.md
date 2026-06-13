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
cd /root/bot/FinalBot
```

Edit main env:

```bash
nano /root/bot/FinalBot/.env
```

## 4BdK Bot Folder

```bash
cd /root/bot/FinalBot-4bdk
```

Edit 4BdK env:

```bash
nano /root/bot/FinalBot-4bdk/.env
```

## 5qn Bot Folder

```bash
cd /root/bot/FinalBot-5qn
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
systemctl restart finalbot-5qn
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
journalctl -u finalbot-4bdk -f
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
