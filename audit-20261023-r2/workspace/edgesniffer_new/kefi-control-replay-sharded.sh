#!/usr/bin/env bash
set -euo pipefail
K=/root/kefi-work
O="$K/out/independent-v1"
R="$O/control-replay"
S="$R/shards-v2"
exec >>"$O/control-replay-sharded.log" 2>&1
rm -rf "$S"
mkdir -p "$S"
run_one() {
  local i="$1"
  "$K/bin/kefi_prospective_discovery_v5" --replay "$K" "$O/matched-controls-v1.jsonl" \
    "$S/$i.jsonl" "$S/$i.summary.json" "2026/10/05/$i" >"$S/$i.log" 2>&1
}
for i in 03 04 05 06 07 08 09 10 11 12; do
  while [ "$(jobs -rp | wc -l)" -ge 10 ]; do wait -n || true; done
  run_one "$i" &
done
wait
cat "$S/03.jsonl" "$S/04.jsonl" "$S/05.jsonl" "$S/06.jsonl" "$S/07.jsonl" "$S/08.jsonl" "$S/09.jsonl" "$S/10.jsonl" "$S/11.jsonl" "$S/12.jsonl" >"$R/replay-120s-v2.jsonl"
python3 "$K/bin/kefi_merge_replay_summaries.py" --directory "$S" --output "$R/replay-120s-v2.summary.json" --expected 03 04 05 06 07 08 09 10 11 12
echo "sharded_replay_finished=$(date -u +%FT%H:%M:%SZ)"
