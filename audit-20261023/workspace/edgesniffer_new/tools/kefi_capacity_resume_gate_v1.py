#!/usr/bin/env python3
"""Fail-closed capacity recovery gate for the retired V8 promotion cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_MOUNT = "/mnt/edgesniffer-kefi-hot"
EXPECTED_SOURCE = "/dev/vdc1"
MIN_FREE_GIB = 150


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause", type=Path, required=True)
    parser.add_argument("--reclaim-receipt", type=Path, required=True)
    parser.add_argument("--dependency-audit", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    if args.receipt.exists() or not args.pause.is_file():
        raise SystemExit("FAIL_CLOSED: missing pause or pre-existing recovery receipt")
    reclaim = json.loads(args.reclaim_receipt.read_text())
    audit = json.loads(args.dependency_audit.read_text())
    if audit.get("classification") != "DEPENDENCY_NONE_PROVEN":
        raise SystemExit("FAIL_CLOSED: dependency proof is not clean")
    if reclaim.get("dependency_audit_sha256") != sha(args.dependency_audit):
        raise SystemExit("FAIL_CLOSED: reclaim is not bound to dependency audit")
    if int(reclaim.get("released_bytes", 0)) <= 0:
        raise SystemExit("FAIL_CLOSED: no verified released bytes")

    stat = shutil.disk_usage(EXPECTED_MOUNT)
    free_gib = stat.free / (1024 ** 3)
    if free_gib < MIN_FREE_GIB:
        raise SystemExit("FAIL_CLOSED: hot-volume floor not met")
    mount_source = os.popen(f"findmnt -no SOURCE --target {EXPECTED_MOUNT}").read().strip()
    if mount_source != EXPECTED_SOURCE:
        raise SystemExit("FAIL_CLOSED: unexpected hot-volume mount identity")

    result = {
        "schema": "KEFI_CAPACITY_RECOVERY_RESUME_GATE_V1",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mount": EXPECTED_MOUNT,
        "mount_source": mount_source,
        "free_gib": round(free_gib, 3),
        "minimum_free_gib": MIN_FREE_GIB,
        "pause_sha256": sha(args.pause),
        "dependency_audit_sha256": sha(args.dependency_audit),
        "reclaim_receipt_sha256": sha(args.reclaim_receipt),
        "released_bytes": reclaim["released_bytes"],
        "outcomes_or_pnl_accessed": False,
    }
    write_once(args.receipt, result)
    archived_pause = args.pause.with_name(args.pause.stem + ".cleared-" + result["recorded_at_utc"].replace(":", "").replace("Z", "Z") + args.pause.suffix)
    os.replace(args.pause, archived_pause)
    print(json.dumps({"receipt": str(args.receipt), "sha256": sha(args.receipt), "archived_pause": str(archived_pause), "free_gib": result["free_gib"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
