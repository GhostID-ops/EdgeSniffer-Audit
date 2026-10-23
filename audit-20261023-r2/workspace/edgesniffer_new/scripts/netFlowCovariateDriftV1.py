#!/usr/bin/env python3
"""Outcome-blind covariate drift diagnostic for the frozen net-flow feature."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import random
import statistics
from typing import Any


SCHEMA = "NET_FLOW_COVARIATE_DRIFT_V1"
QUANTILES = (0.50, 0.75, 0.80, 0.90, 0.95, 0.975, 0.99)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "N": len(values),
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "P50": quantile(values, 0.50),
        "P75": quantile(values, 0.75),
        "P80": quantile(values, 0.80),
        "P90": quantile(values, 0.90),
        "P95": quantile(values, 0.95),
        "P97_5": quantile(values, 0.975),
        "P99": quantile(values, 0.99),
        "max": max(values) if values else None,
    }


def threshold_rank(values: list[float], threshold: float) -> dict[str, Any]:
    below = sum(value < threshold for value in values)
    equal = sum(value == threshold for value in values)
    above = sum(value > threshold for value in values)
    rank = 100 * (below + 0.5 * equal) / len(values) if values else None
    return {"percentileRank": rank, "belowN": below, "equalN": equal, "aboveN": above}


def ks_distance(left: list[float], right: list[float]) -> float:
    left_sorted, right_sorted = sorted(left), sorted(right)
    i = j = 0
    distance = 0.0
    while i < len(left_sorted) or j < len(right_sorted):
        if j >= len(right_sorted) or (i < len(left_sorted) and left_sorted[i] <= right_sorted[j]):
            value = left_sorted[i]
        else:
            value = right_sorted[j]
        while i < len(left_sorted) and left_sorted[i] <= value:
            i += 1
        while j < len(right_sorted) and right_sorted[j] <= value:
            j += 1
        distance = max(distance, abs(i / len(left_sorted) - j / len(right_sorted)))
    return distance


def wasserstein_distance(left: list[float], right: list[float]) -> float:
    left_sorted, right_sorted = sorted(left), sorted(right)
    support = sorted(set(left_sorted + right_sorted))
    i = j = 0
    total = 0.0
    for current, following in zip(support, support[1:]):
        while i < len(left_sorted) and left_sorted[i] <= current:
            i += 1
        while j < len(right_sorted) and right_sorted[j] <= current:
            j += 1
        total += abs(i / len(left_sorted) - j / len(right_sorted)) * (following - current)
    return total


def ci(values: list[float]) -> list[float | None]:
    return [quantile(values, 0.025), quantile(values, 0.975)]


def bootstrap(left: list[float], right: list[float], replicates: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    median_shifts: list[float] = []
    p90_shifts: list[float] = []
    ks_values: list[float] = []
    wasserstein_values: list[float] = []
    for _ in range(replicates):
        a = [left[rng.randrange(len(left))] for _ in left]
        b = [right[rng.randrange(len(right))] for _ in right]
        median_shifts.append(float(quantile(b, 0.5)) - float(quantile(a, 0.5)))
        p90_shifts.append(float(quantile(b, 0.9)) - float(quantile(a, 0.9)))
        ks_values.append(ks_distance(a, b))
        wasserstein_values.append(wasserstein_distance(a, b))
    return {
        "medianShift95CI": ci(median_shifts),
        "P90Shift95CI": ci(p90_shifts),
        "KSDistance95CI": ci(ks_values),
        "wassersteinDistance95CI": ci(wasserstein_values),
    }


def load_index(path: pathlib.Path, end: int) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < end:
        raise RuntimeError(f"INDEX_TOO_SHORT:{len(rows)}:{end}")
    selected = rows[:end]
    for expected, row in enumerate(selected, 1):
        if int(row.get("ordinal", -1)) != expected:
            raise RuntimeError(f"NON_CONTIGUOUS_INDEX:{expected}:{row.get('ordinal')}")
    return selected


def prefix_sha(rows: list[dict[str, Any]]) -> str:
    payload = "\n".join(f"{row['ordinal']}:{row['mint']}" for row in rows).encode()
    return hashlib.sha256(payload).hexdigest()


def load_discovery_features(path: pathlib.Path, anchor: str, end: int) -> dict[int, float]:
    values: dict[int, float] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        if header.get("schema") != "SAME_ANCHOR_CAUSAL_SEPARABILITY_V1_ANALYSIS_ROWS":
            raise RuntimeError("DISCOVERY_SCHEMA_MISMATCH")
        for line in handle:
            row = json.loads(line)
            ordinal = int(row.get("ordinal", 0))
            if row.get("anchor") != anchor or not 1 <= ordinal <= end:
                continue
            # Outcome keys are intentionally never accessed.
            feature = (row.get("features") or {}).get("net_external_flow_sol")
            causal = (row.get("availability") or {}).get("causalAvailabilityPass") is True
            if causal and isinstance(feature, (int, float)) and math.isfinite(float(feature)):
                if ordinal in values:
                    raise RuntimeError(f"DUPLICATE_DISCOVERY_ORDINAL:{ordinal}")
                values[ordinal] = float(feature)
    return values


def load_unseen_features(path: pathlib.Path, start: int, end: int) -> dict[int, float]:
    values: dict[int, float] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        if header.get("outcomeFieldsPresent") is not False:
            raise RuntimeError("UNSEEN_ARTIFACT_NOT_OUTCOME_BLIND")
        for line in handle:
            row = json.loads(line)
            ordinal = int(row["ordinal"])
            if not start <= ordinal <= end:
                raise RuntimeError(f"UNSEEN_ROW_OUT_OF_RANGE:{ordinal}")
            if row.get("causalAvailabilityPass") is not True:
                continue
            feature = row.get("net_external_flow_sol")
            if isinstance(feature, (int, float)) and math.isfinite(float(feature)):
                if ordinal in values:
                    raise RuntimeError(f"DUPLICATE_UNSEEN_ORDINAL:{ordinal}")
                values[ordinal] = float(feature)
    return values


def block_report(bounds: list[int], features: dict[int, float], threshold: float) -> dict[str, Any]:
    start, end = bounds
    values = [value for ordinal, value in features.items() if start <= ordinal <= end]
    exceed = sum(value >= threshold for value in values)
    total = end - start + 1
    return {
        "ordinalRange": bounds,
        "totalMints": total,
        "causalAnchorN": len(values),
        "causalAnchorRate": len(values) / total,
        "thresholdExceedN": exceed,
        "thresholdExceedRateAmongAnchors": exceed / len(values) if values else None,
        "thresholdExceedRateAmongAllMints": exceed / total,
        "distribution": distribution(values),
    }


def classify(ks: float, unseen_rank: float, median_shift: float, discovery_iqr: float) -> str:
    displacement = abs(unseen_rank - 90.0)
    standardized_median = abs(median_shift) / discovery_iqr if discovery_iqr else math.inf
    if ks >= 0.20 or displacement >= 10:
        return "NET_FLOW_DISTRIBUTION_MATERIAL_DRIFT"
    if ks >= 0.10 or displacement >= 3 or standardized_median >= 0.25:
        return "NET_FLOW_DISTRIBUTION_MODEST_DRIFT"
    return "NET_FLOW_DISTRIBUTION_STABLE"


def markdown(report: dict[str, Any]) -> str:
    discovery = report["populations"]["discovery"]
    unseen = report["populations"]["unseen"]
    lines = [
        "# NET_FLOW_COVARIATE_DRIFT_V1", "", f"## {report['verdict']}", "",
        "Outcome-blind causal feature diagnostic only. No economic labels or replay outcomes were read.", "",
        "| Population | N | Mean | Median | P75 | P90 | P95 | P99 | Max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| Discovery 1-6848 | {discovery['N']} | {discovery['mean']:.6f} | {discovery['median']:.6f} | {discovery['P75']:.6f} | {discovery['P90']:.6f} | {discovery['P95']:.6f} | {discovery['P99']:.6f} | {discovery['max']:.6f} |",
        f"| Unseen 6849-8293 | {unseen['N']} | {unseen['mean']:.6f} | {unseen['median']:.6f} | {unseen['P75']:.6f} | {unseen['P90']:.6f} | {unseen['P95']:.6f} | {unseen['P99']:.6f} | {unseen['max']:.6f} |",
        "", "## Drift", "",
        f"- KS distance: `{report['drift']['KSDistance']:.6f}`",
        f"- Wasserstein distance: `{report['drift']['wassersteinDistanceSol']:.6f} SOL`",
        f"- Median shift: `{report['drift']['medianShiftSol']:.6f} SOL`",
        f"- P90 shift: `{report['drift']['P90ShiftSol']:.6f} SOL`",
        f"- Frozen threshold unseen percentile rank: `{report['frozenThreshold']['unseenPercentileRank']:.3f}`",
        "", "The frozen 16.996559399 SOL candidate remains unchanged and its unseen economic test remains unread.", "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--discovery-rows", type=pathlib.Path, required=True)
    parser.add_argument("--unseen-features", type=pathlib.Path, required=True)
    parser.add_argument("--index", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    if sha256_file(args.discovery_rows) != contract["parents"]["sameAnchorAnalysisRowsSha256"]:
        raise RuntimeError("DISCOVERY_HASH_MISMATCH")
    if sha256_file(args.unseen_features) != contract["parents"]["unseenFeatureRowsSha256"]:
        raise RuntimeError("UNSEEN_FEATURE_HASH_MISMATCH")

    discovery_start, discovery_end = contract["populations"]["discovery"]
    unseen_start, unseen_end = contract["populations"]["unseen"]
    index = load_index(args.index, unseen_end)
    if prefix_sha(index[:discovery_end]) != contract["parents"]["discoveryPrefixSha256"]:
        raise RuntimeError("DISCOVERY_PREFIX_MISMATCH")
    if prefix_sha(index[unseen_start - 1:unseen_end]) != contract["parents"]["unseenPrefixSha256"]:
        raise RuntimeError("UNSEEN_PREFIX_MISMATCH")

    discovery_features = load_discovery_features(args.discovery_rows, contract["anchor"], discovery_end)
    unseen_features = load_unseen_features(args.unseen_features, unseen_start, unseen_end)
    discovery_values = list(discovery_features.values())
    unseen_values = list(unseen_features.values())
    threshold = float(contract["frozenThresholdSol"])
    discovery_dist = distribution(discovery_values)
    unseen_dist = distribution(unseen_values)
    ks = ks_distance(discovery_values, unseen_values)
    wasserstein = wasserstein_distance(discovery_values, unseen_values)
    median_shift = float(unseen_dist["median"]) - float(discovery_dist["median"])
    p90_shift = float(unseen_dist["P90"]) - float(discovery_dist["P90"])
    unseen_rank = float(threshold_rank(unseen_values, threshold)["percentileRank"])
    discovery_iqr = float(discovery_dist["P75"]) - float(quantile(discovery_values, 0.25))
    verdict = classify(ks, unseen_rank, median_shift, discovery_iqr)

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "verdict": verdict,
        "outcomeBlind": True,
        "outcomeFieldsAccessed": 0,
        "economicSimulations": 0,
        "contractSha256": sha256_file(args.contract),
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "sourceHashes": {
            "discoveryFeatureParent": sha256_file(args.discovery_rows),
            "unseenFeatureParent": sha256_file(args.unseen_features),
            "discoveryPrefix": prefix_sha(index[:discovery_end]),
            "unseenPrefix": prefix_sha(index[unseen_start - 1:unseen_end]),
        },
        "populations": {"discovery": discovery_dist, "unseen": unseen_dist},
        "frozenThreshold": {
            "valueSol": threshold,
            "unchanged": True,
            "discovery": threshold_rank(discovery_values, threshold),
            "unseen": threshold_rank(unseen_values, threshold),
            "unseenPercentileRank": unseen_rank,
        },
        "chronological": {
            "discoveryBlocks": [block_report(bounds, discovery_features, threshold) for bounds in contract["populations"]["discoveryBlocks"]],
            "unseenHalves": [block_report(bounds, unseen_features, threshold) for bounds in contract["populations"]["unseenBlocks"]],
        },
        "drift": {
            "KSDistance": ks,
            "wassersteinDistanceSol": wasserstein,
            "medianShiftSol": median_shift,
            "medianShiftVsDiscoveryIQR": median_shift / discovery_iqr if discovery_iqr else None,
            "P90ShiftSol": p90_shift,
            "bootstrap": bootstrap(discovery_values, unseen_values, int(contract["bootstrap"]["replicates"]), int(contract["bootstrap"]["seed"])),
        },
        "classificationRule": contract["classification"],
        "fixedStatement": "The frozen 16.996559399 SOL candidate remains unchanged and its unseen economic test remains unread.",
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE",
        "contractSha256": report["contractSha256"],
        "scriptSha256": report["scriptSha256"],
        "reportSha256": sha256_file(report_path),
        "sourceHashes": report["sourceHashes"],
        "outcomeBlind": True,
        "outcomeFieldsAccessed": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
