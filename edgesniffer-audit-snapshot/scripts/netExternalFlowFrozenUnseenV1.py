#!/usr/bin/env python3
"""Freeze and feature-gate the one permitted net-flow unseen study."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
from typing import Any

from lifecycleAnchorBlindBaselinesV1 import reconstruct_anchor_metadata
from sameAnchorCausalSeparabilityV1 import extract_features
from sandboxPumpExactFollowThroughAtlasV1 import collect_partition


SCHEMA = "NET_EXTERNAL_FLOW_FROZEN_UNSEEN_V1"


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prefix_sha(rows: list[dict[str, Any]]) -> str:
    payload = "\n".join(f"{row['ordinal']}:{row['mint']}" for row in rows).encode()
    return sha256_bytes(payload)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise RuntimeError("EMPTY_DISCOVERY_FEATURE_POPULATION")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def read_index(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for expected, row in enumerate(rows, 1):
        if int(row.get("ordinal", -1)) != expected:
            raise RuntimeError(f"NON_CONTIGUOUS_INDEX:{expected}:{row.get('ordinal')}")
    return rows


def freeze(args: argparse.Namespace) -> int:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    expected_parent = contract["parents"]["sameAnchorAnalysisRowsSha256"]
    actual_parent = sha256_file(args.discovery_rows)
    if actual_parent != expected_parent:
        raise RuntimeError(f"DISCOVERY_PARENT_HASH_MISMATCH:{actual_parent}")

    discovery_values: list[float] = []
    discovery_mints: set[str] = set()
    with gzip.open(args.discovery_rows, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        for line in handle:
            row = json.loads(line)
            if row.get("anchor") != contract["population"]["anchor"]:
                continue
            ordinal = int(row.get("ordinal", 0))
            if not 1 <= ordinal <= int(contract["population"]["discoveryOrdinalEnd"]):
                continue
            value = (row.get("features") or {}).get(contract["feature"]["id"])
            causal = (row.get("availability") or {}).get("causalAvailabilityPass") is True
            if causal and isinstance(value, (int, float)) and math.isfinite(float(value)):
                if row["mint"] in discovery_mints:
                    raise RuntimeError(f"DUPLICATE_DISCOVERY_MINT:{row['mint']}")
                discovery_mints.add(row["mint"])
                discovery_values.append(float(value))

    status = json.loads(args.status.read_text(encoding="utf-8"))
    index = read_index(args.index)
    latest_ready = min(
        len(index),
        int(status.get("exactAdmittedN", 0)),
        int(status.get("discoveryMintsAvailable", 0)),
        int(contract["population"]["unseenOrdinalMaximumEnd"]),
    )
    if latest_ready < int(contract["population"]["unseenOrdinalStart"]):
        raise RuntimeError(f"NO_UNSEEN_RANGE_READY:{latest_ready}")
    if status.get("allFeatureRowsAvailabilityCausal") is not True:
        raise RuntimeError("LATEST_READY_NOT_CAUSAL")
    discovery_end = int(contract["population"]["discoveryOrdinalEnd"])
    unseen_start = int(contract["population"]["unseenOrdinalStart"])
    frozen = {
        "schema": SCHEMA + "_THRESHOLD_FREEZE",
        "frozenAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "contractSha256": sha256_file(args.contract),
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "parentAnalysisRowsSha256": actual_parent,
        "parentHeader": header,
        "feature": contract["feature"]["id"],
        "anchor": contract["population"]["anchor"],
        "quantile": contract["threshold"]["quantile"],
        "quantileMethod": contract["threshold"]["method"],
        "discoveryEligibleN": len(discovery_values),
        "NET_FLOW_Q90_DISCOVERY_THRESHOLD": percentile(discovery_values, 0.9),
        "discoveryOrdinalRange": [1, discovery_end],
        "unseenOrdinalRange": [unseen_start, latest_ready],
        "discoveryPrefixSha256": prefix_sha(index[:discovery_end]),
        "unseenPrefixSha256": prefix_sha(index[unseen_start - 1:latest_ready]),
        "combinedPrefixSha256": prefix_sha(index[:latest_ready]),
        "researchStatusSha256AtFreeze": sha256_file(args.status),
        "indexSha256AtFreeze": sha256_file(args.index),
        "unseenOutcomesRead": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / "threshold-freeze.json"
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        comparable = dict(existing)
        comparable.pop("frozenAtUtc", None)
        proposed = dict(frozen)
        proposed.pop("frozenAtUtc", None)
        if comparable != proposed:
            raise RuntimeError("IMMUTABLE_THRESHOLD_FREEZE_MISMATCH")
    else:
        destination.write_bytes(canonical_json(frozen))
    (args.output / "threshold-freeze.sha256").write_text(sha256_file(destination) + "\n", encoding="ascii")
    print(json.dumps(frozen, indent=2, sort_keys=True))
    return 0


def gate(args: argparse.Namespace) -> int:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    frozen_path = args.output / "threshold-freeze.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    recorded_sha = (args.output / "threshold-freeze.sha256").read_text(encoding="ascii").strip()
    if sha256_file(frozen_path) != recorded_sha:
        raise RuntimeError("THRESHOLD_FREEZE_HASH_MISMATCH")
    if frozen["contractSha256"] != sha256_file(args.contract):
        raise RuntimeError("FROZEN_CONTRACT_HASH_MISMATCH")

    index = read_index(args.index)
    start, end = map(int, frozen["unseenOrdinalRange"])
    selected_rows = index[start - 1:end]
    if prefix_sha(selected_rows) != frozen["unseenPrefixSha256"]:
        raise RuntimeError("UNSEEN_PREFIX_HASH_MISMATCH")
    by_partition: dict[str, list[dict[str, Any]]] = {}
    for row in selected_rows:
        by_partition.setdefault(row["partitionKey"], []).append(row)

    feature_rows: list[dict[str, Any]] = []
    counters = {
        "unseenPopulationN": len(selected_rows),
        "eligibleAnchorN": 0,
        "featureMatchN": 0,
        "exactEvaluableMatchN": 0,
        "anchorUnavailableN": 0,
        "causalFeatureUnavailableN": 0,
    }
    for partition, rows in sorted(by_partition.items()):
        readiness_path = args.evidence_root / "readiness-v1" / "partitions" / f"{partition.replace('/', '-')}-v4.json"
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        targets = {row["mint"] for row in rows}
        events_by_mint, collection_counters = collect_partition(args.evidence_root, readiness, targets)
        for index_row in rows:
            mint = index_row["mint"]
            create_ms = int(index_row["createReceivedAtUnixNs"]) // 1_000_000
            anchor = reconstruct_anchor_metadata(events_by_mint.get(mint, []), create_ms).get(contract["population"]["anchor"])
            if anchor is None:
                counters["anchorUnavailableN"] += 1
                continue
            counters["eligibleAnchorN"] += 1
            anchor_row = {
                "mint": mint,
                "ordinal": int(index_row["ordinal"]),
                "anchor": contract["population"]["anchor"],
                **{key: anchor[key] for key in ("decisionUnixMs", "decisionUtc", "mintAgeMs", "triggerSignature", "triggerSlot")},
                "anchorMetadata": anchor,
            }
            features, availability = extract_features(anchor_row, events_by_mint.get(mint, []), 0.001)
            value = features.get("net_external_flow_sol")
            causal = availability.get("causalAvailabilityPass") is True
            exact = availability.get("exactEconomicFeatureStatus") == "EXACT_CAUSAL"
            matched = causal and isinstance(value, (int, float)) and float(value) >= float(frozen["NET_FLOW_Q90_DISCOVERY_THRESHOLD"])
            if not causal or not isinstance(value, (int, float)):
                counters["causalFeatureUnavailableN"] += 1
            if matched:
                counters["featureMatchN"] += 1
                if exact:
                    counters["exactEvaluableMatchN"] += 1
            feature_rows.append({
                "schema": SCHEMA + "_UNSEEN_FEATURE_ROW",
                "ordinal": int(index_row["ordinal"]),
                "mint": mint,
                "partitionKey": partition,
                "decisionUnixMs": int(anchor["decisionUnixMs"]),
                "triggerSignature": anchor["triggerSignature"],
                "net_external_flow_sol": value,
                "causalAvailabilityPass": causal,
                "exactCausalEntryState": exact,
                "fingerprintMatch": matched,
                "sameTimestampPostTriggerRowsExcluded": availability.get("sameTimestampPostTriggerRowsExcluded"),
                "collectionCounters": collection_counters,
            })

    feature_rows.sort(key=lambda row: row["ordinal"])
    rows_path = args.output / "unseen-feature-gate.jsonl.gz"
    with gzip.open(rows_path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": {
            "schema": SCHEMA + "_UNSEEN_FEATURE_GATE",
            "thresholdFreezeSha256": recorded_sha,
            "outcomeFieldsPresent": False,
            "rowCount": len(feature_rows),
        }}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in feature_rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    minimum = int(contract["sampleGate"]["minimumExactEvaluableMatches"])
    if counters["exactEvaluableMatchN"] < minimum:
        verdict = "TOO_FEW_UNSEEN_MATCHES"
    else:
        verdict = "MATCH_GATE_PASSED_ECONOMICS_REQUIRED"
    report = {
        "schema": SCHEMA + "_FEATURE_GATE_REPORT",
        "verdict": verdict,
        "thresholdFreezeSha256": recorded_sha,
        "contractSha256": sha256_file(args.contract),
        "frozenThreshold": frozen["NET_FLOW_Q90_DISCOVERY_THRESHOLD"],
        "unseenOrdinalRange": [start, end],
        "unseenPrefixSha256": frozen["unseenPrefixSha256"],
        "counters": counters,
        "minimumExactEvaluableMatches": minimum,
        "outcomeLabelsRead": 0,
        "economicsEvaluated": False,
        "featureRowsSha256": sha256_file(rows_path),
    }
    (args.output / "feature-gate-report.json").write_bytes(canonical_json(report))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if verdict == "TOO_FEW_UNSEEN_MATCHES" else 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("freeze", "gate"))
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--index", type=pathlib.Path, required=True)
    parser.add_argument("--status", type=pathlib.Path)
    parser.add_argument("--discovery-rows", type=pathlib.Path)
    parser.add_argument("--evidence-root", type=pathlib.Path)
    args = parser.parse_args()
    if args.phase == "freeze":
        if not args.status or not args.discovery_rows:
            parser.error("freeze requires --status and --discovery-rows")
        return freeze(args)
    if not args.evidence_root:
        parser.error("gate requires --evidence-root")
    return gate(args)


if __name__ == "__main__":
    raise SystemExit(main())
