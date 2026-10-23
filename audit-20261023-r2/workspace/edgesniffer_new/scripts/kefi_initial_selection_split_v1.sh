#!/usr/bin/env bash
# Freeze the 40/20 selection split using master metadata only. Never opens a snapshot.
set -euo pipefail

MASTER_ROOT="${1:-/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/master-cohort-v1}"
MASTER="$MASTER_ROOT/master-manifest.json"
EXPECTED_MASTER_SHA="aa287917553c2035f46378bc72f3de6dd5d76e7fa0f0677c3e96022e64a2e106"
OUT="$MASTER_ROOT/initial-selection-split-v1.json"
TMP="$OUT.tmp.$$"

[[ -r "$MASTER" ]] || { echo "missing master manifest: $MASTER" >&2; exit 2; }
[[ "$(sha256sum "$MASTER" | awk '{print $1}')" == "$EXPECTED_MASTER_SHA" ]] || {
  echo "master manifest hash mismatch" >&2; exit 3;
}
[[ "$(jq -r '.total_snapshots' "$MASTER")" == "60" ]] || { echo "master count is not 60" >&2; exit 4; }
[[ "$(jq -r '.selection_analysis_started' "$MASTER")" == "false" ]] || { echo "selection analysis already marked started" >&2; exit 5; }
[[ ! -e "$OUT" ]] || { echo "refusing to overwrite frozen split: $OUT" >&2; exit 6; }

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq --arg created "$now" --arg master_sha "$EXPECTED_MASTER_SHA" '
  .snapshots as $all |
  if ($all|length) != 60 then error("expected exactly 60 snapshots") else . end |
  {
    schema: "KEFI_INITIAL_SELECTION_SPLIT_V1",
    created_at_utc: $created,
    master_manifest_path: input_filename,
    master_manifest_sha256: $master_sha,
    split_rule: "chronological master order; first 40 development, final 20 unopened_selection_holdout",
    development_count: 40,
    unopened_selection_holdout_count: 20,
    holdout_opened: false,
    selection_analysis_started: false,
    development_snapshots: $all[0:40],
    unopened_selection_holdout_snapshots: $all[40:60]
  }
' "$MASTER" > "$TMP"

dev="$(jq '.development_snapshots|length' "$TMP")"
hold="$(jq '.unopened_selection_holdout_snapshots|length' "$TMP")"
[[ "$dev" == "40" && "$hold" == "20" ]] || { rm -f "$TMP"; echo "invalid split counts" >&2; exit 7; }
mv -f "$TMP" "$OUT"
sha256sum "$OUT" | awk '{print $1 "  " $2}'
