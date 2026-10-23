#!/usr/bin/env python3
"""One-shot, outcome-blind external evaluation for the frozen 2CQ V2 fingerprint.

This program deliberately has two irreversible stages.  ``certify`` creates an
immutable membership/evidence freeze without scoring a holdout state.  ``score``
refuses to run unless that freeze, the allocation manifests, and the frozen
model all still agree exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqFullMetaCanonicalStatePlaneV1 as state_plane
import twoCqProspectiveDatasetAllocationV1 as allocation
import twoCqProspectiveSymmetricChoiceMaterializerV1 as materializer

BUCKET = "FINGERPRINT_V2_HOLDOUT_N150"
ORDINALS = list(range(501, 651))
EXPECTED_FINGERPRINT_SHA256 = "7606a3a138591516f503e7f7b56501f1828b92947a2b38707bff166fbb233b64"
FREEZE_SCHEMA = "2CQ_FINGERPRINT_V2_HOLDOUT_FREEZE_V1"
RESULT_SCHEMA = "2CQ_FINGERPRINT_V2_HOLDOUT_ONE_SHOT_RESULT_V1"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_immutable(path: pathlib.Path, value: Any) -> str:
    if path.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    path.chmod(0o444)
    return sha256(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_entries(root: pathlib.Path) -> tuple[list[dict[str, Any]], list[str]]:
    directory = root / "manifests" / BUCKET / "batches"
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise RuntimeError("HOLDOUT_MANIFEST_BATCHES_MISSING")
    entries: list[dict[str, Any]] = []
    hashes: list[str] = []
    previous = None
    for sequence, path in enumerate(paths, 1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("bucket") != BUCKET or payload.get("sequence") != sequence:
            raise RuntimeError(f"HOLDOUT_MANIFEST_INVALID:{path}")
        if payload.get("previousManifestSha256") != previous:
            raise RuntimeError(f"HOLDOUT_MANIFEST_CHAIN_INVALID:{path}")
        previous = sha256(path)
        hashes.append(previous)
        entries.extend(payload.get("entries") or [])
    if [int(x.get("ordinal") or 0) for x in entries] != ORDINALS:
        raise RuntimeError("HOLDOUT_ORDINALS_NOT_EXACT_501_650")
    if any(x.get("assignment", {}).get("bucket") != BUCKET for x in entries):
        raise RuntimeError("NON_HOLDOUT_ENTRY_PRESENT")
    identities = [(x.get("signature"), x.get("mint")) for x in entries]
    if len(set(identities)) != len(ORDINALS):
        raise RuntimeError("HOLDOUT_DUPLICATE_IDENTITY")
    chronological = [(int(x["realtimeReceiptUnixNs"]), x["signature"], x["mint"]) for x in entries]
    if chronological != sorted(chronological):
        raise RuntimeError("HOLDOUT_CHRONOLOGY_INVALID")
    for entry in entries:
        for reference, expected in (("eligibilityArtifactReference", "eligibilityArtifactSha256"),
                                    ("full120sUniverseReference", "full120sUniverseSha256"),
                                    ("windowPromotionManifestReference", "windowPromotionManifestSha256")):
            source = pathlib.Path(entry[reference])
            if not source.is_file() or sha256(source) != entry[expected]:
                raise RuntimeError(f"HOLDOUT_EVIDENCE_HASH_INVALID:{entry['ordinal']}:{reference}")
    return entries, hashes


def selection(connection: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    row = connection.execute("SELECT slot,tx_index FROM transactions WHERE signature=?", (entry["signature"],)).fetchone()
    if row is None or int(row[0]) != int(entry["canonicalSlot"]) or row[1] is None:
        raise RuntimeError(f"CANONICAL_ORDER_MISSING:{entry['ordinal']}")
    return {"signature": entry["signature"], "mint": entry["mint"],
            "entryReceivedAtUnixNs": int(entry["realtimeReceiptUnixNs"]), "entrySlot": int(row[0]),
            "entryTransactionIndex": int(row[1]), "route": "PUMP_BONDING_CURVE",
            "acquisitionClass": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION"}


def reconstruct(connection: sqlite3.Connection, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Same bounded, chronological state reconstruction used for the frozen V2
    # development study.  Each call to build_choice independently enforces the
    # immutable 120-second causal/order boundary.
    slice_ns = 30 * 60 * 1_000_000_000
    groups: list[list[dict[str, Any]]] = []
    for entry in entries:
        if not groups or int(groups[-1][0]["realtimeReceiptUnixNs"]) // slice_ns != int(entry["realtimeReceiptUnixNs"]) // slice_ns:
            groups.append([entry])
        else:
            groups[-1].append(entry)
    result: list[dict[str, Any]] = []
    for group in groups:
        events, creates, incomplete = state_plane.load_state(connection, [int(x["realtimeReceiptUnixNs"]) for x in group])
        for entry in group:
            choice, reasons, _ = materializer.build_choice(selection(connection, entry), events, creates, incomplete)
            if choice is None:
                raise RuntimeError(f"HOLDOUT_RECONSTRUCTION_DIVERGED:{entry['ordinal']}:{','.join(reasons)}")
            choice["eligibleOrdinal"] = int(entry["ordinal"])
            result.append(choice)
    if [int(x["eligibleOrdinal"]) for x in result] != ORDINALS:
        raise RuntimeError("HOLDOUT_RECONSTRUCTED_ORDER_INVALID")
    materializer.assert_outcome_blind(result)
    return result


def verify_gap_receipt(path: pathlib.Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = json.dumps(payload, sort_keys=True).lower()
    if "validobservationsadmitted" not in raw:
        raise RuntimeError("STORAGE_INCIDENT_GAP_RECEIPT_UNRECOGNIZED")
    def walk(value: Any) -> list[int]:
        if isinstance(value, dict):
            own = [int(v) for k, v in value.items() if k == "validObservationsAdmitted"]
            return own + sum((walk(v) for v in value.values()), [])
        if isinstance(value, list):
            return sum((walk(v) for v in value), [])
        return []
    admitted = walk(payload)
    if any(admitted):
        raise RuntimeError("STORAGE_INCIDENT_GAP_HAS_ADMITTED_ROWS")
    # The receipt is the authoritative gap disposition; additionally require no
    # holdout entry to name a known incident/gap exclusion in its evidence refs.
    if any("gap" in json.dumps(x).lower() and "coverage" in json.dumps(x).lower() for x in entries):
        raise RuntimeError("HOLDOUT_ENTRY_REFERENCES_COVERAGE_GAP")
    return {"path": str(path), "sha256": sha256(path), "validObservationsAdmitted": admitted}


def status_safety(path: pathlib.Path) -> dict[str, Any]:
    status = json.loads(path.read_text(encoding="utf-8"))
    for key in ("fingerprintHoldoutAccessed", "fingerprintHoldoutAccessedCount", "economicOutcomesRead", "pnlRead"):
        if int(status.get(key, 0) or 0) != 0:
            raise RuntimeError(f"PRE_EVALUATION_ACCESS_TAINT:{key}")
    return {"path": str(path), "sha256": sha256(path),
            "fingerprintHoldoutAccessed": int(status.get("fingerprintHoldoutAccessed", 0) or 0),
            "economicOutcomesRead": int(status.get("economicOutcomesRead", 0) or 0),
            "pnlRead": int(status.get("pnlRead", 0) or 0)}


def fingerprint(path: pathlib.Path) -> tuple[dict[str, Any], str]:
    digest = sha256(path)
    if digest != EXPECTED_FINGERPRINT_SHA256:
        raise RuntimeError(f"FROZEN_FINGERPRINT_HASH_MISMATCH:{digest}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("modelFamily") != "FULL_CAUSAL_LOGISTIC_V1":
        raise RuntimeError("FROZEN_FINGERPRINT_MODEL_FAMILY_INVALID")
    names = tuple(value.get("featureSet") or [])
    if names != tuple(timing.FULL_FEATURES):
        raise RuntimeError("FROZEN_FINGERPRINT_FEATURE_SET_INVALID")
    model = value.get("model") or {}
    if set(model.get("means") or {}) != set(names) or set(model.get("scales") or {}) != set(names) or set(model.get("coefficients") or {}) != set(names):
        raise RuntimeError("FROZEN_FINGERPRINT_MODEL_SCHEMA_INVALID")
    return value, digest


def certify(args: argparse.Namespace) -> dict[str, Any]:
    if args.freeze.exists() or args.result.exists():
        raise RuntimeError("ONE_SHOT_OUTPUT_EXISTS")
    entries, batch_hashes = load_entries(args.allocation_root)
    model, model_sha = fingerprint(args.fingerprint)
    safety = status_safety(args.status)
    gap = verify_gap_receipt(args.gap_receipt, entries)
    connection = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True, timeout=90)
    try:
        rows = reconstruct(connection, entries)
    finally:
        connection.close()
    evidence = [{"ordinal": int(x["ordinal"]), "signature": x["signature"], "mint": x["mint"],
                 "canonicalSlot": int(x["canonicalSlot"]), "realtimeReceiptUnixNs": int(x["realtimeReceiptUnixNs"]),
                 "eligibilityArtifactReference": x["eligibilityArtifactReference"], "eligibilityArtifactSha256": x["eligibilityArtifactSha256"],
                 "full120sUniverseReference": x["full120sUniverseReference"], "full120sUniverseSha256": x["full120sUniverseSha256"],
                 "windowPromotionManifestReference": x["windowPromotionManifestReference"], "windowPromotionManifestSha256": x["windowPromotionManifestSha256"]}
                for x in entries]
    receipt = {"schema": FREEZE_SCHEMA, "frozenAtUtc": utc_now(), "bucket": BUCKET,
               "ordinalRange": [501, 650], "n": 150, "manifestBatchHashes": batch_hashes,
               "manifestSetSha256": allocation.current_set_hash(BUCKET, batch_hashes),
               "fingerprintPath": str(args.fingerprint), "fingerprintSha256": model_sha,
               "evaluatorCodePath": str(pathlib.Path(__file__).resolve()), "evaluatorCodeSha256": sha256(pathlib.Path(__file__).resolve()),
               "allocationContractPath": str(args.contract), "allocationContractSha256": sha256(args.contract),
               "preEvaluationSafety": safety, "storageIncidentGapCheck": gap,
               "certification": {"uniqueOrdinals": 150, "uniqueAdmissions": len({(x['signature'], x['mint']) for x in entries}),
                                  "chronologicalOrdering": "PASS", "allEvidenceHashes": "PASS", "allChoicesReconstructed": len(rows),
                                  "postTriggerContamination": "NOT_PRESENT_BY_FROZEN_BUILD_CHOICE", "incompleteCausalEvidence": "NONE_ADMITTED", "holdoutAccessesBeforeScoring": 0},
               "observations": evidence, "outcomeBlind": True, "economicOutcomesRead": 0, "pnlRead": 0,
               "holdoutScored": False}
    write_immutable(args.freeze, receipt)
    return receipt


def development_mints(allocation_root: pathlib.Path) -> set[str]:
    directory = allocation_root / "manifests" / "FINGERPRINT_V2_DEVELOPMENT_N350" / "batches"
    entries = []
    for path in sorted(directory.glob("*.json")):
        entries.extend(json.loads(path.read_text(encoding="utf-8")).get("entries") or [])
    if [int(x["ordinal"]) for x in entries] != list(range(151, 501)):
        raise RuntimeError("DEVELOPMENT_REFERENCE_MANIFEST_INVALID")
    return {x["mint"] for x in entries}


def metrics(rows: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    names = tuple(model["featureSet"])
    m = model["model"]
    fitted = ([float(m["means"][name]) for name in names], [float(m["scales"][name]) for name in names],
              (float(m["intercept"]), [float(m["coefficients"][name]) for name in names]))
    return timing.ranking_metrics(rows, names, *fitted)


def score(args: argparse.Namespace) -> dict[str, Any]:
    if not args.freeze.is_file() or args.result.exists():
        raise RuntimeError("ONE_SHOT_FREEZE_MISSING_OR_RESULT_EXISTS")
    freeze = json.loads(args.freeze.read_text(encoding="utf-8"))
    if freeze.get("schema") != FREEZE_SCHEMA or freeze.get("holdoutScored") is not False:
        raise RuntimeError("HOLDOUT_FREEZE_INVALID")
    if freeze.get("evaluatorCodeSha256") != sha256(pathlib.Path(__file__).resolve()):
        raise RuntimeError("EVALUATOR_CODE_CHANGED_AFTER_FREEZE")
    if freeze.get("fingerprintSha256") != EXPECTED_FINGERPRINT_SHA256:
        raise RuntimeError("FREEZE_FINGERPRINT_IDENTITY_INVALID")
    entries, hashes = load_entries(args.allocation_root)
    if hashes != freeze.get("manifestBatchHashes") or allocation.current_set_hash(BUCKET, hashes) != freeze.get("manifestSetSha256"):
        raise RuntimeError("HOLDOUT_MANIFEST_CHANGED_AFTER_FREEZE")
    if canonical_sha([{k: x[k] for k in ("ordinal", "signature", "mint", "canonicalSlot", "realtimeReceiptUnixNs")} for x in entries]) != canonical_sha(
        [{k: x[k] for k in ("ordinal", "signature", "mint", "canonicalSlot", "realtimeReceiptUnixNs")} for x in freeze["observations"]]):
        raise RuntimeError("HOLDOUT_MEMBERSHIP_CHANGED_AFTER_FREEZE")
    model, model_sha = fingerprint(args.fingerprint)
    connection = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True, timeout=90)
    try:
        rows = reconstruct(connection, entries)
    finally:
        connection.close()
    overall = metrics(rows, model)
    slices = []
    for lower, upper in ((501, 550), (551, 600), (601, 650)):
        subset = [x for x in rows if lower <= int(x["eligibleOrdinal"]) <= upper]
        slices.append({"ordinals": [lower, upper], "n": len(subset), "metrics": metrics(subset, model)})
    dev_mint_set = development_mints(args.allocation_root)
    novel = [x for x in rows if x["mint"] not in dev_mint_set]
    development = {"expandingFoldMeanTop1Pct": 92.14, "expandingFoldTop3Pct": 99.64,
                   "expandingFoldMRR": 0.957, "expandingFoldPairwiseConcordance": 0.9967,
                   "fullDevelopmentDescriptiveTop1Pct": 94.29}
    comparison = {"top1VsExpandingFoldPp": overall["actualRankedFirstPct"] - development["expandingFoldMeanTop1Pct"],
                  "top1VsFullDevelopmentPp": overall["actualRankedFirstPct"] - development["fullDevelopmentDescriptiveTop1Pct"],
                  "top3VsExpandingFoldPp": overall["actualRankedTop3Pct"] - development["expandingFoldTop3Pct"],
                  "mrrVsExpandingFold": overall["meanReciprocalRank"] - development["expandingFoldMRR"],
                  "pairwiseVsExpandingFold": overall["mintBalancedPairwiseConcordance"] - development["expandingFoldPairwiseConcordance"]}
    result = {"schema": RESULT_SCHEMA, "evaluatedAtUtc": utc_now(), "oneShot": True,
              "holdoutFreezePath": str(args.freeze), "holdoutFreezeSha256": sha256(args.freeze),
              "fingerprintPath": str(args.fingerprint), "fingerprintSha256": model_sha,
              "evaluatorCodePath": str(pathlib.Path(__file__).resolve()), "evaluatorCodeSha256": sha256(pathlib.Path(__file__).resolve()),
              "evaluationContract": {"bucket": BUCKET, "ordinals": [501, 650], "modelFamily": "FULL_CAUSAL_LOGISTIC_V1", "featureSet": model["featureSet"],
                                     "scoring": "frozen standardized logistic ranking; unchanged candidate sets and missing-value behavior", "uncertaintyIntervals": "NOT_PREDECLARED_FOR_V2; omitted rather than introduced after opening holdout"},
              "n": len(rows), "distinctSelectedMints": len({x["mint"] for x in rows}), "metrics": overall,
              "chronologicalSlicesDescriptiveOnly": slices,
              "novelMintDescriptiveOnly": {"n": len(novel), "metrics": metrics(novel, model) if novel else None,
                                               "definition": "selected mint absent from frozen development ordinals 151-500"},
              "developmentReference": development, "developmentToHoldoutChange": comparison,
              "leakageIntegrityAudit": {"holdoutAccessesBeforeOneShot": 0, "thisOneShotEvaluation": 1,
                                          "holdoutInfluencedTrainingOrSelection": False, "economicOutcomesRead": 0, "pnlRead": 0,
                                          "postEntryInformationInCausalFeatures": False, "coverageGapAdmissions": 0,
                                          "fingerprintHashChanged": False, "storageMigrationChangedMembership": False},
              "outcomeBlind": True, "economicOutcomesRead": 0, "pnlRead": 0, "submissions": 0,
              "interpretationConstraint": "This measures selection imitation at known teacher acquisition moments only; it does not establish autonomous WHEN detection, execution latency, fillability, PnL, or trading alpha."}
    write_immutable(args.result, result)
    receipt = {"schema": "2CQ_FINGERPRINT_V2_HOLDOUT_RESULT_SEAL_V1", "sealedAtUtc": utc_now(),
               "resultPath": str(args.result), "resultSha256": sha256(args.result), "freezePath": str(args.freeze),
               "freezeSha256": sha256(args.freeze), "fingerprintSha256": model_sha,
               "evaluatorCodeSha256": sha256(pathlib.Path(__file__).resolve()), "outcomeBlind": True, "pnlRead": 0}
    write_immutable(args.receipt, receipt)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("certify", "score"))
    parser.add_argument("--allocation-root", type=pathlib.Path, required=True)
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--fingerprint", type=pathlib.Path, required=True)
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--status", type=pathlib.Path, required=True)
    parser.add_argument("--gap-receipt", type=pathlib.Path, required=True)
    parser.add_argument("--freeze", type=pathlib.Path, required=True)
    parser.add_argument("--result", type=pathlib.Path, required=True)
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    args = parser.parse_args()
    value = certify(args) if args.mode == "certify" else score(args)
    print(json.dumps({"status": "ok", "schema": value["schema"], "n": value.get("n")}, sort_keys=True))


if __name__ == "__main__":
    main()
