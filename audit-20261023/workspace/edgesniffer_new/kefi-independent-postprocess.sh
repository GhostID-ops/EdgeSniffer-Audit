#!/usr/bin/env bash
set -euo pipefail

K=/root/kefi-work
O="$K/out/independent-v1"
S="$O/shards"
LOG="$O/postprocess.log"
exec >>"$LOG" 2>&1

echo "postprocess_wait_started=$(date -u +%FT%H:%M:%SZ)"
while test -e "$O/RUNNING"; do sleep 20; done
echo "candidate_scan_finished=$(date -u +%FT%H:%M:%SZ)"

python3 "$K/bin/kefi_independent_candidate_merge.py" \
  "$S" "$O/candidate-manifest-v1.json" "$O/candidate-manifest-v1.summary.json" \
  --expected 03 04 05 06 07 08 09 10 11 12

python3 "$K/bin/kefi_control_selector.py" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$S/03.jsonl" "$S/04.jsonl" "$S/05.jsonl" "$S/06.jsonl" \
  "$S/07.jsonl" "$S/08.jsonl" "$S/09.jsonl" "$S/10.jsonl" "$S/11.jsonl" "$S/12.jsonl" \
  --output "$O/matched-controls-v1.jsonl" \
  --manifest "$O/matched-controls-v1.manifest.json" \
  --controls-per-selected 3

mkdir -p "$O/control-replay"
"$K/bin/kefi_prospective_discovery_v4" --replay \
  "$K" "$O/matched-controls-v1.jsonl" \
  "$O/control-replay/replay-120s-v1.jsonl" \
  "$O/control-replay/replay-120s-v1.summary.json" \
  2026/10/05/03 2026/10/05/04 2026/10/05/05 2026/10/05/06 2026/10/05/07 \
  2026/10/05/08 2026/10/05/09 2026/10/05/10 2026/10/05/11 2026/10/05/12

python3 "$K/bin/kefi_exit_analysis.py" \
  --entries "$O/matched-controls-v1.jsonl" \
  --replay "$O/control-replay/replay-120s-v1.jsonl" \
  --output "$O/control-exit-analysis-v1.json"

python3 "$K/bin/kefi_independent_comparison.py" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$O/matched-controls-v1.jsonl" \
  --selected-exits "$K/out/indexed-v1/exit-analysis-v4.json" \
  --control-exits "$O/control-exit-analysis-v1.json" \
  --output "$O/independent-comparison-v1.json"

python3 "$K/bin/kefi_independent_receipt.py" \
  --manifest "$O/candidate-manifest-v1.json" \
  --controls-manifest "$O/matched-controls-v1.manifest.json" \
  --comparison "$O/independent-comparison-v1.json" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$O/matched-controls-v1.jsonl" \
  --control-replay "$O/control-replay/replay-120s-v1.jsonl" \
  --output "$O/independent-comparison-receipt-v1.json"

echo "postprocess_finished=$(date -u +%FT%H:%M:%SZ)"
