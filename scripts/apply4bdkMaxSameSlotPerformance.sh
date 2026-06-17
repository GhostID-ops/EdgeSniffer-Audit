#!/usr/bin/env bash
set -euo pipefail

# Aggressive 4BdK same-slot profile for the dedicated VPS instance.
#
# This sends real trades. Defaults keep buy exposure small while pushing landing
# priority hard. Override any value inline, for example:
#
#   JITO_TIP_LAMPORTS=25000000 PRIORITY_FEE_MICRO_LAMPORTS=30000000 bash scripts/apply4bdkMaxSameSlotPerformance.sh
#
# Cost intuition:
# - 10,000,000 lamports = 0.01 SOL Jito tip per attempted Jito buy.
# - PRIORITY_FEE_MICRO_LAMPORTS is per compute unit; very high values can add
#   meaningful SOL cost on hot buys.

BOT_ROOT="${BOT_ROOT:-/root/bot}"
INSTANCE_DIR="${INSTANCE_DIR:-$BOT_ROOT/FinalBot-4bdk}"
ENV_FILE="${ENV_FILE:-$INSTANCE_DIR/.env}"
RESTART_SERVICE="${RESTART_SERVICE:-true}"
SERVICE_NAME="${SERVICE_NAME:-finalbot-4bdk}"

TRACKED_WALLET="${TRACKED_WALLET:-4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk}"

# Keep exposure intentionally small while testing the expensive lane.
BUY_SOL="${BUY_SOL:-0.04}"
MAX_SESSION_DEPLOYED_SOL="${MAX_SESSION_DEPLOYED_SOL:-0.16}"
MAX_OPEN_POSITIONS="${MAX_OPEN_POSITIONS:-2}"

# Aggressive landing defaults. Raise only for short windows.
TIP_LAMPORTS="${TIP_LAMPORTS:-10000000}"
RUST_HOT_SNIPER_JITO_TIP_LAMPORTS="${RUST_HOT_SNIPER_JITO_TIP_LAMPORTS:-10000000}"
RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS="${RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS:-1500000}"
PRIORITY_FEE_MICRO_LAMPORTS="${PRIORITY_FEE_MICRO_LAMPORTS:-20000000}"
RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS="${RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS:-20000000}"
DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS="${DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS:-30000000}"
JITO_TIP_ORACLE_MAX_LAMPORTS="${JITO_TIP_ORACLE_MAX_LAMPORTS:-30000000}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this as root on the VPS." >&2
  exit 1
fi

if [[ ! -d "$INSTANCE_DIR" ]]; then
  echo "Missing 4BdK instance dir: $INSTANCE_DIR" >&2
  echo "Create it first with scripts/setupMultiBotInstances.sh or set INSTANCE_DIR." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

backup="$ENV_FILE.backup-before-4bdk-max-$(date +%Y%m%d-%H%M%S)"
cp "$ENV_FILE" "$backup"

