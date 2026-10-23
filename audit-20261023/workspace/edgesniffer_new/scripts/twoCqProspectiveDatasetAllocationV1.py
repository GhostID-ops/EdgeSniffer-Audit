#!/usr/bin/env python3
"""Seal the clean full-meta 2CQ dataset allocation before policy scoring.

This deliberately knows nothing about WHAT scores, economic outcomes, or PnL.
It assigns only already-eligible prospective observations in receipt-time order
and writes immutable, chained manifest batches for the four predeclared sets.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import stat
from collections import Counter
from typing import Any


SCHEMA = "2CQ_FULL_META_PROSPECTIVE_DATASET_ALLOCATION_V1"
WHAT_POLICY_SHA256 = "bedb0ec347876948937b92c25c2f9df9a3644ce2bb73f3ac258b956dd4d9d1cc"
FEATURE_SET_SHA256 = "ea30c08655bcf709b1bb20db3a5681c2979d890520545fd85877b42029dbabcd"
BUCKETS = (
    ("WHAT_V1_PROSPECTIVE_VALIDATION_N150", 1, 150),
    ("FINGERPRINT_V2_DEVELOPMENT_N350", 151, 500),
    ("FINGERPRINT_V2_HOLDOUT_N150", 501, 650),
    ("PROSPECTIVE_RESERVE_N650_PLUS", 651, None),
)
PATH_HASH_CACHE: dict[str, str | None] = {}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    os.replace(temporary, path)


def seal(path: pathlib.Path) -> str:
    """Make a completed contract or manifest batch immutable on this host."""
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return sha256_file(path)


def bucket_for(ordinal: int) -> str:
    for name, start, end in BUCKETS:
        if ordinal >= start and (end is None or ordinal <= end):
            return name
    raise RuntimeError(f"ORDINAL_UNASSIGNABLE:{ordinal}")


def bucket_summary_name(bucket: str) -> str:
    return bucket.lower()


def immutable_batches(root: pathlib.Path, bucket: str) -> list[pathlib.Path]:
    return sorted((root / "manifests" / bucket / "batches").glob("*.json"))


def read_existing(root: pathlib.Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[str]]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    hashes: dict[str, list[str]] = {name: [] for name, _, _ in BUCKETS}
    for bucket, _, _ in BUCKETS:
        for path in immutable_batches(root, bucket):
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema") != SCHEMA + "_MANIFEST_BATCH" or value.get("bucket") != bucket:
                raise RuntimeError(f"INVALID_MANIFEST_BATCH:{path}")
            actual = sha256_file(path)
            if value.get("manifestSha256") and value["manifestSha256"] != actual:
                raise RuntimeError(f"MANIFEST_HASH_MISMATCH:{path}")
            hashes[bucket].append(actual)
            for row in value.get("entries") or []:
                key = (str(row["signature"]), str(row["mint"]))
                if key in rows:
                    raise RuntimeError(f"DUPLICATE_ASSIGNMENT:{key[0]}:{key[1]}")
                rows[key] = row
    return rows, hashes


def current_set_hash(bucket: str, batch_hashes: list[str]) -> str:
    return sha256_bytes(canonical({"schema": SCHEMA + "_MANIFEST_SET", "bucket": bucket, "batchHashes": batch_hashes}))


def source_path_hash(path_text: str) -> tuple[str | None, str | None]:
    if not path_text:
        return None, None
    path = pathlib.Path(path_text)
    key = str(path)
    if key not in PATH_HASH_CACHE:
        PATH_HASH_CACHE[key] = sha256_file(path) if path.exists() else None
    return key, PATH_HASH_CACHE[key]


def boundary_contract(root: pathlib.Path, policy: pathlib.Path, allocator_source: pathlib.Path,
                      state_adapter: pathlib.Path | None, normalizer: pathlib.Path | None) -> dict[str, Any]:
    boundary = root / "cohort-boundary.json"
    value = json.loads(boundary.read_text(encoding="utf-8"))
    policy_hash = sha256_file(policy)
    if policy_hash != WHAT_POLICY_SHA256:
        raise RuntimeError(f"FROZEN_WHAT_POLICY_HASH_MISMATCH:{policy_hash}")
    return {
        "schema": SCHEMA,
        "createdAtUtc": utc_now(),
        "purpose": "Clean future-only full-meta chronological allocation before any WHAT score evaluation",
        "outcomeBlind": True,
        "pnlRead": 0,
        "submissions": 0,
        "prospectiveBoundary": {
            "timeUtc": value["boundaryTimeUtc"],
            "slot": int(value["boundarySlot"]),
            "boundaryArtifact": str(boundary),
            "boundaryArtifactSha256": sha256_file(boundary),
            "collectorBinarySha256": value.get("collectorBinarySha256"),
            "continuityEpoch": value.get("continuityEpoch"),
            "source": value.get("source"),
        },
        "frozenInputs": {
            "whatPolicyPath": str(policy),
            "whatPolicySha256": policy_hash,
            "featureSetSha256": FEATURE_SET_SHA256,
            "allocatorSourceSha256": sha256_file(allocator_source),
            "statePlaneAdapterSha256": sha256_file(state_adapter) if state_adapter and state_adapter.exists() else None,
            "normalizerSha256": sha256_file(normalizer) if normalizer and normalizer.exists() else None,
        },
        "allocation": [
            {"bucket": name, "ordinalStart": start, "ordinalEnd": end, "selection": "strict receipt-time, signature, mint order"}
            for name, start, end in BUCKETS
        ],
        "immutableRules": [
            "An observation may be assigned only after unchanged symmetric eligibility is already true.",
            "No observation may be moved between buckets after its immutable manifest batch is sealed.",
            "No score, economic outcome, PnL, or fingerprint result is read by this allocator.",
            "The old historical N150 artifact is not a source for this prospective allocation.",
            "Collection and assignment continue beyond ordinal 650 without an operational stop.",
        ],
        "supersedesForDefinitiveEvaluation": "Historical/instrumentation-continuity validation bookkeeping only; it is preserved but not used as the definitive prospective WHAT evaluation population.",
    }


def evaluation_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Predeclare scoring interpretation without looking at a prospective score."""
    return {
        "schema": "2CQ_WHAT_V1_PROSPECTIVE_EVALUATION_CONTRACT_V1",
        "createdAtUtc": utc_now(),
        "outcomeBlind": True,
        "pnlRead": 0,
        "submissions": 0,
        "dataset": {
            "bucket": "WHAT_V1_PROSPECTIVE_VALIDATION_N150",
            "ordinals": [1, 150],
            "requiredN": 150,
            "selection": "The immutable assignment manifest in strict prospective receipt-time order.",
        },
        "frozenScoring": {
            "whatPolicySha256": contract["frozenInputs"]["whatPolicySha256"],
            "featureSetSha256": contract["frozenInputs"]["featureSetSha256"],
            "rankingRule": "Rank every complete contemporaneous candidate with the frozen cross-mint model; selected state is evaluated at its rank; exact top-score ties are rejected.",
            "preprocessing": "Exactly the standardized preprocessing serialized in the frozen WHAT policy; no refit, rescaling, imputation change, or score-direction change.",
        },
        "primaryMetrics": {
            "actualRankedFirstPct": "Percent of selected states receiving rank 1 in their complete candidate set.",
            "meanReciprocalRank": "Mean reciprocal selected-state rank across choice episodes.",
            "mintBalancedPairwiseConcordance": "Mean selected-state pairwise concordance against its contemporaneous controls.",
        },
        "secondaryMetrics": {
            "actualRankedTop3Pct": "Percent of selected states in ranks 1 through 3.",
            "actualRankedTop10Pct": "Percent of selected states in the best ceiling(10%) of the candidate set, at least one state.",
            "medianRank": "Median selected-state rank.",
            "meanCandidateStates": "Mean complete candidate-set size.",
        },
        "nullAndBaseline": {
            "definition": "Candidate-set-conditioned uniform random ranker, computed by the frozen ranking metric implementation.",
            "metrics": ["chanceRankedFirstPct", "chanceRankedTop3Pct", "chanceRankedTop10Pct"],
            "reason": "Each choice episode has a different legal alternative count; a global prevalence baseline would be invalid.",
        },
        "uncertainty": {
            "method": "Deterministic 10,000-resample percentile bootstrap clustered by selected mint; resample mint clusters with replacement, retaining all choice episodes in a sampled cluster.",
            "confidenceInterval": [2.5, 97.5],
            "seed": "2CQ_WHAT_V1_PROSPECTIVE_N150_BOOTSTRAP_20260922",
            "reason": "Repeated selected-mint episodes must not be treated as independent observations.",
        },
        "verdict": {
            "type": "DESCRIPTIVE_INFERENTIAL_NO_BINARY_PROMOTION_GATE",
            "rule": "Seal the exact frozen metric values, candidate-set null comparisons, and clustered intervals. No supported/not-supported or deploy/no-deploy threshold is invented after observing N150.",
            "basis": "The inherited frozen WHAT experiment defined one-time evaluation mechanics but no predeclared binary acceptance threshold. The clean prospective path has no prevalidation empirical gate that can be honestly introduced now.",
        },
        "prohibitions": [
            "No policy refit or tuning on N150.",
            "No N150+ reserve access.",
            "No economic outcome, PnL, or live-submission access.",
            "No fingerprint development or holdout access before their separately declared stages.",
        ],
    }


