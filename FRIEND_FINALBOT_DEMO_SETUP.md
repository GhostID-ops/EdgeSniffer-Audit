# FinalBot Friend Demo Setup

Use the isolated demo launcher for a friend/acquirer test. It creates a separate working folder, separate `.env`, separate `data/`, a throwaway burner wallet, and a password-protected UI.

Do not give a friend access to your main bot folder or main wallet.

## Start An Isolated Demo

```bash
cd /root/bot/FinalBot-cya
npm install
npm run build
npm run rust-sidecar:jito-build
npm run friend:demo -- friend-demo 3001
```

The command prints:

```text
dir:    /root/bot/FinalBot-cya/friend-demo
env:    /root/bot/FinalBot-cya/friend-demo/.env
login:  friend / GENERATED_PASSWORD
open:   http://localhost:3001/friend
```

The generated demo starts in simulation mode. If the friend switches to live mode, demo safety caps apply:

```text
FRIEND_MAX_LIVE_BUY_SOL=0.03
FRIEND_MAX_LIVE_TIP_LAMPORTS=0
FRIEND_MAX_LIVE_PRIORITY_FEE_MICRO_LAMPORTS=0
```

## Friend Access

Prefer Tailscale or SSH tunneling. Do not expose the UI publicly without HTTPS in front of it.

SSH tunnel from the friend's machine:

```bash
ssh -L 3001:127.0.0.1:3001 root@YOUR_VPS_IP
```

Then they open:

```text
http://localhost:3001/friend
```

Use the login printed by `npm run friend:demo`.

## Tiny Live Test Rules

For a real-money V1 trial, use only a burner wallet.

Recommended process:

1. Start in simulation mode and confirm the UI, logs, telemetry, and settings work.
2. Paste the friend's burner private key into the friend UI only.
3. Fund that burner with a tiny amount.
4. Switch to live mode only after confirming sizing and tracked wallets.
5. Keep demo caps enabled until the friend has seen enough.

## Health Checks

```bash
npm run build
npm run vps:doctor
curl -I http://127.0.0.1:3001/friend
```

Expected:

- `npm run build` passes.
- `npm run vps:doctor` runs.
- Unauthenticated `/friend` returns `401`.
- Authenticated `/friend` loads the UI.

## Normal Friend Update

For an existing friend's git clone:

```bash
cd /root/bot/FinalBot-cya
git fetch origin
git checkout main
git reset --hard origin/main
npm install
npm run build
npm run rust-sidecar:jito-build
npm run vps:doctor
```

Only apply strategy presets when you intentionally want to change his sizing/risk values.
