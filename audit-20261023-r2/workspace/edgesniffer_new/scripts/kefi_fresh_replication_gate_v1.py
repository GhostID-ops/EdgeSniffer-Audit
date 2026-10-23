#!/usr/bin/env python3
"""Create a fresh kEfi paper boundary only after a clean live source gate."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, value: object) -> str:
    if path.exists():
        raise RuntimeError(f"FAIL_CLOSED: immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(canonical(value))
    os.replace(temp, path)
    return sha(path)


def window_id(at: dt.datetime) -> str:
    return "kefi-" + at.strftime("%Y%m%dT%H%M%S") + f"{at.microsecond // 1000:03d}Z"


def unit_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--pre-registration", required=True)
    parser.add_argument("--collector-unit", required=True)
    parser.add_argument("--watchdog-unit", required=True)
    parser.add_argument("--materializer-timer", required=True)
    parser.add_argument("--collector-binary", required=True)
    parser.add_argument("--collector-unit-file", required=True)
    parser.add_argument("--watchdog-unit-file", required=True)
    parser.add_argument("--required-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    root = Path(args.source_root)
    health_path = root / "health.json"
    pre = Path(args.pre_registration)
    boundary_path = root / "scientific-boundaries" / "paper-boundary-v1.json"
    gate_path = root / "scientific-boundaries" / "live-gate-v1.json"
    if boundary_path.exists() or gate_path.exists():
        raise SystemExit("FAIL_CLOSED: target root already has a gate or boundary")
    if not pre.is_file():
        raise SystemExit("FAIL_CLOSED: immutable pre-registration is absent")
    pre_payload = json.loads(pre.read_text())
    if pre_payload.get("status") != "PRE_REGISTERED":
        raise SystemExit("FAIL_CLOSED: invalid pre-registration")
    started_at: dt.datetime | None = None
    while True:
        if not health_path.is_file() or not unit_active(args.collector_unit) or not unit_active(args.watchdog_unit):
            time.sleep(args.poll_seconds)
            continue
        try:
            health = json.loads(health_path.read_text())
            current = utcnow()
            latest_received = health.get("latestReceivedAtUtc")
            started = health.get("startedAtUtc")
            if not latest_received or not started:
                time.sleep(args.poll_seconds)
                continue
            received = dt.datetime.fromisoformat(latest_received.replace("Z", "+00:00"))
            origin = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
            if started_at is None:
                started_at = origin
            clean = (
                origin == started_at
                and (current - received).total_seconds() <= 45
                and int(health.get("framesReceived", 0)) > 0
                and int(health.get("continuityGaps", 0)) == 0
                and int(health.get("reconnects", 0)) == 0
                and int(health.get("receiveErrors", 0)) == 0
                and int(health.get("currentEpoch", 0)) == 1
            )
            if clean and (current - started_at).total_seconds() >= args.required_seconds:
                break
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(args.poll_seconds)

    created = utcnow()
    gate = {
        "schema": "KEFI_CAPTURE_FIRST_REPLICATION_LIVE_GATE_V1",
        "status": "PASS",
        "checkedAtUtc": timestamp(created),
        "requiredCleanSeconds": args.required_seconds,
        "sourceStartedAtUtc": health["startedAtUtc"],
        "sourceEpoch": health["currentEpoch"],
        "framesReceived": health["framesReceived"],
        "latestReceivedAtUtc": health["latestReceivedAtUtc"],
        "latestSlot": health.get("latestSlot"),
        "continuityGaps": 0,
        "reconnects": 0,
        "receiveErrors": 0,
        "collectorUnit": args.collector_unit,
        "watchdogUnit": args.watchdog_unit,
    }
    gate_hash = write_once(gate_path, gate)
    boundary = {
        "schema": "KEFI_CAPTURE_FIRST_REPLICATION_PAPER_BOUNDARY_V1",
        "status": "ACTIVE",
        "boundaryId": "kefi-replication-v1-" + created.strftime("%Y%m%dT%H%M%S%fZ"),
        "boundaryUtc": timestamp(created),
        "windowNotBeforeId": window_id(created),
        "sourceRoot": str(root),
        "sourceEpoch": health["currentEpoch"],
        "sourceFramesReceivedAtBoundary": health["framesReceived"],
        "sourceLatestReceivedAtBoundaryUtc": health["latestReceivedAtUtc"],
        "sourceLatestSlotAtBoundary": health.get("latestSlot"),
        "preRegistrationPath": str(pre),
        "preRegistrationSha256": sha(pre),
        "liveGatePath": str(gate_path),
        "liveGateSha256": gate_hash,
        "collectorBinary": args.collector_binary,
        "collectorBinarySha256": sha(Path(args.collector_binary)),
        "collectorUnitSha256": sha(Path(args.collector_unit_file)),
        "watchdogUnitSha256": sha(Path(args.watchdog_unit_file)),
        "rules": {"sourceFreshnessRequired": True, "continuityGapPolicy": "FAIL_CLOSED", "recoveryOverlapSlots": 32, "preBoundaryEvidenceExcluded": True, "outcomeBlind": True},
    }
    boundary_hash = write_once(boundary_path, boundary)
    subprocess.run(["systemctl", "enable", "--now", args.materializer_timer], check=True)
    print(json.dumps({"status": "BOUNDARY_CREATED", "boundary": str(boundary_path), "boundary_sha256": boundary_hash, "gate": str(gate_path), "gate_sha256": gate_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
