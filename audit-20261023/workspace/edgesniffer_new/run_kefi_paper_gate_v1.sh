#!/usr/bin/env bash
set -euo pipefail
root=/root/kefi-yellowstone-prospective-v1
out=/root/kefi-work/out/prospective-v1
bin=/root/kefi-work/bin
while systemctl is-active --quiet kefi-prospective-inputs-v1.service; do sleep 15; done
test -s "$out/decoded-20261006-22.jsonl"
python3 "$bin/kefi_prepare_paper_inputs_v1.py" \
  --journal "$root/kefi-wallet-pump-candidates.jsonl" \
  --decoded "$out/decoded-20261006-22.jsonl" \
  --fresh-candidates "$out/fresh-candidates.jsonl" \
  --replay-targets "$out/replay-targets.jsonl" \
  --summary "$out/input-summary.json"
/root/kefi-work/bin/kefi_prospective_discovery_v5 --replay \
  "$root" "$out/replay-targets.jsonl" "$out/replay-events.jsonl" \
  "$out/replay-summary.json" 2026/10/06/22
python3 "$bin/kefi_make_replay_windows_v1.py" \
  --candidates "$out/fresh-candidates.jsonl" \
  --replay "$out/replay-events.jsonl" \
  --root "$root" \
  --output "$out/replay-windows.jsonl" \
  --summary "$out/window-summary.json"
python3 "$bin/kefi_prospective_paper_gate_v1.py" \
  --model /root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json \
  --expected-model-sha256 e13ceacd57462addf03ee2c1c7ad40ca0db1aa9936c64ceaa44e2cfca06eead4 \
  --fresh-candidates "$out/fresh-candidates.jsonl" \
  --replay-windows "$out/replay-windows.jsonl" \
  --output-signals "$out/paper-signals.jsonl" \
  --output-summary "$out/paper-summary.json"
sha256sum "$out"/*.json "$out"/*.jsonl > "$out/artifact-sha256.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$out/completed-at-utc.txt"
