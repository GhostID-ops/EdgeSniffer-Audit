#!/usr/bin/env python3
"""Idempotent, fail-closed archival of one sealed evidence partition.

The default is a plan only.  --archive copies to an NFS staging directory,
checks a complete per-file SHA-256 manifest, then atomically publishes an
archive object and receipt.  It never releases local evidence; a deliberately
separate, future action must pass the release gate and re-check stability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def write_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)


def manifest(root: Path):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("source must be a real sealed directory")
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink is not supported in archive source: {path}")
        if path.is_file():
            stat = path.stat()
            entries.append({"path": str(path.relative_to(root)), "bytes": stat.st_size,
                            "sha256": hash_file(path)})
    if not entries:
        raise ValueError("empty partition is not an archive candidate")
    body = {"schema": "partition-tree-manifest-v1", "files": entries}
    raw = canonical(body)
    return body, hashlib.sha256(raw).hexdigest(), raw


def tree_sizes(root: Path):
    count = 0; total = 0
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            count += 1; total += p.stat().st_size
    return count, total


def copy_and_verify(source: Path, destination: Path, expected_manifest, progress: Path, partition_id: str):
    expected_paths = {entry["path"] for entry in expected_manifest["files"]}
    for existing in sorted(destination.rglob("*")):
        if existing.is_symlink():
            raise ValueError(f"symlink in staging payload: {existing}")
        if existing.is_file() and str(existing.relative_to(destination)) not in expected_paths:
            raise ValueError(f"unexpected file in staging payload: {existing}")
    copied = reused = 0
    for entry in expected_manifest["files"]:
        src = source / entry["path"]; dst = destination / entry["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file() and not dst.is_symlink() and dst.stat().st_size == entry["bytes"] and hash_file(dst) == entry["sha256"]:
            reused += 1
        else:
            if dst.exists():
                dst.unlink()
            shutil.copy2(src, dst)
            copied += 1
        if (copied + reused) % 256 == 0:
            write_atomic(progress, {"schema": "partition-archive-progress-v1", "partition_id": partition_id,
              "updated_utc": utcnow(), "phase": "COPYING", "copied_files": copied, "reused_files": reused,
              "total_files": len(expected_manifest["files"])})
    write_atomic(progress, {"schema": "partition-archive-progress-v1", "partition_id": partition_id,
      "updated_utc": utcnow(), "phase": "NFS_READBACK_VERIFY", "copied_files": copied, "reused_files": reused,
      "total_files": len(expected_manifest["files"])})
    copied_manifest, _, _ = manifest(destination)
    if copied_manifest != expected_manifest:
        raise ValueError("full NFS readback manifest mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--partition-id", required=True)
    parser.add_argument("--archive", action="store_true", help="perform copy/verification; otherwise print plan")
    args = parser.parse_args()
    source = Path(args.source).absolute(); archive_root = Path(args.archive_root).absolute()
    try:
        before_count, before_bytes = tree_sizes(source)
        source_manifest, identity, source_raw = manifest(source)
        plan = {"schema": "partition-archive-v1", "partition_id": args.partition_id,
                "source": str(source), "file_count": before_count, "bytes": before_bytes,
                "tree_sha256": identity, "mode": "ARCHIVE" if args.archive else "PLAN_ONLY",
                "release_local": False}
        if not args.archive:
            print(json.dumps(plan, sort_keys=True)); return 0
        final = archive_root / "cas" / "raw" / "sha256" / identity[:2] / identity
        receipt = archive_root / "receipts" / f"{identity}.json"
        progress = archive_root / "archive-progress" / f"{identity}.json"
        if final.exists() and receipt.is_file():
            existing, existing_id, _ = manifest(final)
            if existing != source_manifest or existing_id != identity:
                raise ValueError("existing canonical object fails identity verification")
            plan.update({"status": "ALREADY_VERIFIED", "archive": str(final), "receipt": str(receipt)})
            print(json.dumps(plan, sort_keys=True)); return 0
        staging_parent = archive_root / "staging"; staging_parent.mkdir(parents=True, exist_ok=True)
        # Deterministic staging makes interrupted archive verification resumable.
        staging = staging_parent / f"{identity}.payload"
        if staging.exists():
            if not staging.is_dir() or staging.is_symlink():
                raise ValueError("staging path is not a real directory")
        if not staging.exists():
            staging.mkdir()
        write_atomic(progress, {"schema": "partition-archive-progress-v1", "partition_id": args.partition_id,
          "updated_utc": utcnow(), "phase": "STAGING", "total_files": before_count})
        # Reuse verified staged files, repair only incomplete/corrupt files, then
        # independently hash the full NFS payload before publication.
        copy_and_verify(source, staging, source_manifest, progress, args.partition_id)
        write_atomic(progress, {"schema": "partition-archive-progress-v1", "partition_id": args.partition_id,
          "updated_utc": utcnow(), "phase": "SOURCE_STABILITY_VERIFY", "total_files": before_count})
        after_count, after_bytes = tree_sizes(source)
        after_manifest, after_id, _ = manifest(source)
        if (before_count, before_bytes, identity) != (after_count, after_bytes, after_id):
            raise ValueError("source changed during archive; refusing publication")
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise ValueError("canonical destination appeared during archive; rerun idempotently")
        shutil.move(str(staging), str(final))
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt_payload = {**plan, "status": "NFS_VERIFIED", "archive": str(final),
                           "verified_utc": utcnow(), "source_manifest_sha256": identity,
                           "archive_manifest_sha256": identity,
                           "local_present": True, "local_release_eligible": False}
        receipt.write_bytes(canonical(receipt_payload))
        receipt_hash = hash_file(receipt)
        write_atomic(progress, {"schema": "partition-archive-progress-v1", "partition_id": args.partition_id,
          "updated_utc": utcnow(), "phase": "COMPLETE", "archive": str(final), "receipt": str(receipt),
          "receipt_sha256": receipt_hash, "local_present": True})
        print(json.dumps({**receipt_payload, "receipt": str(receipt), "receipt_sha256": receipt_hash}, sort_keys=True))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(exc), "source": str(source)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
