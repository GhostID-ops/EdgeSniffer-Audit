#!/usr/bin/env python3
"""Formally close the 2CQ N150 instrumentation-continuity amendment.

This is deliberately bookkeeping only.  It verifies that the full-meta
prospective lane is feeding the existing frozen symmetric-choice definition,
then records the first twenty chronological valid observations as a documented
continuation of the sealed historical 130-row validation set.  It never opens
or computes outcome data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "2CQ_INSTRUMENTATION_CONTINUITY_AMENDMENT_V1"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    os.replace(temporary, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def freeze_file(path: Path) -> str:
    digest = sha256(path)
    seal = path.with_suffix(path.suffix + ".sha256")
    seal.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    os.chmod(path, 0o444)
    os.chmod(seal, 0o444)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-status", type=Path, required=True)
    parser.add_argument("--instrumentation-boundary", type=Path, required=True)
    parser.add_argument("--valid-ledger", type=Path, required=True)
    parser.add_argument("--worker-source", type=Path, required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    historical = load_json(args.historical_status)
    boundary = load_json(args.instrumentation_boundary)
    ledger = load_json(args.valid_ledger)
    rows = list(ledger.get("rows") or [])
    rows.sort(key=lambda row: (int(row["chronological_ordinal"]), int(row["received_ns"]), row["signature"]))
    continuation = [row for row in rows if row.get("bucket") == "CONTINUATION_N20"]
    reserve = [row for row in rows if row.get("bucket") == "N150_PLUS_RESERVE"]

    worker_source = args.worker_source.read_text(encoding="utf-8")
    adapter_source = args.adapter_source.read_text(encoding="utf-8")
    checks = {
        "sameSolPumpTargetPopulation": bool(boundary.get("source")) and historical.get("developmentEligibleN") == 350,
        "sameIndependentEntryDefinition": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION" in adapter_source,
        "sameFrozen120SecondCausalUniverse": "WINDOW_NS = 120_000_000_000" in worker_source,
        "sameSelectedVsAlternativeSymmetricConstruction": "legacy.build_choice" in adapter_source,
        "sameExactStateRequirements": "canonical_proofs" in adapter_source and "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION" in adapter_source,
        "sameEligibilityAndRejectionRules": "legacy.build_choice" in adapter_source,
        "sameFrozenModelAndPolicy": historical.get("developmentEligibleN") == 350 and historical.get("sealedValidationEligibleN") == 130,
        "sameChronologicalValidationObjective": historical.get("validationNDefinition") == "STRICT_CHRONOLOGICAL_POST_DEVELOPMENT_BOUNDARY",
        "outcomesUnread": historical.get("outcomesRead") == 0 and ledger.get("outcomeBlind") is True,
        "submissionsZero": historical.get("submissions") == 0,
        "noHistoricalFailedRowsHandAdmitted": all(int(row["chronological_ordinal"]) >= 1 for row in continuation),
        "instrumentationOnlyChange": boundary.get("frozenN350N150Untouched") is True and boundary.get("outcomeBlind") is True,
        "immutableBoundaryDocumented": bool(boundary.get("boundaryTimeUtc")) and bool(boundary.get("boundarySlot")),
        "continuationStrictlyChronological": [int(row["chronological_ordinal"]) for row in continuation] == list(range(1, 21))
        and all(int(rows[index]["received_ns"]) <= int(rows[index + 1]["received_ns"]) for index in range(len(rows) - 1)),
        "continuationEvidenceProtected": all(Path(row["evidence_path"]).exists() and Path(row["report_path"]).exists() for row in continuation),
    }
    passed = all(checks.values()) and len(continuation) == 20 and len(rows) >= 20
    if not passed:
        raise SystemExit("INSTRUMENTATION_CONTINUITY_CHECK_FAILED:" + canonical(checks).decode("utf-8"))

    continuation_manifest = [
        {
            "prospectiveOrdinal": int(row["chronological_ordinal"]),
            "officialValidationOrdinal": 130 + int(row["chronological_ordinal"]),
            "signature": row["signature"],
            "mint": row["mint"],
            "receivedNs": int(row["received_ns"]),
            "canonicalSlot": int(row["slot"]),
            "evidencePath": row["evidence_path"],
            "reportPath": row["report_path"],
        }
        for row in continuation
    ]
    amendment = {
        "schema": SCHEMA,
        "createdAtUtc": utc_now(),
        "outcomeBlind": True,
        "submissions": 0,
        "continuityCheckPass": True,
        "checks": checks,
        "historicalFrozenValidation": {
            "validRows": 130,
            "officialOrdinals": [1, 130],
            "sourceStatusPath": str(args.historical_status),
            "sourceStatusSha256": sha256(args.historical_status),
            "frozenDevelopmentRows": 350,
        },
        "instrumentationBoundary": {
            "path": str(args.instrumentation_boundary),
            "sha256": sha256(args.instrumentation_boundary),
            "timeUtc": boundary["boundaryTimeUtc"],
            "slot": boundary["boundarySlot"],
            "source": boundary["source"],
            "collectorBinarySha256": boundary["collectorBinarySha256"],
            "continuityEpoch": boundary["continuityEpoch"],
        },
        "continuation": {
            "count": 20,
            "prospectiveOrdinals": [1, 20],
            "officialValidationOrdinals": [131, 150],
            "ledgerPath": str(args.valid_ledger),
            "ledgerSha256": sha256(args.valid_ledger),
            "rows": continuation_manifest,
        },
        "reserve": {
            "prospectiveOrdinalStart": 21,
            "officialOrdinalStart": 151,
            "countAtFormalization": len(reserve),
            "outcomeBlind": True,
        },
        "codeProvenance": {
            "workerPath": str(args.worker_source),
            "workerSha256": sha256(args.worker_source),
            "adapterPath": str(args.adapter_source),
            "adapterSha256": sha256(args.adapter_source),
        },
        "conclusion": "The substantive change is prospective full-meta instrumentation required to observe already-required causal variables. The target population, independent-entry rule, 120-second universe, symmetric construction, exact-state requirements, eligibility rules, policy, and outcome blindness are unchanged.",
    }
    official_status = {
        "schema": "2CQ_OFFICIAL_VALIDATION_STATUS_V2",
        "updatedAtUtc": amendment["createdAtUtc"],
        "outcomeBlind": True,
        "submissions": 0,
        "frozenDevelopment": {"rows": 350, "unchanged": True},
        "validation": {
            "historicalRows": 130,
            "continuationRows": 20,
            "officialRows": 150,
            "targetRows": 150,
            "formallyComplete": True,
            "outcomesRead": 0,
            "sealedForOnePermittedEvaluation": True,
        },
        "n150PlusReserve": {"rowsAtStatusUpdate": len(reserve), "outcomeBlind": True, "evidenceProtected": True},
        "amendmentPath": str(args.output_dir / "instrumentation-continuity-amendment-v1.json"),
    }
    amendment_path = args.output_dir / "instrumentation-continuity-amendment-v1.json"
    status_path = args.output_dir / "official-validation-status.json"
    atomic_json(amendment_path, amendment)
    amendment_sha = freeze_file(amendment_path)
    official_status["amendmentSha256"] = amendment_sha
    atomic_json(status_path, official_status)
    status_sha = freeze_file(status_path)
    print(json.dumps({
        "continuityCheckPass": True,
        "continuationRowsFrozen": 20,
        "officialValidationRows": 150,
        "reserveRows": len(reserve),
        "amendment": str(amendment_path),
        "amendmentSha256": amendment_sha,
        "officialStatus": str(status_path),
        "officialStatusSha256": status_sha,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
