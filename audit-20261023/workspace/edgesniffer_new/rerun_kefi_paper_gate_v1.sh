#!/usr/bin/env bash
set -euo pipefail
root=/root/kefi-yellowstone-prospective-v1
out=/root/kefi-work/out/prospective-v1
bin=/root/kefi-work/bin
python3 "$bin/kefi_prepare_paper_inputs_v1.py" \
  --journal "$root/kefi-wallet-pump-candidates.jsonl" \
  --decoded "$out/decoded-20261006-22.jsonl" \
  --fresh-candidates "$out/fresh-candidates.jsonl" \
  --replay-targets "$out/replay-targets.jsonl" \
  --summary "$out/input-summary.json"
python3 "$bin/kefi_prospective_paper_gate_v1.py" \
  --model /root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json \
  --expected-model-sha256 e13ceacd57462addf03ee2c1c7ad40ca0db1aa9936c64ceaa44e2cfca06eead4 \
  --fresh-candidates "$out/fresh-candidates.jsonl" \
  --replay-windows "$out/replay-windows.jsonl" \
  --output-signals "$out/paper-signals.jsonl" \
  --output-summary "$out/paper-summary.json"
python3 "$bin/kefi_paper_gate_integrity_v1.py" \
  --root "$root" \
  --model /root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json \
  --candidates "$out/fresh-candidates.jsonl" \
  --windows "$out/replay-windows.jsonl" \
  --signals "$out/paper-signals.jsonl" \
  --summary "$out/paper-summary.json" \
  --receipt "$out/paper-integrity-receipt.json"
sha256sum "$out"/paper-summary.json "$out"/paper-signals.jsonl "$out"/paper-integrity-receipt.json > "$out/paper-result-sha256.txt"
