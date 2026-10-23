#!/usr/bin/env bash
set -euo pipefail

unit="edgesniffer-kefi-prospective-discovery-v1.service"
dir="/home/franky/edgesniffer-kefi-full-meta-v1/operations/kefi-prospective-discovery-v1"
tmp_rows="$dir/discovery-v1.tmp.jsonl"
tmp_summary="$dir/discovery-v1-summary.tmp.json"
final_rows="$dir/discovery-v1.jsonl"
final_summary="$dir/discovery-v1-summary.json"
manifest="$dir/discovery-v1-manifest.sha256"

while systemctl is-active --quiet "$unit"; do
  sleep 30
done

# The producer is successful only when both complete files exist. A failed or
# interrupted scan leaves temporary output and is never promoted.
test -s "$tmp_rows"
test -s "$tmp_summary"
grep -q '"decoder_failures": 0' "$tmp_summary"

mv -- "$tmp_rows" "$final_rows"
mv -- "$tmp_summary" "$final_summary"
sha256sum "$final_rows" "$final_summary" > "$manifest"
sync
