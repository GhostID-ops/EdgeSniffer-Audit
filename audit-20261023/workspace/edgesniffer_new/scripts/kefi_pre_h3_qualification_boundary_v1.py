#!/usr/bin/env python3
"""Create the one immutable PRE_H3_ACTIONABILITY_QUALIFICATION_V1 boundary.

The command deliberately has no evaluator path.  It only freezes the launch
contract, records the source baseline, and opens one FUTURE_UNOPENED epoch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path


SCHEMA = "PRE_H3_ACTIONABILITY_QUALIFICATION_V1_BOUNDARY_V1"
MAX_HEALTH_AGE_SECONDS = 60
ALLOWED_DEPLOYMENT_STATUSES = {"FROZEN_DEPLOYMENT", "FROZEN_REMEDIATED_DEPLOYMENT_READY"}
BASELINE_ZERO_FIELDS = (
    "reconnects", "continuityGaps", "receiveErrors", "rollingWindowPromotionFailures",
    "rollingWindowQueueFull", "sharedBurstGroupsFailed",
)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def atomic_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())


def load_module(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"FAIL_CLOSED: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def healthy_source(source_root: Path) -> tuple[dict, str]:
    health_path = source_root / "health.json"
    if not health_path.is_file():
        raise RuntimeError("FAIL_CLOSED: source health missing")
    health = json.loads(health_path.read_text())
    latest = parse_utc(str(health.get("latestReceivedAtUtc", "")))
    age = (dt.datetime.now(dt.timezone.utc) - latest).total_seconds()
    if age > MAX_HEALTH_AGE_SECONDS:
        raise RuntimeError(f"FAIL_CLOSED: source stale for {age:.1f}s")
    if not str(health.get("sourceInstanceId", "")) or int(health.get("currentEpoch", -1)) < 0:
        raise RuntimeError("FAIL_CLOSED: source identity unavailable")
    bad = {field: health.get(field) for field in BASELINE_ZERO_FIELDS if int(health.get(field, 0)) != 0}
    if bad:
        raise RuntimeError(f"FAIL_CLOSED: source is not a clean launch baseline {bad}")
    return health, sha256(health_path)


def verify_deployment(path: Path, expected_sha256: str) -> dict:
    """Fail closed unless the exact reviewed bundle and its listed files match."""
    actual = sha256(path)
    if actual != expected_sha256:
        raise RuntimeError("FAIL_CLOSED: deployment manifest SHA-256 mismatch")
    deployment = json.loads(path.read_text())
    if deployment.get("status") not in ALLOWED_DEPLOYMENT_STATUSES:
        raise RuntimeError("FAIL_CLOSED: deployment manifest is not frozen")
    required = ("canonical_input_required", "receipt_concatenation_forbidden", "voided_epoch_exclusion_required")
    if any(deployment.get(field) is not True for field in required):
        raise RuntimeError("FAIL_CLOSED: deployment manifest lacks required remediation protections")
    for name, expected in dict(deployment.get("files", {})).items():
        candidate = path.parent / name
        if not candidate.is_file() or sha256(candidate) != expected:
            raise RuntimeError(f"FAIL_CLOSED: deployment component hash mismatch: {name}")
    return deployment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--registry-script", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--deployment-manifest", type=Path, required=True)
    parser.add_argument("--expected-deployment-sha256", required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    args = parser.parse_args()

    if args.cohort_root.exists() and any(args.cohort_root.iterdir()):
        raise SystemExit("FAIL_CLOSED: qualification cohort root already exists")
    for path in (args.registry_script, args.deployment_manifest, args.spec, args.fingerprint):
        if not path.is_file():
            raise SystemExit(f"FAIL_CLOSED: required frozen launch input missing: {path}")
    deployment = verify_deployment(args.deployment_manifest, args.expected_deployment_sha256)
    health, health_hash = healthy_source(args.source_root)
    boundary = utc_now()
    boundary_id = "pre-h3-actionability-qualification-v1-" + boundary.replace("-", "").replace(":", "").replace(".", "")
    seed = hashlib.sha256(
        f"PRE_H3_ACTIONABILITY_QUALIFICATION_V1|{boundary}|{sha256(args.fingerprint)}|{sha256(args.deployment_manifest)}".encode()
    ).hexdigest()
    args.cohort_root.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema": SCHEMA,
        "status": "ACTIVE",
        "boundaryId": boundary_id,
        "boundaryUtc": boundary,
        "qualificationNotH3": True,
        "outcomesSealedUntilRepresentationGates": True,
        "oneShotEvaluationOnly": True,
        "sourceRoot": str(args.source_root),
        "sourceHealthSha256": health_hash,
        "sourceBaseline": {field: health.get(field) for field in (*BASELINE_ZERO_FIELDS, "sourceInstanceId", "currentEpoch", "latestSlot")},
        "sourceInstanceId": health["sourceInstanceId"],
        "continuityEpoch": int(health["currentEpoch"]),
        "controlSeed": seed,
        "controlSeedSha256": hashlib.sha256(seed.encode()).hexdigest(),
        "specPath": str(args.spec), "specSha256": sha256(args.spec),
        "fingerprintPath": str(args.fingerprint), "fingerprintSha256": sha256(args.fingerprint),
        "deploymentManifestPath": str(args.deployment_manifest),
        "deploymentManifestSha256": args.expected_deployment_sha256,
        "deploymentStatus": deployment["status"],
        "deploymentComponentHashes": deployment["files"],
        "boundaryCreatorPath": str(Path(__file__).resolve()),
        "boundaryCreatorSha256": sha256(Path(__file__).resolve()),
        "epochPolicyPath": str(args.registry_script), "epochPolicySha256": sha256(args.registry_script),
        "hardWallPolicy": "source-instance change, continuity-epoch change, reconnect, continuity gap, receive error, or scientific integrity failure invalidates this cohort; no bridge or same-cohort restart",
    }
    staged = args.cohort_root / ".boundary-staged.json"
    staged.write_bytes(canonical(receipt))
    with staged.open("rb") as handle:
        os.fsync(handle.fileno())
    registry = load_module(args.registry_script, "kefi_preh3_registry")
    record = registry.append(args.registry_root, {
        "epoch_id": boundary_id, "kind": "FUTURE_UNOPENED", "status": "UNOPENED",
        "start_utc": boundary, "end_utc": "", "reason": f"{SCHEMA}:{hashlib.sha256(canonical(receipt)).hexdigest()}",
    })
    receipt["epochRegistryRoot"] = str(args.registry_root)
    receipt["epochRecordSha256"] = record["record_sha256"]
    receipt["epochId"] = record["epoch_id"]
    atomic_exclusive(args.cohort_root / "boundary.json", receipt)
    staged.unlink(missing_ok=True)
    print(json.dumps({"boundary_utc": boundary, "boundary_id": boundary_id,
                      "boundary_path": str(args.cohort_root / "boundary.json"),
                      "boundary_sha256": sha256(args.cohort_root / "boundary.json")}, sort_keys=True))


if __name__ == "__main__":
    main()
