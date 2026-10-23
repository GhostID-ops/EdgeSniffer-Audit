#!/usr/bin/env bash

set -uo pipefail

DATA_PATH="${EDGESNIFFER_DISK_GUARD_DATA_PATH:-/mnt/HC_Volume_106775198}"
STATE_PATH="${EDGESNIFFER_DISK_GUARD_STATE_PATH:-/root/bot/EdgeSniffer}"
DATA_MIN_FREE_BYTES="${EDGESNIFFER_DISK_GUARD_DATA_MIN_FREE_BYTES:-10737418240}"
STATE_MIN_FREE_BYTES="${EDGESNIFFER_DISK_GUARD_STATE_MIN_FREE_BYTES:-10737418240}"
INTERVAL_SECONDS="${EDGESNIFFER_DISK_GUARD_INTERVAL_SECONDS:-10}"
EMITTER_UNIT="${EDGESNIFFER_DISK_GUARD_EMITTER_UNIT:-edgesniffer-ranker-v2-n25-tiny-live-v3-emitter.service}"
EVENT_FILE="${EDGESNIFFER_DISK_GUARD_EVENT_FILE:-/var/lib/edgesniffer-storage-offload-v3/disk-guard/events.jsonl}"
LATCH_FILE="${EDGESNIFFER_DISK_GUARD_LATCH_FILE:-/var/lib/edgesniffer-storage-offload-v3/disk-guard/entry-blocked.latch}"
DRY_RUN="${EDGESNIFFER_DISK_GUARD_DRY_RUN:-false}"
CHECK_ONCE="${EDGESNIFFER_DISK_GUARD_CHECK_ONCE:-false}"

free_bytes() {
  local path="$1"
  local value
  value="$(df --output=avail -B1 "$path" 2>/dev/null | tail -n 1 | tr -d '[:space:]')"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s' "$value"
  else
    printf '0'
  fi
}

emit_critical_event() {
  local now="$1"
  local data_free="$2"
  local state_free="$3"
  local reason="$4"
  local message
  message="EdgeSniffer disk guard blocked new entries: reason=$reason data_free_bytes=$data_free state_free_bytes=$state_free emitter=$EMITTER_UNIT"

  logger -p daemon.crit -t edgesniffer-disk-guard -- "$message" || true
  mkdir -p "$(dirname "$EVENT_FILE")" 2>/dev/null || true
  printf '{"event":"disk_space_entry_blocked","severity":"critical","ts":"%s","reason":"%s","dataPath":"%s","dataFreeBytes":%s,"dataMinFreeBytes":%s,"statePath":"%s","stateFreeBytes":%s,"stateMinFreeBytes":%s,"emitterUnit":"%s","dryRun":%s}\n' \
    "$now" "$reason" "$DATA_PATH" "$data_free" "$DATA_MIN_FREE_BYTES" \
    "$STATE_PATH" "$state_free" "$STATE_MIN_FREE_BYTES" "$EMITTER_UNIT" "$DRY_RUN" \
    >> "$EVENT_FILE" 2>/dev/null || true
}

mkdir -p "$(dirname "$LATCH_FILE")"

while true; do
  data_free="$(free_bytes "$DATA_PATH")"
  state_free="$(free_bytes "$STATE_PATH")"
  reason=""

  if (( data_free < DATA_MIN_FREE_BYTES )); then
    reason="data_volume_below_threshold"
  elif (( state_free < STATE_MIN_FREE_BYTES )); then
    reason="state_volume_below_threshold"
  fi

  if [[ -n "$reason" && ! -e "$LATCH_FILE" ]]; then
    now="$(date -u +%FT%T.%3NZ)"
    emit_critical_event "$now" "$data_free" "$state_free" "$reason"

    if [[ "$DRY_RUN" != "true" ]]; then
      printf '%s reason=%s data_free_bytes=%s state_free_bytes=%s\n' \
        "$now" "$reason" "$data_free" "$state_free" > "$LATCH_FILE"
      systemctl stop "$EMITTER_UNIT" || true
    fi
  fi

  if [[ "$CHECK_ONCE" == "true" ]]; then
    if [[ -n "$reason" ]]; then
      exit 2
    fi
    exit 0
  fi

  sleep "$INTERVAL_SECONDS"
done
