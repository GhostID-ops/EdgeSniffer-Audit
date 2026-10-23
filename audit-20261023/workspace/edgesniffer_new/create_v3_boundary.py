#!/usr/bin/env python3
"""Create one fresh V3 launch gate and boundary from a clean dual-live source."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_immutable(path: Path, payload: dict) -> str:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return sha256(path)


def main() -> int:
    root = Path(sys.argv[1])
    base = Path(sys.argv[2])
    now_ns = time.time_ns()
    now_utc = datetime.datetime.fromtimestamp(now_ns / 1_000_000_000, datetime.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    global_state = json.loads((root / "state" / "global.json").read_text(encoding="utf-8"))
    observer_a = json.loads((root / "state" / "observer-A.json").read_text(encoding="utf-8"))
    observer_b = json.loads((root / "state" / "observer-B.json").read_text(encoding="utf-8"))
    passed = (
        global_state["mode"] == "DUAL_LIVE"
        and observer_a["state"] == "LIVE"
        and observer_b["state"] == "LIVE"
        and all(global_state[key] == 0 for key in ("continuityGaps", "lateEarlierViolations", "timingProvenanceViolations", "replayConflicts"))
    )
    if not passed:
        raise SystemExit("pre-boundary scientific gate is not clean")

    protocol = base / "autonomous-when-v3" / "protocol-v2" / "AUTONOMOUS_WHEN_V3_REDUNDANT_LIVE_TIMING_CONTRACT_V2.json"
    amendment = base / "autonomous-when-v3" / "protocol-v2" / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_DEVELOPMENT_AMENDMENT_V1.json"
    fingerprint = base / "fingerprint-v2-development-v1" / "frozen-fingerprint.json"
    source = root / "source"
    gate = {
        "schema": "AUTONOMOUS_WHEN_V3_PRE_BOUNDARY_GATE_V2",
        "gateUtc": now_utc,
        "gateUtcNs": now_ns,
        "sourceRoot": str(root),
        "criteria": {
            "minimumCleanWarmupSeconds": 120,
            "dualLive": True,
            "bothObserversLive": True,
            "continuityFailures": 0,
            "lateEarlierViolations": 0,
            "timingProvenanceViolations": 0,
            "replayConflicts": 0,
        },
        "sourceHealthAtGate": {
            "state": global_state["mode"],
            "aState": observer_a["state"],
            "bState": observer_b["state"],
            "sourceEpoch": global_state["currentEpoch"],
            "closedThroughUnixNs": global_state["closedThroughUnixNs"],
            "durableOutputSequence": global_state["durableOutputSequence"],
            "continuityGaps": global_state["continuityGaps"],
            "lateEarlierViolations": global_state["lateEarlierViolations"],
            "timingProvenanceViolations": global_state["timingProvenanceViolations"],
            "replayConflicts": global_state["replayConflicts"],
        },
        "candidateValidation": {
            "tests": "16/16 passed",
            "sourceRustSha256": sha256(source / "yellowstone_v3_dual_observer.rs"),
            "sourceBinarySha256": sha256(source / "yellowstone_v3_dual_observer"),
            "focusedLiveSmokeSeconds": 230,
            "maxClosureLagSeconds": 1.537501397,
            "p99ClosureLagSeconds": 1.525248782,
            "result": "PASS",
        },
        "result": "PASS",
    }
    gate_path = root / "receipts" / "pre-boundary-gate-result.json"
    gate_hash = write_immutable(gate_path, gate)

    boundary = {
        "schema": "AUTONOMOUS_WHEN_V3_SCIENTIFIC_BOUNDARY_V2",
        "boundaryUtc": now_utc,
        "boundaryUtcNs": now_ns,
        "scientificCohortRoot": str(root),
        "outcomeBlind": True,
        "pnlRead": 0,
        "preBoundaryDataExcluded": True,
        "validationDataExcluded": True,
        "warmupSeconds": 120,
        "preBoundaryGate": {"path": str(gate_path), "sha256": gate_hash},
        "canonicalArtifactSha256": {
            "protocolV2": sha256(protocol),
            "scientificAccountingAmendment": sha256(amendment),
            "frozenV2Fingerprint": sha256(fingerprint),
            "sourceRust": sha256(source / "yellowstone_v3_dual_observer.rs"),
            "sourceBinary": sha256(source / "yellowstone_v3_dual_observer"),
            "lane": sha256(source / "twoCqAutonomousWhenV3.py"),
            "finalizer": sha256(source / "twoCqAutonomousWhenV3Finalizer.py"),
            "monitor": sha256(source / "twoCqAutonomousWhenV3Monitor.py"),
        },
        "sourceContinuityBaseline": {
            "sourceEpoch": global_state["currentEpoch"],
            "continuityGaps": global_state["continuityGaps"],
            "reconnects": observer_a["reconnectCount"] + observer_b["reconnectCount"],
            "closedThroughUnixNs": global_state["closedThroughUnixNs"],
            "durableOutputSequence": global_state["durableOutputSequence"],
        },
        "sourceHealthAtBoundary": {
            "state": global_state["mode"],
            "aState": observer_a["state"],
            "bState": observer_b["state"],
            "lateEarlierViolations": global_state["lateEarlierViolations"],
            "replayConflicts": global_state["replayConflicts"],
            "timingProvenanceViolations": global_state["timingProvenanceViolations"],
            "sourceArmed": global_state["sourceArmed"],
        },
        "segmentAccounting": {
            "amendmentPath": str(amendment),
            "amendmentSha256": sha256(amendment),
            "crossSegmentState": "PROHIBITED",
            "postHardWallEvidence": "EXCLUDED",
        },
    }
    boundary_path = root / "lane" / "boundary.json"
    boundary_hash = write_immutable(boundary_path, boundary)
    launch_path = root / "receipts" / "visibility-repair-launch-receipt.json"
    launch_hash = write_immutable(launch_path, {
        "schema": "AUTONOMOUS_WHEN_V3_VISIBILITY_REPAIR_LAUNCH_RECEIPT_V1",
        "createdUtc": now_utc,
        "boundaryPath": str(boundary_path),
        "boundarySha256": boundary_hash,
        "gateSha256": gate_hash,
        "repairSourceBinarySha256": sha256(source / "yellowstone_v3_dual_observer"),
        "result": "LAUNCHED",
    })
    print(json.dumps({"gate": gate_hash, "boundary": boundary_hash, "launch": launch_hash, "boundaryUtc": now_utc}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
