#!/usr/bin/env bash
# Archive one independently audited inactive tree and release it only after
# complete source/archive SHA-256 equality and a second dependency audit.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 SOURCE ARCHIVE_ROOT" >&2
  exit 64
fi

source_tree=$(readlink -f -- "$1")
mkdir -p -- "$2"
archive_root=$(readlink -f -- "$2")
name=$(basename -- "$source_tree")
work_root=/root/kefi-work/evidence-lake-v1
audit_tool=/root/kefi-work/bin/evidence_lake_legacy_tree_audit_v1.py
release_tool=/root/kefi-work/bin/evidence_lake_tree_transparent_release_v1.py
catalog="$work_root/catalog.sqlite3"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
audit_dir="$work_root/legacy-audits/verified-archive-$stamp"
receipt_dir="$work_root/archive-receipts"
release_dir="$work_root/release-receipts"
destination="$archive_root/$name"
archive_receipt="$receipt_dir/$name.archive.json"

mkdir -p "$audit_dir" "$receipt_dir" "$release_dir" "$archive_root"

python3 "$audit_tool" --path "$source_tree" --output "$audit_dir/$name.pre-copy.json" \
  --catalog "$catalog" --settle-seconds 3 --ignore-process-pid "$$"
grep -q '"outcome":"DEPENDENCY_NONE_PROVEN"' "$audit_dir/$name.pre-copy.json"

# Resumable copy. Final full manifests below, rather than rsync metadata,
# establish the cryptographic identity of every file.
ionice -c3 nice -n 19 rsync -aHAX --numeric-ids --partial --append-verify --mkpath \
  "$source_tree/" "$destination/"

python3 - "$source_tree" "$destination" "$archive_receipt" "$release_tool" <<'PY'
import hashlib, importlib.util, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

src, dst, receipt, tool = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("release_tool", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
src_files, src_hash, src_bytes = module.manifest(src)
dst_files, dst_hash, dst_bytes = module.manifest(dst)
if src_hash != dst_hash or src_files != dst_files or src_bytes != dst_bytes:
    raise SystemExit("FAIL_CLOSED: source/archive SHA-256 manifests differ")
payload = {
  "schema":"INACTIVE_TREE_NFS_ARCHIVE_V2",
  "status":"NFS_VERIFIED",
  "source":str(src),
  "archive_tree":str(dst),
  "source_manifest_sha256":src_hash,
  "archive_manifest_sha256":dst_hash,
  "file_count":len(src_files),
  "bytes":src_bytes,
  "verified_at_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
  "local_present":True,
  "local_release_eligible":False,
}
raw=(json.dumps(payload, sort_keys=True, separators=(",",":"))+"\n").encode()
receipt.parent.mkdir(parents=True, exist_ok=True)
tmp=receipt.with_suffix(receipt.suffix+".tmp")
tmp.write_bytes(raw)
os.replace(tmp, receipt)
print(json.dumps({"manifest_sha256":src_hash,"file_count":len(src_files),"bytes":src_bytes,"receipt":str(receipt),"receipt_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))
PY

# The independent second audit catches a writer or a new reference that began
# while the archive was being copied.
python3 "$audit_tool" --path "$source_tree" --output "$audit_dir/$name.pre-release.json" \
  --catalog "$catalog" --settle-seconds 3 --ignore-process-pid "$$"
grep -q '"outcome":"DEPENDENCY_NONE_PROVEN"' "$audit_dir/$name.pre-release.json"

python3 "$release_tool" --local-tree "$source_tree" --archive-tree "$destination" \
  --archive-receipt "$archive_receipt" --dependency-audit "$audit_dir/$name.pre-release.json" \
  --release-receipt-dir "$release_dir" --mode RELEASE
