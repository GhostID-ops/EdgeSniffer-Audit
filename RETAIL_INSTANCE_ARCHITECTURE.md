# Obsidian Retail Instance Architecture

This is the safe V1 shape for acquisition demos and early retail customers.

## Core Rule

One customer = one bot process = one folder = one wallet/config/data set.

Do not run multiple customer wallets inside one `botService.ts` process. The bot intentionally has singleton in-memory state for latency: pending buys, processed signatures, open positions, Rust sidecar handles, confirmation maps, and risk state. Separate OS processes are the clean boundary.

## Immediate Isolation Fixes

Friend/demo instances now use explicit paths:

- `INSTANCE_ID`
- `INSTANCE_ROOT`
- `INSTANCE_ENV_PATH`
- `DATA_DIR`
- `LOG_DIR`
- `EXECUTION_TELEMETRY_PATH`

New friend instances no longer copy your main `data/trades.jsonl` or `data/execution-telemetry.jsonl`. This prevents old main-bot history from appearing in a customer UI.

## Recommended VPS Layout

```text
/root/bot/FinalBot-cya/                  # repo/code
/root/bot/finalbot-instances/mark/       # customer instance
  .env                                   # Mark config + wallet key
  data/                                  # Mark trades/telemetry only
  logs/                                  # Mark logs only
  finalbot-mark.service                  # generated service file
  finalbot-mark.nginx.conf               # generated nginx snippet
```

## Create A Customer Instance Plan

From the repo on the VPS:

```bash
cd /root/bot/FinalBot-cya
npm run retail:instance -- plan --id mark --port 3101 --root /root/bot/finalbot-instances --repo /root/bot/FinalBot-cya
```

This writes files under:

```text
/root/bot/finalbot-instances/mark/
```

It does not start anything yet.

## Install As A Systemd Service

```bash
cd /root/bot/FinalBot-cya
npm run retail:instance -- install --id mark --port 3101 --root /root/bot/finalbot-instances --repo /root/bot/FinalBot-cya
```

Then check:

```bash
systemctl status finalbot-mark --no-pager
journalctl -u finalbot-mark -n 120 --no-pager
curl -I http://127.0.0.1:3101/friend-simple
```

## Manage A Customer Instance

```bash
cd /root/bot/FinalBot-cya
npm run retail:instance -- restart --id mark --port 3101 --root /root/bot/finalbot-instances --repo /root/bot/FinalBot-cya
npm run retail:instance -- logs --id mark --port 3101 --root /root/bot/finalbot-instances --repo /root/bot/FinalBot-cya
npm run retail:instance -- stop --id mark --port 3101 --root /root/bot/finalbot-instances --repo /root/bot/FinalBot-cya
```

## Public Link Shape

Retail users should not use SSH tunnels. Put nginx + HTTPS in front of the local instance port.

Example nginx target:

```text
https://demo.obsidianlabs.ai/mark/friend-simple
```

The generated nginx snippet is:

```text
/root/bot/finalbot-instances/mark/finalbot-mark.nginx.conf
```

Put that inside the HTTPS server block, then reload nginx:

```bash
nginx -t
systemctl reload nginx
```

## Current Security Status

Good enough for controlled demo:

- separate process per user
- separate `.env`
- separate data/log folder
- basic auth
- small-wallet/tiny-size recommendation

Not enough for full retail with large funds:

- private key is still stored in `.env` plaintext
- no real customer account system yet
- no database-level tenant model
- no per-user billing/license enforcement
- no encryption-at-rest wrapper yet

## Next Retail Hardening

1. Encrypt private keys at rest using a server master key.
2. Replace Basic Auth with real user login/session auth.
3. Add owner/admin panel to create/restart/customer instances without terminal.
4. Put nginx HTTPS in front of each instance.
5. Add per-instance health cards: Streams, Rust executor, ShredStream, wallet funded, trading running.
6. Add per-instance resource limits and log rotation.