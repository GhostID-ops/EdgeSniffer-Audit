#!/usr/bin/env python3
"""Fail-closed pre-N3000 audit and immutable pipeline freeze manifest."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
from typing import Any


SCHEMA = "PUMP_EXACT_ALPHA_PIPELINE_FREEZE_AUDIT_V1"
PRIMARY_CELLS = {
    "size_0.50_entry_25_sellable_500",
    "size_1.00_entry_25_sellable_500",
}
EXPECTED_LABEL_CELLS = {
    f"size_{size:.2f}_entry_{entry}_sellable_{sellable}"
    for size in (0.01, 0.10, 0.50, 1.00)
    for entry in (25, 50, 100)
    for sellable in (500, 1000)
}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def audit(ledger_root: pathlib.Path, research_root: pathlib.Path) -> dict[str, Any]:
    scripts = pathlib.Path(__file__).parent
    readiness = json.loads((ledger_root / "readiness-v1/current.json").read_text())
    status = json.loads((research_root / "status.json").read_text())
    boundaries_path = research_root / "boundaries.json"
    boundaries = json.loads(boundaries_path.read_text())
    blockers: list[str] = []

    index_count = 0
    index_mints: set[str] = set()
    expected_ordinal = 1
    with (research_root / "discovery-mint-index.jsonl").open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            index_count += 1
            if row.get("ordinal") != expected_ordinal:
                blockers.append("NON_CONTIGUOUS_DISCOVERY_ORDINAL")
            expected_ordinal += 1
            if row["mint"] in index_mints:
                blockers.append("DUPLICATE_DISCOVERY_MINT")
            index_mints.add(row["mint"])

    opportunity_rows = 0
    row_mints: set[str] = set()
    mint_states: set[tuple[str, str]] = set()
    feature_schema: tuple[str, ...] | None = None
    causal_violations = 0
    label_schema_violations = 0
    duplicate_state_rows = 0
    primary_replay_cells: collections.Counter[str] = collections.Counter()
    primary_outcomes: dict[str, collections.Counter[str]] = {
        key: collections.Counter() for key in PRIMARY_CELLS
    }
    primary_returns: dict[str, set[float]] = {key: set() for key in PRIMARY_CELLS}
    for path in sorted((research_root / "rows-v2").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                opportunity_rows += 1
                mint = row["mint"]
                state_key = (mint, row["observationState"])
                if state_key in mint_states:
                    duplicate_state_rows += 1
                mint_states.add(state_key)
                row_mints.add(mint)
                current_schema = tuple(sorted(row["features"]))
                if feature_schema is None:
                    feature_schema = current_schema
                elif feature_schema != current_schema:
                    blockers.append("INCONSISTENT_FEATURE_SCHEMA")
                if set(row["labels"]) != EXPECTED_LABEL_CELLS:
                    label_schema_violations += 1
                for key in PRIMARY_CELLS:
                    cell = row["labels"].get(key) or {}
                    if cell.get("status") != "EXACT":
                        continue
                    if cell.get("replayCanonicalEvidenceIds"):
                        primary_replay_cells[key] += 1
                    primary_outcomes[key][str(cell.get("outcome") or "MISSING")] += 1
                    if isinstance(cell.get("returnPct"), (int, float)):
                        primary_returns[key].add(round(float(cell["returnPct"]), 12))
                causal = row.get("causalInputs") or {}
                cutoff = int(causal.get("featureCutoffUnixMs") or -1)
                times = [
                    int(item["observedUnixMs"])
                    for key in (
                        "localObservations",
                        "failedObservations",
                        "recurrentWalletPriorObservations",
                    )
                    for item in causal.get(key, [])
                ]
                if any(
                    not item.get("evidenceId")
                    for key in (
                        "localObservations",
                        "failedObservations",
                        "recurrentWalletPriorObservations",
                    )
                    for item in causal.get(key, [])
                ):
                    causal_violations += 1
                create_observation = causal.get("createObservation") or {}
                if not create_observation.get("evidenceId"):
                    causal_violations += 1
                elif create_observation.get("observedUnixMs") is not None:
                    times.append(int(create_observation["observedUnixMs"]))
                supply_observation = causal.get("supplyObservation")
                if supply_observation:
                    if not supply_observation.get("evidenceId"):
                        causal_violations += 1
                    elif supply_observation.get("observedUnixMs") is not None:
                        times.append(int(supply_observation["observedUnixMs"]))
                global_last = causal.get("globalPriorObservationLastUnixMs")
                if global_last is not None:
                    times.append(int(global_last))
                if (
                    row.get("availabilityCausalityPass") is not True
                    or cutoff < 0
                    or any(value > cutoff for value in times)
                    or causal.get("maxFeatureObservationUnixMs") not in (None, max(times, default=None))
                ):
                    causal_violations += 1

    manifest_selected = 0
    manifest_built = 0
    manifest_excluded = 0
    manifest_hashes = []
    exclusion_reasons: collections.Counter[str] = collections.Counter()
    for path in sorted((research_root / "manifests-v2").glob("*.json")):
        manifest = json.loads(path.read_text())
        selected = int(manifest.get("selectedMints") or 0)
        built = int(manifest.get("builtMints") or 0)
        excluded = manifest.get("excluded") or []
        unique_excluded = {row["mint"] for row in excluded}
        if built + len(unique_excluded) != selected:
            blockers.append(f"PARTITION_MINT_ACCOUNTING_MISMATCH:{manifest.get('partitionKey')}")
        if manifest.get("allFeatureRowsAvailabilityCausal") is not True:
            blockers.append(f"PARTITION_CAUSALITY_BLOCKER:{manifest.get('partitionKey')}")
        manifest_selected += selected
        manifest_built += built
        manifest_excluded += len(unique_excluded)
        exclusion_reasons.update(row["reason"] for row in excluded)
        manifest_hashes.append(
            [manifest.get("partitionKey"), manifest.get("sha256"), manifest.get("sourceFingerprintSha256")]
        )

    if duplicate_state_rows:
        blockers.append(f"DUPLICATE_MINT_STATE_ROWS:{duplicate_state_rows}")
    if causal_violations:
        blockers.append(f"AVAILABILITY_CAUSALITY_VIOLATIONS:{causal_violations}")
    if label_schema_violations:
        blockers.append(f"ECONOMIC_LABEL_SCHEMA_VIOLATIONS:{label_schema_violations}")
    for key in sorted(PRIMARY_CELLS):
        if primary_replay_cells[key] == 0 or len(primary_returns[key]) <= 1:
            blockers.append(f"NON_DISCRIMINATIVE_POST_ENTRY_REPLAY:{key}")
    if not row_mints.issubset(index_mints):
        blockers.append("TABLE_CONTAINS_NON_DISCOVERY_MINT")
    if status.get("validationRowsInspected") != 0 or status.get("holdoutRowsInspected") != 0:
        blockers.append("VALIDATION_OR_HOLDOUT_EXPOSED")
    if status.get("liveSubmissions") != 0:
        blockers.append("LIVE_SUBMISSION_DETECTED")
    if set(status.get("primaryEconomicCells") or []) != PRIMARY_CELLS:
        blockers.append("PRIMARY_ECONOMIC_TARGET_MISMATCH")
    exact_n = int(readiness.get("exactAdmittedN") or 0)
    expected_discovery_n = min(exact_n, 10_000)
    if index_count != expected_discovery_n or len(index_mints) != expected_discovery_n:
        blockers.append(
            f"DISCOVERY_INDEX_COUNT_MISMATCH:{len(index_mints)}!={expected_discovery_n}"
        )
    if manifest_selected != expected_discovery_n:
        blockers.append(
            f"MANIFEST_SELECTED_COUNT_MISMATCH:{manifest_selected}!={expected_discovery_n}"
        )
    if manifest_built != len(row_mints):
        blockers.append(
            f"MANIFEST_BUILT_COUNT_MISMATCH:{manifest_built}!={len(row_mints)}"
        )
    if manifest_built + manifest_excluded != expected_discovery_n:
        blockers.append("GLOBAL_MINT_ACCOUNTING_MISMATCH")
    if int(status.get("exactAdmittedN") or -1) != exact_n:
        blockers.append("READINESS_TABLE_EXACT_N_MISMATCH")
    feature_audit = status.get("featureAvailabilityAudit") or {}
    required_feature_families = {
        "transaction_counts",
        "buy_sell_flow",
        "unique_wallets",
        "reserve_movement",
        "recurrent_wallets",
        "market_regime_context",
        "first_impulse",
        "fast_flow",
    }
    if set(feature_audit) != required_feature_families or any(
        value != "REALTIME_OBSERVATION_AT_OR_BEFORE_DECISION"
        for value in feature_audit.values()
    ):
        blockers.append("FEATURE_FAMILY_AVAILABILITY_AUDIT_INCOMPLETE")
    prior_index = status.get("priorWalletIndex") or {}
    if not prior_index.get("sourceFingerprintSha256"):
        blockers.append("PRIOR_WALLET_INDEX_UNFINGERPRINTED")
    if prior_index.get("implementationVersion") != 2:
        blockers.append("PRIOR_WALLET_INDEX_VERSION_MISMATCH")
    if int(prior_index.get("malformedEventIdentities") or 0) != 0:
        blockers.append("PRIOR_WALLET_IDENTITY_DECODE_FAILURE")
    if prior_index.get("semantics") != (
        "full-realtime-source-universe; strictly prior observation; current mint excluded"
    ):
        blockers.append("PRIOR_WALLET_INDEX_SEMANTICS_MISMATCH")
    scans = sorted((research_root / "scans").glob("N*.json"))
    if exact_n < 3000 and scans:
        blockers.append("ALPHA_SCAN_STARTED_BEFORE_N3000")

    timing_semantics = {
        "entryDelayMsFromDecision": [25, 50, 100],
        "firstSellableMsFromDecision": [500, 1000],
        "tpPct": 20,
        "slPct": -20,
        "maxHoldMsFromDecision": 120000,
        "primaryEntryDelayMs": 25,
        "primaryFirstSellableMs": 500,
    }
    economic_schema = {
        "sizesSol": [0.01, 0.10, 0.50, 1.00],
        "primarySizesSol": [0.50, 1.00],
        "labels": sorted(EXPECTED_LABEL_CELLS),
        "engine": "nonlinear_counterfactual_no_mc_proxy",
        "failedTransactionsMutateState": False,
    }
    code_files = [
        "buildPumpExactAlphaResearchV1.py",
        "scanPumpExactAlphaFingerprintsV1.py",
        "trackPumpExactEvidenceReadinessV1.py",
        "reconcilePumpExactEvidenceLedgerV1.py",
        "validatePumpExactEvidenceLedgerV1.py",
    ]
    hashes = {
        "featureSchemaSha256": canonical_hash(feature_schema or []),
        "timingSemanticsSha256": canonical_hash(timing_semantics),
        "economicLabelSchemaSha256": canonical_hash(economic_schema),
        "splitBoundaryDefinitionSha256": sha256_file(boundaries_path),
        "scannerCodeSha256": sha256_file(scripts / "scanPumpExactAlphaFingerprintsV1.py"),
        "pipelineCodeSha256": {
            name: sha256_file(scripts / name) for name in code_files
        },
    }
    return {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "classification": (
            "BLOCKED"
            if blockers
            else (
                "PUMP_EXACT_ALPHA_PIPELINE_N3000_READY"
                if exact_n >= 3000
                else "PUMP_EXACT_ALPHA_PIPELINE_FROZEN_WAITING_N3000"
            )
        ),
        "blockers": sorted(set(blockers)),
        "exactAdmittedUniqueMintN": exact_n,
        "discoveryIndexUniqueMintN": len(index_mints),
        "expectedDiscoveryIndexUniqueMintN": expected_discovery_n,
        "researchTableUniqueMintN": len(row_mints),
        "opportunityRows": opportunity_rows,
        "opportunityRowsAreIndependentSamples": False,
        "performanceSampleUnit": "unique mint, earliest valid trigger, maximum one execution",
        "manifestMintAccounting": {
            "selected": manifest_selected,
            "built": manifest_built,
            "excluded": manifest_excluded,
            "exclusionReasons": dict(sorted(exclusion_reasons.items())),
        },
        "availabilityCausality": {
            "violations": causal_violations,
            "featureSource": "realtime raw observation availability",
            "labelSource": "finalized canonical future truth",
            "featureFamilyAudit": feature_audit,
        },
        "populationScope": "exact-admitted normal SOL-quoted Pump launches only",
        "excludedPopulations": ["Mayhem", "non-SOL-quoted including USDC"],
        "primaryEconomicCells": sorted(PRIMARY_CELLS),
        "robustnessCells": "0.01/0.10 SOL and alternate timing cells",
        "postEntryReplaySanity": {
            key: {
                "nonEmptyReplayCanonicalEvidenceCells": primary_replay_cells[key],
                "distinctExactReturns": len(primary_returns[key]),
                "outcomes": dict(sorted(primary_outcomes[key].items())),
            }
            for key in sorted(PRIMARY_CELLS)
        },
        "boundaries": boundaries,
        "hashes": hashes,
        "evidenceSnapshotHashes": {
            "tableInputManifestsSha256": canonical_hash(manifest_hashes),
            "readinessCurrentSha256": sha256_file(
                ledger_root / "readiness-v1/current.json"
            ),
        },
        "N3000Ready": exact_n >= 3000,
        "alphaScanFiles": [str(path) for path in scans],
        "validationRowsInspected": status.get("validationRowsInspected"),
        "holdoutRowsInspected": status.get("holdoutRowsInspected"),
        "liveSubmissions": status.get("liveSubmissions"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    report = audit(args.ledger_root, args.research_root)
    atomic_json(args.research_root / "pipeline-freeze-audit-current.json", report)
    if args.freeze and report["classification"] != "BLOCKED":
        freeze_path = args.research_root / "PUMP_EXACT_ALPHA_RESEARCH_PIPELINE_V1.freeze.json"
        if freeze_path.exists():
            existing = json.loads(freeze_path.read_text())
            if existing.get("hashes") != report.get("hashes"):
                raise RuntimeError("IMMUTABLE_PIPELINE_FREEZE_HASH_MISMATCH")
        else:
            atomic_json(freeze_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["classification"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
