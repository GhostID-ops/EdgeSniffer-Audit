#!/usr/bin/env bash
set -euo pipefail

TIMER=edgesniffer-2cq-prospective-tier-a-entry-census-v3.timer
CENSUS=edgesniffer-2cq-prospective-tier-a-entry-census-v3.service
OBSERVER=edgesniffer-allenhark-compact-delivery-observer-v1.service
OLD_CONTAINER=jito-shredstream-proxy
ROLLBACK_CONTAINER=jito-shredstream-proxy-pre-final-repair-v1
IMAGE=allenhark-shredstream-proxy:realtime-coverage-final-repair-v1
ROOT=/mnt/HC_Volume_106775198/edgesniffer-research/allenhark-realtime-coverage-final-repair-v1
INTERVALS=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-prospective-tier-a-entry-census-v3/invalid-collection-intervals.jsonl
HEALTH=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-realtime-delivery-path-localization-v1/observer-health.json

append_interval() {
    reason=$1
    start=$2
    end=$3
    python3 - "$INTERVALS" "$reason" "$start" "$end" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
row = {
    "schema": "2CQ_PROSPECTIVE_TIER_A_ENTRY_CENSUS_V3_INVALID_INTERVAL_V1",
    "reason": sys.argv[2],
    "pauseStartUtc": sys.argv[3],
    "pauseEndUtc": sys.argv[4],
    "firstValidPostRecoveryUtc": sys.argv[4],
    "collectionValidity": "INVALID_FOR_COLLECTION",
    "admissionPermanentlyExcluded": True,
}
with path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(row, separators=(",", ":")) + "\n")
PY
}

mkdir -p "$ROOT/telemetry" "$ROOT/deployment"
systemctl stop "$TIMER"
systemctl stop "$CENSUS" || true
down=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
printf '%s\n' "$down" > "$ROOT/deployment/DOWN_START_UTC"

systemctl stop "$OBSERVER"
docker rm -f "$ROLLBACK_CONTAINER" >/dev/null 2>&1 || true
docker stop "$OLD_CONTAINER" >/dev/null
docker rename "$OLD_CONTAINER" "$ROLLBACK_CONTAINER"

rollback() {
    rc=$?
    if (( rc == 0 )); then
        return
    fi
    set +e
    docker logs "$OLD_CONTAINER" > "$ROOT/deployment/failed-cutover-proxy.log" 2>&1
    docker rm -f "$OLD_CONTAINER" >/dev/null 2>&1 || true
    if docker inspect "$ROLLBACK_CONTAINER" >/dev/null 2>&1; then
        docker rename "$ROLLBACK_CONTAINER" "$OLD_CONTAINER" >/dev/null 2>&1 || true
        docker start "$OLD_CONTAINER" >/dev/null 2>&1 || true
    fi
    rm -f /etc/systemd/system/edgesniffer-allenhark-compact-delivery-observer-v1.service.d/20-no-runtime-rollover.conf
    systemctl daemon-reload
    systemctl start "$OBSERVER" || true
    sleep 2
    rollback_healthy=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    append_interval "ALLENHARK_REALTIME_COVERAGE_FINAL_REPAIR_V1_ROLLBACK" "$down" "$rollback_healthy"
    systemctl start "$TIMER" || true
    exit "$rc"
}
trap rollback ERR

docker run -d \
    --name "$OLD_CONTAINER" \
    --hostname CTbot \
    --network host \
    --restart unless-stopped \
    -v /root/jito/auth.json:/auth.json:ro \
    -v "$ROOT/telemetry":/instrumentation \
    -e ALLENHARK_INSTRUMENTATION_PATH=/instrumentation/events-v1.bin \
    -e ALLENHARK_INSTRUMENTATION_MAX_BYTES=1073741824 \
    -e DESIRED_REGIONS=frankfurt \
    -e SRC_BIND_PORT=20000 \
    -e RUST_LOG=info \
    -e BLOCK_ENGINE_URL=https://fra.jito.allenhark.network \
    -e AUTH_KEYPAIR=/auth.json \
    -e DEST_IP_PORTS=127.0.0.1:8001 \
    -e GRPC_SERVICE_PORT=9999 \
    "$IMAGE" shredstream >/dev/null

for _ in $(seq 1 60); do
    docker logs "$OLD_CONTAINER" > "$ROOT/deployment/cutover-proxy-health.log" 2>&1
    if grep -q 'ALLENHARK_INSTRUMENTATION.*fec_final_retry_attempts=' "$ROOT/deployment/cutover-proxy-health.log" \
        && grep -Eq 'successful_heartbeat_count=[1-9][0-9]*' "$ROOT/deployment/cutover-proxy-health.log"; then
        break
    fi
    sleep 1
done
grep -q 'ALLENHARK_INSTRUMENTATION.*fec_final_retry_attempts=' "$ROOT/deployment/cutover-proxy-health.log"
grep -Eq 'successful_heartbeat_count=[1-9][0-9]*' "$ROOT/deployment/cutover-proxy-health.log"

mkdir -p /etc/systemd/system/edgesniffer-allenhark-compact-delivery-observer-v1.service.d
install -m 0644 /root/bot/EdgeSniffer/ops/allenhark-observer-no-rollover.conf \
    /etc/systemd/system/edgesniffer-allenhark-compact-delivery-observer-v1.service.d/20-no-runtime-rollover.conf
systemctl daemon-reload
systemctl start "$OBSERVER"

for _ in $(seq 1 60); do
    if python3 - "$HEALTH" "$down" <<'PY'
import datetime as dt
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
down = dt.datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
try:
    health = json.loads(path.read_text())
    receipt = dt.datetime.fromisoformat(health["lastReceiptAtUtc"].replace("Z", "+00:00"))
    ok = health.get("connected") is True and health.get("decodeFailures") == 0 and receipt > down
except Exception:
    ok = False
raise SystemExit(0 if ok else 1)
PY
    then
        break
    fi
    sleep 1
done

python3 - "$HEALTH" "$down" <<'PY'
import datetime as dt
import json
import pathlib
import sys

health = json.loads(pathlib.Path(sys.argv[1]).read_text())
down = dt.datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
receipt = dt.datetime.fromisoformat(health["lastReceiptAtUtc"].replace("Z", "+00:00"))
assert health.get("connected") is True
assert health.get("decodeFailures") == 0
assert receipt > down
PY

pid=$(docker inspect -f '{{.State.Pid}}' "$OLD_CONTAINER")
nsenter -t "$pid" -n ss -u -a -n -m -e | grep -A1 ':20000' > "$ROOT/deployment/socket-health.txt"
grep -q 'rb268435456' "$ROOT/deployment/socket-health.txt"
if grep -Eq 'd[1-9][0-9]*\)' "$ROOT/deployment/socket-health.txt"; then
    false
fi

healthy=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
printf '%s\n' "$healthy" > "$ROOT/deployment/HEALTHY_RESUME_UTC"
append_interval "ALLENHARK_REALTIME_COVERAGE_FINAL_REPAIR_V1_DEPLOYMENT" "$down" "$healthy"

systemctl start "$TIMER"
trap - ERR
printf 'DOWN_START_UTC=%s\nHEALTHY_RESUME_UTC=%s\n' "$down" "$healthy"
docker inspect "$OLD_CONTAINER" --format 'IMAGE={{.Config.Image}} STATUS={{.State.Status}} PID={{.State.Pid}} RESTARTS={{.RestartCount}}'
systemctl show "$OBSERVER" -p ActiveState -p SubState -p MainPID -p RuntimeMaxUSec -p NRestarts
