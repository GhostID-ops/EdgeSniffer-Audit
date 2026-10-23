#!/usr/bin/env bash
set -euo pipefail

# Install a limited terminal workflow for Marcus.
#
# Security model:
# - Marcus does NOT get root.
# - Marcus does NOT get direct access to /root/bot/FinalBot-cya/.env.
# - Marcus can control only the finalbot-marcus systemd service.
# - Marcus can view only his service logs through journalctl.
#
# Usage as root on VPS:
#   cd /root/bot/FinalBot-cya
#   bash scripts/installMarcusTerminalAccess.sh
#   passwd marcusbot

USERNAME="${MARCUS_SSH_USER:-marcusbot}"
SERVICE="${MARCUS_SERVICE:-finalbot-marcus}"
PORT="${MARCUS_PORT:-3102}"
PUBLIC_URL="${MARCUS_PUBLIC_URL:-https://marcus.yourcrypto.dev/friend-simple}"
WRAPPER="/usr/local/bin/finalbot"
SUDOERS="/etc/sudoers.d/${USERNAME}-finalbot"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this as root on the VPS."
  exit 1
fi

if ! id "$USERNAME" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "FinalBot Marcus" "$USERNAME"
fi

# Lets the user read journal logs without sudo on Ubuntu systems that support this group.
if getent group systemd-journal >/dev/null 2>&1; then
  usermod -aG systemd-journal "$USERNAME"
fi

cat > "$WRAPPER" <<SH
#!/usr/bin/env bash
set -euo pipefail

SERVICE="$SERVICE"
PORT="$PORT"
PUBLIC_URL="$PUBLIC_URL"

usage() {
  cat <<EOF
FinalBot Marcus terminal

Commands:
  finalbot status       Show whether the bot service is running
  finalbot logs         Show recent logs
  finalbot follow       Live-follow logs
  finalbot restart      Restart Marcus bot service
  finalbot stop         Stop Marcus bot service
  finalbot start        Start Marcus bot service
  finalbot check        Local HTTP health check
  finalbot url          Print Marcus UI link

EOF
}

cmd="\${1:-menu}"
case "\$cmd" in
  status)
    sudo /bin/systemctl status "\$SERVICE" --no-pager -l
    ;;
  logs)
    journalctl -u "\$SERVICE" -n "\${2:-160}" --no-pager -l
    ;;
  follow)
    journalctl -u "\$SERVICE" -f -l
    ;;
  restart)
    sudo /bin/systemctl restart "\$SERVICE"
    echo "Restarted \$SERVICE"
    ;;
  stop)
    sudo /bin/systemctl stop "\$SERVICE"
    echo "Stopped \$SERVICE"
    ;;
  start)
    sudo /bin/systemctl start "\$SERVICE"
    echo "Started \$SERVICE"
    ;;
  check)
    curl -I "http://127.0.0.1:\$PORT/friend-simple"
    ;;
  url)
    echo "\$PUBLIC_URL"
    ;;
  menu|help|-h|--help)
    usage
    ;;
  *)
    usage
    echo "Unknown command: \$cmd"
    exit 2
    ;;
esac
SH
chmod 0755 "$WRAPPER"

cat > "$SUDOERS" <<EOF
$USERNAME ALL=(root) NOPASSWD: /bin/systemctl status $SERVICE --no-pager -l
$USERNAME ALL=(root) NOPASSWD: /bin/systemctl start $SERVICE
$USERNAME ALL=(root) NOPASSWD: /bin/systemctl stop $SERVICE
$USERNAME ALL=(root) NOPASSWD: /bin/systemctl restart $SERVICE
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

PROFILE="/home/$USERNAME/.profile"
if ! grep -q "FinalBot Marcus terminal" "$PROFILE" 2>/dev/null; then
  cat >> "$PROFILE" <<'EOF'

echo
echo "FinalBot Marcus terminal:"
echo "  finalbot status   - service status"
echo "  finalbot follow   - live logs"
echo "  finalbot restart  - restart bot"
echo "  finalbot url      - UI link"
echo
EOF
fi

chown "$USERNAME:$USERNAME" "$PROFILE"

echo "Installed limited terminal access for $USERNAME."
echo
echo "Set/reset his SSH password:"
echo "  passwd $USERNAME"
echo
echo "Marcus SSH command:"
echo "  ssh $USERNAME@87.99.145.246"
echo
echo "After login:"
echo "  finalbot status"
echo "  finalbot follow"
echo "  finalbot restart"
