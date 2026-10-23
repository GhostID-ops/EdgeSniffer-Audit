#!/usr/bin/env python3
"""Finalize the bounded evidence census from already materialized row facts."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import pathlib
import statistics
from typing import Any

from evidenceUniverseStaticCostBridgeV1 import CATEGORIES


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def write_rows(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("classification_script", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--scan-log", type=pathlib.Path)
    args = parser.parse_args()

    source_report_path = args.source / "report.json"
    source_census_path = args.source / "census-rows.jsonl.gz"
    source_bridge_path = args.source / "bridge-rows.jsonl.gz"
    source_provenance_path = args.source / "provenance.json"
    report = json.loads(source_report_path.read_text(encoding="utf-8"))
    prior_provenance = json.loads(source_provenance_path.read_text(encoding="utf-8"))
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    rows = load_rows(source_census_path)

    changed = 0
    for row in rows:
        if (
            row["category"] == "UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT"
            and row.get("missingArrivalExternalSolEventN", 0) > 0
            and not row.get("missingArrivalCouldAffectEligibility", False)
        ):
            row["category"] = "ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE"
            changed += 1

    non_evaluable = [row for row in rows if row["category"] != "EXACT_EVALUABLE"]
    benign = {
        "INERT_OR_NO_EXTERNAL_ACTIVITY",
        "ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE",
    }
    report["categories"] = {}
    for category in CATEGORIES:
        subset = [row for row in non_evaluable if row["category"] == category]
        report["categories"][category] = {
            "N": len(subset),
            "pctAll": len(subset) / len(rows),
            "pctNonEvaluable": len(subset) / len(non_evaluable),
            "blocks": {str(block): sum(row["block"] == block for row in subset) for block in range(1, 6)},
            "risk": (
                "ECONOMICALLY_BENIGN_FOR_FROZEN_STATE_UNIVERSE"
                if category == "ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE"
                else "ECONOMICALLY_BENIGN"
                if category in benign
                else "SELECTION_BIAS_DANGEROUS"
            ),
        }

    any_external = [row for row in rows if row["decodedExternalEventN"] > 0 or row.get("missingArrivalExternalEventN", 0) > 0]
    eligible = [row for row in rows if row["eligibleExternalSolEventN"] > 0 or row.get("missingArrivalCouldAffectEligibility", False)]
    incomplete = {
        "RELEVANT_ACTIVITY_BUT_SOURCE_DATA_INCOMPLETE",
        "UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT",
        "PARSER_COLLECTOR_OR_SOURCE_GAP",
        "OTHER_DOCUMENTED_REASON",
    }
    source_complete = [row for row in rows if row["category"] not in incomplete]
    eligible_source_complete = [row for row in eligible if row["category"] not in incomplete]
    populations = {}
    for name, population in (
        ("allLaunches", rows),
        ("anySuccessfulExternalPumpEconomicActivity", any_external),
        ("sufficientExistingCandidateStateActivity", eligible),
        ("requiredSourceEvidencePresentBeforeReplay", source_complete),
        ("eligibleStateWithRequiredSourceEvidencePresent", eligible_source_complete),
    ):
        exact_n = sum(row["category"] == "EXACT_EVALUABLE" for row in population)
        populations[name] = {"N": len(population), "exactEvaluableN": exact_n, "coverage": exact_n / len(population) if population else None}
    report["coveragePopulations"] = populations

    report["chronology"] = []
    dangerous = set(CATEGORIES) - benign
    for block in range(1, 6):
        block_rows = [row for row in rows if row["block"] == block]
        active = [row for row in block_rows if row["decodedExternalEventN"] > 0 or row.get("missingArrivalExternalEventN", 0) > 0]
        prior = next(item for item in json.loads(source_report_path.read_text(encoding="utf-8"))["chronology"] if item["block"] == block)
        report["chronology"].append({
            "block": block,
            "ordinals": prior["ordinals"],
            "N": len(block_rows),
            "exactN": sum(row["category"] == "EXACT_EVALUABLE" for row in block_rows),
            "rawCoverage": sum(row["category"] == "EXACT_EVALUABLE" for row in block_rows) / len(block_rows),
            "activeN": len(active),
            "activeCoverage": sum(row["category"] == "EXACT_EVALUABLE" for row in active) / len(active) if active else None,
            "dangerousN": sum(row["category"] in dangerous for row in block_rows),
        })

    report["instructionKindsByNonEvaluableCategory"] = {
        category: dict(sum((collections.Counter(row["instructionKinds"]) for row in non_evaluable if row["category"] == category), collections.Counter()))
        for category in CATEGORIES
    }
    if args.scan_log:
        partition_activity = {}
        for line in args.scan_log.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "partition" in item and "transactions" in item:
                partition_activity[str(item["partition"])] = int(item["transactions"])
        if partition_activity:
            activity_cut = statistics.median(partition_activity.values())
            high = {key for key, value in partition_activity.items() if value > activity_cut}
            report["congestion"] = {}
            for name, population in (
                ("higherActivityPartitions", [row for row in rows if row["partitionKey"] in high]),
                ("lowerActivityPartitions", [row for row in rows if row["partitionKey"] not in high]),
            ):
                active = [row for row in population if row["decodedExternalEventN"] > 0 or row.get("missingArrivalExternalEventN", 0) > 0]
                report["congestion"][name] = {
                    "N": len(population),
                    "activeN": len(active),
                    "activeCoverage": sum(row["category"] == "EXACT_EVALUABLE" for row in active) / len(active) if active else None,
                    "dangerousN": sum(row["category"] in dangerous for row in population),
                }
    report["population"] = {
        "total": len(rows),
        "exactEvaluable": sum(row["category"] == "EXACT_EVALUABLE" for row in rows),
        "nonEvaluable": len(non_evaluable),
    }
    relevant = eligible
    report["dangerousLossAmongRelevantActivity"] = sum(row["category"] in dangerous for row in relevant) / len(relevant) if relevant else 1
    gate = contract["coverageVerdictGate"]
    active_coverage = populations["anySuccessfulExternalPumpEconomicActivity"]["coverage"] or 0
    worst_block = min(item["activeCoverage"] for item in report["chronology"] if item["activeCoverage"] is not None)
    if active_coverage >= gate["healthyMinimumCoverageAmongSuccessfulExternalActivity"] and report["dangerousLossAmongRelevantActivity"] <= gate["healthyMaximumDangerousLossAmongRelevantActivity"]:
        report["coverageVerdict"] = "EVIDENCE_UNIVERSE_HEALTHY_FOR_ALPHA_RESEARCH"
    elif active_coverage >= gate["partialMinimumCoverageAmongSuccessfulExternalActivity"] and worst_block >= gate["partialMinimumWorstChronologicalBlockCoverage"]:
        report["coverageVerdict"] = "EVIDENCE_UNIVERSE_PARTIAL_BUT_USABLE"
    else:
        report["coverageVerdict"] = "EVIDENCE_UNIVERSE_SELECTION_BIAS_BLOCKER"
    report["appropriateNextResearchBranch"] = "evidence-pipeline repair" if report["coverageVerdict"] == "EVIDENCE_UNIVERSE_SELECTION_BIAS_BLOCKER" else report["appropriateNextResearchBranch"]
    report["classificationFinalization"] = {
        "lateMissingArrivalSolMintsMovedFromUnsupportedToNoEligibleState": changed,
        "sourceReportSha256": sha256_file(source_report_path),
        "sourceCensusRowsSha256": sha256_file(source_census_path),
    }

    args.output.mkdir(parents=True, exist_ok=True)
    output_report = args.output / "report.json"
    output_census = args.output / "census-rows.jsonl.gz"
    output_bridge = args.output / "bridge-rows.jsonl.gz"
    output_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_rows(output_census, rows)
    output_bridge.write_bytes(source_bridge_path.read_bytes())
    provenance = {
        **prior_provenance,
        "scannerScriptSha256": prior_provenance["scriptSha256"],
        "classificationScriptSha256": sha256_file(args.classification_script),
        "finalizerScriptSha256": sha256_file(pathlib.Path(__file__)),
        "sourceReportSha256": sha256_file(source_report_path),
        "sourceCensusRowsSha256": sha256_file(source_census_path),
        "reportSha256": sha256_file(output_report),
        "censusRowsSha256": sha256_file(output_census),
        "bridgeRowsSha256": sha256_file(output_bridge),
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"changed": changed, "coverageVerdict": report["coverageVerdict"], "economicDiagnosis": report["economicDiagnosis"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
