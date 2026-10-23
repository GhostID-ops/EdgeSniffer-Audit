#!/usr/bin/env python3
"""Fail-closed release of one verified inactive directory to its NFS twin.

The local pathname is retained as a symlink to the canonical NFS archive.  The
tool deliberately requires a fresh dependency-none audit and rehashes both
trees before any local bytes are removed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(root: Path):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("tree must be a real directory")
    files = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"symlink in release tree: {item}")
        if item.is_file():
            files.append({"path": str(item.relative_to(root)), "bytes": item.stat().st_size, "sha256": sha(item)})
    if not files:
        raise ValueError("empty tree is not releaseable")
    raw = canonical({"schema": "partition-tree-manifest-v1", "files": files})
    return files, hashlib.sha256(raw).hexdigest(), sum(item["bytes"] for item in files)


def load(path: Path):
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-tree", required=True)
    parser.add_argument("--archive-tree", required=True)
    parser.add_argument("--archive-receipt", required=True)
    parser.add_argument("--dependency-audit", required=True)
    parser.add_argument("--release-receipt-dir", required=True)
    parser.add_argument("--mode", choices=["PLAN", "VERIFY", "RELEASE"], default="PLAN")
    args = parser.parse_args()
    local = Path(args.local_tree).absolute()
    archive = Path(args.archive_tree).absolute()
    archive_receipt = Path(args.archive_receipt).absolute()
    audit_path = Path(args.dependency_audit).absolute()
    try:
        audit = load(audit_path)
        receipt = load(archive_receipt)
        if audit.get("outcome") != "DEPENDENCY_NONE_PROVEN":
            raise ValueError("dependency audit does not prove absence")
        if receipt.get("status") not in {"NFS_VERIFIED", "ALREADY_VERIFIED"}:
            raise ValueError("archive receipt is not NFS_VERIFIED")
        if receipt.get("source") != str(local):
            raise ValueError("archive receipt source path mismatch")
        local_files, local_identity, local_bytes = manifest(local)
        archive_files, archive_identity, archive_bytes = manifest(archive)
        expected = receipt.get("source_manifest_sha256")
        if not expected or local_identity != expected or archive_identity != expected:
            raise ValueError("tree identity does not match archive receipt")
        if local_files != archive_files or local_bytes != archive_bytes:
            raise ValueError("tree inventories differ")
        plan = {
            "schema": "tree-transparent-release-v1", "mode": args.mode,
            "local_tree": str(local), "archive_tree": str(archive),
            "tree_sha256": local_identity, "released_bytes": local_bytes,
            "archive_receipt_sha256": sha(archive_receipt),
            "dependency_audit_sha256": sha(audit_path),
            "archive_verified": True, "local_release_permitted": args.mode == "RELEASE",
        }
        if args.mode != "RELEASE":
            print(json.dumps(plan, sort_keys=True)); return 0
        backup = local.with_name(local.name + ".release-backup-" + local_identity[:12])
        if backup.exists() or backup.is_symlink():
            raise ValueError("release backup path already exists")
        os.replace(local, backup)
        try:
            os.symlink(archive, local, target_is_directory=True)
            linked_files, linked_identity, linked_bytes = manifest(local.resolve())
            if linked_identity != local_identity or linked_files != local_files or linked_bytes != local_bytes:
                raise ValueError("post-link tree verification failed")
            shutil.rmtree(backup)
        except Exception:
            if local.is_symlink():
                local.unlink()
            os.replace(backup, local)
            raise
        output_dir = Path(args.release_receipt_dir).absolute()
        output_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = output_dir / (local.name + ".release.json")
        release = {**plan, "released_at_utc": utcnow(), "local_released": True,
                   "local_path_now_symlink": str(local), "previous_release_receipt_sha256": None}
        receipt_path.write_bytes(canonical(release))
        release["release_receipt_sha256"] = sha(receipt_path)
        print(json.dumps({**release, "release_receipt": str(receipt_path)}, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
