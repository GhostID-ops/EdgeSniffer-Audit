#!/usr/bin/env python3
"""Execute the one-time, outcome-blind WHAT V1 evaluation on sealed N150."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import random
import sqlite3
import stat
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqFullMetaCanonicalStatePlaneV1 as state_plane
import twoCqProspectiveDatasetAllocationV1 as allocation
import twoCqProspectiveSymmetricChoiceMaterializerV1 as materializer
import twoCqSymmetricCrossSectionalPolicyV1 as frozen_policy


SCHEMA = "2CQ_PROSPECTIVE_WHAT_V1_OFFICIAL_EVALUATION"
BUCKET = "WHAT_V1_PROSPECTIVE_VALIDATION_N150"
REQUIRED_N = 150
POLICY_SHA256 = "bedb0ec347876948937b92c25c2f9df9a3644ce2bb73f3ac258b956dd4d9d1cc"
FEATURE_SET_SHA256 = "ea30c08655bcf709b1bb20db3a5681c2979d890520545fd85877b42029dbabcd"
CONTRACT_SHA256 = "c83f291155062dcb73740787092a940cba6bd4ca68b5bb4b06279f3d7e5e4704"
BOOTSTRAP_SEED = "2CQ_WHAT_V1_PROSPECTIVE_N150_BOOTSTRAP_20260922"


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_manifest(root: pathlib.Path) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    directory = root / "manifests" / BUCKET / "batches"
    files = sorted(directory.glob("*.json"))
    if not files:
        raise RuntimeError("SEALED_N150_MANIFEST_MISSING")
    entries: list[dict[str, Any]] = []
    hashes: list[str] = []
    previous = None
    for sequence, path in enumerate(files, start=1):
        if stat.S_IMODE(path.stat().st_mode) & stat.S_IWUSR:
            raise RuntimeError(f"MANIFEST_BATCH_NOT_SEALED:{path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != allocation.SCHEMA + "_MANIFEST_BATCH":
            raise RuntimeError(f"MANIFEST_SCHEMA_INVALID:{path}")
        if value.get("bucket") != BUCKET or value.get("sequence") != sequence:
            raise RuntimeError(f"MANIFEST_SEQUENCE_INVALID:{path}")
        if value.get("previousManifestSha256") != previous:
            raise RuntimeError(f"MANIFEST_CHAIN_INVALID:{path}")
        body = {key: value[key] for key in (
            "schema", "createdAtUtc", "bucket", "sequence", "previousManifestSha256",
            "allocationContractSha256", "entries",
        )}
        if value.get("manifestBodySha256") != allocation.sha256_bytes(allocation.canonical(body)):
            raise RuntimeError(f"MANIFEST_BODY_HASH_INVALID:{path}")
        previous = sha256_file(path)
        hashes.append(previous)
        entries.extend(value.get("entries") or [])
    manifest_hash = allocation.current_set_hash(BUCKET, hashes)
    expected = list(range(1, REQUIRED_N + 1))
    ordinals = [int(entry.get("ordinal") or 0) for entry in entries]
    if ordinals != expected:
        raise RuntimeError("SEALED_N150_ORDINALS_INVALID")
    if len({(entry["signature"], entry["mint"]) for entry in entries}) != REQUIRED_N:
        raise RuntimeError("SEALED_N150_MEMBERSHIP_DUPLICATE")
    order = [(int(entry["realtimeReceiptUnixNs"]), str(entry["signature"]), str(entry["mint"])) for entry in entries]
    if order != sorted(order):
        raise RuntimeError("SEALED_N150_CHRONOLOGY_INVALID")
    return entries, manifest_hash, {"batchCount": len(files), "batchHashes": hashes}


def verify_entry_artifacts(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        checks = (
            ("eligibilityArtifactReference", "eligibilityArtifactSha256"),
            ("full120sUniverseReference", "full120sUniverseSha256"),
            ("windowPromotionManifestReference", "windowPromotionManifestSha256"),
        )
        for path_key, hash_key in checks:
            path = pathlib.Path(str(entry[path_key]))
            if not path.exists() or sha256_file(path) != str(entry[hash_key]):
                raise RuntimeError(f"SEALED_EVIDENCE_HASH_INVALID:{entry['ordinal']}:{path_key}")


def selection(connection: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    row = connection.execute(
        "SELECT slot,tx_index FROM transactions WHERE signature=?", (str(entry["signature"]),)
    ).fetchone()
    if row is None or int(row[0]) != int(entry["canonicalSlot"]):
        raise RuntimeError(f"SELECTED_CANONICAL_ORDER_MISSING:{entry['ordinal']}")
    return {
        "signature": str(entry["signature"]), "mint": str(entry["mint"]),
        "entryReceivedAtUnixNs": int(entry["realtimeReceiptUnixNs"]),
        "entrySlot": int(row[0]), "entryTransactionIndex": int(row[1]),
        "route": "PUMP_BONDING_CURVE", "acquisitionClass": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION",
    }


def episodes(connection: sqlite3.Connection, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    groups: list[list[dict[str, Any]]] = []
    for entry in entries:
        if not groups or int(entry["realtimeReceiptUnixNs"]) > int(groups[-1][0]["realtimeReceiptUnixNs"]) + materializer.MAX_AGE_NS:
            groups.append([entry])
        else:
            groups[-1].append(entry)
    for group in groups:
        boundaries = [int(entry["realtimeReceiptUnixNs"]) for entry in group]
        events, creates, incomplete = state_plane.load_state(connection, boundaries)
        for entry in group:
            choice, reasons, _ = materializer.build_choice(selection(connection, entry), events, creates, incomplete)
            if choice is None:
                raise RuntimeError(f"SEALED_N150_RECONSTRUCTION_DIVERGED:{entry['ordinal']}:{','.join(reasons)}")
            choice["eligibleOrdinal"] = int(entry["ordinal"])
            output.append(choice)
    materializer.assert_outcome_blind(output)
    return output


def bootstrap(rows: list[dict[str, Any]], means: list[float], scales: list[float], model: tuple[float, list[float]]) -> dict[str, list[float]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[str(row["mint"])].append(row)
    clusters = sorted(grouped)
    generator = random.Random(BOOTSTRAP_SEED)
    samples: dict[str, list[float]] = collections.defaultdict(list)
    for _ in range(10_000):
        sample = [row for mint in (generator.choice(clusters) for _ in clusters) for row in grouped[mint]]
        metrics = timing.ranking_metrics(sample, timing.FULL_FEATURES, means, scales, model)
        for key in ("actualRankedFirstPct", "meanReciprocalRank", "mintBalancedPairwiseConcordance"):
            samples[key].append(float(metrics[key]))
    return {key: [timing.percentile(values, 0.025), timing.percentile(values, 0.975)] for key, values in samples.items()}


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise RuntimeError("OFFICIAL_WHAT_V1_RESULT_ALREADY_EXISTS")
    if sha256_file(args.policy) != POLICY_SHA256:
        raise RuntimeError("FROZEN_POLICY_HASH_MISMATCH")
    if sha256_file(args.contract) != CONTRACT_SHA256:
        raise RuntimeError("FROZEN_EVALUATION_CONTRACT_HASH_MISMATCH")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("dataset", {}).get("bucket") != BUCKET or int(contract.get("dataset", {}).get("requiredN") or 0) != REQUIRED_N:
        raise RuntimeError("FROZEN_EVALUATION_CONTRACT_DATASET_MISMATCH")
    entries, manifest_hash, manifest_detail = read_manifest(args.allocation_root)
    if manifest_hash != args.manifest_hash:
        raise RuntimeError("SEALED_N150_HASH_MISMATCH")
    verify_entry_artifacts(entries)
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    means, scales, model = frozen_policy.unpack_fitted(policy["crossMintModel"])
    if tuple(policy["crossMintModel"]["featureNames"]) != timing.FULL_FEATURES:
        raise RuntimeError("FROZEN_FEATURE_SET_ORDER_MISMATCH")
    connection = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True, timeout=60)
    try:
        rows = episodes(connection, entries)
    finally:
        connection.close()
    if len(rows) != REQUIRED_N:
        raise RuntimeError("VALIDATION_N_NOT_150")
    metrics = timing.ranking_metrics(rows, timing.FULL_FEATURES, means, scales, model)
    intervals = bootstrap(rows, means, scales, model)
    code_hash = sha256_file(pathlib.Path(__file__))
    result = {
        "schema": SCHEMA,
        "evaluatedAtUtc": allocation.utc_now(),
        "sealedN150ManifestHash": manifest_hash,
        "manifestIntegrity": manifest_detail | {"entries": len(entries), "duplicateOrdinals": 0, "chronological": True},
        "frozenWhatPolicySha256": POLICY_SHA256,
        "frozenFeatureSetSha256": FEATURE_SET_SHA256,
        "frozenEvaluationContractSha256": CONTRACT_SHA256,
        "evaluatorCodeSha256": code_hash,
        "validationN": REQUIRED_N,
        "validationRowsEvaluated": len(rows),
        "validationRowsSkipped": 0,
        "crossSectionalMetrics": metrics,
        "mintClusteredBootstrap95": intervals,
        "outcomeBlind": True,
        "whatScoresAccessedBeforeThisRun": 0,
        "validationTuningPerformed": False,
        "policyModified": False,
        "n151PlusAccessedForEvaluation": 0,
        "economicOutcomesRead": 0,
        "pnlRead": 0,
        "submissions": 0,
        "verdict": "WHAT_V1_ONE_TIME_PROSPECTIVE_VALIDATION_COMPLETE",
    }
    atomic_json(args.output, result)
    args.output.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allocation-root", type=pathlib.Path, required=True)
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--policy", type=pathlib.Path, required=True)
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--manifest-hash", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(execute(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
