#!/usr/bin/env bash
set -euo pipefail

SERVICE=edgesniffer-2cq-prospective-tier-a-entry-census-v3.service
TIMER=edgesniffer-2cq-prospective-tier-a-entry-census-v3.timer
ROOT=/root/bot/EdgeSniffer
COHORT=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-prospective-tier-a-entry-census-v3
ROLLBACK=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-tier-a-interval-continuity-v1/rollback
STAGED=/root/bot/EdgeSniffer/.staged/2cq-tier-a-interval-continuity-v1
DROPIN=/etc/systemd/system/${SERVICE}.d/20-interval-continuity.conf

if [[ "$(systemctl show "$SERVICE" -p ActiveState --value)" != "inactive" ]]; then
  echo "CENSUS_ONESHOT_ACTIVE_DEPLOYMENT_BLOCKED" >&2
  exit 2
fi

mkdir -p "$ROLLBACK" "$(dirname "$DROPIN")"
cp -a "$ROOT/scripts/twoCqProspectiveTierAEntryCensusV3.py" "$ROLLBACK/twoCqProspectiveTierAEntryCensusV3.py.before"
systemctl cat "$SERVICE" > "$ROLLBACK/systemd.before.txt"
cp -a "$COHORT/status.json" "$ROLLBACK/status.before.json"
cp -a "$COHORT/state.json" "$ROLLBACK/state.before.json"
sha256sum "$COHORT/cohort-contract.json" "$COHORT/state.json" \
  "$COHORT/development/eligible.jsonl" > "$ROLLBACK/cohort.before.sha256"

systemctl stop "$TIMER"
if [[ "$(systemctl show "$SERVICE" -p ActiveState --value)" != "inactive" ]]; then
  systemctl start "$TIMER"
  echo "CENSUS_BECAME_ACTIVE_DEPLOYMENT_BLOCKED" >&2
  exit 3
fi

install -m 0755 "$STAGED/twoCqProspectiveTierAEntryCensusV3.py" \
  "$ROOT/scripts/twoCqProspectiveTierAEntryCensusV3.py"
install -m 0644 "$STAGED/20-interval-continuity.conf" "$DROPIN"
python3 -m py_compile "$ROOT/scripts/twoCqProspectiveTierAEntryCensusV3.py"
systemctl daemon-reload

OLD_SHA=$(sha256sum "$ROLLBACK/twoCqProspectiveTierAEntryCensusV3.py.before" | cut -d' ' -f1)
NEW_SHA=$(sha256sum "$ROOT/scripts/twoCqProspectiveTierAEntryCensusV3.py" | cut -d' ' -f1)
AT=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
STATUS_SHA=$(sha256sum "$COHORT/status.json" | cut -d' ' -f1)
printf '{"schema":"2CQ_TIER_A_INTERVAL_CONTINUITY_REPAIR_V1","deployedAtUtc":"%s","oldScriptSha256":"%s","newScriptSha256":"%s","preDeployStatusSha256":"%s","cohortReset":false,"eligibilityPolicy":"same compact-source CREATE-to-entry connected interval","outcomesRead":0,"submissions":0}\n' \
  "$AT" "$OLD_SHA" "$NEW_SHA" "$STATUS_SHA" > "$COHORT/continuity-gate-repair-v1.json"
chmod 0444 "$COHORT/continuity-gate-repair-v1.json"

systemctl start "$TIMER"
systemctl start --no-block "$SERVICE"
echo "DEPLOYED_AT=$AT"
echo "OLD_SHA=$OLD_SHA"
echo "NEW_SHA=$NEW_SHA"
systemctl show "$SERVICE" -p ActiveState -p SubState -p MainPID
