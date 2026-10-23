#!/usr/bin/env bash
set -euo pipefail

# Start the main FinalBot copytrader in an interactive tmux terminal session.
# This is for the owner/operator, not for customer instances.
# It intentionally avoids touching finalbot-marcus, EdgeSniffer, or 2CQ services.

REPO="${FINALBOT_REPO:-/root/bot/FinalBot-cya}"
SESSION="${FINALBOT_TMUX_SESSION:-finalbot-cya-live}"

cd "$REPO"

echo "Repo: $REPO"
echo "tmux session: $SESSION"
echo

echo "Stopping only known main FinalBot services if they exist..."
for service in finalbot finalbot-cya; do
  if systemctl list-unit-files "${service}.service" >/dev/null 2>&1; then
    systemctl stop "$service" || true
  fi
done

echo "Checking for existing tmux session..."
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists."
  echo "Attach with: tmux attach -t $SESSION"
  exit 0
fi

existing_copytrade="$(
  pgrep -af '([t]sx .*src/copytrade.ts|[n]ode .*src/copytrade.ts|npm run bot)' || true
)"
if [[ -n "$existing_copytrade" ]]; then
  echo "Refusing to start: an existing direct copytrade process is already running."
  echo
  echo "$existing_copytrade"
  echo
  echo "If this is stale, stop it first. Do not start a second copytrader on the same wallet."
  exit 1
fi

echo "Building TypeScript before interactive start..."
npm run build

echo
echo "Starting interactive copytrader in tmux..."
tmux new -d -s "$SESSION" "cd '$REPO' && npm run bot"

echo
echo "Started."
echo "Attach: tmux attach -t $SESSION"
echo "Detach without stopping: Ctrl+b, then d"
echo "Stop from inside bot: q or Ctrl+C"
echo "Emergency panic hotkey: p"
