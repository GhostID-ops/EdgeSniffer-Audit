#!/usr/bin/env bash
# Record a failed one-shot selection evaluation without reading any snapshot.
set -euo pipefail
ROOT="${1:-/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/master-cohort-v1}"
OUT="$ROOT/holdout-failclosed-v1.json"
MARKER="$ROOT/holdout-opened-v1.json"
[[ -r "$MARKER" && ! -e "$OUT" ]] || { echo "missing opened marker or existing failure receipt" >&2; exit 2; }
jq -n --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg marker "$MARKER" --arg marker_sha "$(sha256sum "$MARKER"|awk '{print $1}')" '{schema:"KEFI_INITIAL_SELECTION_HOLDOUT_FAILCLOSED_V1",recorded_at_utc:$at,holdout_opened_marker_path:$marker,holdout_opened_marker_sha256:$marker_sha,cohort_status:"INVALIDATED_FOR_SELECTION_EVALUATION",evaluation_completed:false,partial_or_interim_metrics_exposed:false,reason:"NEW_TO_OBSERVED_WALLET target membership was not persisted across observer epoch A/B; evaluator rejected a holdout chosen mint already observed in Cohort A rather than silently changing the frozen primary universe",required_disposition:"do not rerun, score, tune, reinterpret, or bridge this 40/20 master cohort; future cohort requires durable wallet-observation history across collection epochs",outcomes_or_pnl_accessed:false}' > "$OUT.tmp.$$"
mv "$OUT.tmp.$$" "$OUT"
sha256sum "$OUT"
