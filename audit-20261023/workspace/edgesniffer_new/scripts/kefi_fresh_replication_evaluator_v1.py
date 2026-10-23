#!/usr/bin/env python3
"""One-shot, pre-registered kEfi replication evaluator.

It remains outcome-blind until the fresh source has produced the declared
minimum of six materialized source partitions and 8,000 frozen-rule matches.
Once that gate is met it runs the existing strict economics replay exactly
twice, requires byte-identical output, writes the concentration audit, and
leaves the result PENDING_REVIEW. It never enables execution.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


FINGERPRINT = Path("/mnt/edgesniffer-expansion/kefi-cold-archives/20261007T-capacity-recovery/independent-v1/rich-preentry-fingerprint-v2.json")
RULE = Path("/mnt/edgesniffer-expansion/kefi-cold-archives/kefi-frozen-rules/20261009T222500Z-crowded-impulse-v1/frozen-rule.json")
CONTRACT = Path("/mnt/edgesniffer-expansion/kefi-frozen-contracts/kefi-crowded-impulse-economic-paper-v1/contract.json")
OPPORTUNITIES = Path("/root/kefi-work/bin/kefi_capture_first_frozen_opportunities_v1.py")
ECONOMIC = Path("/root/kefi-work/bin/kefi_crowded_impulse_economic_paper_v1.py")
STABILITY = Path("/root/kefi-work/bin/kefi_capture_first_economic_stability_audit_v1.py")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)


def iso_from_ns(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1_000_000_000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def materialized_bounds(market: Path) -> tuple[int, int, set[str]]:
    first: int | None = None
    last: int | None = None
    partitions: set[str] = set()
    with market.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            stamp = int(row["received_at_unix_ns"])
            first = stamp if first is None else min(first, stamp)
            last = stamp if last is None else max(last, stamp)
            partition = row.get("source_partition")
            if partition:
                partitions.add(str(partition))
    if first is None or last is None:
        raise ValueError("empty materialized market tape")
    return first, last, partitions


def execute(command: list[str]) -> dict:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "command failed")
    line = next((line for line in reversed(completed.stdout.splitlines()) if line.strip()), "{}")
    return json.loads(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--control-out", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--minimum-partitions", default=6, type=int)
    parser.add_argument("--minimum-matches", default=8000, type=int)
    args = parser.parse_args()

    done = args.result_root / "one-shot-complete.json"
    readiness = args.result_root / "readiness.json"
    if done.exists():
        print(json.dumps({"status": "ALREADY_COMPLETE", "receipt": str(done)}, sort_keys=True))
        return 0

    boundary_path = args.source_root / "scientific-boundaries/paper-boundary-v1.json"
    health_path = args.source_root / "health.json"
    # The materializer publishes this name atomically after it has verified
    # overlapping window rows are byte-identical. Keep this spelling aligned
    # with build_deduplicated_market() in the materializer.
    market = args.control_out / "market-deduplicated.jsonl"
    if not boundary_path.is_file() or not health_path.is_file() or not market.is_file():
        atomic(readiness, {"schema": "KEFI_REPLICATION_READINESS_V1", "status": "WAITING_FOR_MATERIALIZATION"})
        print(json.dumps({"status": "WAITING_FOR_MATERIALIZATION"}, sort_keys=True))
        return 0
    boundary = json.loads(boundary_path.read_text())
    health = json.loads(health_path.read_text())
    if boundary.get("status") != "ACTIVE" or any(int(health.get(key, 0)) for key in ("continuityGaps", "reconnects", "receiveErrors", "rollingWindowPromotionFailures", "rollingWindowQueueFull")):
        atomic(readiness, {"schema": "KEFI_REPLICATION_READINESS_V1", "status": "FAIL_CLOSED_SOURCE_NOT_CLEAN", "boundary_sha256": sha(boundary_path), "health": health})
        print(json.dumps({"status": "FAIL_CLOSED_SOURCE_NOT_CLEAN"}, sort_keys=True))
        return 0

    _, last_ns, partitions = materialized_bounds(market)
    cutoff_ns = last_ns - 120_000_000_000
    if cutoff_ns <= 0:
        raise RuntimeError("FAIL_CLOSED: insufficient materialized horizon")
    stage = args.result_root / "outcome-blind-current"
    opportunity_path = stage / "opportunities.jsonl"
    summary_path = stage / "summary.json"
    result = execute([
        sys.executable, str(OPPORTUNITIES), "--market", str(market), "--fingerprint", str(FINGERPRINT),
        "--rule", str(RULE), "--boundary-utc", boundary["boundaryUtc"], "--cutoff-utc", iso_from_ns(cutoff_ns),
        "--out-opportunities", str(opportunity_path), "--out-summary", str(summary_path),
    ])
    ready = len(partitions) >= args.minimum_partitions and int(result["matches"]) >= args.minimum_matches
    atomic(readiness, {
        "schema": "KEFI_REPLICATION_READINESS_V1", "status": "READY_FOR_ONE_SHOT_ECONOMICS" if ready else "COLLECTING_OUTCOME_BLIND_SAMPLE",
        "boundary_sha256": sha(boundary_path), "market_sha256": sha(market), "materialized_source_partitions": sorted(partitions),
        "materialized_source_partition_count": len(partitions), "opportunities": result["opportunities"], "frozen_matches": result["matches"],
        "required_partitions": args.minimum_partitions, "required_matches": args.minimum_matches, "outcome_blind": True,
    })
    if not ready:
        print(json.dumps({"status": "COLLECTING_OUTCOME_BLIND_SAMPLE", "partitions": len(partitions), "matches": result["matches"]}, sort_keys=True))
        return 0

    run = args.result_root / ("one-shot-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    run.mkdir(parents=True, exist_ok=False)
    frozen_opportunities = run / "opportunities.jsonl"
    frozen_summary = run / "opportunity-summary.json"
    os.replace(opportunity_path, frozen_opportunities)
    os.replace(summary_path, frozen_summary)
    replay_a = run / "economic-paper-result.json"
    replay_b = run / "economic-paper-result.replay.json"
    command = [sys.executable, str(ECONOMIC), "--contract", str(CONTRACT), "--opportunities", str(frozen_opportunities), "--swaps", str(market)]
    execute([*command, "--output", str(replay_a)])
    execute([*command, "--output", str(replay_b)])
    if sha(replay_a) != sha(replay_b):
        raise RuntimeError("FAIL_CLOSED: strict economic replay is not byte-identical")
    stability = run / "economic-stability-audit.json"
    execute([sys.executable, str(STABILITY), "--opportunities", str(frozen_opportunities), "--result", str(replay_a), "--expected-result-sha256", sha(replay_a), "--out", str(stability)])
    receipt = {
        "schema": "KEFI_FRESH_REPLICATION_ONE_SHOT_ECONOMICS_V1", "status": "PENDING_REVIEW_NOT_PROMOTED",
        "boundary_sha256": sha(boundary_path), "market_sha256": sha(market), "opportunity_sha256": sha(frozen_opportunities),
        "result_sha256": sha(replay_a), "replay_sha256": sha(replay_b), "stability_sha256": sha(stability),
        "execution_enabled": False, "pnl_accessed": False,
    }
    atomic(done, receipt)
    print(json.dumps({"status": "ONE_SHOT_COMPLETE", **receipt}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
