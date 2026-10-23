#!/usr/bin/env bash
set -euo pipefail
BASE=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1
ROOT="$1"
TRACE="$ROOT/adapter-trace"
LANE="$ROOT/lane"
set -a
source /root/bot/EdgeSniffer/.env
set +a
export YELLOWSTONE_V3_ROOT="$ROOT"
export YELLOWSTONE_FULL_META_ENDPOINT=https://grpc.va.shyft.to/
export PYTHONPATH=/root/bot/EdgeSniffer/scripts${PYTHONPATH:+:$PYTHONPATH}
export V3_ADAPTER_EXIT_RECEIPT="$TRACE/adapter-top-level-exit.json"
/root/bot/EdgeSniffer/rust-exact-ledger/target/release/yellowstone_v3_dual_observer >"$ROOT/source.log" 2>&1 &
SOURCE_PID=$!
trap 'kill "$SOURCE_PID" 2>/dev/null || true; wait "$SOURCE_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  [[ -s "$ROOT/health.json" ]] && break
  kill -0 "$SOURCE_PID" 2>/dev/null || { cat "$ROOT/source.log" >&2; exit 1; }
  sleep 1
done
[[ -s "$ROOT/health.json" ]] || { echo 'V3_SOURCE_HEALTH_NOT_READY' >&2; exit 1; }
/usr/bin/python3 /root/v3_adapter_diagnostic_wrapper.py --trace "$TRACE" --health "$LANE/health.json" -- \
  /usr/bin/python3 "$BASE/autonomous-when-v3/source/twoCqAutonomousWhenV3.py" \
  --root "$LANE" --normalizer /root/bot/EdgeSniffer/rust-exact-ledger/target/release/yellowstone_state_plane_normalizer \
  --fingerprint "$BASE/fingerprint-v2-development-v1/frozen-fingerprint.json" --env /root/bot/EdgeSniffer/.env \
  --spec "$BASE/autonomous-when-v1/spec.json" --boundary "$LANE/boundary.json"
