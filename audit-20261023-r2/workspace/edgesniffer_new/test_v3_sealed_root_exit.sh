#!/usr/bin/env bash
set -euo pipefail

base=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1
source_root="$base/autonomous-when-v3/scientific-20260930T073833Z"
binary=/root/bot/EdgeSniffer/rust-exact-ledger/target/release/yellowstone_v3_dual_observer
test_root=$(mktemp -d /tmp/v3-sealed-root-exit-XXXXXX)
cleanup() { rm -rf "$test_root" /tmp/v3-sealed-root-exit.out; }
trap cleanup EXIT

mkdir -p "$test_root/state"
cp "$source_root/state/global.json" "$test_root/state/global.json"
python3 - "$test_root/state/global.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
state["mode"] = "FAIL_CLOSED"
state["terminalReason"] = "TEST_SEALED_ROOT"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
PY

set -a
. /root/bot/EdgeSniffer/.env
set +a
set +e
timeout 10s env \
  YELLOWSTONE_V3_ROOT="$test_root" \
  YELLOWSTONE_FULL_META_ENDPOINT=https://grpc.va.shyft.to/ \
  "$binary" >/tmp/v3-sealed-root-exit.out 2>&1
status=$?
set -e

echo "sealed_root_clean_exit_status=$status"
cat /tmp/v3-sealed-root-exit.out
test "$status" -eq 0
