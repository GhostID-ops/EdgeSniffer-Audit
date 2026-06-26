#!/usr/bin/env bash
set -euo pipefail

# Overnight Omego shadow-TP30 simulation instance.
#
# Base .env is COPIED FROM THE finalbot-cya INSTANCE (so every value not listed
# in the overrides below is identical to your live finalbot-cya box), then the
# requested deltas are applied:
#   - tracked wallet  -> Omego
#   - TAKE_PROFIT_PCT -> 30
#   - MIRROR_SELLS    -> true   (full mirror exits)
#   - BUY_ONCE_PER_TOKEN -> false
#   - shadow/paper execution (no real broadcasts), unique WEB_PORT, no Telegram.
#
# Code is synced from the main repo ($SOURCE_DIR) so the instance runs current code.

BOT_ROOT="${BOT_ROOT:-/root/bot}"
SOURCE_DIR="${SOURCE_DIR:-$BOT_ROOT/FinalBot}"        # code source (main repo)
CYA_DIR="${CYA_DIR:-$BOT_ROOT/FinalBot-cya}"          # finalbot-cya instance (env source)
NAME="omego-shadow-tp30"
DEST="$BOT_ROOT/FinalBot-$NAME"
WEB_PORT="${WEB_PORT:-3130}"                          # unique port; must not clash with finalbot-cya
START_SERVICES="${START_SERVICES:-false}"

OMEGO_WALLET="omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this as root on the VPS." >&2
  exit 1
fi
[[ -d "$SOURCE_DIR" ]] || { echo "Code source not found: $SOURCE_DIR" >&2; exit 1; }
if [[ ! -f "$CYA_DIR/.env" ]]; then
  echo "finalbot-cya .env not found: $CYA_DIR/.env" >&2
  echo "Set CYA_DIR=/path/to/your/FinalBot-cya and re-run." >&2
  exit 1
fi
command -v node >/dev/null || { echo "node is required." >&2; exit 1; }
command -v npm  >/dev/null || { echo "npm is required."  >&2; exit 1; }

# 1) Sync code (never overwrite an existing instance .env), then seed .env from cya.
if [[ -d "$DEST" && -f "$DEST/.env" ]]; then
  cp "$DEST/.env" "$DEST/.env.backup-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$DEST"
if command -v rsync >/dev/null; then
  rsync -a --exclude .env --exclude node_modules --exclude dist \
    --exclude rust-sidecar/target --exclude .restore-backups \
    "$SOURCE_DIR/" "$DEST/"
else
  cp -a "$SOURCE_DIR/." "$DEST/"
  rm -rf "$DEST/node_modules" "$DEST/dist" "$DEST/rust-sidecar/target" "$DEST/.restore-backups"
fi
# Base env = the finalbot-cya instance's env ("same rest of ENV values").
cp "$CYA_DIR/.env" "$DEST/.env"

# 2) Apply only the requested overrides (keeps everything else from cya).
ENV_FILE="$DEST/.env" node - \
  "TRACKED_WALLETS=$OMEGO_WALLET" \
  "SIMULATION_MODE=true" \
  "SHADOW_EXECUTION_MODE=true" \
  "SIMULATION_SOL_BALANCE=10" \
  "SHADOW_LATENCY_MS=12" \
  "SHADOW_ENTRY_SLIPPAGE_PCT=2" \
  "SHADOW_EXIT_SLIPPAGE_PCT=3" \
  "SHADOW_TX_FEE_SOL=0" \
  "SHADOW_FAILURE_RATE_PCT=0" \
  "TAKE_PROFIT_PCT=30" \
  "MIRROR_SELLS=false" \
  "MIRROR_SELL_MODE=full" \
  "BUY_ONCE_PER_TOKEN=false" \
  "WEB_PORT=$WEB_PORT" \
  "TELEGRAM_BOT_TOKEN=" \
  "TELEGRAM_CHAT_ID=" \
  "TELEGRAM_BROADCAST_CHAT_ID=" <<'NODE'
const fs = require("fs");
const path = process.env.ENV_FILE;
const updates = {};
for (const pair of process.argv.slice(2)) {
  const eq = pair.indexOf("=");
  if (eq <= 0) continue;
  updates[pair.slice(0, eq)] = pair.slice(eq + 1);
}
const updateKeys = new Set(Object.keys(updates));
const kept = [];
for (const line of fs.readFileSync(path, "utf8").split(/\r?\n/)) {
  const t = line.trim();
  if (!t || t.startsWith("#")) { kept.push(line); continue; }
  const eq = t.indexOf("=");
  if (eq <= 0) { kept.push(line); continue; }
  if (!updateKeys.has(t.slice(0, eq).trim())) kept.push(line);
}
for (const [k, v] of Object.entries(updates)) kept.push(`${k}=${v}`);
const out = kept.join("\n");
fs.writeFileSync(path, out.endsWith("\n") ? out : out + "\n");
console.log(`Updated ${path}: ${Object.keys(updates).join(", ")}`);
NODE

# 3) Build.
echo "Installing deps / building in $DEST"
(cd "$DEST" && npm install && npm run build)

# 4) systemd unit.
cat > "/etc/systemd/system/finalbot-$NAME.service" <<EOF
[Unit]
Description=FinalBot $NAME (Omego shadow TP30 simulation)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEST
ExecStart=/usr/bin/npm run bot
Restart=always
RestartSec=3
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "finalbot-$NAME" >/dev/null
[[ "$START_SERVICES" == "true" ]] && systemctl restart "finalbot-$NAME" || true

cat <<EOF

Omego shadow TP30 simulation installed as finalbot-$NAME (WEB_PORT=$WEB_PORT).

Start (overnight):  systemctl start finalbot-$NAME
Stop (morning):     systemctl stop  finalbot-$NAME
Watch:              journalctl -u finalbot-$NAME -f
Trades:             $DEST/data/trades.jsonl
EOF
