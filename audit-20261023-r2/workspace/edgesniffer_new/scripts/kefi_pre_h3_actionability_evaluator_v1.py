#!/usr/bin/env python3
"""Frozen outcome-blind evaluator for PRE_H3_ACTIONABILITY_QUALIFICATION_V1.

Input assignments must already have been produced by the strict cohort and
control matcher.  This script cannot create a cohort or select controls; it
only evaluates the frozen all-target and paired populations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from pathlib import Path


SCHEMA = "KEFI_PRE_H3_ACTIONABILITY_EVALUATOR_V1"
BOOTSTRAP_REPLICATES = 10_000
MIN_TARGETS = 300
MIN_DISTINCT_POOLS = 40
MIN_CHRONOLOGICAL_BLOCKS = 8
MAX_POOL_SHARE = 0.10
MIN_CONTROL_COVERAGE = 0.70
MIN_ABSOLUTE_TARGET_ACTIONABILITY = 0.70
MIN_EFFECT_SIZE = 0.10


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def validate_assignment(item: dict) -> None:
    required = {"target_id", "cluster_id", "pool", "chronological_block", "target_complete_actionable", "controls"}
    missing = required - item.keys()
    if missing:
        raise RuntimeError(f"FAIL_CLOSED: assignment missing {sorted(missing)}")
    if not isinstance(item["controls"], list) or len(item["controls"]) > 4:
        raise RuntimeError("FAIL_CLOSED: invalid frozen control set")
    if not isinstance(item["target_complete_actionable"], bool):
        raise RuntimeError("FAIL_CLOSED: target outcome must be binary")
    if not str(item["target_id"]) or not str(item["cluster_id"]) or not str(item["pool"]):
        raise RuntimeError("FAIL_CLOSED: blank target identity")
    for control in item["controls"]:
        if not isinstance(control, dict) or not isinstance(control.get("complete_actionable"), bool):
            raise RuntimeError("FAIL_CLOSED: invalid control outcome")


def rates(items: list[dict]) -> tuple[float, float, float]:
    target_rate = sum(item["target_complete_actionable"] for item in items) / len(items)
    paired = [item for item in items if item["controls"]]
    if not paired:
        return target_rate, float("nan"), float("nan")
    paired_target = sum(item["target_complete_actionable"] for item in paired) / len(paired)
    paired_control = sum(
        sum(control["complete_actionable"] for control in item["controls"]) / len(item["controls"])
        for item in paired
    ) / len(paired)
    return target_rate, paired_control, paired_target - paired_control


def bootstrap(items: list[dict], seed: str, replicates: int) -> tuple[list[float], list[float]]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        clusters[item["cluster_id"]].append(item)
    cluster_ids = sorted(clusters)
    if not cluster_ids:
        return [], []
    rng = random.Random(seed)
    absolute, deltas = [], []
    for _ in range(replicates):
        sample = [row for _ in cluster_ids for row in clusters[rng.choice(cluster_ids)]]
        at, _, delta = rates(sample)
        absolute.append(at)
        if not math.isnan(delta):
            deltas.append(delta)
    return absolute, deltas


def classify(items: list[dict], seed: str, replicates: int) -> dict:
    if not items:
        raise RuntimeError("FAIL_CLOSED: no target assignments")
    for item in items:
        validate_assignment(item)
    ids = [str(item["target_id"]) for item in items]
    if len(ids) != len(set(ids)):
        raise RuntimeError("FAIL_CLOSED: duplicate target identity")
    at_all, ac_paired, delta_paired = rates(items)
    paired = [item for item in items if item["controls"]]
    pools = Counter(str(item["pool"]) for item in items)
    blocks = {str(item["chronological_block"]) for item in items}
    coverage = len(paired) / len(items)
    maximum_pool_share = max(pools.values()) / len(items)
    absolute_bootstrap, delta_bootstrap = bootstrap(items, seed, replicates)
    at_ci = [percentile(absolute_bootstrap, 0.025), percentile(absolute_bootstrap, 0.975)]
    delta_ci = ([percentile(delta_bootstrap, 0.025), percentile(delta_bootstrap, 0.975)]
                if delta_bootstrap else [None, None])
    quality = {
        "minimum_targets": len(items) >= MIN_TARGETS,
        "minimum_distinct_pools": len(pools) >= MIN_DISTINCT_POOLS,
        "minimum_chronological_blocks": len(blocks) >= MIN_CHRONOLOGICAL_BLOCKS,
        "maximum_pool_share": maximum_pool_share <= MAX_POOL_SHARE,
        "minimum_control_coverage": coverage >= MIN_CONTROL_COVERAGE,
        "paired_comparison_available": bool(paired),
    }
    quality_pass = all(quality.values())
    pass_result = quality_pass and at_ci[0] >= MIN_ABSOLUTE_TARGET_ACTIONABILITY and delta_ci[0] is not None and delta_ci[0] >= MIN_EFFECT_SIZE
    reject_result = quality_pass and (at_ci[1] < MIN_ABSOLUTE_TARGET_ACTIONABILITY or (delta_ci[1] is not None and delta_ci[1] <= 0.0))
    status = "PASS" if pass_result else "REJECT" if reject_result else "INCONCLUSIVE"
    return {
        "schema": SCHEMA,
        "status": status,
        "targets_all": len(items),
        "targets_paired": len(paired),
        "control_coverage": coverage,
        "A_T_all": at_all,
        "A_T_all_ci95_cluster_bootstrap": at_ci,
        "A_C_paired_equal_target_weighted": ac_paired if paired else None,
        "Delta_A_paired": delta_paired if paired else None,
        "Delta_A_paired_ci95_cluster_bootstrap": delta_ci,
        "distinct_pools": len(pools),
        "chronological_blocks": len(blocks),
        "maximum_pool_share": maximum_pool_share,
        "quality_gates": quality,
        "frozen_thresholds": {
            "bootstrap_replicates": replicates,
            "minimum_targets": MIN_TARGETS,
            "minimum_distinct_pools": MIN_DISTINCT_POOLS,
            "minimum_chronological_blocks": MIN_CHRONOLOGICAL_BLOCKS,
            "maximum_pool_share": MAX_POOL_SHARE,
            "minimum_control_coverage": MIN_CONTROL_COVERAGE,
            "minimum_absolute_target_actionability": MIN_ABSOLUTE_TARGET_ACTIONABILITY,
            "minimum_paired_effect_size": MIN_EFFECT_SIZE,
        },
        "estimand": "A_T_all uses every admitted target; Delta_A_paired uses only targets with >=1 frozen matched control",
        "pnl_accessed": False,
        "outcome_blind": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=BOOTSTRAP_REPLICATES)
    args = parser.parse_args()
    if args.bootstrap_replicates < 1:
        raise SystemExit("FAIL_CLOSED: bootstrap replicates must be positive")
    items = [json.loads(line) for line in args.assignments.read_text().splitlines() if line]
    result = classify(items, args.seed, args.bootstrap_replicates)
    result["assignments_sha256"] = hashlib.sha256(args.assignments.read_bytes()).hexdigest()
    result["evaluator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    atomic(args.out, result)
    print(json.dumps({"status": result["status"], "result_sha256": digest(result)}, sort_keys=True))


if __name__ == "__main__":
    main()
