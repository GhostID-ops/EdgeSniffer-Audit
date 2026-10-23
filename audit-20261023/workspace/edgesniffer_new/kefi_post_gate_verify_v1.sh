#!/usr/bin/env bash
set -euo pipefail
root=/root/kefi-yellowstone-prospective-v1
out=/root/kefi-work/out/prospective-v1
while systemctl is-active --quiet kefi-paper-gate-v1.service; do sleep 15; done
test -s "$out/paper-summary.json"
test -f "$out/paper-signals.jsonl"
python3 /root/kefi-work/bin/kefi_paper_gate_integrity_v1.py \
  --root "$root" \
  --model /root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json \
  --candidates "$out/fresh-candidates.jsonl" \
  --windows "$out/replay-windows.jsonl" \
  --signals "$out/paper-signals.jsonl" \
  --summary "$out/paper-summary.json" \
  --receipt "$out/paper-integrity-receipt.json"
sha256sum "$out"/paper-summary.json "$out"/paper-signals.jsonl "$out"/paper-integrity-receipt.json > "$out/paper-result-sha256.txt"