def entry_from_ledger(row: dict[str, Any], ordinal: int, bucket: str, contract: dict[str, Any]) -> dict[str, Any]:
    evidence_path, evidence_hash = source_path_hash(str(row.get("evidence_path") or ""))
    report_path, report_hash = source_path_hash(str(row.get("report_path") or ""))
    promotion_manifest = pathlib.Path(evidence_path).parent / "manifest.json" if evidence_path else None
    promotion_hash = sha256_file(promotion_manifest) if promotion_manifest and promotion_manifest.exists() else None
    received_ns = int(row["received_ns"])
    return {
        "ordinal": ordinal,
        "signature": str(row["signature"]),
        "mint": str(row["mint"]),
        "realtimeReceiptUnixNs": received_ns,
        "realtimeReceiptUtc": dt.datetime.fromtimestamp(received_ns / 1_000_000_000, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "canonicalSlot": int(row["slot"]),
        "canonicalOrderReference": report_path,
        "createProvenanceReference": report_path,
        "full120sUniverseReference": evidence_path,
        "full120sUniverseSha256": evidence_hash,
        "windowPromotionManifestReference": str(promotion_manifest) if promotion_manifest else None,
        "windowPromotionManifestSha256": promotion_hash,
        "selectedStateEvidenceReference": report_path,
        "alternativeStateEvidenceReference": report_path,
        "eligibilityArtifactReference": report_path,
        "eligibilityArtifactSha256": report_hash,
        "instrumentationBoundary": contract["prospectiveBoundary"],
        "statePlaneGeneration": {
            "adapterSha256": contract["frozenInputs"]["statePlaneAdapterSha256"],
            "normalizerSha256": contract["frozenInputs"]["normalizerSha256"],
        },
        "assignment": {"bucket": bucket, "sourceValidLedgerOrdinal": int(row["chronological_ordinal"]), "batchId": str(row["batch_id"])},
        "outcomeBlind": True,
        "pnlRead": 0,
    }


def append_batches(root: pathlib.Path, contract: dict[str, Any], ledger_rows: list[dict[str, Any]]) -> dict[str, Any]:
    existing, hashes = read_existing(root)
    ordered = sorted(ledger_rows, key=lambda row: (int(row["received_ns"]), str(row["signature"]), str(row["mint"])))
    pending: dict[str, list[dict[str, Any]]] = {name: [] for name, _, _ in BUCKETS}
    prior_key: tuple[int, str, str] | None = None
    for ordinal, row in enumerate(ordered, start=1):
        key = (str(row["signature"]), str(row["mint"]))
        current_key = (int(row["received_ns"]), key[0], key[1])
        if prior_key and current_key < prior_key:
            raise RuntimeError("LEDGER_CHRONOLOGY_NOT_MONOTONIC")
        prior_key = current_key
        bucket = bucket_for(ordinal)
        present = existing.get(key)
        if present:
            if int(present["ordinal"]) != ordinal or present["assignment"]["bucket"] != bucket:
                raise RuntimeError(f"IMMUTABLE_ASSIGNMENT_CONFLICT:{key[0]}:{key[1]}")
            continue
        pending[bucket].append(entry_from_ledger(row, ordinal, bucket, contract))
    written: dict[str, list[str]] = {name: [] for name, _, _ in BUCKETS}
    for bucket, entries in pending.items():
        if not entries:
            continue
        directory = root / "manifests" / bucket / "batches"
        directory.mkdir(parents=True, exist_ok=True)
        previous = hashes[bucket][-1] if hashes[bucket] else None
        sequence = len(hashes[bucket]) + 1
        body = {
            "schema": SCHEMA + "_MANIFEST_BATCH",
            "createdAtUtc": utc_now(),
            "bucket": bucket,
            "sequence": sequence,
            "previousManifestSha256": previous,
            "allocationContractSha256": sha256_file(root / "assignment-contract.json"),
            "entries": entries,
        }
        # Hash the exact immutable body, then write the self-describing result.
        body_hash = sha256_bytes(canonical(body))
        value = dict(body)
        value["manifestBodySha256"] = body_hash
        path = directory / f"{sequence:06d}-{entries[0]['ordinal']:06d}-{entries[-1]['ordinal']:06d}.json"
        atomic_json(path, value)
        digest = seal(path)
        hashes[bucket].append(digest)
        written[bucket].append(str(path))
    all_existing, final_hashes = read_existing(root)
    counts = Counter(entry["assignment"]["bucket"] for entry in all_existing.values())
    return {
        "assignedRows": len(all_existing),
        "newManifestPaths": written,
        "bucketCounts": {name: int(counts[name]) for name, _, _ in BUCKETS},
        "bucketManifestHashes": {name: current_set_hash(name, final_hashes[name]) for name, _, _ in BUCKETS},
    }


def create_or_verify(root: pathlib.Path, policy: pathlib.Path, allocator_source: pathlib.Path,
                     state_adapter: pathlib.Path | None, normalizer: pathlib.Path | None) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    contract_path = root / "assignment-contract.json"
    evaluation_path = root / "what-v1-evaluation-contract.json"
    if contract_path.exists():
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        if contract.get("schema") != SCHEMA:
            raise RuntimeError("EXISTING_ASSIGNMENT_CONTRACT_INVALID")
        if contract["frozenInputs"].get("whatPolicySha256") != WHAT_POLICY_SHA256 or contract["frozenInputs"].get("featureSetSha256") != FEATURE_SET_SHA256:
            raise RuntimeError("EXISTING_ASSIGNMENT_CONTRACT_FROZEN_INPUT_MISMATCH")
    else:
        contract = boundary_contract(root.parent, policy, allocator_source, state_adapter, normalizer)
        atomic_json(contract_path, contract)
        seal(contract_path)
    if evaluation_path.exists():
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        if evaluation.get("schema") != "2CQ_WHAT_V1_PROSPECTIVE_EVALUATION_CONTRACT_V1":
            raise RuntimeError("EXISTING_WHAT_EVALUATION_CONTRACT_INVALID")
    else:
        evaluation = evaluation_contract(contract)
        atomic_json(evaluation_path, evaluation)
        seal(evaluation_path)
    return contract, sha256_file(contract_path), evaluation, sha256_file(evaluation_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True, help="Full-meta prospective lane root")
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    parser.add_argument("--allocation-root", type=pathlib.Path, required=True)
    parser.add_argument("--policy", type=pathlib.Path, required=True)
    parser.add_argument("--state-adapter", type=pathlib.Path)
    parser.add_argument("--normalizer", type=pathlib.Path)
    args = parser.parse_args()
    contract, contract_hash, _, evaluation_hash = create_or_verify(
        args.allocation_root, args.policy, pathlib.Path(__file__), args.state_adapter, args.normalizer)
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    if ledger.get("schema") != "2CQ_FULL_META_PROSPECTIVE_VALID_OBSERVATION_LEDGER_V1" or not ledger.get("outcomeBlind") or int(ledger.get("submissions") or 0) != 0:
        raise RuntimeError("VALID_LEDGER_SCIENTIFIC_CONTRACT_INVALID")
    result = append_batches(args.allocation_root, contract, list(ledger.get("rows") or []))
    result.update({
        "schema": SCHEMA,
        "allocationContractPath": str(args.allocation_root / "assignment-contract.json"),
        "allocationContractSha256": contract_hash,
        "whatEvaluationContractPath": str(args.allocation_root / "what-v1-evaluation-contract.json"),
        "whatEvaluationContractSha256": evaluation_hash,
        "outcomeBlind": True,
        "whatScoresAccessed": 0,
        "fingerprintDevelopmentAnalysisStarted": False,
        "fingerprintHoldoutAccessed": 0,
        "economicOutcomesRead": 0,
        "pnlRead": 0,
        "submissions": 0,
    })
    atomic_json(args.allocation_root / "current-status.json", result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
