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
  exit 1
fi

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Missing source .env: $SOURCE_DIR/.env" >&2
  echo "Fill the main .env first so private/API keys can be copied into each shadow instance." >&2
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
    cp "$SOURCE_DIR/.env" "$dest/.env"
  else
    echo "Updating code in existing $dest" >&2
    cp "$dest/.env" "$dest/.env.backup-before-cya-shadow-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    if command -v rsync >/dev/null; then
      rsync -a \
        --exclude .env \
        --exclude node_modules \
        --exclude dist \
        --exclude rust-sidecar/target \
        --exclude .restore-backups \
        "$SOURCE_DIR/" "$dest/"
    fi
    if [[ ! -f "$dest/.env" ]]; then
      cp "$SOURCE_DIR/.env" "$dest/.env"
    fi
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
const updateKeys = new Set(Object.keys(updates));
const kept = [];
for (const rawLine of text.split(/\r?\n/)) {
  const trimmed = rawLine.trim();
  if (!trimmed || trimmed.startsWith("#")) {
    kept.push(rawLine);
    continue;
  }
  const eq = trimmed.indexOf("=");
  if (eq <= 0) {
    kept.push(rawLine);
    continue;
  }
  const key = trimmed.slice(0, eq).trim();
  if (!updateKeys.has(key)) kept.push(rawLine);
}
for (const [key, value] of Object.entries(updates)) {
  kept.push(`${key}=${value}`);
}
text = kept.join("\n");
fs.writeFileSync(path, text.endsWith("\n") ? text : `${text}\n`);
console.log(`Updated ${path}: ${Object.keys(updates).join(", ")}`);
NODE
}

install_service() {
  local name="$1"
  local dir="$2"
  local service="/etc/systemd/system/finalbot-$name.service"

  cat > "$service" <<EOF
[Unit]
Description=FinalBot $name CyA shadow TP simulation
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

setup_strategy() {
  local suffix="$1"
  local take_profit="$2"
  local name="cya-shadow-$suffix"
  local dir
  dir="$(copy_instance "$name")"

  set_env "$dir/.env" \
    TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o \
    SIMULATION_MODE=true \
    SHADOW_EXECUTION_MODE=true \
    SHADOW_LATENCY_MS=80 \
    SHADOW_ENTRY_SLIPPAGE_PCT=2 \
    SHADOW_EXIT_SLIPPAGE_PCT=3 \
    SHADOW_TX_FEE_SOL=0 \
    SHADOW_FAILURE_RATE_PCT=0 \
    SIMULATION_SOL_BALANCE=10 \
    BUY_ONCE_PER_TOKEN=true \
    MIRROR_SELLS=false \
    MIRROR_SELL_MODE=none \
    HYBRID_EXIT_ENABLED=false \
    HYBRID_EXIT_SOURCE_EXIT_REMAINING=false \
    STOP_LOSS_PCT= \
    MIN_HOLD_BEFORE_STOP_LOSS_MS=0 \
    TAKE_PROFIT_PCT="$take_profit" \
    TRAILING_STEPS= \
    MIN_BUY_BEHAVIOR=floor \
    COPY_BUY_PERCENTAGE=100 \
    MIN_BUY_SOL=0.25 \
    MAX_BUY_SOL=0.25 \
    MAX_SOL_PER_TOKEN=0.25 \
    MAX_SESSION_DEPLOYED_SOL=10 \
    MAX_OPEN_POSITIONS=50 \
    PREBUY_MORALIS_FILTER_ENABLED=true \
    PREBUY_MORALIS_TIMEOUT_MS=2500 \
    PREBUY_MIN_VOLUME_USD=0 \
    PREBUY_MIN_LIQUIDITY_USD= \
    PREBUY_MIN_MOMENTUM_PCT= \
    LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false \
    LOCAL_PUMPFUN_SOL_PRICE_USD=72 \
    LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true \
    LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000 \
    LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500 \
    PREBUY_MIN_MARKET_CAP_USD=50000 \
    PREBUY_MAX_MARKET_CAP_USD= \
    PREBUY_FILTER_FAIL_OPEN=false \
    SHOW_MARKET_CAP=true \
    TELEGRAM_BOT_TOKEN= \
    TELEGRAM_CHAT_ID= \
    TELEGRAM_BROADCAST_CHAT_ID= \
    EXECUTION_MULTI_SEND_ENABLED=false \
    RUST_HOT_SNIPER_ENABLED=false \
    RUST_HOT_SNIPER_PRIMARY=false

  echo "Installing dependencies/building in $dir"
  (cd "$dir" && npm install && npm run build)
  install_service "$name" "$dir"
}

setup_strategy tp30 30
setup_strategy tp50 50
setup_strategy tp75 75

systemctl daemon-reload

if [[ "$START_SERVICES" == "true" ]]; then
  systemctl restart finalbot-cya-shadow-tp30 finalbot-cya-shadow-tp50 finalbot-cya-shadow-tp75
fi

cat <<'EOF'

CyA shadow TP simulations are installed.

Start:
  systemctl start finalbot-cya-shadow-tp30 finalbot-cya-shadow-tp50 finalbot-cya-shadow-tp75

Stop:
  systemctl stop finalbot-cya-shadow-tp30 finalbot-cya-shadow-tp50 finalbot-cya-shadow-tp75

Watch:
  journalctl -u finalbot-cya-shadow-tp30 -f
  journalctl -u finalbot-cya-shadow-tp50 -f
  journalctl -u finalbot-cya-shadow-tp75 -f

Trade logs:
  /root/bot/FinalBot-cya-shadow-tp30/data/trades.jsonl
  /root/bot/FinalBot-cya-shadow-tp50/data/trades.jsonl
  /root/bot/FinalBot-cya-shadow-tp75/data/trades.jsonl
EOF
