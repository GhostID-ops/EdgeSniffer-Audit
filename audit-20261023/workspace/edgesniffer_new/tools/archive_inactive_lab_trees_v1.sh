#!/usr/bin/env bash
set -euo pipefail

# Archives one independently audited inactive tree at a time, verifies every
# file twice, then delegates release to the existing fail-closed release tool.
archive_root='/mnt/edgesniffer-expansion/kefi-cold-archives/20261008-capacity-recovery/legacy'
lake='/root/kefi-work/evidence-lake-v1'
catalog="$lake/catalog.sqlite3"
audit_tool='/root/kefi-work/bin/evidence_lake_legacy_tree_audit_v1.py'
release_tool='/root/kefi-work/bin/evidence_lake_tree_transparent_release_v1.py'

sources=(
  /root/2cq-shred-timing-lab-20261004T045632Z
  /root/v3-offline-equivalence-160222-r3
  /root/v3-offline-equivalence-160222-r2
  /root/v3-offline-equivalence-160222
  /root/v3-data-plane-v2-live-shadow-20260930T194532Z
  /root/v3-fullstack-1790722990
  /root/v3-fullstack-1790722966
  /root/v3-direct-fullstack-20260930T015012Z
  /root/v3-direct-validation-20260930T014724Z
)

for source in "${sources[@]}"; do
  name=$(basename "$source")
  audit="$lake/legacy-audits/${name}-independent-$(date -u +%Y%m%dT%H%M%SZ).json"
  destination="$archive_root/$name"
  receipt="$lake/archive-receipts/$name.archive.json"

  python3 "$audit_tool" --path "$source" --output "$audit" --catalog "$catalog" --settle-seconds 3
  grep -q '"outcome":"DEPENDENCY_NONE_PROVEN"' "$audit"
  test ! -e "$destination"
  mkdir -p "$(dirname "$destination")"
  rsync -aHAX --numeric-ids --mkpath "$source/" "$destination/"

  local_manifest=$(mktemp)
  archive_manifest=$(mktemp)
  (cd "$source" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$local_manifest"
  (cd "$destination" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$archive_manifest"
  diff -u "$local_manifest" "$archive_manifest"
  rm -f "$local_manifest" "$archive_manifest"

  canonical_hash=$(python3 - "$source" "$release_tool" <<'PY'
import importlib.util
import sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('release',sys.argv[2])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.manifest(Path(sys.argv[1]))[1])
PY
)
  archive_hash=$(python3 - "$destination" "$release_tool" <<'PY'
import importlib.util
import sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('release',sys.argv[2])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.manifest(Path(sys.argv[1]))[1])
PY
)
  test "$canonical_hash" = "$archive_hash"
  python3 - "$receipt" "$source" "$destination" "$canonical_hash" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
p,src,dst,h=sys.argv[1:]
Path(p).parent.mkdir(parents=True,exist_ok=True)
Path(p).write_text(json.dumps({'schema':'LEGACY_TREE_NFS_ARCHIVE_V1','status':'NFS_VERIFIED','source':src,'archive_tree':dst,'source_manifest_sha256':h,'created_at_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')},sort_keys=True,separators=(',',':'))+'\n')
PY
  final_audit="$lake/legacy-audits/${name}-pre-release-$(date -u +%Y%m%dT%H%M%SZ).json"
  python3 "$audit_tool" --path "$source" --output "$final_audit" --catalog "$catalog" --settle-seconds 3
  grep -q '"outcome":"DEPENDENCY_NONE_PROVEN"' "$final_audit"
  python3 "$release_tool" --local-tree "$source" --archive-tree "$destination" --archive-receipt "$receipt" --dependency-audit "$final_audit" --release-receipt-dir "$lake/release-receipts" --mode RELEASE
done
