#!/usr/bin/env python3
"""Fail closed before the full-meta collector can exhaust its filesystem."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def service_is_active(service: str) -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", service], check=False
    ).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--collector-service", required=True)
    parser.add_argument("--warning-free-gb", type=float, default=50.0)
    parser.add_argument("--emergency-free-gb", type=float, default=12.0)
    parser.add_argument("--execute-emergency-pause", action="store_true")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    volumes = []
    for raw_path in args.path:
        target = Path(raw_path)
        usage = os.statvfs(target)
        free_bytes = usage.f_bavail * usage.f_frsize
        total_bytes = usage.f_blocks * usage.f_frsize
        volumes.append({
            "path": str(target),
            "totalBytes": total_bytes,
            "freeBytes": free_bytes,
            "freeGb": free_bytes / 1_000_000_000,
        })
    previous_path = state_dir / "latest.json"
    previous = {}
    if previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}

    status = "HEALTHY"
    pause_recorded = False
    emergency_volumes = [volume for volume in volumes if volume["freeGb"] < args.emergency_free_gb]
    warning_volumes = [volume for volume in volumes if volume["freeGb"] < args.warning_free_gb]
    if emergency_volumes:
        status = "EMERGENCY_CAPACITY_PAUSE_REQUIRED"
        if args.execute_emergency_pause and service_is_active(args.collector_service):
            subprocess.run(["systemctl", "stop", args.collector_service], check=True)
            pause_recorded = True
            write_json_atomic(
                state_dir / "capacity-pause.json",
                {
                    "schema": "2CQ_FULL_META_CAPACITY_GUARD_V1",
                    "recordedAtUtc": utc_now(),
                    "reason": "FREE_SPACE_BELOW_EMERGENCY_THRESHOLD",
                    "affectedVolumes": emergency_volumes,
                    "collectorService": args.collector_service,
                    "collectorStopped": True,
                    "collectionInterval": "PAUSED_INVALID_FOR_COLLECTION_UNTIL_MANUAL_RESUME",
                },
            )
    elif warning_volumes:
        status = "WARNING_LOW_CAPACITY"

    payload = {
        "schema": "2CQ_FULL_META_CAPACITY_GUARD_V1",
        "generatedAtUtc": utc_now(),
        "volumes": volumes,
        # Keep the primary-volume fields for existing dashboard readers.
        "path": volumes[0]["path"],
        "totalBytes": volumes[0]["totalBytes"],
        "freeBytes": volumes[0]["freeBytes"],
        "freeGb": volumes[0]["freeGb"],
        "warningFreeGb": args.warning_free_gb,
        "emergencyFreeGb": args.emergency_free_gb,
        "collectorService": args.collector_service,
        "collectorActive": service_is_active(args.collector_service),
        "status": status,
        "emergencyPauseRecorded": pause_recorded,
        "previousFreeBytes": previous.get("freeBytes"),
    }
    write_json_atomic(previous_path, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
