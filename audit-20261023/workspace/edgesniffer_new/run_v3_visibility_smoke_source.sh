#!/usr/bin/env bash
set -euo pipefail

lab_root="$1"
duration_seconds="$2"

set -a
. /root/bot/EdgeSniffer/.env
set +a

export YELLOWSTONE_FULL_META_ENDPOINT="https://grpc.va.shyft.to/"
export YELLOWSTONE_V3_ROOT="$lab_root"

exec timeout "$duration_seconds" \
  /root/bot/EdgeSniffer/rust-exact-ledger/target/release/yellowstone_v3_dual_observer_visibility_repair_v1
