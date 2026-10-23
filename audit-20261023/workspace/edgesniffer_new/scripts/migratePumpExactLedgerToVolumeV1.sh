#!/usr/bin/env bash
set -euo pipefail

SOURCE=/var/lib/pump-exact-evidence-ledger-v1
VOLUME=/mnt/HC_Volume_106775198
DESTINATION="$VOLUME/pump-exact-evidence-ledger-v1"
SERVICE=pump-exact-evidence-ledger-v1.service
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="${SOURCE}.root-prebind-${STAMP}"
FSTAB_LINE="$DESTINATION $SOURCE none bind,x-systemd.requires-mounts-for=$VOLUME 0 0"

if mountpoint -q "$SOURCE"; then
  echo "BLOCKED: $SOURCE is already a mountpoint" >&2
  exit 2
fi
test -d "$SOURCE"
test -d "$DESTINATION"
test "$(findmnt -n -o TARGET "$VOLUME")" = "$VOLUME"
test "$(systemctl is-active "$SERVICE")" = active

mkdir -p "$SOURCE/operations-v1/migrations"
python3 - "$SOURCE" "$STAMP" <<'PY'
import datetime, json, pathlib, sys
root, stamp = pathlib.Path(sys.argv[1]), sys.argv[2]
health = json.loads((root / "health.json").read_text())
record = {
    "schema": "PUMP_EXACT_LEDGER_STORAGE_CUTOVER_V1",
    "phase": "PRE_STOP",
    "atUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "lastRawStreamUpdateUtc": health.get("lastRawStreamUpdateUtc"),
    "lastFinalizedReconciliationUtc": health.get("lastFinalizedReconciliationUtc"),
    "lastPartitionAppendUtc": health.get("lastPartitionAppendUtc"),
    "reconciliationBacklog": health.get("reconciliationBacklog"),
    "lastFinalizedSlot": (root / "state/last-finalized-slot").read_text().strip(),
}
(root / "operations-v1/migrations" / f"cutover-{stamp}.json").write_text(
    json.dumps(record, indent=2, sort_keys=True) + "\n"
)
PY

systemctl stop pump-exact-evidence-ledger-watchdog-v1.timer \
  pump-exact-evidence-ledger-v1-reconcile.timer \
  pump-exact-evidence-readiness-v1.timer
systemctl stop pump-exact-evidence-ledger-watchdog-v1.service \
  pump-exact-evidence-ledger-v1-reconcile.service \
  pump-exact-evidence-readiness-v1.service || true
systemctl stop "$SERVICE"
test "$(systemctl is-active "$SERVICE")" = inactive

rsync -aHAXc --numeric-ids --delete "$SOURCE/" "$DESTINATION/"
if test -n "$(rsync -aHAXnci --numeric-ids --delete "$SOURCE/" "$DESTINATION/")"; then
  echo "BLOCKED: checksum verification found source/destination differences" >&2
  systemctl start "$SERVICE"
  exit 3
fi

mv "$SOURCE" "$BACKUP"
mkdir -p "$SOURCE"
mount --bind "$DESTINATION" "$SOURCE"
if ! grep -Fqx "$FSTAB_LINE" /etc/fstab; then
  printf '%s\n' "$FSTAB_LINE" >> /etc/fstab
fi
systemctl daemon-reload

if ! systemctl start "$SERVICE"; then
  umount "$SOURCE" || true
  rmdir "$SOURCE" || true
  mv "$BACKUP" "$SOURCE"
  sed -i "\|^$DESTINATION $SOURCE none bind,|d" /etc/fstab
  systemctl daemon-reload
  systemctl start "$SERVICE"
  echo "BLOCKED: migrated service failed; rolled back" >&2
  exit 4
fi

sleep 20
python3 - "$SOURCE" "$BACKUP" "$STAMP" <<'PY'
import datetime, json, pathlib, sys
root, backup, stamp = map(pathlib.Path, sys.argv[1:])
health = json.loads((root / "health.json").read_text())
assert health.get("processAlive") is True
assert health.get("evidenceFlowHealthy") is True
assert health.get("streamConnected") is True
record_path = root / "operations-v1/migrations" / f"cutover-{stamp.name}.json"
record = json.loads(record_path.read_text())
record.update({
    "phase": "VERIFIED_ACTIVE_ON_VOLUME",
    "verifiedAtUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "postStartLastRawStreamUpdateUtc": health.get("lastRawStreamUpdateUtc"),
    "postStartLastFinalizedReconciliationUtc": health.get("lastFinalizedReconciliationUtc"),
    "postStartLastPartitionAppendUtc": health.get("lastPartitionAppendUtc"),
    "postStartLastFinalizedSlot": (root / "state/last-finalized-slot").read_text().strip(),
    "sourceBackup": str(backup),
    "checksumVerification": "RSYNC_ARCHIVE_CHECKSUM_DRY_RUN_ZERO_DIFFERENCES",
})
record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY

systemctl start pump-exact-evidence-ledger-watchdog-v1.timer \
  pump-exact-evidence-ledger-v1-reconcile.timer \
  pump-exact-evidence-readiness-v1.timer

test "$(findmnt -n -o SOURCE "$SOURCE")" = "/dev/sdb[/pump-exact-evidence-ledger-v1]" \
  || findmnt "$SOURCE"
rm -rf --one-file-system "$BACKUP"

echo "PUMP_EXACT_LEDGER_VOLUME_CUTOVER_COMPLETE"
findmnt "$SOURCE"
df -h / "$VOLUME"
systemctl show "$SERVICE" -p ActiveState -p SubState -p MainPID -p NRestarts --no-pager
