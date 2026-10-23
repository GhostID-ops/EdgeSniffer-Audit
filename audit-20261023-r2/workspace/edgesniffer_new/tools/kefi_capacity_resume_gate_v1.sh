#!/usr/bin/env bash
set -euo pipefail

root='/root/kefi-yellowstone-prospective-v1'
minimum_free_gib=135
log='/root/kefi-work/logs/kefi-capacity-resume-gate-20261008.log'

while true; do
  free_gib=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  if [ "$free_gib" -ge "$minimum_free_gib" ]; then
    mkdir -p "$root/operations"
    printf '{"schema":"KEFI_CAPACITY_AUTO_RESUME_V1","minimum_free_gib":%s,"observed_free_gib":%s,"resumed_at_utc":"%s","continuity_rule":"pre-resume pause interval remains excluded"}\n' "$minimum_free_gib" "$free_gib" "$(date -u +%FT%TZ)" > "$root/operations/capacity-auto-resume-$(date -u +%Y%m%dT%H%M%SZ).json"
    rm -f "$root/operations/capacity-pause.json"
    systemctl start edgesniffer-kefi-full-meta-v1.service
    echo "resumed at $(date -u +%FT%TZ) with ${free_gib}GiB free" >> "$log"
    exit 0
  fi
  echo "waiting $(date -u +%FT%TZ): ${free_gib}GiB < ${minimum_free_gib}GiB" >> "$log"
  sleep 60
done
