#!/usr/bin/env bash
set -u
k=/root/kefi-work
mkdir -p "$k/out/indexed-v1/shards"
run_one() {
  i="$1"
  "$k/bin/kefi_prospective_discovery_v3" --replay "$k" "$k/out/enriched-v1.jsonl" "$k/out/indexed-v1/shards/$i.jsonl" "$k/out/indexed-v1/shards/$i.summary.json" "2026/10/05/$i" >"$k/out/indexed-v1/shards/$i.log" 2>&1
}
for i in 03 04 05 06 07 08 09 10 11 12; do
  while [ "$(jobs -rp | wc -l)" -ge 4 ]; do wait -n || true; done
  run_one "$i" &
done
wait
rm -f "$k/out/indexed-v1/RUNNING"
