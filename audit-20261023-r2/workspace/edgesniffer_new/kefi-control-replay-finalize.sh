#!/usr/bin/env bash
set -euo pipefail
K=/root/kefi-work
O="$K/out/independent-v1"
R="$O/control-replay"
exec >>"$O/control-replay-finalize.log" 2>&1
mkdir -p "$R"
if test -e "$R/replay-120s-v1.jsonl"; then
  mv -f "$R/replay-120s-v1.jsonl" "$R/replay-120s-v1.incomplete.jsonl"
fi
rm -f "$R/replay-120s-v1.summary.json"

"$K/bin/kefi_prospective_discovery_v5" --replay \
  "$K" "$O/matched-controls-v1.jsonl" \
  "$R/replay-120s-v2.jsonl" \
  "$R/replay-120s-v2.summary.json" \
  2026/10/05/03 2026/10/05/04 2026/10/05/05 2026/10/05/06 2026/10/05/07 \
  2026/10/05/08 2026/10/05/09 2026/10/05/10 2026/10/05/11 2026/10/05/12

python3 "$K/bin/kefi_filter_unsupported_controls.py" \
  --controls "$O/matched-controls-v1.jsonl" \
  --summary "$R/replay-120s-v2.summary.json" \
  --replay "$R/replay-120s-v2.jsonl" \
  --controls-out "$O/matched-controls-v2.complete.jsonl" \
  --replay-out "$R/replay-120s-v2.complete.jsonl" \
  --manifest "$O/matched-controls-v2.filter-manifest.json"

python3 "$K/bin/kefi_exit_analysis.py" \
  --entries "$O/matched-controls-v2.complete.jsonl" \
  --replay "$R/replay-120s-v2.complete.jsonl" \
  --output "$O/control-exit-analysis-v2.json"

python3 "$K/bin/kefi_independent_comparison.py" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$O/matched-controls-v2.complete.jsonl" \
  --selected-exits "$K/out/indexed-v1/exit-analysis-v4.json" \
  --control-exits "$O/control-exit-analysis-v2.json" \
  --output "$O/independent-comparison-v2.json"

python3 "$K/bin/kefi_independent_receipt.py" \
  --manifest "$O/candidate-manifest-v1.json" \
  --controls-manifest "$O/matched-controls-v2.filter-manifest.json" \
  --comparison "$O/independent-comparison-v2.json" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$O/matched-controls-v2.complete.jsonl" \
  --control-replay "$R/replay-120s-v2.complete.jsonl" \
  --output "$O/independent-comparison-receipt-v2.json"
echo "finished_utc=$(date -u +%FT%H:%M:%SZ)"
