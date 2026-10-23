#!/usr/bin/env bash
set -euo pipefail

pilot_dir=/etc/8ycb-live-dust-pilot
state_dir=/var/lib/8ycb-live-dust-pilot
binary=/usr/local/lib/8ycb-sniper/eight-ycb-sniper
expected_binary_sha256=51c5dcf6e0095b9fe883e061eaae6cf849099f9f8cb5f7131fb07f4ca97f30d7
emergency_binary=/usr/local/lib/8ycb-live-dust-pilot/emergency-exit
expected_emergency_sha256=19bd4339d214ffb3bd6786f9892642ad06c28b92a963625215ba21d13af3659f

fail(){ echo "PILOT_PREFLIGHT_BLOCKED: $*" >&2; exit 1; }

if [ "${1:-}" = --offline ]; then
  test -x "$binary" || fail "production binary missing"
  test -x "$emergency_binary" || fail "emergency exit binary missing"
  emergency_actual=$(sha256sum "$emergency_binary" | awk '{print $1}')
  test "$emergency_actual" = "$expected_emergency_sha256" || fail "emergency exit binary hash changed"
  actual=$(sha256sum "$binary" | awk '{print $1}')
  test "$actual" = "$expected_binary_sha256" || fail "production binary hash changed"
  "$binary" safety-proof >/dev/null || fail "binary safety proof failed"
  test "${MAX_LIVE_ENTRIES:-}" = 1 || fail "one-entry cap missing"
  test "${MAX_OPEN_POSITIONS:-}" = 1 || fail "one-position cap missing"
  test "${POSITION_SIZE_LAMPORTS:-}" = 10000000 || fail "dust size changed"
  test "${JUPITER_FALLBACK_ENABLED:-}" = false || fail "Jupiter fallback must remain disabled"
  test "${RUST_ONLY_BUYS:-}" = true || fail "Rust-only buys required"
  test "${RUST_ONLY_SELLS:-}" = true || fail "Rust-only sells required"
  test "${FAST_SOURCE_REQUIRED_FOR_LIVE:-}" = false || fail "unsupported source admission mode"
  test -n "${PILOT_ABSOLUTE_MAX_TOTAL_DEBIT_LAMPORTS:-}" || fail "absolute debit cap missing"
  test "${PILOT_SETUP_RESERVE_LAMPORTS:-}" = 3000000 || fail "setup reserve changed"
  test "${BUY_SLIPPAGE_BPS:-}" = 3500 || fail "buy slippage changed"
  test "${MAX_NETWORK_FEE_LAMPORTS:-}" = 76010000 || fail "network fee cap changed"
  test "${DELIVERY_COST_CAP_LAMPORTS:-}" = 76000000 || fail "delivery cap changed"
  test "${CALIBRATION_PRIORITY_FEE_LAMPORTS:-}" = 75000000 || fail "priority fee cap changed"
  test "${CALIBRATION_JITO_TIP_LAMPORTS:-}" = 1000000 || fail "Jito tip cap changed"
  test "${JITO_TIP_CAP_LAMPORTS:-}" = 1000000 || fail "Jito tip ceiling changed"
  max_curve=$(( (POSITION_SIZE_LAMPORTS * (10000 + BUY_SLIPPAGE_BPS) + 9999) / 10000 ))
  max_total=$(( max_curve + (2 * MAX_NETWORK_FEE_LAMPORTS) + CALIBRATION_JITO_TIP_LAMPORTS + PILOT_SETUP_RESERVE_LAMPORTS ))
  test "$max_total" -le "$PILOT_ABSOLUTE_MAX_TOTAL_DEBIT_LAMPORTS" || fail "configured debit bound exceeded"
  test ! -e /etc/8ycb-execution-gate/market-submission.enabled || fail "global gate unexpectedly armed"
  if systemctl is-enabled --quiet 8ycb-sniper.service 2>/dev/null || systemctl is-active --quiet 8ycb-sniper.service 2>/dev/null; then
    fail "legacy sender is enabled or active"
  fi
  echo "PILOT_OFFLINE_PREFLIGHT_PASS"
  exit 0
fi

