#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/root/bot/FinalBot-cya}"
WALLET="${WALLET:-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq}"
LABEL="${LABEL:-Omego}"
SINCE="${SINCE:-$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)}"
UNTIL="${UNTIL:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
MAX_TXS="${MAX_TXS:-50000}"
ENTRY_LIMIT="${ENTRY_LIMIT:-1200}"
LEAD_LIMIT="${LEAD_LIMIT:-300}"
HORIZON_HOURS="${HORIZON_HOURS:-24}"
HELIUS_SLEEP_MS="${HELIUS_SLEEP_MS:-1500}"
PATH_AUDIT_SLEEP_MS="${PATH_AUDIT_SLEEP_MS:-1500}"

cd "$BOT_DIR"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$BOT_DIR/data/omego-weekly-$STAMP"
mkdir -p "$RUN_DIR"

cat > "$RUN_DIR/run-config.txt" <<EOF
BOT_DIR=$BOT_DIR
WALLET=$WALLET
LABEL=$LABEL
SINCE=$SINCE
UNTIL=$UNTIL
MAX_TXS=$MAX_TXS
ENTRY_LIMIT=$ENTRY_LIMIT
LEAD_LIMIT=$LEAD_LIMIT
HORIZON_HOURS=$HORIZON_HOURS
HELIUS_SLEEP_MS=$HELIUS_SLEEP_MS
PATH_AUDIT_SLEEP_MS=$PATH_AUDIT_SLEEP_MS
EOF

cat > "$RUN_DIR/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "== Omego weekly slow audit started at $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
echo "Run dir: $RUN_DIR"

echo
echo "== Build check =="
npm run build

echo
echo "== Source coverage audit =="
node scripts/auditSourceCoverage.mjs \
  --wallet="$WALLET" \
  --label="$LABEL" \
  --since="$SINCE" \
  --until="$UNTIL" \
  --max-txs="$MAX_TXS" \
  --sleep-ms="$HELIUS_SLEEP_MS" \
  --telemetry="data/execution-telemetry.jsonl" \
  --trades="data/trades.jsonl" \
  --out-json="$RUN_DIR/source-coverage.json" \
  --out-md="$RUN_DIR/source-coverage.md"

echo
echo "== Ledger audit =="
npx tsx src/walletLedgerAudit.ts "$WALLET" "$MAX_TXS" --cache
cp "data/wallet-ledger-audit-$WALLET.json" "$RUN_DIR/wallet-ledger-audit.json" 2>/dev/null || true
cp "data/wallet-ledger-audit-$WALLET.md" "$RUN_DIR/wallet-ledger-audit.md" 2>/dev/null || true

echo
echo "== Path audit =="
PATH_AUDIT_QUIET=true PATH_AUDIT_SLEEP_MS="$PATH_AUDIT_SLEEP_MS" \
  npx tsx src/pathBasedWalletAudit.ts "$WALLET" "$MAX_TXS" "$ENTRY_LIMIT" "$HORIZON_HOURS" "$LEAD_LIMIT"
cp "data/path-wallet-audit-$WALLET.json" "$RUN_DIR/path-wallet-audit.json" 2>/dev/null || true
cp "data/path-wallet-audit-$WALLET.md" "$RUN_DIR/path-wallet-audit.md" 2>/dev/null || true

echo
echo "== Quant audit =="
npx tsx src/walletQuantAudit.ts "$WALLET" "$MAX_TXS" "$ENTRY_LIMIT" > "$RUN_DIR/wallet-quant-audit.json"

echo
echo "== Package results =="
cd "$BOT_DIR/data"
tar -czf "$(basename "$RUN_DIR").tar.gz" "$(basename "$RUN_DIR")"
ls -lh "$(basename "$RUN_DIR").tar.gz"

echo "== Omego weekly slow audit finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
EOF

chmod +x "$RUN_DIR/run.sh"

(
  export BOT_DIR WALLET LABEL SINCE UNTIL MAX_TXS ENTRY_LIMIT LEAD_LIMIT HORIZON_HOURS HELIUS_SLEEP_MS PATH_AUDIT_SLEEP_MS RUN_DIR
  nohup bash "$RUN_DIR/run.sh" > "$RUN_DIR/weekly-audit.log" 2>&1 &
  echo $! > "$RUN_DIR/weekly-audit.pid"
)

echo "Started $LABEL weekly slow audit."
echo "Run dir: $RUN_DIR"
echo "PID: $(cat "$RUN_DIR/weekly-audit.pid")"
echo
echo "Watch:"
echo "tail -f \"$RUN_DIR/weekly-audit.log\""
echo
echo "Stop:"
echo "kill $(cat "$RUN_DIR/weekly-audit.pid")"
