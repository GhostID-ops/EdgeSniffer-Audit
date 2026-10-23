#!/usr/bin/env python3
"""Gate a future-only 2CQ symmetric cohort on fresh dual-source CREATE coverage.

This script is outcome-blind. It reads only finalized CREATE provenance and
closed exact-partition metadata. The existing N350/N150 cohort is never opened
or mutated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
from typing import Any

import twoCqSolPumpCreateProvenanceRegistryV1 as registry


SCHEMA = "2CQ_DUAL_SOURCE_CREATE_COVERAGE_GATE_V1"
CONTRACT_SCHEMA = "2CQ_DUAL_SOURCE_CREATE_SYMMETRIC_COHORT_V1_CONTRACT"
ALLENHARK_SOURCE = "allenhark-jito-shreds-grpc-v1"
YELLOWSTONE_SOURCE = "yellowstone-pump-transactions-v1"
REQUIRED_PARTITIONS = 3
MIN_UNION_COVERAGE = 99.5


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def status_rows(exact_root: pathlib.Path, baseline_end_slot: int) -> list[tuple[dict[str, Any], pathlib.Path]]:
    rows: list[tuple[dict[str, Any], pathlib.Path]] = []
    for path in sorted((exact_root / "partitions").glob("*/*/*/*/reconciliation-status.json")):
        status = load_json(path)
        if (
            status.get("temporalHourClosed") is True
            and status.get("exactReplayComplete") is True
            and int(status.get("endSlot") or 0) > baseline_end_slot
        ):
            rows.append((status, path))
    return rows


def coverage_for_partition(
    connection: sqlite3.Connection, partition_key: str, start_slot: int, end_slot: int,
) -> dict[str, int | float | None | bool]:
    # A closed exact partition counts only after the isolated registry has
    # consumed that same finalized report. This prevents a zero-denominator
    # partition that simply has not been indexed yet from advancing the gate.
    registry_processed = connection.execute(
        "SELECT 1 FROM yellowstone_processed_partitions WHERE partition_key=?",
        (partition_key,),
    ).fetchone() is not None
    finalized = int(connection.execute(
        "SELECT COUNT(*) FROM create_registry WHERE slot BETWEEN ? AND ?", (start_slot, end_slot)
    ).fetchone()[0])
    allenhark = int(connection.execute("""
        SELECT COUNT(*) FROM create_registry r
        WHERE r.slot BETWEEN ? AND ? AND EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature=r.signature AND a.source=?
        )
    """, (start_slot, end_slot, ALLENHARK_SOURCE)).fetchone()[0])
    yellowstone = int(connection.execute("""
        SELECT COUNT(*) FROM create_registry r
        WHERE r.slot BETWEEN ? AND ? AND EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature=r.signature AND a.source=?
        )
    """, (start_slot, end_slot, YELLOWSTONE_SOURCE)).fetchone()[0])
    both = int(connection.execute("""
        SELECT COUNT(*) FROM create_registry r
        WHERE r.slot BETWEEN ? AND ? AND EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature=r.signature AND a.source=?
        ) AND EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature=r.signature AND a.source=?
        )
    """, (start_slot, end_slot, ALLENHARK_SOURCE, YELLOWSTONE_SOURCE)).fetchone()[0])
    union = int(connection.execute("""
        SELECT COUNT(*) FROM create_registry r
        WHERE r.slot BETWEEN ? AND ? AND EXISTS (
          SELECT 1 FROM source_arrival_candidates a WHERE a.signature=r.signature
        )
    """, (start_slot, end_slot)).fetchone()[0])
    return {
        "registryProcessed": registry_processed,
        "finalizedCreateDenominator": finalized,
        "allenharkMatches": allenhark,
        "yellowstoneMatches": yellowstone,
        "bothSourceMatches": both,
        "unionMatches": union,
        "unexplainedMisses": finalized - union,
        "unionCoveragePercent": round(100 * union / finalized, 4) if finalized else None,
    }


def initialize_gate_contract(args: argparse.Namespace) -> dict[str, Any]:
    path = args.gate_root / "gate-contract.json"
    if path.exists():
        return load_json(path)
    closed = status_rows(args.exact_root, -1)
    if not closed:
        raise RuntimeError("NO_CLOSED_EXACT_PARTITION_FOR_GATE_BASELINE")
    baseline, _ = closed[-1]
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "armedAtUtc": iso(now()),
        "baselinePartitionKey": baseline["partitionKey"],
        "baselineEndSlot": int(baseline["endSlot"]),
        "requiredAdditionalClosedPartitions": REQUIRED_PARTITIONS,
        "minimumUnionCoveragePercent": MIN_UNION_COVERAGE,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(path, contract, 0o444)
    return contract


def current_source_health(allenhark_health: pathlib.Path, registry_status: pathlib.Path) -> dict[str, Any]:
    allenhark = load_json(allenhark_health)
    dual = load_json(registry_status)
    return {
        "allenhark": {
            "connected": allenhark.get("connected"),
            "reconnects": allenhark.get("reconnects"),
            "decodeFailures": allenhark.get("decodeFailures"),
            "continuityEpochs": dual.get("continuityEpochs"),
        },
        "yellowstone": {
            "sourceSpecificReceiptsRetained": dual.get("yellowstoneCaptured"),
            "sourceClocksIndependentlyAuditable": dual.get("sourceClocksIndependentlyAuditable"),
        },
    }


def activate_cohort(args: argparse.Namespace, coverage: dict[str, Any]) -> dict[str, Any]:
    contract_path = args.new_cohort_root / "contract.json"
    if contract_path.exists():
        return {"activated": False, "reason": "ALREADY_ACTIVATED", "contractPath": str(contract_path)}
    requested_ns = int(now().timestamp() * 1_000_000_000)
    try:
        boundary = registry.canonical_boundary(args.all_transaction_root, requested_ns)
    except RuntimeError as exc:
        # The cohort must start at an actual post-gate realtime receipt, never
        # at an inferred wall-clock boundary. A later timer cycle retries.
        return {"activated": False, "reason": "WAITING_FOR_POST_GATE_ALLENHARK_BOUNDARY", "detail": str(exc)}
    start_ns = int(boundary["firstRealtimeReceiptUnixNs"])
    contract = {
        "schema": CONTRACT_SCHEMA,
        "cohortId": "2CQ_SOL_PUMP_DUAL_SOURCE_CREATE_SYMMETRIC_V1",
        "cohortStartUtc": iso(dt.datetime.fromtimestamp(start_ns / 1_000_000_000, dt.timezone.utc)),
        "cohortStartSlot": int(boundary["firstCanonicalSlot"]),
        "creationProvenance": {
            "registry": str(args.registry_db),
            "finalizedCanonicalProofRequired": True,
            "sourcePrecedence": [ALLENHARK_SOURCE, YELLOWSTONE_SOURCE],
            "sourceTimestampsKeptSeparate": True,
            "clockMergingPermitted": False,
            "yellowstoneRequiresCalibratedClockDomain": True,
        },
        "population": {
            "developmentTargetN": 350,
            "validationTargetN": 150,
            "futureOnly": True,
            "historicalBackfillPermitted": False,
        },
        "onlineChoice": {
            "economicFamilies": ["SOL_PUMP_CURVE", "SOL_PUMP_CPI"],
            "ageMaximumMs": 120000,
            "exactFinalizedStateRequired": True,
            "knownFlatIndependentEntryRequired": True,
            "completePreEntryHistoryRequired": True,
            "selectedAndControlsSymmetric": True,
            "legalContemporaneousAlternativeRequired": True,
        },
        "integrity": {
            "outcomesPermitted": False,
            "profitabilityPermitted": False,
            "submissionsPermitted": False,
            "validationRowsPermittedBeforeFreeze": False,
            "validationSealed": True,
        },
        "coverageGate": coverage,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(contract_path, contract, 0o444)
    atomic_json(args.new_cohort_root / "activation.json", {
        "schema": SCHEMA + "_ACTIVATION",
        "activatedAtUtc": iso(now()),
        "boundary": boundary,
        "coverageGate": coverage,
        "outcomesRead": 0,
        "submissions": 0,
    })
    subprocess.run(["/bin/systemctl", "start", args.new_cohort_timer], check=True)
    return {"activated": True, "contractPath": str(contract_path), "boundary": boundary}


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = initialize_gate_contract(args)
    connection = sqlite3.connect(f"file:{args.registry_db}?mode=ro", uri=True)
    try:
        rows = status_rows(args.exact_root, int(contract["baselineEndSlot"]))
        completed: list[dict[str, Any]] = []
        for status, _path in rows:
            metrics = coverage_for_partition(
                connection,
                str(status["partitionKey"]),
                int(status["startSlot"]),
                int(status["endSlot"]),
            )
            # A partition is eligible for the gate only after the registry has
            # seen its CREATEs. Zero CREATEs are recorded but cannot be used to
            # manufacture a denominator-free pass.
            completed.append({"partitionKey": status["partitionKey"], **metrics})
    finally:
        connection.close()
    indexed_completed = [row for row in completed if row["registryProcessed"] is True]
    usable = [row for row in indexed_completed if int(row["finalizedCreateDenominator"]) > 0]
    denominator = sum(int(row["finalizedCreateDenominator"]) for row in usable)
    union = sum(int(row["unionMatches"]) for row in usable)
    misses = sum(int(row["unexplainedMisses"]) for row in usable)
    coverage = round(100 * union / denominator, 4) if denominator else None
    health = current_source_health(args.allenhark_health, args.registry_status)
    gate_pass = (
        len(completed) >= REQUIRED_PARTITIONS
        and len(indexed_completed) >= REQUIRED_PARTITIONS
        and denominator > 0
        and coverage is not None
        and coverage >= MIN_UNION_COVERAGE
        and misses == 0
        and health["allenhark"]["connected"] is True
        and health["yellowstone"]["sourceClocksIndependentlyAuditable"] is True
    )
    summary = {
        "schema": SCHEMA + "_STATUS",
        "atUtc": iso(now()),
        "gateContract": contract,
        "additionalClosedPartitions": completed,
        "additionalRegistryProcessedPartitions": len(indexed_completed),
        "additionalCreateCoverage": {
            "finalizedCreateDenominator": denominator,
            "allenharkMatches": sum(int(row["allenharkMatches"]) for row in usable),
            "yellowstoneMatches": sum(int(row["yellowstoneMatches"]) for row in usable),
            "bothSourceMatches": sum(int(row["bothSourceMatches"]) for row in usable),
            "unionMatches": union,
            "unionCoveragePercent": coverage,
            "unexplainedMisses": misses,
        },
        "continuityHealth": health,
        "coverageGatePass": gate_pass,
        "outcomesRead": 0,
        "submissions": 0,
    }
    if gate_pass:
        summary["activation"] = activate_cohort(args, summary["additionalCreateCoverage"])
    else:
        summary["activation"] = {"activated": False, "reason": "WAITING_FOR_FRESH_CLOSED_PARTITIONS_OR_COVERAGE_GATE"}
    atomic_json(args.gate_root / "status.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-root", type=pathlib.Path, required=True)
    parser.add_argument("--registry-db", type=pathlib.Path, required=True)
    parser.add_argument("--registry-status", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--allenhark-health", type=pathlib.Path, required=True)
    parser.add_argument("--all-transaction-root", type=pathlib.Path, required=True)
    parser.add_argument("--new-cohort-root", type=pathlib.Path, required=True)
    parser.add_argument("--new-cohort-timer", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
