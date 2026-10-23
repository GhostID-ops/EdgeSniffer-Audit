#!/usr/bin/env bash
# Integrity-only verification. Reads manifests and receipts plus hashes snapshots;
# never parses snapshot candidate/selection payloads.
set -euo pipefail

ROOT="${1:-/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/master-cohort-v1}"
MASTER="$ROOT/master-manifest.json"
OUT="$ROOT/master-integrity-v1.json"
TMP="$OUT.tmp.$$"
EXPECTED_MASTER_SHA="aa287917553c2035f46378bc72f3de6dd5d76e7fa0f0677c3e96022e64a2e106"

[[ -f "$MASTER" ]] || { echo "missing master" >&2; exit 2; }
[[ "$(sha256sum "$MASTER" | awk '{print $1}')" == "$EXPECTED_MASTER_SHA" ]] || { echo "master hash mismatch" >&2; exit 3; }
[[ "$(jq -r '.total_snapshots' "$MASTER")" == 60 ]] || { echo "not 60 snapshots" >&2; exit 4; }
[[ "$(jq -r '.cohort_a.snapshot_count' "$MASTER")" == 49 && "$(jq -r '.cohort_b.snapshot_count' "$MASTER")" == 11 ]] || { echo "wrong A/B counts" >&2; exit 5; }

fail=0; checked=0
while IFS=$'\t' read -r snapshot snapshot_sha receipt receipt_sha cohort; do
  checked=$((checked+1))
  [[ "$cohort" == */live/* || "$cohort" == */supplemental-cohort-b-v1/* ]] || { echo "quarantined/unapproved cohort: $cohort" >&2; fail=1; continue; }
  [[ -f "$snapshot" && -f "$receipt" ]] || { echo "missing artifact" >&2; fail=1; continue; }
  [[ "$(sha256sum "$snapshot" | awk '{print $1}')" == "$snapshot_sha" ]] || { echo "snapshot hash mismatch: $snapshot" >&2; fail=1; }
  [[ "$(sha256sum "$receipt" | awk '{print $1}')" == "$receipt_sha" ]] || { echo "receipt hash mismatch: $receipt" >&2; fail=1; }
  jq -e '
    .warmup_complete == true and
    .pre_action_only == true and
    .teacher_event_excluded_from_state == true and
    .authoritative_pool_identity == true and
    .chosen_pool_previously_eligible == true and
    .outcomes_or_pnl_accessed == false
  ' "$receipt" >/dev/null || { echo "receipt contract mismatch: $receipt" >&2; fail=1; }
done < <(jq -r '.snapshots[] | [.snapshot_path,.snapshot_sha256,.receipt_path,.receipt_sha256,.cohort_path] | @tsv' "$MASTER")

[[ "$checked" == 60 && "$fail" == 0 ]] || { echo "integrity verification failed" >&2; exit 6; }
[[ ! -e "$OUT" ]] || { echo "refusing to overwrite integrity receipt" >&2; exit 7; }
jq -n --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg master "$MASTER" --arg master_sha "$EXPECTED_MASTER_SHA" '{schema:"KEFI_CHOICESTATE_MASTER_INTEGRITY_V1",verified_at_utc:$timestamp,master_manifest_path:$master,master_manifest_sha256:$master_sha,snapshot_count:60,cohort_a_count:49,cohort_b_count:11,all_snapshot_hashes_verified:true,all_receipt_hashes_verified:true,all_receipts_confirmed_warmup_and_pre_action_integrity:true,quarantined_cohorts_included:false,selection_or_outcomes_opened:false}' > "$TMP"
mv -f "$TMP" "$OUT"
sha256sum "$OUT"
