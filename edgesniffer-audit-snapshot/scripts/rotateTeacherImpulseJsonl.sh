#!/usr/bin/env bash
set -euo pipefail

BASE="/var/lib/edgesniffer-teacher-impulse-parity-v1"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ROT="${BASE}/rotated/${TS}"

UNITS=(
  edgesniffer-teacher-impulse-parity-v1-child-mc5000-fb5000-nosubmit.service
  edgesniffer-teacher-impulse-parity-v1-child-mc5000-dom55-nosubmit.service
  edgesniffer-teacher-impulse-parity-v1-child-mc5000-nosubmit.service
  edgesniffer-teacher-impulse-parity-v1-nosubmit.service
  edgesniffer-teacher-impulse-parity-v1-capsule-normalizer.service
  edgesniffer-teacher-impulse-parity-v1-observation-emitter.service
  edgesniffer-teacher-impulse-parity-v1-mayhem-supply-enricher.service
  edgesniffer-teacher-impulse-parity-v1-detected-swap-adapter.service
  edgesniffer-teacher-impulse-parity-v1-source-feed.service
)

MAIN_FILES=(
  detected-swaps.jsonl
  normalized-observations.jsonl
  candidate-capsules.jsonl
  decision-inputs.jsonl
  decision-receipts.jsonl
)

CHILDREN=(
  mc5000
  mc5000-dom55
  mc5000-fb5000
)

if [[ ! -d "${BASE}" ]]; then
  echo "missing base dir: ${BASE}" >&2
  exit 1
fi

echo "before_df:"
df -hT /
echo "before_size:"
du -sh "${BASE}" 2>/dev/null || true

systemctl stop "${UNITS[@]}" || true

mkdir -p "${ROT}/children"

for file in "${MAIN_FILES[@]}"; do
  src="${BASE}/${file}"
  if [[ -f "${src}" ]]; then
    mv "${src}" "${ROT}/${file}"
  fi
  : > "${src}"
done

for child in "${CHILDREN[@]}"; do
  mkdir -p "${BASE}/children/${child}" "${ROT}/children/${child}"
  src="${BASE}/children/${child}/decision-receipts.jsonl"
  if [[ -f "${src}" ]]; then
    mv "${src}" "${ROT}/children/${child}/decision-receipts.jsonl"
  fi
  : > "${src}"
done

cat > "${ROT}/ROTATION.txt" <<EOF
rotated_at_utc=${TS}
reason=active_jsonl_disk_pressure
reports_preserved=${BASE}/reports
services_stopped_and_files_recreated=true
EOF

echo "rotated_dir=${ROT}"
echo "active_jsonl_after_rotate:"
find "${BASE}" -maxdepth 3 -type f -name '*.jsonl' -printf '%s %p\n' | sort -nr
echo "rotated_size_before_compress:"
du -sh "${ROT}" 2>/dev/null || true

systemctl start \
  edgesniffer-teacher-impulse-parity-v1-child-mc5000-fb5000-nosubmit.service \
  edgesniffer-teacher-impulse-parity-v1-child-mc5000-dom55-nosubmit.service \
  edgesniffer-teacher-impulse-parity-v1-child-mc5000-nosubmit.service \
  edgesniffer-teacher-impulse-parity-v1-nosubmit.service \
  edgesniffer-teacher-impulse-parity-v1-capsule-normalizer.service \
  edgesniffer-teacher-impulse-parity-v1-observation-emitter.service \
  edgesniffer-teacher-impulse-parity-v1-mayhem-supply-enricher.service \
  edgesniffer-teacher-impulse-parity-v1-detected-swap-adapter.service
systemctl start edgesniffer-teacher-impulse-parity-v1-source-feed.service

echo "service_state_after_restart:"
systemctl --no-pager --plain is-active "${UNITS[@]}" || true

echo "starting_low_priority_compression"
(
  cd "${ROT}"
  for file in detected-swaps.jsonl normalized-observations.jsonl candidate-capsules.jsonl decision-inputs.jsonl decision-receipts.jsonl; do
    [[ -f "${file}" ]] && nice -n 19 gzip -1 "${file}"
  done
  for child in "${CHILDREN[@]}"; do
    [[ -f "children/${child}/decision-receipts.jsonl" ]] && nice -n 19 gzip -1 "children/${child}/decision-receipts.jsonl"
  done
  date -u > COMPRESSION_DONE_UTC.txt
) > "${ROT}/compression.log" 2>&1 &

echo "compression_pid=$!"
echo "after_df:"
df -hT /
