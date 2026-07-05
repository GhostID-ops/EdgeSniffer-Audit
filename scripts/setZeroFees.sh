#!/usr/bin/env bash
# Zero out (or near-zero) tip & priority fees in a FinalBot .env.
# Sells are already fee-free; this touches the BUY + hot-sniper knobs only.
# Usage:  bash scripts/setZeroFees.sh [/path/to/.env]
# Default target: /root/bot/FinalBot-cya/.env  (the CyA VPS instance)
set -euo pipefail

ENV="${1:-/root/bot/FinalBot-cya/.env}"

# --- knobs -------------------------------------------------------------------
# Helius Sender's /fast endpoint requires a MINIMUM tip to accept a tx. At a true
# 0 tip, Jito bundles can't land (need a tip) AND Helius Sender rejects, so you
# fall through to plain RPC = slowest entries. Set HELIUS_TIP to 1000000 (0.001)
# to keep your best same-slot lander alive for ~$0.07/buy. Leave 0 for literal
# zero fees (RPC-fallback landing, slower).
HELIUS_TIP="${HELIUS_TIP:-0}"
# ----------------------------------------------------------------------------

[ -f "$ENV" ] || { echo "env not found: $ENV" >&2; exit 1; }
cp "$ENV" "$ENV.bak.$(date +%s)"

set_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '  %-42s = %s\n' "$key" "$val"
}

echo "Zeroing tip & priority fees in $ENV (backup saved alongside):"
# Jito tips -> 0 (Jito bundles won't land without a tip; that's intentional here)
set_kv TIP_LAMPORTS 0
set_kv RUST_HOT_SNIPER_JITO_TIP_LAMPORTS 0
# Helius tip -> HELIUS_TIP (0 = literal zero; 1000000 = keep Helius Sender landing fast)
set_kv RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS "$HELIUS_TIP"
# Priority fee -> 0 on both the normal and hot-sniper buy paths
set_kv PRIORITY_FEE_MICRO_LAMPORTS 0
set_kv RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS 0
# Kill the dynamic/oracle inflators so they can't push the zeros back up
set_kv DYNAMIC_PRIORITY_FEE_ENABLED false
set_kv DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS 0
set_kv JITO_TIP_ORACLE_ENABLED false
set_kv JITO_TIP_ORACLE_MIN_LAMPORTS 0
set_kv JITO_TIP_ORACLE_MAX_LAMPORTS 0

echo
echo "Done. Review, then restart:  systemctl restart finalbot-cya"
if [ "$HELIUS_TIP" = "0" ]; then
  echo "NOTE: literal 0 tips -> Jito & Helius Sender won't land; entries go via RPC fallback (slower)."
  echo "      For near-zero-but-fast, re-run with:  HELIUS_TIP=1000000 bash scripts/setZeroFees.sh $ENV"
  echo "      and set USE_JITO_DIRECT=false so the race doesn't waste time on dead Jito attempts."
fi
