#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/root/bot/FinalBot-cya}"
CONFIG="${1:-data/wallet-audit-wallets.json}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$BOT_DIR/data/wallet-audits/run-$STAMP"

cd "$BOT_DIR"
mkdir -p "$RUN_DIR"
nohup npm run audit:wallet-cohort -- "$CONFIG" "$RUN_DIR" > "$RUN_DIR/runner.log" 2>&1 &
PID=$!
echo "$PID" > "$RUN_DIR/runner.pid"
printf '%s\n' "$RUN_DIR" > "$BOT_DIR/data/wallet-audits/latest-run.txt"

echo "Wallet cohort audit started."
echo "runDir=$RUN_DIR"
echo "pid=$PID"
echo "watch: tail -f \"$RUN_DIR/runner.log\""
echo "state: cat \"$RUN_DIR/state.json\""
echo "stop: kill $PID"