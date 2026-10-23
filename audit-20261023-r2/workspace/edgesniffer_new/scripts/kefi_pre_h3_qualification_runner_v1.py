#!/usr/bin/env python3
"""Low-impact, outcome-sealed consumer for PRE_H3_ACTIONABILITY_QUALIFICATION_V1.

It only materializes fully post-boundary sealed windows.  A source hard wall is
durably recorded once and the cohort never resumes.  Representation readiness
reads identities/control coverage only; the frozen evaluator is invoked once,
after those gates pass.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


MAX_HEALTH_AGE_SECONDS = 90
ZERO_COUNTERS = ("reconnects", "continuityGaps", "receiveErrors", "rollingWindowPromotionFailures", "rollingWindowQueueFull", "sharedBurstGroupsFailed")
MIN_TARGETS, MIN_POOLS, MIN_BLOCKS, MAX_POOL_SHARE, MIN_CONTROL_COVERAGE = 300, 40, 8, .10, .70


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc_ns(value: str) -> int:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def acquire_lock(root: Path) -> int:
    return os.open(root / ".runner.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def release_lock(root: Path, descriptor: int) -> None:
    os.close(descriptor); (root / ".runner.lock").unlink(missing_ok=True)


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def invalidate(root: Path, reason: str, detail: dict) -> None:
    path = root / "INVALIDATED_HARD_WALL.json"
    if path.exists():
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical({"schema": "PRE_H3_ACTIONABILITY_QUALIFICATION_V1_INVALIDATION_V1", "invalidatedAtUtc": now(), "reason": reason, "detail": detail, "noBridge": True, "sameCohortRestartProhibited": True})); handle.flush(); os.fsync(handle.fileno())


def source_gate(boundary: dict) -> tuple[str, dict]:
    health_path = Path(boundary["sourceRoot"]) / "health.json"
    health = load(health_path)
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(str(health["latestReceivedAtUtc"]).replace("Z", "+00:00"))).total_seconds()
    except Exception as error:
        return "HARD", {"reason": "malformed-source-freshness", "error": str(error)}
    if age > MAX_HEALTH_AGE_SECONDS:
        return "WAIT", {"reason": "source-stale", "age_seconds": age}
    baseline = boundary["sourceBaseline"]
    for field in ("sourceInstanceId", "currentEpoch"):
        if health.get(field) != baseline.get(field):
            return "HARD", {"reason": "source-identity-break", "field": field, "expected": baseline.get(field), "actual": health.get(field)}
    for field in ZERO_COUNTERS:
        if int(health.get(field, 0)) > int(baseline.get(field, 0)):
            return "HARD", {"reason": "source-hard-wall-counter", "field": field, "baseline": baseline.get(field), "actual": health.get(field)}
    return "OK", {"latestReceivedAtUtc": health["latestReceivedAtUtc"], "latestSlot": health.get("latestSlot")}


def post_boundary_window(promotion_root: Path, boundary_ns: int, processed: set[str]) -> str | None:
    candidates = []
    for manifest_path in sorted((promotion_root / "shared-windows").glob("*/manifest.json")):
        window_id = manifest_path.parent.name
        if window_id in processed:
            continue
        try:
            manifest = load(manifest_path)
            start = int(manifest["actualFrameStartNs"])
            end = int(manifest["actualFrameEndNs"])
        except Exception:
            continue
        if start >= boundary_ns and end > start:
            candidates.append((start, window_id))
    return min(candidates)[1] if candidates else None


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, text=True, capture_output=True)


def representation(assignments: Path) -> dict:
    items = [json.loads(line) for line in assignments.read_text().splitlines() if line]
    # Deliberately do not read target/control outcome fields before the one shot.
    pools = Counter(str(item["pool"]) for item in items)
    controls = sum(bool(item["controls"]) for item in items)
    blocks = {str(item["chronological_block"]) for item in items}
    value = {
        "targets": len(items), "distinct_pools": len(pools), "chronological_blocks": len(blocks),
        "maximum_pool_share": max(pools.values()) / len(items) if items else 1.0,
        "control_coverage": controls / len(items) if items else 0.0,
    }
    value["ready"] = (value["targets"] >= MIN_TARGETS and value["distinct_pools"] >= MIN_POOLS and
                      value["chronological_blocks"] >= MIN_BLOCKS and value["maximum_pool_share"] <= MAX_POOL_SHARE and
                      value["control_coverage"] >= MIN_CONTROL_COVERAGE)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load(args.config)
    root = Path(config["cohort_root"])
    try:
        descriptor = acquire_lock(root)
    except FileExistsError:
        print(json.dumps({"status": "ALREADY_RUNNING"})); return
    try:
        if (root / "INVALIDATED_HARD_WALL.json").exists():
            print(json.dumps({"status": "INVALIDATED"})); return
        if (root / "one-shot-result.json").exists():
            print(json.dumps({"status": "ONE_SHOT_COMPLETE"})); return
        boundary = load(root / "boundary.json")
        gate, detail = source_gate(boundary)
        if gate == "HARD":
            invalidate(root, detail["reason"], detail); print(json.dumps({"status": "INVALIDATED", **detail})); return
        if gate == "WAIT":
            atomic(root / "status.json", {"status": "WAITING_SOURCE_FRESHNESS", "checkedAtUtc": now(), **detail}); print(json.dumps({"status": "WAITING_SOURCE_FRESHNESS"})); return
        output = Path(config["materializer_out"])
        state = load(output / "state.json") if (output / "state.json").exists() else {"processed": []}
        window_id = post_boundary_window(Path(config["promotion_root"]), parse_utc_ns(boundary["boundaryUtc"]), set(state.get("processed", [])))
        if window_id:
            result = run([sys.executable, config["materializer"], "--root", config["promotion_root"], "--out", str(output), "--decoder", config["decoder"], "--window-id", window_id, "--skip-deduplicated-market", "--scientific", "--registry-script", config["registry_script"], "--registry-root", config["registry_root"], "--boundary-utc", boundary["boundaryUtc"]])
            if result.returncode:
                if "INELIGIBLE" in (result.stderr + result.stdout) or "FAIL_CLOSED" in (result.stderr + result.stdout):
                    invalidate(root, "scientific-materialization-failure", {"window_id": window_id, "stderr": result.stderr[-4000:], "stdout": result.stdout[-4000:]})
                    print(json.dumps({"status": "INVALIDATED", "window_id": window_id})); return
                raise RuntimeError(result.stderr[-4000:])
            ingest = run([sys.executable, config["indexer"], "--materializer-out", str(output), "--index-root", config["index_root"], "--require-scientific-order", "--export-qualification-input"])
            if ingest.returncode:
                invalidate(root, "canonical-ingest-failure", {"window_id": window_id, "stderr": ingest.stderr[-4000:]}); print(json.dumps({"status": "INVALIDATED"})); return
        receipts = sorted((output / "receipts").glob("*.json"))
        canonical_root = Path(config["index_root"]) / "qualification-input-v1"
        canonical_input, canonical_manifest = canonical_root / "canonical-events.jsonl", canonical_root / "manifest.json"
        if not receipts or not canonical_input.is_file() or not canonical_manifest.is_file():
            atomic(root / "status.json", {"status": "WAITING_SEALED_POST_BOUNDARY_WINDOW", "checkedAtUtc": now(), **detail}); print(json.dumps({"status": "WAITING"})); return
        assignments = root / "sealed" / "assignments.jsonl"
        manifest = root / "sealed" / "assignments-manifest.json"
        command = [sys.executable, config["assignment_builder"], "--fingerprint", config["fingerprint"], "--boundary-receipt", str(root / "boundary.json"), "--canonical-input", str(canonical_input), "--canonical-manifest", str(canonical_manifest), "--registry-script", config["registry_script"], "--registry-root", config["registry_root"], "--frozen-scorer", config["frozen_scorer"], "--mechanics", config["mechanics"], "--evaluator", config["evaluator"], "--out-assignments", str(assignments), "--out-manifest", str(manifest), "--control-seed", boundary["controlSeed"]]
        built = run(command)
        if built.returncode:
            if "no pre-entry eligible fingerprint targets" in (built.stderr + built.stdout):
                atomic(root / "status.json", {"status": "COLLECTING_NO_TARGETS_YET", "checkedAtUtc": now(), "materialized_receipts": len(receipts), **detail}); print(json.dumps({"status": "COLLECTING_NO_TARGETS_YET"})); return
            invalidate(root, "assignment-builder-integrity-failure", {"stderr": built.stderr[-4000:], "stdout": built.stdout[-4000:]}); print(json.dumps({"status": "INVALIDATED"})); return
        progress = representation(assignments)
        atomic(root / "representation-gate.json", {"schema": "PRE_H3_ACTIONABILITY_REPRESENTATION_GATE_V1", "checkedAtUtc": now(), "outcomesRead": False, **progress})
        if not progress["ready"]:
            atomic(root / "status.json", {"status": "COLLECTING_REPRESENTATION_GATE", "checkedAtUtc": now(), "materialized_receipts": len(receipts), **progress}); print(json.dumps({"status": "COLLECTING_REPRESENTATION_GATE", **progress})); return
        result = run([sys.executable, config["evaluator"], "--assignments", str(assignments), "--out", str(root / "one-shot-result.json"), "--seed", boundary["controlSeed"]])
        if result.returncode:
            invalidate(root, "frozen-evaluator-failure", {"stderr": result.stderr[-4000:], "stdout": result.stdout[-4000:]}); print(json.dumps({"status": "INVALIDATED"})); return
        atomic(root / "status.json", {"status": "ONE_SHOT_EVALUATION_COMPLETE", "completedAtUtc": now(), "resultSha256": sha256(root / "one-shot-result.json"), **progress})
        print(json.dumps({"status": "ONE_SHOT_EVALUATION_COMPLETE"}))
    finally:
        release_lock(root, descriptor)


if __name__ == "__main__":
    main()