ENV_FILE="$ENV_FILE" node - \
  TRACKED_WALLETS="$TRACKED_WALLET" \
  SIMULATION_MODE=false \
  SHADOW_EXECUTION_MODE=false \
  MIRROR_SELLS=true \
  MIRROR_SELL_MODE=proportional \
  MIRROR_SELL_PROPORTIONAL_MIN_PCT=10 \
  MIRROR_SELL_PROPORTIONAL_MAX_PCT=100 \
  MIRROR_SELL_PROPORTIONAL_FALLBACK=full \
  BUY_ONCE_PER_TOKEN=true \
  MIN_BUY_BEHAVIOR=floor \
  MIN_BUY_SOL="$BUY_SOL" \
  MAX_BUY_SOL="$BUY_SOL" \
  MAX_SOL_PER_TOKEN="$BUY_SOL" \
  MAX_SESSION_DEPLOYED_SOL="$MAX_SESSION_DEPLOYED_SOL" \
  MAX_OPEN_POSITIONS="$MAX_OPEN_POSITIONS" \
  STOP_LOSS_PCT=50 \
  MIN_HOLD_BEFORE_STOP_LOSS_MS=10000 \
  TAKE_PROFIT_PCT= \
  TRAILING_STEPS= \
  PREBUY_MORALIS_FILTER_ENABLED=false \
  LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false \
  LOCAL_PUMPFUN_SOL_PRICE_USD= \
  LOCAL_PUMPFUN_MIN_MARKET_CAP_SOL= \
  LOCAL_PUMPFUN_MAX_MARKET_CAP_SOL= \
  PREBUY_MIN_MARKET_CAP_USD= \
  PREBUY_MAX_MARKET_CAP_USD= \
  PREBUY_FILTER_FAIL_OPEN=true \
  BLOCK_PUMPFUN_MAYHEM=false \
  PUMPFUN_MAYHEM_STRICT=false \
  SHYFT_GRPC_ENABLED=true \
  SHYFT_GRPC_URL=https://grpc.va.shyft.to \
  SHYFT_RABBITSTREAM_ENABLED=true \
  SHYFT_RABBITSTREAM_URL=https://rabbitstream.va.shyft.to/ \
  SHYFT_GRPC_COMMITMENT=processed \
  SHYFT_GRPC_RECONNECT_MS=500 \
  JITO_SHREDSTREAM_ENABLED=true \
  JITO_SHREDSTREAM_GRPC_URL=http://127.0.0.1:9999 \
  JITO_REGION=ny \
  JITO_REGIONS=ny,slc,ams,frankfurt,london,tokyo,singapore \
  DESIRED_REGIONS=ny \
  USE_JITO_DIRECT=true \
  USE_JITO_FALLBACK=true \
  JITO_SEND_MODE=race \
  BROADCAST_RACE_ENABLED=true \
  EXECUTION_MULTI_SEND_ENABLED=true \
  SENDER_RPC_FALLBACK_ENABLED=true \
  JITO_REGION_RACE_ENABLED=true \
  RUST_SIDECAR_ENABLED=true \
  RUST_SIDECAR_YELLOWSTONE_ENABLED=true \
  RUST_EXECUTOR_ENABLED=true \
  RUST_EXECUTOR_TIMEOUT_MS=1000 \
  RUST_HOT_SNIPER_ENABLED=true \
  RUST_HOT_SNIPER_PRIMARY=true \
  RUST_HOT_SNIPER_PRIMARY_FALLBACK_ENABLED=true \
  RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=40 \
  RUST_HOT_SNIPER_BLOCKHASH_REFRESH_MS=400 \
  RUST_HOT_SNIPER_BLOCKHASH_MAX_AGE_MS=12000 \
  JITO_CONNECTION_WARMUP_ENABLED=true \
  JITO_CONNECTION_WARMUP_MS=20000 \
  TIP_LAMPORTS="$TIP_LAMPORTS" \
  RUST_HOT_SNIPER_JITO_TIP_LAMPORTS="$RUST_HOT_SNIPER_JITO_TIP_LAMPORTS" \
  RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS="$RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS" \
  JITO_TIP_ORACLE_ENABLED=true \
  JITO_TIP_ORACLE_PERCENTILE=p99 \
  JITO_TIP_ORACLE_REFRESH_MS=2000 \
  JITO_TIP_ORACLE_MIN_LAMPORTS="$TIP_LAMPORTS" \
  JITO_TIP_ORACLE_MAX_LAMPORTS="$JITO_TIP_ORACLE_MAX_LAMPORTS" \
  JITO_TIP_FLOOR_URL=https://bundles.jito.wtf/api/v1/bundles/tip_floor \
  DYNAMIC_PRIORITY_FEE_ENABLED=true \
  DYNAMIC_PRIORITY_FEE_REFRESH_MS=2000 \
  PRIORITY_FEE_MICRO_LAMPORTS="$PRIORITY_FEE_MICRO_LAMPORTS" \
  DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS="$DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS" \
  RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS="$RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS" \
  FAST_MODE=true \
  CONFIRMATION_TIMEOUT_MS=8000 \
  CONFIRMATION_NULL_STATUS_TIMEOUT_MS=2500 \
  CONFIRMATION_REBROADCAST_RETRIES=0 \
  WARM_WALLET_MODE=true \
  KEEP_WSOL_ATA_OPEN=true \
  PUMPFUN_EXACT_SOL_IN_BUYS=true \
  PUMPSWAP_DIRECT_BUY_ENABLED=true \
  PUMPSWAP_DIRECT_PREFLIGHT=true \
  MIRROR_SLIPPAGE_BPS=8000 \
  MIRROR_RETRY_SLIPPAGE_BPS=12000 <<'NODE'
const fs = require("fs");
const path = process.env.ENV_FILE;
const updates = {};
for (const pair of process.argv.slice(2)) {
  const eq = pair.indexOf("=");
  if (eq <= 0) continue;
  updates[pair.slice(0, eq)] = pair.slice(eq + 1);
}

const updateKeys = new Set(Object.keys(updates));
const old = fs.readFileSync(path, "utf8");
const kept = [];
for (const rawLine of old.split(/\r?\n/)) {
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

kept.push("");
kept.push("# 4BdK max same-slot performance profile");
kept.push(`# Applied at ${new Date().toISOString()}`);
for (const [key, value] of Object.entries(updates)) {
  kept.push(`${key}=${value}`);
}

fs.writeFileSync(path, kept.join("\n").replace(/\n*$/, "\n"));
console.log(`Updated ${path}: ${Object.keys(updates).length} keys`);
NODE

echo "Backup saved to $backup"

echo "Building TypeScript and Rust hot sniper..."
(cd "$INSTANCE_DIR" && npm install && npm run build && npm run rust-sidecar:jito-build)

systemctl daemon-reload
if [[ "$RESTART_SERVICE" == "true" ]]; then
  systemctl restart "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager
else
  echo "RESTART_SERVICE=false, not restarting $SERVICE_NAME"
fi

cat <<EOF

4BdK max same-slot profile applied.

Watch:
  journalctl -u $SERVICE_NAME -f

Check same-slot quality after copied buys:
  cd $INSTANCE_DIR
  npm run telemetry:scan-today

Restore previous env if needed:
  cp "$backup" "$ENV_FILE"
  systemctl restart "$SERVICE_NAME"
EOF
