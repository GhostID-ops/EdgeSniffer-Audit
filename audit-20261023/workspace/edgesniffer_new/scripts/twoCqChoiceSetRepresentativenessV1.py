#!/usr/bin/env python3
"""Outcome-blind coverage-bias gate for the N350 2CQ choice-set cohort."""

from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import hashlib
import json
import math
import pathlib
import statistics
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing


SCHEMA = "2CQ_CHOICE_SET_REPRESENTATIVENESS_V1"
DEVELOPMENT_N = 350
CLASSIFIER_TRAIN_END = 245


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def ks_distance(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 1.0
    a, b = sorted(left), sorted(right)
    points = sorted(set(a + b))
    return max(
        abs(bisect.bisect_right(a, value) / len(a) - bisect.bisect_right(b, value) / len(b))
        for value in points
    )


def numeric_comparison(name: str, covered: list[float], missing: list[float]) -> dict[str, Any]:
    covered_mean = statistics.fmean(covered)
    missing_mean = statistics.fmean(missing)
    covered_var = statistics.pvariance(covered) if len(covered) > 1 else 0.0
    missing_var = statistics.pvariance(missing) if len(missing) > 1 else 0.0
    pooled = math.sqrt((covered_var + missing_var) / 2.0)
    if pooled <= 1e-12:
        smd = 0.0 if covered_mean == missing_mean else math.inf
    else:
        smd = (covered_mean - missing_mean) / pooled
    return {
        "field": name,
        "coveredMean": covered_mean,
        "missingMean": missing_mean,
        "coveredMedian": statistics.median(covered),
        "missingMedian": statistics.median(missing),
        "standardizedMeanDifference": smd,
        "absoluteStandardizedMeanDifference": abs(smd),
        "ksDistance": ks_distance(covered, missing),
    }


def categorical_comparison(field: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    covered = collections.Counter(str(row[field]) for row in rows if row["covered"])
    missing = collections.Counter(str(row[field]) for row in rows if not row["covered"])
    covered_n, missing_n = sum(covered.values()), sum(missing.values())
    levels = sorted(set(covered) | set(missing))
    details = []
    for level in levels:
        covered_share = covered[level] / covered_n
        missing_share = missing[level] / missing_n
        details.append({
            "level": level,
            "coveredN": covered[level],
            "missingN": missing[level],
            "coveredShare": covered_share,
            "missingShare": missing_share,
            "absoluteShareDifference": abs(covered_share - missing_share),
        })
    return {
        "field": field,
        "totalVariation": 0.5 * sum(row["absoluteShareDifference"] for row in details),
        "maximumSingleLevelShareDifference": max(row["absoluteShareDifference"] for row in details),
        "levels": details,
    }


def make_rows(
    tape: list[dict[str, Any]], covered_ids: set[str], omissions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    omission_by_id = {str(row["episodeId"]): row for row in omissions}
    feature_names = sorted({name for row in tape for name in row["features"]})
    rows = []
    for row in tape:
        episode_id = str(row["signature"])
        covered = episode_id in covered_ids
        omission = omission_by_id.get(episode_id)
        if covered == (omission is not None):
            raise RuntimeError(f"COVERAGE_MEMBERSHIP_INCONSISTENT:{episode_id}")
        structural = {
            "eligibleOrdinal": float(row["eligibleOrdinal"]),
            "causalAgeMs": (
                int(row["decisionStateReceivedAtUnixNs"]) - int(row["createReceivedAtUnixNs"])
            ) / 1_000_000,
            "decisionToTeacherReceiptMs": float(row["decisionToTeacherReceiptMs"]),
            "naturalFirstBuySol": float(row["naturalFirstBuySol"]),
            "earlierExternalControlN": float(row["earlierExternalControlN"]),
        }
        features = {name: float(row["features"].get(name) or 0.0) for name in feature_names}
        rows.append({
            "episodeId": episode_id,
            "eligibleOrdinal": int(row["eligibleOrdinal"]),
            "mint": row["mint"],
            "covered": covered,
            "route": row["route"],
            "acquisitionClass": row["acquisitionClass"],
            "sizeTier": str(row["sizeTier"]),
            "structural": structural,
            "features": features,
            "omissionReasons": [] if covered else sorted(omission["reasons"]),
        })
    return rows, feature_names


def classifier_auc(rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    names = [
        "causalAgeMs", "decisionToTeacherReceiptMs", "naturalFirstBuySol",
        "earlierExternalControlN",
    ] + [f"feature:{name}" for name in feature_names]

    def vector(row: dict[str, Any]) -> list[float]:
        return [
            row["structural"]["causalAgeMs"],
            row["structural"]["decisionToTeacherReceiptMs"],
            row["structural"]["naturalFirstBuySol"],
            row["structural"]["earlierExternalControlN"],
        ] + [row["features"][name] for name in feature_names]

    train = [row for row in rows if row["eligibleOrdinal"] <= CLASSIFIER_TRAIN_END]
    assessment = [row for row in rows if row["eligibleOrdinal"] > CLASSIFIER_TRAIN_END]
    train_x = [vector(row) for row in train]
    train_y = [int(row["covered"]) for row in train]
    assessment_x = [vector(row) for row in assessment]
    assessment_y = [int(row["covered"]) for row in assessment]
    means, scales = timing.standardizer(train_x)
    model = timing.fit_logistic(
        timing.transform(train_x, means, scales), train_y, [1.0] * len(train_y)
    )
    scores = timing.predict(timing.transform(assessment_x, means, scales), model)
    auc = timing.auc(assessment_y, scores)
    direction_agnostic = max(auc, 1.0 - auc) if auc is not None else 1.0
    return {
        "featureN": len(names),
        "trainingN": len(train),
        "assessmentN": len(assessment),
        "assessmentCoveredN": sum(assessment_y),
        "assessmentAuc": auc,
        "directionAgnosticAssessmentAuc": direction_agnostic,
    }


def gate(name: str, value: float, operator: str, threshold: float) -> dict[str, Any]:
    passed = value >= threshold if operator == ">=" else value <= threshold
    return {"name": name, "value": value, "operator": operator, "threshold": threshold, "passed": passed}


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("outcomesPermitted") is not False or contract.get("validationPermitted") is not False:
        raise RuntimeError("CONTRACT_NOT_OUTCOME_BLIND")
    tape = read_jsonl_gz(args.entry_tape)
    choice_set = read_jsonl_gz(args.choice_set)
    omissions = read_jsonl_gz(args.omissions)
    if [int(row["eligibleOrdinal"]) for row in tape] != list(range(1, DEVELOPMENT_N + 1)):
        raise RuntimeError("DEVELOPMENT_POPULATION_NOT_EXACT_ORDINALS_1_350")
    covered_ids = {str(row["episodeId"]) for row in choice_set}
    rows, feature_names = make_rows(tape, covered_ids, omissions)
    covered_rows = [row for row in rows if row["covered"]]
    missing_rows = [row for row in rows if not row["covered"]]

    chronological = []
    for block in range(5):
        start, end = block * 70 + 1, (block + 1) * 70
        sample = [row for row in rows if start <= row["eligibleOrdinal"] <= end]
        covered_n = sum(row["covered"] for row in sample)
        chronological.append({
            "block": block + 1, "ordinals": [start, end], "n": len(sample),
            "coveredN": covered_n, "coveredShare": covered_n / len(sample),
        })
    block_shares = [row["coveredShare"] for row in chronological]

    structural_fields = list(contract["structuralNumericAssessment"]["fields"])
    structural = [numeric_comparison(
        name,
        [row["structural"][name] for row in covered_rows],
        [row["structural"][name] for row in missing_rows],
    ) for name in structural_fields]
    features = [numeric_comparison(
        name,
        [row["features"][name] for row in covered_rows],
        [row["features"][name] for row in missing_rows],
    ) for name in feature_names]
    categorical = [categorical_comparison(name, rows) for name in contract["categoricalAssessment"]["fields"]]
    classifier = classifier_auc(rows, feature_names)

    feature_smd = [row["absoluteStandardizedMeanDifference"] for row in features]
    feature_ks = [row["ksDistance"] for row in features]
    gates = [
        gate("overallCoveredShare", len(covered_rows) / len(rows), ">=", float(contract["minimumOverallCoveredShare"])),
        gate("minimumChronologicalBlockCoveredShare", min(block_shares), ">=", float(contract["chronologicalAssessment"]["minimumCoveredSharePerBlock"])),
        gate("chronologicalBlockCoveredShareRange", max(block_shares) - min(block_shares), "<=", float(contract["chronologicalAssessment"]["maximumCoveredShareRange"])),
        gate("maximumCategoricalTotalVariation", max(row["totalVariation"] for row in categorical), "<=", float(contract["categoricalAssessment"]["maximumTotalVariation"])),
        gate("maximumCategoricalLevelShareDifference", max(row["maximumSingleLevelShareDifference"] for row in categorical), "<=", float(contract["categoricalAssessment"]["maximumSingleLevelShareDifference"])),
        gate("maximumStructuralAbsoluteSmd", max(row["absoluteStandardizedMeanDifference"] for row in structural), "<=", float(contract["structuralNumericAssessment"]["maximumAbsoluteStandardizedMeanDifference"])),
        gate("maximumStructuralKs", max(row["ksDistance"] for row in structural), "<=", float(contract["structuralNumericAssessment"]["maximumKsDistance"])),
        gate("p90FeatureAbsoluteSmd", float(percentile(feature_smd, 0.90)), "<=", float(contract["causalFeatureAssessment"]["maximumP90AbsoluteStandardizedMeanDifference"])),
        gate("maximumFeatureAbsoluteSmd", max(feature_smd), "<=", float(contract["causalFeatureAssessment"]["maximumAbsoluteStandardizedMeanDifference"])),
        gate("p90FeatureKs", float(percentile(feature_ks, 0.90)), "<=", float(contract["causalFeatureAssessment"]["maximumP90KsDistance"])),
        gate("maximumFeatureKs", max(feature_ks), "<=", float(contract["causalFeatureAssessment"]["maximumKsDistance"])),
        gate("coverageClassifierDirectionAgnosticAuc", classifier["directionAgnosticAssessmentAuc"], "<=", float(contract["coverageClassifierAssessment"]["maximumDirectionAgnosticAssessmentAuc"])),
    ]
    authorized = all(row["passed"] for row in gates)
    report = {
        "schema": SCHEMA,
        "population": {"developmentN": len(rows), "coveredN": len(covered_rows), "missingN": len(missing_rows), "coveredShare": len(covered_rows) / len(rows)},
        "chronological": chronological,
        "categorical": categorical,
        "structuralNumeric": structural,
        "causalFeatures": sorted(features, key=lambda row: row["absoluteStandardizedMeanDifference"], reverse=True),
        "coverageClassifier": classifier,
        "gates": gates,
        "failedGates": [row["name"] for row in gates if not row["passed"]],
        "crossSectionalPolicyEligibleForSealedValidation": authorized,
        "integrity": {"developmentOrdinals": [1, 350], "validationRowsRead": 0, "outcomesRead": 0, "submissions": 0},
        "sourceHashes": {"contract": sha256_file(args.contract), "entryTape": sha256_file(args.entry_tape), "choiceSet": sha256_file(args.choice_set), "omissions": sha256_file(args.omissions), "script": sha256_file(pathlib.Path(__file__))},
        "verdict": "2CQ_CHOICE_SET_REPRESENTATIVENESS_PASS" if authorized else "2CQ_CHOICE_SET_REPRESENTATIVENESS_BIAS_BLOCKER",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with gzip.open(args.output / "entry-membership.jsonl.gz", "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--entry-tape", type=pathlib.Path, required=True)
    parser.add_argument("--choice-set", type=pathlib.Path, required=True)
    parser.add_argument("--omissions", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
