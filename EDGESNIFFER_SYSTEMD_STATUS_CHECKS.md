# EdgeSniffer Systemd Status Checks

Use these on the VPS to confirm the 2CQ scanner is still running in the background.

## SSH In

```bash
ssh root@87.99.145.246
```

## Green Active Status

For the current v4 scanner:

```bash
systemctl status edgesniffer-2cqv4 --no-pager -l
```

You want to see:

```text
Active: active (running)
```

If your terminal supports color, `active (running)` should appear green.

## Confirm The Scanner Process Exists

```bash
ps -eo pid,ppid,tty,lstart,cmd | grep -E "paper:launch|launchPaperTrader" | grep -v grep
```

Healthy output should include:

```text
npm run paper:launch
tsx src/commands/launchPaperTrader.ts
```

## Watch The Live Log

```bash
tail -f /root/bot/EdgeSniffer/data/2cqv4-systemd.log
```

Exit the live log with:

```text
Ctrl+C
```

## Confirm It Is V4

```bash
tail -n 80 /root/bot/EdgeSniffer/data/2cqv4-systemd.log | grep "2CQ FROZEN lane enabled"
```

You want to see:

```text
profile=2cq-flow-projection-v4
```

## Check Restart History

```bash
journalctl -u edgesniffer-2cqv4 -n 80 --no-pager -l
```

Planned restarts can show `timeout` if `RuntimeMaxSec=2h` is active. That is okay when the service immediately starts again.

## Quick One-Shot Health Check

```bash
systemctl is-active edgesniffer-2cqv4
systemctl status edgesniffer-2cqv4 --no-pager -l | head -20
ps -eo pid,ppid,tty,lstart,cmd | grep -E "paper:launch|launchPaperTrader" | grep -v grep
tail -n 30 /root/bot/EdgeSniffer/data/2cqv4-systemd.log
```

## Run A V4 Report

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v4
LATEST="$(ls -td data/reports/2cq-v4-frozen-* | head -1)"
cat "$LATEST/2cq-frozen-report.md"
```
