#!/usr/bin/env bash
set -euo pipefail

BOT_ROOT="${BOT_ROOT:-/root/bot}"
SOURCE_DIR="${SOURCE_DIR:-$BOT_ROOT/FinalBot}"
NAME="${NAME:-cya-mc50-100-nofee}"
DEST_DIR="${DEST_DIR:-$BOT_ROOT/FinalBot-$NAME}"
SERVICE_NAME="finalbot-$NAME"
START_SERVICE="${START_SERVICE:-false}"

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
  echo "Fill the main .env first so private/API keys can be copied into the new instance." >&2
  exit 1
fi

command -v node >/dev/null || { echo "node is required." >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required." >&2; exit 1; }

if [[ ! -d "$DEST_DIR" ]]; then
  echo "Creating $DEST_DIR from $SOURCE_DIR" >&2
  if command -v rsync >/dev/null; then
    rsync -a \
      --exclude node_modules \
      --exclude dist \
      --exclude rust-sidecar/target \
      --exclude .restore-backups \
      "$SOURCE_DIR/" "$DEST_DIR/"
  else
    cp -a "$SOURCE_DIR" "$DEST_DIR"
    rm -rf "$DEST_DIR/node_modules" "$DEST_DIR/dist" "$DEST_DIR/rust-sidecar/target" "$DEST_DIR/.restore-backups"
  fi
  cp "$SOURCE_DIR/.env" "$DEST_DIR/.env"
else
  echo "Updating code in existing $DEST_DIR" >&2
  cp "$DEST_DIR/.env" "$DEST_DIR/.env.backup-before-$NAME-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
  if command -v rsync >/dev/null; then
    rsync -a \
      --exclude .env \
      --exclude node_modules \
      --exclude dist \
      --exclude rust-sidecar/target \
      --exclude .restore-backups \
      "$SOURCE_DIR/" "$DEST_DIR/"
  fi
  if [[ ! -f "$DEST_DIR/.env" ]]; then
    cp "$SOURCE_DIR/.env" "$DEST_DIR/.env"
  fi
fi

echo "Applying CyA MC 50k-100k no-fee preset in $DEST_DIR"
(cd "$DEST_DIR" && node scripts/upsertEnv.mjs --from-file .env.cya-vps-mc50-100-no-fees)

echo "Installing dependencies and building in $DEST_DIR"
(cd "$DEST_DIR" && npm install && npm run build && npm run rust-sidecar:jito-build)

cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=FinalBot CyA MC 50k-100k no-fee live instance
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEST_DIR
ExecStart=/usr/bin/npm run bot
Restart=always
RestartSec=3
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null

if [[ "$START_SERVICE" == "true" ]]; then
  systemctl restart "$SERVICE_NAME"
fi

cat <<EOF

CyA MC 50k-100k no-fee instance installed.

Folder:
  $DEST_DIR

Service:
  $SERVICE_NAME

Start:
  systemctl start $SERVICE_NAME

Stop:
  systemctl stop $SERVICE_NAME

Watch:
  journalctl -u $SERVICE_NAME -f

Verify env:
  cd $DEST_DIR
  npm run vps:doctor
  grep -nE '^(TRACKED_WALLETS|MIN_BUY_SOL|MAX_BUY_SOL|MAX_OPEN_POSITIONS|PREBUY_MIN_MARKET_CAP_USD|PREBUY_MAX_MARKET_CAP_USD|TIP_LAMPORTS|PRIORITY_FEE_MICRO_LAMPORTS|DYNAMIC_PRIORITY_FEE_ENABLED|JITO_TIP_ORACLE_ENABLED|JUPITER_FALLBACK_ENABLED|RUST_ONLY_BUYS|RUST_ONLY_SELLS)=' .env
EOF
