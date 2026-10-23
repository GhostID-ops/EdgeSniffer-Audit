#!/usr/bin/env python3
"""Freeze a new V3 boundary only after the source warm-up gate passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_immutable(path: Path, payload: dict) -> None:
    if path.exists():
        raise SystemExit(f"immutable output exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".partial")
    with temp.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    path.chmod(0o444)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    health = json.loads((root / "health.json").read_text(encoding="utf-8"))
    global_state = health.get("global") or {}
    observer_a = health.get("observers", {}).get("A", {})
    observer_b = health.get("observers", {}).get("B", {})
    criteria = {
        "bothObserversLive": observer_a.get("state") == "LIVE" and observer_b.get("state") == "LIVE",
        "dualLive": health.get("globalState") == "DUAL_LIVE",
        "continuityFailures": int(health.get("continuityGaps", -1)) == 0,
        "replayConflicts": int(global_state.get("replayConflicts", -1)) == 0,
        "timingProvenanceViolations": int(global_state.get("timingProvenanceViolations", -1)) == 0,
        "lateEarlierViolations": int(global_state.get("lateEarlierViolations", -1)) == 0,
        "sourceArmed": global_state.get("sourceArmed") is True,
    }
    if not all(criteria.values()):
        raise SystemExit(f"pre-boundary gate failed: {criteria}")
    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(observer_a["lastHealthTransitionUtc"].replace("Z", "+00:00"))
    if (now - started).total_seconds() < 120:
        raise SystemExit("pre-boundary warm-up under 120 seconds")
    source_dir = BASE / "autonomous-when-v3" / "source"
    artifacts = {
        "sourcePython": sha256(source_dir / "twoCqAutonomousWhenV3.py"),
        "sourceBinary": sha256(Path("/root/bot/EdgeSniffer/rust-exact-ledger/target/release/yellowstone_v3_dual_observer")),
        "lane": sha256(source_dir / "twoCqAutonomousWhenV3.py"),
        "finalizer": sha256(source_dir / "twoCqAutonomousWhenV3Finalizer.py"),
        "monitor": sha256(source_dir / "twoCqAutonomousWhenV3Monitor.py"),
        "frozenV2Fingerprint": sha256(BASE / "fingerprint-v2-development-v1" / "frozen-fingerprint.json"),
        "protocolV2": sha256(BASE / "autonomous-when-v3" / "protocol-v2" / "AUTONOMOUS_WHEN_V3_REDUNDANT_LIVE_TIMING_CONTRACT_V2.json"),
        "scientificAccountingAmendment": sha256(BASE / "autonomous-when-v3" / "protocol-v2" / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_DEVELOPMENT_AMENDMENT_V1.json"),
    }
    gate_time = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    baseline = {
        "sourceEpoch": health["currentEpoch"],
        "continuityGaps": health["continuityGaps"],
        "reconnects": observer_a.get("reconnectCount", 0) + observer_b.get("reconnectCount", 0),
        "closedThroughUnixNs": health["closedThroughUnixNs"],
        "durableOutputSequence": health["durableOutputSequence"],
    }
    gate = {
        "schema": "AUTONOMOUS_WHEN_V3_PRE_BOUNDARY_GATE_V2",
        "result": "PASS",
        "gateUtc": gate_time,
        "criteria": {**criteria, "minimumCleanWarmupSeconds": 120},
        "sourceRoot": str(root),
        "sourceHealthAtGate": {"state": health["globalState"], **baseline, "aState": observer_a["state"], "bState": observer_b["state"], "lateEarlierViolations": global_state["lateEarlierViolations"], "replayConflicts": global_state["replayConflicts"], "timingProvenanceViolations": global_state["timingProvenanceViolations"]},
    }
    gate_path = root / "receipts" / "pre-boundary-gate-result.json"
    atomic_immutable(gate_path, gate)
    boundary = {
        "schema": "AUTONOMOUS_WHEN_V3_SCIENTIFIC_BOUNDARY_V2",
        "boundaryUtc": gate_time,
        "boundaryUtcNs": int(now.timestamp() * 1_000_000_000),
        "scientificCohortRoot": str(root),
        "warmupSeconds": 120,
        "outcomeBlind": True,
        "pnlRead": 0,
        "preBoundaryDataExcluded": True,
        "validationDataExcluded": True,
        "canonicalArtifactSha256": artifacts,
        "preBoundaryGate": {"path": str(gate_path), "sha256": sha256(gate_path)},
        "sourceContinuityBaseline": baseline,
        "sourceHealthAtBoundary": {"state": health["globalState"], "aState": observer_a["state"], "bState": observer_b["state"], "sourceArmed": global_state["sourceArmed"], "lateEarlierViolations": global_state["lateEarlierViolations"], "replayConflicts": global_state["replayConflicts"], "timingProvenanceViolations": global_state["timingProvenanceViolations"]},
        "segmentAccounting": {"amendmentPath": str(BASE / "autonomous-when-v3" / "protocol-v2" / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_DEVELOPMENT_AMENDMENT_V1.json"), "amendmentSha256": artifacts["scientificAccountingAmendment"], "crossSegmentState": "PROHIBITED", "postHardWallEvidence": "EXCLUDED"},
    }
    boundary_path = root / "lane" / "boundary.json"
    atomic_immutable(boundary_path, boundary)
    print(json.dumps({"gate": str(gate_path), "gate_sha256": sha256(gate_path), "boundary": str(boundary_path), "boundary_sha256": sha256(boundary_path), "boundary_utc": gate_time}, sort_keys=True))


if __name__ == "__main__":
    main()
