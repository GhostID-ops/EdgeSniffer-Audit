#!/usr/bin/env python3
"""Refresh the frozen net-flow unseen feature gate without reading outcomes."""

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


SCHEMA = "NET_FLOW_UNSEEN_GATE_REFRESH_V1"


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prefix_sha(rows: list[dict[str, Any]]) -> str:
    payload = "\n".join(f"{row['ordinal']}:{row['mint']}" for row in rows).encode()
    return hashlib.sha256(payload).hexdigest()


def load_index(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for expected, row in enumerate(rows, 1):
        if int(row.get("ordinal", -1)) != expected:
            raise RuntimeError(f"NON_CONTIGUOUS_INDEX:{expected}:{row.get('ordinal')}")
    return rows


def load_previous(path: pathlib.Path, expected_end: int) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        if header.get("outcomeFieldsPresent") is not False:
            raise RuntimeError("PREVIOUS_FEATURE_ARTIFACT_NOT_OUTCOME_BLIND")
        for line in handle:
            row = json.loads(line)
            ordinal = int(row["ordinal"])
            if ordinal > expected_end:
                raise RuntimeError(f"PREVIOUS_ROW_BEYOND_FROZEN_END:{ordinal}")
            if ordinal in rows:
                raise RuntimeError(f"DUPLICATE_PREVIOUS_ORDINAL:{ordinal}")
            rows[ordinal] = row
    return rows


def write_rows(path: pathlib.Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--threshold-freeze", type=pathlib.Path, required=True)
    parser.add_argument("--previous-features", type=pathlib.Path, required=True)
    parser.add_argument("--previous-report", type=pathlib.Path, required=True)
    parser.add_argument("--index", type=pathlib.Path, required=True)
    parser.add_argument("--status", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    parents = contract["parents"]
    if sha256_file(args.threshold_freeze) != parents["thresholdFreezeSha256"]:
        raise RuntimeError("THRESHOLD_FREEZE_HASH_MISMATCH")
    if sha256_file(args.previous_features) != parents["previousFeatureRowsSha256"]:
        raise RuntimeError("PREVIOUS_FEATURE_HASH_MISMATCH")
    if sha256_file(args.previous_report) != parents["previousFeatureGateReportSha256"]:
        raise RuntimeError("PREVIOUS_REPORT_HASH_MISMATCH")
    threshold_freeze = json.loads(args.threshold_freeze.read_text(encoding="utf-8"))
    threshold = float(contract["fingerprint"]["frozenThresholdSol"])
    if float(threshold_freeze["NET_FLOW_Q90_DISCOVERY_THRESHOLD"]) != threshold:
        raise RuntimeError("FROZEN_THRESHOLD_VALUE_MISMATCH")

    status = json.loads(args.status.read_text(encoding="utf-8"))
    if status.get("allFeatureRowsAvailabilityCausal") is not True:
        raise RuntimeError("LATEST_READY_NOT_CAUSAL")
    index = load_index(args.index)
    end = min(
        len(index),
        int(status.get("exactAdmittedN", 0)),
        int(status.get("discoveryMintsAvailable", 0)),
        int(contract["population"]["maximumEndOrdinal"]),
    )
    start = int(contract["population"]["unseenOrdinalStart"])
    previous_end = int(parents["previousEndOrdinal"])
    if end < previous_end:
        raise RuntimeError(f"READY_ENDPOINT_REGRESSED:{end}:{previous_end}")
    if prefix_sha(index[:6848]) != parents["discoveryPrefixSha256"]:
        raise RuntimeError("DISCOVERY_PREFIX_MISMATCH")

    args.output.mkdir(parents=True, exist_ok=True)
    endpoint = {
        "schema": SCHEMA + "_ENDPOINT_FREEZE",
        "frozenAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "contractSha256": sha256_file(args.contract),
        "thresholdFreezeSha256": parents["thresholdFreezeSha256"],
        "frozenThresholdSol": threshold,
        "unseenOrdinalRange": [start, end],
        "deltaOrdinalRange": [previous_end + 1, end] if end > previous_end else None,
        "unseenPrefixSha256": prefix_sha(index[start - 1:end]),
        "combinedPrefixSha256": prefix_sha(index[:end]),
        "indexSha256AtRefresh": sha256_file(args.index),
        "statusSha256AtRefresh": sha256_file(args.status),
        "outcomesRead": 0,
    }
    endpoint_path = args.output / "endpoint-freeze.json"
    endpoint_path.write_text(json.dumps(endpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    feature_rows = load_previous(args.previous_features, previous_end)
    delta = index[previous_end:end]
    by_partition: dict[str, list[dict[str, Any]]] = {}
    for row in delta:
        by_partition.setdefault(row["partitionKey"], []).append(row)
    for partition, index_rows in sorted(by_partition.items()):
        readiness_path = args.evidence_root / "readiness-v1" / "partitions" / f"{partition.replace('/', '-')}-v4.json"
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        targets = {row["mint"] for row in index_rows}
        events_by_mint, collection_counters = collect_partition(args.evidence_root, readiness, targets)
        for index_row in index_rows:
            mint = index_row["mint"]
            create_ms = int(index_row["createReceivedAtUnixNs"]) // 1_000_000
            anchor = reconstruct_anchor_metadata(events_by_mint.get(mint, []), create_ms).get(contract["fingerprint"]["anchor"])
            if anchor is None:
                continue
            anchor_row = {
                "mint": mint,
                "ordinal": int(index_row["ordinal"]),
                "anchor": contract["fingerprint"]["anchor"],
                **{key: anchor[key] for key in ("decisionUnixMs", "decisionUtc", "mintAgeMs", "triggerSignature", "triggerSlot")},
                "anchorMetadata": anchor,
            }
            features, availability = extract_features(anchor_row, events_by_mint.get(mint, []), 0.001)
            value = features.get("net_external_flow_sol")
            causal = availability.get("causalAvailabilityPass") is True
            exact = availability.get("exactEconomicFeatureStatus") == "EXACT_CAUSAL"
            matched = causal and isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) >= threshold
            ordinal = int(index_row["ordinal"])
            feature_rows[ordinal] = {
                "schema": SCHEMA + "_UNSEEN_FEATURE_ROW",
                "ordinal": ordinal,
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
            }

    rows = [feature_rows[ordinal] for ordinal in sorted(feature_rows) if start <= ordinal <= end]
    if len(rows) != len({row["mint"] for row in rows}):
        raise RuntimeError("DUPLICATE_FEATURE_MINT")
    matches = [row for row in rows if row.get("fingerprintMatch")]
    exact_matches = [row for row in matches if row.get("exactCausalEntryState")]
    minimum = int(contract["population"]["minimumExactEvaluableMatches"])
    if len(exact_matches) < minimum:
        verdict = "PREVALIDATION_WINDOW_EXHAUSTED_BELOW_GATE" if end == 9999 else "TOO_FEW_UNSEEN_MATCHES"
    else:
        verdict = "MATCH_GATE_PASSED_ECONOMIC_EVALUATION_REQUIRED"

    rows_path = args.output / "unseen-feature-gate-refresh.jsonl.gz"
    write_rows(rows_path, {
        "schema": SCHEMA + "_UNSEEN_FEATURE_GATE",
        "endpointFreezeSha256": sha256_file(endpoint_path),
        "outcomeFieldsPresent": False,
        "rowCount": len(rows),
    }, rows)
    report = {
        "schema": SCHEMA + "_REPORT",
        "verdict": verdict,
        "currentEndOrdinal": end,
        "unseenOrdinalRange": [start, end],
        "totalUnseenMints": end - start + 1,
        "eligibleCausalAnchorN": len(rows),
        "thresholdMatchN": len(matches),
        "exactEvaluableMatchN": len(exact_matches),
        "minimumExactEvaluableMatches": minimum,
        "frozenThresholdSol": threshold,
        "thresholdUnchanged": True,
        "outcomeLabelsRead": 0,
        "economicsEvaluated": False,
        "endpointFreezeSha256": sha256_file(endpoint_path),
        "featureRowsSha256": sha256_file(rows_path),
        "unseenPrefixSha256": endpoint["unseenPrefixSha256"],
        "collectorStatusAtRefresh": {
            "statusGeneratedAtUtc": status.get("generatedAtUtc"),
            "exactAdmittedN": status.get("exactAdmittedN"),
            "allFeatureRowsAvailabilityCausal": status.get("allFeatureRowsAvailabilityCausal"),
        },
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if len(exact_matches) < minimum else 3


if __name__ == "__main__":
    raise SystemExit(main())
