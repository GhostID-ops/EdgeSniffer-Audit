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

node - <<'NODE'
const fs = require("fs");
const instances = [
  ["/root/bot/FinalBot-4bdk/.env", "3101"],
  ["/root/bot/FinalBot-5qn/.env", "3102"],
  ["/root/bot/FinalBot-cya/.env", "3103"],
];

for (const [envPath, port] of instances) {
  if (!fs.existsSync(envPath)) continue;
  let text = fs.readFileSync(envPath, "utf8");
  const updates = {
    BOT_CONTROL_API_ENABLED: "true",
    WEB_PORT: port,
  };
  for (const [key, value] of Object.entries(updates)) {
    const re = new RegExp(`^#?\\s*${key}=.*$`, "m");
    if (re.test(text)) text = text.replace(re, `${key}=${value}`);
    else text += `\n${key}=${value}`;
  }
  fs.writeFileSync(envPath, text);
  console.log(`Enabled control API for ${envPath} on port ${port}`);
}
NODE

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
systemctl restart finalbot-4bdk 2>/dev/null || true
systemctl restart finalbot-5qn 2>/dev/null || true
systemctl restart finalbot-cya 2>/dev/null || true
systemctl restart finalbot-control

echo "FinalBot control panel installed."
echo "Local URL on VPS: http://127.0.0.1:$PORT"
echo
echo "From Windows PowerShell, open an SSH tunnel:"
echo "  ssh -L $PORT:127.0.0.1:$PORT root@87.99.145.246"
echo
echo "Then open in your browser:"
echo "  http://127.0.0.1:$PORT"
