#!/usr/bin/env bash
set -euo pipefail

# Drain one sealed canonical source through the frozen, outcome-blind shadow
# consumer.  Each child commits atomically; this driver stops at the first
# failure and never starts a new capture or alters the sealed source journal.
if [[ $# -ne 2 ]]; then
  echo "usage: $0 SOURCE_ROOT OUTPUT_ROOT" >&2
  exit 64
fi

SOURCE_ROOT=$1
OUTPUT_ROOT=$2
MAX_BATCHES=${KEFI_SELECTION_SHADOW_MAX_BATCHES:-64}
MAX_INPUTS=${KEFI_SELECTION_SHADOW_MAX_INPUTS:-65536}
BIN=${KEFI_SELECTION_SHADOW_BINARY:-/root/bot/EdgeSniffer/rust-sidecar/target/release/kefi_frozen_selection_shadow_consumer_v1}

if [[ ! -f /etc/edgesniffer/kefi-choice-state-observer-v1-live.env ]]; then
  echo "pool identity environment missing" >&2
  exit 65
fi
set -a
source /etc/edgesniffer/kefi-choice-state-observer-v1-live.env
set +a

read -r SOURCE_INSTANCE CONTINUITY_EPOCH TRACKED_WALLET < <(
  python3 - "$SOURCE_ROOT" "$OUTPUT_ROOT" <<'PY'
import json, sys
source, output = sys.argv[1:]
binding = json.load(open(f"{output}/run-binding.json"))
if binding["canonicalRoot"] != source:
    raise SystemExit("output binding source root mismatch")
print(binding["sourceInstanceId"], binding["continuityEpoch"], binding["trackedWallet"])
PY
)

export KEFI_SELECTION_SHADOW_CANONICAL_ROOT="$SOURCE_ROOT"
export KEFI_SELECTION_SHADOW_SOURCE_INSTANCE_ID="$SOURCE_INSTANCE"
export KEFI_SELECTION_SHADOW_CONTINUITY_EPOCH="$CONTINUITY_EPOCH"
export KEFI_TRACKED_WALLET="$TRACKED_WALLET"
export KEFI_SELECTION_SHADOW_OUTPUT_ROOT="$OUTPUT_ROOT"
export KEFI_SELECTION_SHADOW_MAX_INPUTS="$MAX_INPUTS"

checkpoint() {
  python3 - "$OUTPUT_ROOT/consumer-checkpoint.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["durableOutputSequence"])
PY
}

for batch in $(seq 1 "$MAX_BATCHES"); do
  before=$(checkpoint)
  log="$OUTPUT_ROOT/materializer-auto-$(printf '%04d' "$batch").log"
  started=$(date +%s)
  "$BIN" >"$log" 2>&1
  after=$(checkpoint)
  elapsed=$(( $(date +%s) - started ))
  printf '{"schema":"KEFI_FROZEN_SELECTION_MATERIALIZER_BATCH_V1","batch":%s,"beforeSequence":"%s","afterSequence":"%s","elapsedSeconds":%s}\n' \
    "$batch" "$before" "$after" "$elapsed" | tee -a "$OUTPUT_ROOT/materializer-auto-summary.jsonl"
  if [[ "$after" == "$before" ]]; then
    exit 0
  fi
done

echo "materializer reached configured batch limit without draining source" >&2
exit 66
