#!/usr/bin/env bash
set -euo pipefail

BOT_ROOT="${BOT_ROOT:-/root/bot}"
SOURCE_DIR="${SOURCE_DIR:-$BOT_ROOT/FinalBot}"
START_SERVICES="${START_SERVICES:-false}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this as root on the VPS." >&2
  exit 1
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source bot folder not found: $SOURCE_DIR" >&2
  echo "Set SOURCE_DIR=/path/to/FinalBot if your bot lives somewhere else." >&2
  exit 1
fi

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Missing source .env: $SOURCE_DIR/.env" >&2
  echo "Create/fill the main .env first so private/API keys can be copied into each instance." >&2
  exit 1
fi

command -v node >/dev/null || { echo "node is required." >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required." >&2; exit 1; }

copy_instance() {
  local name="$1"
  local dest="$BOT_ROOT/FinalBot-$name"

  if [[ ! -d "$dest" ]]; then
    echo "Creating $dest from $SOURCE_DIR" >&2
    if command -v rsync >/dev/null; then
      rsync -a \
        --exclude node_modules \
        --exclude dist \
        --exclude rust-sidecar/target \
        --exclude .restore-backups \
        "$SOURCE_DIR/" "$dest/"
    else
      cp -a "$SOURCE_DIR" "$dest"
      rm -rf "$dest/node_modules" "$dest/dist" "$dest/rust-sidecar/target" "$dest/.restore-backups"
    fi
  else
    echo "Updating code in existing $dest" >&2
    cp "$dest/.env" "$dest/.env.backup-before-multi-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    if command -v rsync >/dev/null; then
      rsync -a \
        --exclude .env \
        --exclude node_modules \
        --exclude dist \
        --exclude rust-sidecar/target \
        --exclude .restore-backups \
        "$SOURCE_DIR/" "$dest/"
    fi
  fi

  if [[ ! -f "$dest/.env" ]]; then
    cp "$SOURCE_DIR/.env" "$dest/.env"
  fi

  echo "$dest"
}

set_env() {
  local env_file="$1"
  shift
  ENV_FILE="$env_file" node - "$@" <<'NODE'
const fs = require("fs");
const path = process.env.ENV_FILE;
const pairs = process.argv.slice(2);
const updates = {};
for (const pair of pairs) {
  const eq = pair.indexOf("=");
  if (eq <= 0) continue;
  updates[pair.slice(0, eq)] = pair.slice(eq + 1);
}
let text = fs.readFileSync(path, "utf8");
for (const [key, value] of Object.entries(updates)) {
  const re = new RegExp(`^#?\\s*${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=.*$`, "m");
  if (re.test(text)) text = text.replace(re, `${key}=${value}`);
  else text += `\n${key}=${value}`;
}
fs.writeFileSync(path, text);
console.log(`Updated ${path}: ${Object.keys(updates).join(", ")}`);
NODE
}

install_service() {
  local name="$1"
  local dir="$2"
  local service="/etc/systemd/system/finalbot-$name.service"

  cat > "$service" <<EOF
[Unit]
Description=FinalBot $name instance
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$dir
ExecStart=/usr/bin/npm run bot
Restart=always
RestartSec=3
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF

  systemctl enable "finalbot-$name" >/dev/null
}

setup_instance() {
  local name="$1"
  shift
  local dir
  dir="$(copy_instance "$name")"

  set_env "$dir/.env" \
    SHYFT_GRPC_URL=https://grpc.va.shyft.to \
    SHYFT_RABBITSTREAM_URL=https://rabbitstream.va.shyft.to/ \
    JITO_REGION=ny \
    JITO_REGIONS=ny \
    DESIRED_REGIONS=ny \
    USE_JITO_DIRECT=true \
    USE_JITO_FALLBACK=true \
    JITO_SEND_MODE=race \
    BROADCAST_RACE_ENABLED=true \
    EXECUTION_MULTI_SEND_ENABLED=true \
    SENDER_RPC_FALLBACK_ENABLED=true \
    JITO_TIP_ORACLE_ENABLED=true \
    JITO_TIP_ORACLE_PERCENTILE=p75 \
    JITO_TIP_ORACLE_REFRESH_MS=5000 \
    JITO_SHREDSTREAM_ENABLED=true \
    JITO_SHREDSTREAM_GRPC_URL=http://127.0.0.1:9999 \
    "$@"

  echo "Installing dependencies/building in $dir"
  (cd "$dir" && npm install && npm run build)
  install_service "$name" "$dir"
}

setup_instance 5qn \
  TRACKED_WALLETS=5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF \
  MIRROR_SELLS=false \
  BUY_ONCE_PER_TOKEN=true \
  MAX_OPEN_POSITIONS=2 \
  MIN_BUY_SOL=0.02 \
  MAX_BUY_SOL=0.05 \
  MAX_SOL_PER_TOKEN=0.05 \
  MAX_SESSION_DEPLOYED_SOL=0.15 \
  STOP_LOSS_PCT=45 \
  TRAILING_STEPS=60:15,100:40,160:85,250:150,400:260,700:500,1000:760

setup_instance cya \
  TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o \
  MIRROR_SELLS=false \
  BUY_ONCE_PER_TOKEN=true \
  MAX_OPEN_POSITIONS=2 \
  MIN_BUY_SOL=0.02 \
  MAX_BUY_SOL=0.04 \
  MAX_SOL_PER_TOKEN=0.04 \
  MAX_SESSION_DEPLOYED_SOL=0.12 \
  STOP_LOSS_PCT=40 \
  TRAILING_STEPS=40:12,70:25,120:60,200:125,350:230,600:430

setup_instance omego \
  TRACKED_WALLETS=omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq \
  MIRROR_SELLS=false \
  BUY_ONCE_PER_TOKEN=true \
  MAX_OPEN_POSITIONS=1 \
  MIN_BUY_SOL=0.02 \
  MAX_BUY_SOL=0.03 \
  MAX_SOL_PER_TOKEN=0.03 \
  MAX_SESSION_DEPLOYED_SOL=0.09 \
  STOP_LOSS_PCT=35 \
  TRAILING_STEPS=45:15,75:30,120:65,200:125,350:230

setup_instance 4bdk \
  TRACKED_WALLETS=4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk \
  MIRROR_SELLS=true \
  BUY_ONCE_PER_TOKEN=false \
  MAX_OPEN_POSITIONS=2 \
  MIN_BUY_SOL=0.02 \
  MAX_BUY_SOL=0.05 \
  MAX_SOL_PER_TOKEN=0.06 \
  MAX_SESSION_DEPLOYED_SOL=0.18 \
  STOP_LOSS_PCT=50 \
  TRAILING_STEPS=80:25,150:70,250:140,400:260,700:500,1000:760

systemctl daemon-reload

if [[ "$START_SERVICES" == "true" ]]; then
  systemctl restart finalbot-5qn finalbot-cya finalbot-omego finalbot-4bdk
fi

cat <<'EOF'

Done.

Start all:
  systemctl start finalbot-5qn finalbot-cya finalbot-omego finalbot-4bdk

Stop all:
  systemctl stop finalbot-5qn finalbot-cya finalbot-omego finalbot-4bdk

Watch logs:
  journalctl -u finalbot-5qn -f
  journalctl -u finalbot-cya -f
  journalctl -u finalbot-omego -f
  journalctl -u finalbot-4bdk -f
EOF
