#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/root/bot/FinalBot}"
PORT="${VPS_CONTROL_PORT:-8787}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this as root on the VPS." >&2
  exit 1
fi

if [[ ! -d "$BOT_DIR" ]]; then
  echo "Bot directory not found: $BOT_DIR" >&2
  exit 1
fi

cd "$BOT_DIR"
npm install
npm run build

cat > /etc/systemd/system/finalbot-control.service <<EOF
[Unit]
Description=FinalBot local VPS control panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/npm run vps:control
Restart=always
RestartSec=3
Environment=NODE_ENV=production
Environment=VPS_CONTROL_PORT=$PORT

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable finalbot-control
systemctl restart finalbot-control

echo "FinalBot control panel installed."
echo "Local URL on VPS: http://127.0.0.1:$PORT"
echo
echo "From Windows PowerShell, open an SSH tunnel:"
echo "  ssh -L $PORT:127.0.0.1:$PORT root@87.99.145.246"
echo
echo "Then open in your browser:"
echo "  http://127.0.0.1:$PORT"
