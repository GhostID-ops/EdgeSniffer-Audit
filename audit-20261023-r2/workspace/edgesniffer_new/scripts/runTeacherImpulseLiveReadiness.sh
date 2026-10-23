#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${TEACHER_IMPULSE_PARITY_DIR:-/var/lib/edgesniffer-teacher-impulse-parity-v1}"
BOT_DIR="${EDGESNIFFER_BOT_DIR:-/root/bot/FinalBot-cya}"
BIN_DIR="${BOT_DIR}/rust-sidecar/target/release"
OUT_DIR="${BASE_DIR}/reports"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "${OUT_DIR}"

INPUTS="${BASE_DIR}/decision-inputs.jsonl"
CAPSULES="${BASE_DIR}/candidate-capsules.jsonl"
PARENT_RECEIPTS="${BASE_DIR}/decision-receipts.jsonl"
MC5000_RECEIPTS="${BASE_DIR}/children/mc5000/decision-receipts.jsonl"
DOM55_RECEIPTS="${BASE_DIR}/children/mc5000-dom55/decision-receipts.jsonl"
FB5000_RECEIPTS="${BASE_DIR}/children/mc5000-fb5000/decision-receipts.jsonl"

PARITY_REPORT="${OUT_DIR}/teacher-executable-parity-${TS}.md"
FINGERPRINT_REPORT="${OUT_DIR}/teacher-fingerprint-${TS}.md"
READINESS_REPORT="${OUT_DIR}/teacher-live-readiness-mc5000-${TS}.md"

"${BIN_DIR}/teacher_impulse_executable_parity_report" \
  --inputs "${INPUTS}" \
  --capsules "${CAPSULES}" \
  --receipt-lane "mc5000=${MC5000_RECEIPTS}" \
  --receipt-lane "cut_mc5000=${PARENT_RECEIPTS}:derived=mc5000" \
  --receipt-lane "cut_mc5000_dom55=${PARENT_RECEIPTS}:derived=mc5000_dom55" \
  --receipt-lane "cut_mc5000_fb5000=${PARENT_RECEIPTS}:derived=mc5000_fb5000" \
  --tp-pcts 15,20 \
  --sl-pct -20 \
  --delays-ms 250,500,750,1000 \
  --cost-bps 250 \
  --horizon-ms 120000 \
  --max-mark-mc-usd 500000 \
  > "${PARITY_REPORT}"

"${BIN_DIR}/teacher_impulse_fingerprint_report" \
  --receipts "${PARENT_RECEIPTS}" \
  --inputs "${INPUTS}" \
  --capsules "${CAPSULES}" \
  --horizon-ms 120000 \
  --tp-pct 20 \
  --sl-pct -20 \
  --min-cut-sample 25 \
  --max-mark-mc-usd 500000 \
  --output "${FINGERPRINT_REPORT}"

"${BIN_DIR}/teacher_impulse_live_readiness_report" \
  --receipts "${MC5000_RECEIPTS}" \
  --inputs "${INPUTS}" \
  --lane-name mc5000 \
  > "${READINESS_REPORT}"

ln -sfn "${PARITY_REPORT}" "${OUT_DIR}/teacher-executable-parity-latest.md"
ln -sfn "${FINGERPRINT_REPORT}" "${OUT_DIR}/teacher-fingerprint-latest.md"
ln -sfn "${READINESS_REPORT}" "${OUT_DIR}/teacher-live-readiness-mc5000-latest.md"

echo "parity_report=${PARITY_REPORT}"
echo "fingerprint_report=${FINGERPRINT_REPORT}"
echo "readiness_report=${READINESS_REPORT}"
