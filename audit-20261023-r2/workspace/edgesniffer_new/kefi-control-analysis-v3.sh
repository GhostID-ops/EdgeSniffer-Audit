#!/usr/bin/env bash
set -euo pipefail
K=/root/kefi-work
O="$K/out/independent-v1"
R="$O/control-replay"
exec >>"$O/control-analysis-v3.log" 2>&1
python3 "$K/bin/kefi_exit_analysis.py" \
  --entries "$O/matched-controls-v2.complete.jsonl" \
  --replay "$R/replay-120s-v2.complete.jsonl" \
  --output "$O/control-exit-analysis-v2.json" || true
python3 "$K/bin/kefi_filter_unsupported_controls.py" \
  --controls "$O/matched-controls-v2.complete.jsonl" \
  --summary "$R/replay-120s-v2.summary.json" \
  --invalid-report "$O/control-exit-analysis-v2.json" \
  --replay "$R/replay-120s-v2.complete.jsonl" \
  --controls-out "$O/matched-controls-v3.complete.jsonl" \
  --replay-out "$R/replay-120s-v3.complete.jsonl" \
  --manifest "$O/matched-controls-v3.filter-manifest.json"
python3 "$K/bin/kefi_exit_analysis.py" \
  --entries "$O/matched-controls-v3.complete.jsonl" \
  --replay "$R/replay-120s-v3.complete.jsonl" \
  --output "$O/control-exit-analysis-v3.json"
python3 "$K/bin/kefi_independent_comparison.py" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$O/matched-controls-v3.complete.jsonl" \
  --selected-exits "$K/out/indexed-v1/exit-analysis-v4.json" \
  --control-exits "$O/control-exit-analysis-v3.json" \
  --output "$O/independent-comparison-v3.json"
python3 "$K/bin/kefi_independent_receipt.py" \
  --manifest "$O/candidate-manifest-v1.json" \
  --controls-manifest "$O/matched-controls-v3.filter-manifest.json" \
  --comparison "$O/independent-comparison-v3.json" \
  --selected "$K/out/enriched-v1.jsonl" \
  --controls "$O/matched-controls-v3.complete.jsonl" \
  --control-replay "$R/replay-120s-v3.complete.jsonl" \
  --output "$O/independent-comparison-receipt-v3.json"
echo "analysis_v3_finished=$(date -u +%FT%H:%M:%SZ)"
