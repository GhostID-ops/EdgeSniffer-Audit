#!/usr/bin/env python3
"""Read-only dependency audit for one evidence partition.

This is intentionally conservative.  It returns only DEPENDENCY_PRESENT,
DEPENDENCY_UNKNOWN, or DEPENDENCY_NONE_PROVEN and hashes the immutable audit
record.  It never changes pins, services, or source data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


TOOL_VERSION = "partition-dependency-audit-v1"


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canon(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def under(path: Path, ancestor: Path):
    try:
        path.resolve().relative_to(ancestor.resolve())
        return True
    except ValueError:
        return False


def file_snapshot(root: Path):
    result = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"unsupported symlink in partition: {item}")
        if item.is_file():
            stat = item.stat()
            result.append({"path": str(item.relative_to(root)), "bytes": stat.st_size,
                           "mtime_ns": stat.st_mtime_ns, "sha256": sha256(item)})
    return result


def proc_handles(partition: Path):
    matches = []
    proc = Path("/proc")
    if not proc.is_dir():
        # Unit tests may run on non-Linux hosts. Production audits require
        # Linux /proc and therefore record this separately through deployment.
        return matches
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = Path(os.readlink(fd))
                    if under(target, partition):
                        command = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                        matches.append({"pid": int(pid_dir.name), "fd": fd.name, "target": str(target), "cmdline": command})
                except (FileNotFoundError, PermissionError, OSError):
                    continue
        except (FileNotFoundError, PermissionError):
            continue
    return matches


def catalog_refs(db: Path, partition: Path):
    if not db.is_file():
        return {"catalog_error": "catalog-missing", "pins": [], "receipts": []}
    try:
        with sqlite3.connect(db) as conn:
            pins = conn.execute("SELECT experiment_id,path,pin_type,reason FROM pins").fetchall()
            receipts = conn.execute("SELECT receipt_path,receipt_sha256,receipt_kind FROM receipts").fetchall()
    except sqlite3.Error as exc:
        return {"catalog_error": str(exc), "pins": [], "receipts": []}
    relevant = []
    for experiment, raw_path, pin_type, reason in pins:
        pin_path = Path(raw_path)
        if under(partition, pin_path) or under(pin_path, partition):
            relevant.append({"experiment": experiment, "path": raw_path, "pin_type": pin_type, "reason": reason})
    return {"pins": relevant, "receipts": [{"path": p, "sha256": h, "kind": k} for p, h, k in receipts]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--partition-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--settle-seconds", type=float, default=3.0)
    args = parser.parse_args()
    partition = Path(args.partition).absolute(); manifest_path = partition / "manifest.json"
    if not partition.is_dir() or not manifest_path.is_file():
        raise SystemExit("FAIL_CLOSED: partition directory or manifest.json missing")
    manifest = json.loads(manifest_path.read_text())
    before = file_snapshot(partition)
    handles = proc_handles(partition)
    time.sleep(args.settle_seconds)
    after = file_snapshot(partition)
    catalog = catalog_refs(Path(args.catalog), partition)
    changed = before != after
    end = manifest.get("closedAtUtc")
    preboundary = bool(end and end < args.boundary_utc)
    temporal_closed = manifest.get("temporalHourClosed") is True
    replay_complete = manifest.get("exactReplayComplete") is True
    reasons = []
    if handles: reasons.append("open-file-handle")
    if changed: reasons.append("recent-mtime-or-content-activity")
    if catalog["pins"]: reasons.append("catalog-active-pin-overlap")
    if not temporal_closed: reasons.append("not-temporally-sealed")
    if not replay_complete: reasons.append("exact-replay-not-complete")
    if catalog.get("catalog_error"): reasons.append("catalog-unavailable")
    if not preboundary: reasons.append("not-before-active-boundary")
    if handles or changed or catalog["pins"]:
        outcome = "DEPENDENCY_PRESENT"
    elif not temporal_closed or not replay_complete or not preboundary or catalog.get("catalog_error"):
        outcome = "DEPENDENCY_UNKNOWN"
    else:
        outcome = "DEPENDENCY_NONE_PROVEN"
    audit = {
        "schema": "evidence-partition-dependency-audit-v1", "audit_tool_version": TOOL_VERSION,
        "audit_timestamp": utcnow(), "partition_id": args.partition_id, "absolute_path": str(partition),
        "capture_start": manifest.get("openedAtUtc"), "capture_end": end,
        "sealed_status": {"temporal_hour_closed": temporal_closed, "exact_replay_complete": replay_complete},
        "file_count": len(before), "total_bytes": sum(item["bytes"] for item in before),
        "file_sha256_identities": before, "declared_raw_sha256": manifest.get("rawSha256"),
        "declared_compressed_sha256": manifest.get("compressedSha256"), "open_file_handles": handles,
        "writers_detected": handles, "recent_mtime_activity": changed,
        "active_experiment_references": catalog["pins"], "shadow_references": catalog["pins"],
        "control_lane_references": catalog["pins"], "frozen_fingerprint_references": catalog["pins"],
        "promoted_references": [], "replay_dependencies": {"exact_replay_complete": replay_complete},
        "decoder_dependencies": "not-proven-absent-by-manifest", "feature_intermediate_dependencies": "not-proven-absent-by-manifest",
        "result_provenance_references": "not-proven-absent-by-manifest", "catalog_references": catalog,
        "receipt_references": catalog["receipts"], "outcome": outcome, "reasons": reasons,
        "local_release_permitted": False,
    }
    out_dir = Path(args.output_dir).absolute(); out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = args.partition_id.replace("/", "-").replace(":", "-")
    output = out_dir / f"{safe_id}.json"
    output.write_bytes(canon(audit))
    print(json.dumps({"outcome": outcome, "audit": str(output), "audit_sha256": sha256(output), "reasons": reasons}, sort_keys=True))


if __name__ == "__main__":
    main()
