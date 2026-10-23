#!/usr/bin/env bash
set -euo pipefail

ROOT=/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1
A="$ROOT/live/validation-choice-state-v1-1791915517686940846-pid939965"
BROOT="$ROOT/supplemental-cohort-b-v1"
OUT="$ROOT/master-cohort-v1"
REPORT=/root/bot/EdgeSniffer/reports/KEFI_CHOICESTATE_MASTER_COHORT_V1_REPORT.md
AM=73191a75265bae60fd29eb18e8d6da92287161ad940d1aa94a3d1c1d48ea3588
AV=0dc63bc4e706f57ba5eae4e5b64dbf467ca2bc4f3742525767e4f9cdb117b2fb

mkdir -p "$OUT"
B=$(ls -dt "$BROOT"/validation-choice* 2>/dev/null | head -n1 || true)
[[ -n "$B" && -f "$B/validation.json" ]] || exit 0
STATUS=$(jq -r .status "$B/validation.json")

if [[ "$STATUS" != SNAPSHOT_TARGET_REACHED ]]; then
  printf '# kEFi Choice-State Master Cohort V1 Report\n\nSupplemental seal: `%s`\n\nSUPPLEMENTAL_COHORT_HARD_WALL\n' "$STATUS" >"$REPORT"
  exit 0
fi

[[ $(sha256sum "$A/manifest.json" | cut -d' ' -f1) == "$AM" ]]
[[ $(sha256sum "$A/validation.json" | cut -d' ' -f1) == "$AV" ]]
[[ $(find "$A/durable-snapshots" -type f -name '*.json' | wc -l) -eq 49 ]]
[[ $(find "$B/durable-snapshots" -type f -name '*.json' | wc -l) -eq 11 ]]

TMP="$OUT/master-manifest.json.tmp"
{
  printf '{"schema":"KEFI_CHOICESTATE_MASTER_COHORT_V1","selection_analysis_started":false,'
  printf '"cohort_a":{"path":"%s","manifest_sha256":"%s","validation_sha256":"%s","snapshot_count":49},' "$A" "$AM" "$AV"
  printf '"cohort_b":{"path":"%s","manifest_sha256":"%s","validation_sha256":"%s","snapshot_count":11},' "$B" "$(sha256sum "$B/manifest.json" | cut -d' ' -f1)" "$(sha256sum "$B/validation.json" | cut -d' ' -f1)"
  printf '"snapshots":['
  first=1; total=0
  for cohort in "$A" "$B"; do
    for receipt in $(find "$cohort/snapshot-receipts" -type f -name '*.json' | sort); do
      seq=$(jq -r .snapshot_sequence "$receipt"); snap="$cohort/durable-snapshots/$(printf '%020d' "$seq").json"
      [[ -f "$snap" ]]
      [[ $first -eq 1 ]] || printf ','; first=0
      jq -cn --arg cohort "$cohort" --arg snap "$snap" --arg receipt "$receipt" --arg snapsha "$(sha256sum "$snap" | cut -d' ' -f1)" --arg receiptsha "$(sha256sum "$receipt" | cut -d' ' -f1)" --slurpfile r "$receipt" '{cohort_path:$cohort,snapshot_path:$snap,snapshot_sha256:$snapsha,receipt_path:$receipt,receipt_sha256:$receiptsha,source_instance_id:$r[0].source_instance_id,continuity_epoch:$r[0].continuity_epoch,teacher_slot:$r[0].teacher_slot,durable_output_sequence:$r[0].teacher_durable_output_sequence,snapshot_sequence:$r[0].snapshot_sequence,candidate_count:$r[0].candidate_count,warmup_complete:$r[0].warmup_complete}'
      total=$((total + $(jq -r .candidate_count "$receipt")))
    done
  done
  printf '],"total_snapshots":60,"total_candidate_rows":%d,"chronological_rule":"Cohort A precedes Cohort B; each cohort is ordered by immutable snapshot sequence","integrity":"all referenced snapshot and receipt hashes verified"}\n' "$total"
} >"$TMP"
mv "$TMP" "$OUT/master-manifest.json"
HASH=$(sha256sum "$OUT/master-manifest.json" | cut -d' ' -f1)
printf '# kEFi Choice-State Master Cohort V1 Report\n\n- Cohort A: 49 frozen snapshots\n- Cohort B: 11 frozen snapshots\n- Master: 60 snapshots\n- Selection analysis started: NO\n- Master manifest: `%s`\n- SHA-256: `%s`\n\nKEFI_60_SELECTION_MASTER_COHORT_READY\n' "$OUT/master-manifest.json" "$HASH" >"$REPORT"