test -x "$binary" || fail "production binary missing"
test -x "$emergency_binary" || fail "emergency exit binary missing"
emergency_actual=$(sha256sum "$emergency_binary" | awk '{print $1}')
test "$emergency_actual" = "$expected_emergency_sha256" || fail "emergency exit binary hash changed"
actual=$(sha256sum "$binary" | awk '{print $1}')
test "$actual" = "$expected_binary_sha256" || fail "production binary hash changed"
"$binary" safety-proof >/dev/null || fail "binary safety proof failed"
test "${LIVE_DUST_PILOT_MODE:-false}" = true || fail "pilot mode not enabled"
test "${MODE:-}" = LIVE_CALIBRATION_ONE_SHOT || fail "unsupported sender mode"
test "${SUBMISSION_ENABLED:-}" = true || fail "submission is not explicitly configured for pilot"
test "${MAX_LIVE_ENTRIES:-}" = 1 || fail "one-entry cap missing"
test "${MAX_OPEN_POSITIONS:-}" = 1 || fail "one-position cap missing"
test "${POSITION_SIZE_LAMPORTS:-}" = 10000000 || fail "dust size changed"
test "${JUPITER_FALLBACK_ENABLED:-}" = false || fail "Jupiter fallback must remain disabled"
test "${RUST_ONLY_BUYS:-}" = true || fail "Rust-only buys required"
test "${RUST_ONLY_SELLS:-}" = true || fail "Rust-only sells required"
test "${FAST_SOURCE_REQUIRED_FOR_LIVE:-}" = false || fail "unsupported source admission mode"
test -n "${PILOT_ABSOLUTE_MAX_TOTAL_DEBIT_LAMPORTS:-}" || fail "absolute debit cap missing"
test "${PILOT_SETUP_RESERVE_LAMPORTS:-}" = 3000000 || fail "setup reserve changed"
test "${BUY_SLIPPAGE_BPS:-}" = 3500 || fail "buy slippage changed"
test "${MAX_NETWORK_FEE_LAMPORTS:-}" = 76010000 || fail "network fee cap changed"
test "${DELIVERY_COST_CAP_LAMPORTS:-}" = 76000000 || fail "delivery cap changed"
test "${CALIBRATION_PRIORITY_FEE_LAMPORTS:-}" = 75000000 || fail "priority fee cap changed"
test "${CALIBRATION_JITO_TIP_LAMPORTS:-}" = 1000000 || fail "Jito tip cap changed"
test "${JITO_TIP_CAP_LAMPORTS:-}" = 1000000 || fail "Jito tip ceiling changed"
max_curve=$(( (POSITION_SIZE_LAMPORTS * (10000 + BUY_SLIPPAGE_BPS) + 9999) / 10000 ))
max_total=$(( max_curve + (2 * MAX_NETWORK_FEE_LAMPORTS) + CALIBRATION_JITO_TIP_LAMPORTS + PILOT_SETUP_RESERVE_LAMPORTS ))
test "$max_total" -le "$PILOT_ABSOLUTE_MAX_TOTAL_DEBIT_LAMPORTS" || fail "configured debit bound exceeded"
test "${PILOT_EXIT_PATH_PROVEN:-}" = true || fail "exit path has not been proven"
test -f "$PILOT_ARM_FILE" || fail "pilot arm file absent"
test "$(cat "$PILOT_ARM_FILE")" = 8YCB_LIVE_DUST_PILOT_ARMED_V1 || fail "pilot arm token invalid"
test -f /etc/8ycb-execution-gate/market-submission.enabled || fail "global submission gate closed"
test "$(cat /etc/8ycb-execution-gate/market-submission.enabled)" = ARMED_FOR_8YCB_REAL_MARKET_SUBMISSION_V1 || fail "global submission token invalid"
test -f "$PILOT_STATUS_PATH" || fail "pilot status missing"
/usr/bin/python3 - "$PILOT_STATUS_PATH" <<'PY' || fail "pilot persistent latch is consumed or disarmed"
import json,sys
r=json.load(open(sys.argv[1]))
if r.get('armed') is not True or int(r.get('submission_count',0)) != 0:
    raise SystemExit(1)
PY
if systemctl is-enabled --quiet 8ycb-sniper.service 2>/dev/null || systemctl is-active --quiet 8ycb-sniper.service 2>/dev/null; then
  fail "legacy sender is enabled or active"
fi
echo "PILOT_PREFLIGHT_PASS"
