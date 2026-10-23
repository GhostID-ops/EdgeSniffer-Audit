#!/usr/bin/env python3
"""One-shot evaluator for the separately preregistered kEfi H2 base score."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def iso_from_ns(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1_000_000_000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def run(command: list[str]) -> dict:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return json.loads(next(line for line in reversed(result.stdout.splitlines()) if line.strip()))


def bounds(market: Path, boundary_ns: int) -> tuple[int, set[str]]:
    latest = 0
    partitions: set[str] = set()
    with market.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            stamp = int(row["received_at_unix_ns"])
            if stamp >= boundary_ns:
                latest = max(latest, stamp)
                if row.get("source_partition"):
                    partitions.add(str(row["source_partition"]))
    if not latest:
        raise RuntimeError("WAITING_FOR_POST_BOUNDARY_MARKET")
    return latest, partitions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--hypothesis-root", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--economic", type=Path, required=True)
    parser.add_argument("--stability", type=Path, required=True)
    args = parser.parse_args()
    root = args.hypothesis_root
    done = root / "one-shot-complete.json"
    if done.exists():
        print(json.dumps({"status": "ALREADY_COMPLETE", "receipt": str(done)}, sort_keys=True))
        return
    spec_path, boundary_path = root / "pre-registration.json", root / "scientific-boundary.json"
    spec, boundary = json.loads(spec_path.read_text()), json.loads(boundary_path.read_text())
    if spec.get("status") != "FROZEN_FOR_FRESH_OUTCOME_BLIND_PAPER_TEST" or boundary.get("status") != "ACTIVE":
        raise SystemExit("FAIL_CLOSED: invalid H2 protocol artifacts")
    health = json.loads((args.source_root / "health.json").read_text())
    baseline = boundary["sourceHealthBaseline"]
    for field in ("continuityGaps", "reconnects", "receiveErrors", "rollingWindowPromotionFailures", "rollingWindowQueueFull", "sharedBurstGroupsFailed"):
        if int(health.get(field, 0)) != int(baseline.get(field, 0)):
            atomic(root / "blocked.json", {"status": "FAILED_CLOSED_SOURCE_CHANGED", "field": field, "baseline": baseline, "current": health})
            print(json.dumps({"status": "FAILED_CLOSED_SOURCE_CHANGED", "field": field}, sort_keys=True))
            return
    market = args.control_root / "market-deduplicated.jsonl"
    if not market.is_file():
        print(json.dumps({"status": "WAITING_FOR_MATERIALIZATION"}, sort_keys=True))
        return
    boundary_ns = int(boundary["boundaryUnixNs"])
    try:
        latest_ns, partitions = bounds(market, boundary_ns)
    except RuntimeError as exc:
        print(json.dumps({"status": str(exc)}, sort_keys=True))
        return
    cutoff_ns = latest_ns - int(spec["fresh_gate"]["horizon_seconds"]) * 1_000_000_000
    if cutoff_ns <= boundary_ns + int(spec["fresh_gate"]["warmup_seconds"]) * 1_000_000_000:
        print(json.dumps({"status": "WAITING_FOR_COMPLETE_HORIZON"}, sort_keys=True))
        return
    stage = root / "outcome-blind-current"
    opportunities, summary = stage / "opportunities.jsonl", stage / "summary.json"
    inputs = spec["inputs"]
    output = run([sys.executable, str(args.adapter), "--market", str(market), "--fingerprint", inputs["fingerprint_path"], "--rule", inputs["selection_rule_path"], "--boundary-utc", boundary["boundaryUtc"], "--cutoff-utc", iso_from_ns(cutoff_ns), "--out-opportunities", str(opportunities), "--out-summary", str(summary)])
    with opportunities.open() as handle:
        base_matches = sum(bool(json.loads(line)["base_fingerprint_match"]) for line in handle if line.strip())
    ready = len(partitions) >= int(spec["fresh_gate"]["minimum_closed_source_partitions"]) and base_matches >= int(spec["fresh_gate"]["minimum_frozen_matches"])
    atomic(root / "readiness.json", {"schema": "KEFI_H2_READINESS_V1", "status": "READY_FOR_ONE_SHOT_ECONOMICS" if ready else "COLLECTING_OUTCOME_BLIND_SAMPLE", "boundary_sha256": sha256(boundary_path), "market_sha256": sha256(market), "partitions": sorted(partitions), "matches": base_matches, "opportunities": output["opportunities"], "outcomes_accessed": False, "pnl_accessed": False})
    if not ready:
        print(json.dumps({"status": "COLLECTING_OUTCOME_BLIND_SAMPLE", "partitions": len(partitions), "matches": base_matches}, sort_keys=True))
        return
    run_root = root / ("one-shot-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    run_root.mkdir()
    frozen_opportunities, frozen_summary = run_root / "opportunities.jsonl", run_root / "opportunity-summary.json"
    os.replace(opportunities, frozen_opportunities); os.replace(summary, frozen_summary)
    result, replay = run_root / "economic-paper-result.json", run_root / "economic-paper-result.replay.json"
    contract = inputs["economic_contract_path"]
    command = [sys.executable, str(args.economic), "--contract", contract, "--opportunities", str(frozen_opportunities), "--swaps", str(market), "--match-field", "base_fingerprint_match"]
    run([*command, "--output", str(result)]); run([*command, "--output", str(replay)])
    if sha256(result) != sha256(replay):
        raise RuntimeError("FAIL_CLOSED: H2 economic replay is not byte-identical")
    stability = run_root / "economic-stability-audit.json"
    run([sys.executable, str(args.stability), "--opportunities", str(frozen_opportunities), "--result", str(result), "--expected-result-sha256", sha256(result), "--match-field", "base_fingerprint_match", "--out", str(stability)])
    atomic(done, {"schema": "KEFI_H2_ONE_SHOT_V1", "status": "PENDING_REVIEW_NOT_PROMOTED", "result_sha256": sha256(result), "replay_sha256": sha256(replay), "stability_sha256": sha256(stability), "execution_enabled": False})
    print(json.dumps({"status": "ONE_SHOT_COMPLETE", "result": str(result)}, sort_keys=True))


if __name__ == "__main__":
    main()
