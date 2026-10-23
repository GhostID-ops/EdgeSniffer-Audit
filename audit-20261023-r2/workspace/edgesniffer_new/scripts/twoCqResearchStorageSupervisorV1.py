#!/usr/bin/env python3
"""Read-only 2CQ storage-tier supervisor with bounded cold-archive dispatch.

The collector's local filesystem is deliberately the hot tier: it holds the
open hourly ledger, currently unresolved causal windows, active state, and
hot promoted evidence.  It must never be used as the only long-term archive.

This supervisor does not delete, move, or relink evidence.  It reports the
current tier budget and, when local free space is below the configured target,
may ask the existing verified closed-partition archiver to run one bounded
batch.  That archiver owns the copy/SHA-256/receipt/atomic-release protocol.
Any non-partition tree requires an explicit source classification and its own
verified receipt; this script will only identify it as pending review.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import tempfile
from typing import Any


SCHEMA = "2CQ_RESEARCH_STORAGE_SUPERVISOR_V1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = pathlib.Path(handle.name)
    os.replace(temporary, path)


def usage(path: pathlib.Path) -> dict[str, Any]:
    stat = os.statvfs(path)
    return {
        "path": str(path),
        "totalBytes": stat.f_blocks * stat.f_frsize,
        "freeBytes": stat.f_bavail * stat.f_frsize,
        "freeGiB": round((stat.f_bavail * stat.f_frsize) / (1024 ** 3), 3),
    }


def service_state(name: str) -> str:
    completed = subprocess.run(
        ["systemctl", "is-active", name], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return completed.stdout.strip() or "unknown"


def dir_bytes_shallow(path: pathlib.Path) -> int | None:
    """Return a cheap direct-file total; never walk an evidence tree."""
    try:
        return sum(entry.stat(follow_symlinks=False).st_size for entry in path.iterdir() if entry.is_file())
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=pathlib.Path, required=True)
    parser.add_argument("--nfs-root", type=pathlib.Path, required=True)
    parser.add_argument("--state", type=pathlib.Path, required=True)
    parser.add_argument("--target-free-gib", type=float, default=150.0)
    parser.add_argument("--warning-free-gib", type=float, default=100.0)
    parser.add_argument("--emergency-free-gib", type=float, default=50.0)
    parser.add_argument("--dispatch-verified-raw-archive", action="store_true")
    parser.add_argument("--verified-raw-archive-service", default="edgesniffer-2cq-full-meta-verified-cold-archive-v1.service")
    args = parser.parse_args()

    if not (args.emergency_free_gib < args.warning_free_gib < args.target_free_gib):
        raise SystemExit("INVALID_WATERMARK_ORDER")
    if not args.research_root.is_dir() or not args.nfs_root.is_dir():
        raise SystemExit("STORAGE_ROOT_UNAVAILABLE")

    hot = usage(args.research_root)
    cold = usage(args.nfs_root)
    free = hot["freeGiB"]
    if free < args.emergency_free_gib:
        status = "EMERGENCY_CAPACITY_PAUSED_OR_PAUSE_REQUIRED"
    elif free < args.warning_free_gib:
        status = "WARNING_ARCHIVE_REQUIRED"
    elif free < args.target_free_gib:
        status = "BELOW_OPERATING_HEADROOM"
    else:
        status = "HEALTHY_HEADROOM"

    dispatched = False
    archive_state = service_state(args.verified_raw_archive_service)
    if args.dispatch_verified_raw_archive and free < args.target_free_gib and archive_state != "active":
        result = subprocess.run(["systemctl", "start", args.verified_raw_archive_service], check=False)
        dispatched = result.returncode == 0
        archive_state = service_state(args.verified_raw_archive_service)

    two_cq = args.research_root / "2cq-yellowstone-full-meta-prospective-v1"
    known_protected = [
        "partitions/current-hour-or-open-partial",
        "promoted-windows/unresolved-or-active",
        "automation/n-worker.sqlite3-and-proof-cache",
        "state-plane",
        "autonomous-when-v3-capture-first/active-or-sealed-scientific-evidence",
    ]
    pending_explicit_review = [
        str(args.research_root / "2cq-launchlab-coverage-v1/rpc-cache"),
        str(two_cq / "promoted-windows"),
        str(two_cq / "aborted-partial-ledgers"),
    ]
    payload = {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "status": status,
        "hotTier": hot,
        "coldTier": cold,
        "watermarksGiB": {
            "target": args.target_free_gib,
            "warning": args.warning_free_gib,
            "emergency": args.emergency_free_gib,
        },
        "verifiedRawArchive": {
            "service": args.verified_raw_archive_service,
            "state": archive_state,
            "dispatched": dispatched,
            "safety": "ARCHIVER_OWNS_COPY_SHA256_RECEIPT_AND_ATOMIC_RELEASE",
        },
        "hotTierRules": {
            "openEvidenceNeverMoved": True,
            "unresolvedWindowsNeverMoved": True,
            "activePromotedEvidenceNeverMoved": True,
            "localHotPathRemainsLocal": True,
        },
        "protectedClasses": known_protected,
        "requiresExplicitVerifiedArchiveReview": pending_explicit_review,
        "shallowRootBytes": dir_bytes_shallow(two_cq) if two_cq.is_dir() else None,
        "outcomeBlind": True,
        "destructiveActionTaken": False,
    }
    atomic_json(args.state, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
