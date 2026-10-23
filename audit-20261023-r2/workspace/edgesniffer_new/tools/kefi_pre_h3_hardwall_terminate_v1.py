#!/usr/bin/env python3
"""Record a PRE_H3 hard-wall termination without opening any outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path: Path, value: object) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary", type=Path, required=True)
    parser.add_argument("--source-health", type=Path, required=True)
    parser.add_argument("--pause-receipt", type=Path, required=True)
    args = parser.parse_args()
    boundary = json.loads(args.boundary.read_text())
    health = json.loads(args.source_health.read_text())
    if not any((health.get("reconnects", 0), health.get("continuityGaps", 0), health.get("receiveErrors", 0))):
        raise SystemExit("FAIL_CLOSED: source health does not prove a PRE_H3 hard wall")
    if "no bridge" not in boundary.get("hardWallPolicy", "").lower():
        raise SystemExit("FAIL_CLOSED: boundary does not carry no-bridge hard-wall policy")
    output = args.boundary.parent / "hard-wall-termination-v1.json"
    write_once(output, {
        "schema": "PRE_H3_ACTIONABILITY_QUALIFICATION_V1_HARD_WALL_TERMINATION",
        "status": "TERMINATED_INVALID_NO_BRIDGE",
        "boundary_sha256": sha(args.boundary),
        "source_health_sha256": sha(args.source_health),
        "capacity_pause_sha256": sha(args.pause_receipt),
        "source_instance_id": health.get("sourceInstanceId"),
        "continuity_epoch": health.get("currentEpoch"),
        "reconnects": health.get("reconnects"),
        "continuity_gaps": health.get("continuityGaps"),
        "receive_errors": health.get("receiveErrors"),
        "last_durable_received_at_utc": health.get("latestReceivedAtUtc"),
        "reason": "Frozen contract defines source-instance changes, epoch changes, reconnects, continuity gaps, receive errors, and scientific integrity failures as hard walls. Capacity pause stopped the source after those events; cohort cannot bridge or restart.",
        "outcomes_or_pnl_accessed": False,
    })
    print(json.dumps({"termination": str(output), "sha256": sha(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
