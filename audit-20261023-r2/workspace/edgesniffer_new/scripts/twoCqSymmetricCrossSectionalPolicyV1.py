#!/usr/bin/env python3
"""Freeze and validate one cross-sectional 2CQ teacher-imitation policy.

The development and validation commands are intentionally separate. Validation
cannot run until an immutable development policy exists, and neither command
accepts economic outcomes as input.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqEventDrivenFingerprintFreezeV1 as event_v1


SCHEMA = "2CQ_SYMMETRIC_CROSS_SECTIONAL_POLICY_V1"
DEVELOPMENT_N = 350
VALIDATION_N = 150
FEATURES = tuple(timing.FULL_FEATURES)
FORBIDDEN_KEY_PARTS = ("pnl", "roi", "profit", "future_return", "exit_outcome")
COHORT_UNIVERSES = {
    "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V1_CONTRACT": (
        "PROSPECTIVE_SYMMETRIC_LAUNCHED_120S_V1"
    ),
    "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V2_CONTRACT": (
        "PROSPECTIVE_SYMMETRIC_LAUNCHED_120S_V2"
    ),
}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_examples(path: pathlib.Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def assert_outcome_blind(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise RuntimeError(f"OUTCOME_FIELD_FORBIDDEN:{location}.{key}")
            if lowered in {"outcomesread", "outcomes_read"}:
                if int(item or 0) != 0:
                    raise RuntimeError(f"OUTCOME_TAINT:{location}.{key}")
                continue
            assert_outcome_blind(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_outcome_blind(item, f"{location}[{index}]")


def validate_examples(rows: list[dict[str, Any]], first: int, last: int) -> None:
    expected = list(range(first, last + 1))
    observed = [int(row.get("eligibleOrdinal") or 0) for row in rows]
    if observed != expected:
        raise RuntimeError(f"ORDINAL_BOUNDARY_INVALID:{first}-{last}")
    for row in rows:
        states = row.get("states") or []
        if sum(bool(state.get("label")) for state in states) != 1:
            raise RuntimeError(f"CHOICE_SET_LABEL_INVALID:{row.get('eligibleOrdinal')}")
        if len(states) < 2 or int(row.get("controlN") or 0) < 1:
            raise RuntimeError(f"CHOICE_SET_HAS_NO_CONTROL:{row.get('eligibleOrdinal')}")
        for state in states:
            features = state.get("features") or {}
            missing = [name for name in FEATURES if name not in features]
            if missing:
                raise RuntimeError(
                    f"FEATURES_MISSING:{row.get('eligibleOrdinal')}:{','.join(missing)}"
                )
    assert_outcome_blind(rows)


def fitted_model(rows: list[dict[str, Any]]) -> tuple[Any, dict[str, Any]]:
    capped = [decision.fit_control_sample(row) for row in rows]
    x, y, weights, _ = timing.flatten(capped, FEATURES)
    means, scales = timing.standardizer(x)
    model = timing.fit_logistic(timing.transform(x, means, scales), y, weights)
    metrics = timing.ranking_metrics(rows, FEATURES, means, scales, model)
    return (means, scales, model), metrics


def fit(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise RuntimeError("DEVELOPMENT_POLICY_ALREADY_FROZEN")
    rows = read_examples(args.development)
    if len(rows) != DEVELOPMENT_N:
        raise RuntimeError(f"DEVELOPMENT_NOT_N350:{len(rows)}")
    validate_examples(rows, 1, DEVELOPMENT_N)

    cohort = json.loads(args.cohort_contract.read_text(encoding="utf-8"))
    cohort_schema = str(cohort.get("schema") or "")
    if cohort_schema not in COHORT_UNIVERSES:
        raise RuntimeError("SYMMETRIC_COHORT_CONTRACT_INVALID")
    if cohort.get("integrity", {}).get("modelFittingBeforeDevelopmentN350") is not False:
        raise RuntimeError("COHORT_FIT_BOUNDARY_INVALID")

    fitted, development_metrics = fitted_model(rows)
    model = event_v1.frozen_model(FEATURES, fitted)
    policy = {
        "schema": SCHEMA,
        "frozenAtUtc": decision.now_utc(),
        "teacherImitationOnly": True,
        "outcomeOptimized": False,
        "policyCountFit": 1,
        "modelFamily": "FULL_CAUSAL_PREDECLARED_NO_TOURNAMENT",
        "candidateUniverse": COHORT_UNIVERSES[cohort_schema],
        "cohortContractSchema": cohort_schema,
        "crossMintModel": model,
        "crossMintRule": "rank every complete contemporaneous candidate; select unique top score",
        "tieRule": "reject exact top-score tie",
        "developmentOrdinals": [1, DEVELOPMENT_N],
        "sealedValidationOrdinals": [DEVELOPMENT_N + 1, DEVELOPMENT_N + VALIDATION_N],
        "developmentChoiceSetsSha256": sha256_file(args.development),
        "cohortContractSha256": sha256_file(args.cohort_contract),
        "developmentDescriptiveMetrics": development_metrics,
        "validationRefitPermitted": False,
        "validationRunsPermitted": 1,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output, policy)
    atomic_json(args.output.with_name("policy-freeze.json"), {
        "schema": SCHEMA + "_FREEZE",
        "policySha256": sha256_file(args.output),
        "developmentChoiceSetsSha256": sha256_file(args.development),
        "modelFamiliesFit": ["FULL_CAUSAL_PREDECLARED_NO_TOURNAMENT"],
        "validationRowsRead": 0,
        "outcomesRead": 0,
    })
    return policy


def unpack_fitted(model: dict[str, Any]) -> tuple[list[float], list[float], tuple[float, list[float]]]:
    names = tuple(model["featureNames"])
    if names != FEATURES:
        raise RuntimeError("FROZEN_FEATURE_ORDER_CHANGED")
    means = [float(model["means"][name]) for name in names]
    scales = [float(model["scales"][name]) for name in names]
    fitted = (
        float(model["intercept"]),
        [float(model["coefficients"][name]) for name in names],
    )
    return means, scales, fitted


def normalized_timing_policy(path: pathlib.Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = str(payload.get("schema") or "")
    if schema == "2CQ_EVENT_LEVEL_TIMING_HAZARD_V1_TIMING_POLICY_CANDIDATE":
        if payload.get("teacherImitationOnly") is not True:
            raise RuntimeError("TIMING_POLICY_NOT_TEACHER_IMITATION")
        if payload.get("sealedValidationRequired") is not True:
            raise RuntimeError("TIMING_POLICY_VALIDATION_BOUNDARY_INVALID")
        if payload.get("profitabilityClaim") is not False:
            raise RuntimeError("TIMING_POLICY_PROFITABILITY_TAINT")
        return {
            "timingFamily": "PROXIMAL_EVENT_LEVEL_HAZARD_V1",
            "timingModel": payload["model"],
            "timingThreshold": float(payload["threshold"]),
            "triggerClock": payload["triggerClock"],
            "dedupeRule": payload["dedupeRule"],
            "canonicalStateOrderRequired": True,
            "sourceSchema": schema,
        }
    if payload.get("timingFamily") == "BASE_PLUS_TRANSITION":
        return {
            "timingFamily": payload["timingFamily"],
            "timingModel": payload["timingModel"],
            "timingThreshold": float(payload["timingThreshold"]),
            "triggerClock": payload.get("triggerClock") or "after every successful external Pump transaction",
            "dedupeRule": payload.get("dedupeRule") or "first accepted rising crossing per mint lifecycle",
            "canonicalStateOrderRequired": False,
            "sourceSchema": str(payload.get("schema") or "LEGACY_TRANSITION_POLICY"),
        }
    raise RuntimeError("PRESERVED_TRANSITION_POLICY_INVALID")


def validate(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.output / "sealed-validation-report.json"
    joint_path = args.output / "joint-imitation-policy.json"
    if report_path.exists() or joint_path.exists():
        raise RuntimeError("SEALED_VALIDATION_ALREADY_CONSUMED")

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if policy.get("schema") != SCHEMA or int(policy.get("policyCountFit") or 0) != 1:
        raise RuntimeError("FROZEN_POLICY_INVALID")
    rows = read_examples(args.validation)
    if len(rows) != VALIDATION_N:
        raise RuntimeError(f"VALIDATION_NOT_N150:{len(rows)}")
    validate_examples(rows, DEVELOPMENT_N + 1, DEVELOPMENT_N + VALIDATION_N)

    means, scales, fitted = unpack_fitted(policy["crossMintModel"])
    metrics = timing.ranking_metrics(rows, FEATURES, means, scales, fitted)
    transition = normalized_timing_policy(args.transition_policy)

    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": SCHEMA + "_SEALED_VALIDATION",
        "validatedAtUtc": decision.now_utc(),
        "validationOrdinals": [351, 500],
        "validationN": VALIDATION_N,
        "crossSectionalMetrics": metrics,
        "policySha256": sha256_file(args.policy),
        "validationChoiceSetsSha256": sha256_file(args.validation),
        "modelRefitOnValidation": False,
        "modelsTestedOnValidation": 1,
        "outcomesRead": 0,
        "submissions": 0,
        "verdict": "2CQ_SYMMETRIC_CROSS_SECTIONAL_ONE_TIME_VALIDATION_COMPLETE",
    }
    atomic_json(report_path, report)

    joint = {
        "schema": "2CQ_SYMMETRIC_JOINT_IMITATION_POLICY_V1",
        "assembledAtUtc": decision.now_utc(),
        "candidateUniverse": policy["candidateUniverse"],
        "timingFamily": transition["timingFamily"],
        "timingModel": transition["timingModel"],
        "timingThreshold": transition["timingThreshold"],
        "timingScoreDomain": "LOGISTIC_PROBABILITY",
        "timingTriggerClock": transition["triggerClock"],
        "timingDedupeRule": transition["dedupeRule"],
        "canonicalStateOrderRequired": transition["canonicalStateOrderRequired"],
        "crossMintModel": policy["crossMintModel"],
        "crossMintScoreDomain": "STANDARDIZED_LOGISTIC_LOGIT_FOR_RANKING",
        "decisionRule": (
            "after a canonically ordered successful external Pump transaction, require a fresh "
            "below-to-at-or-above timing-threshold crossing for that mint; then accept only when "
            "the same mint is the unique top-ranked member of the complete symmetric LAUNCHED_120S risk set"
        ),
        "failClosedRules": [
            "missing prior timing state",
            "missing or non-finite timing feature",
            "missing or non-finite cross-sectional feature",
            "changed mint absent from complete symmetric risk set",
            "exact top-score tie",
            "non-canonical event order",
        ],
        "policySha256": sha256_file(args.policy),
        "validationReportSha256": sha256_file(report_path),
        "timingSourceSchema": transition["sourceSchema"],
        "timingSourceSha256": sha256_file(args.transition_policy),
        "componentValidation": {
            "crossMintWhat": "ONE_TIME_SEALED_N150_COMPLETE",
            "eventTimingWhen": "DEVELOPMENT_ONLY_AWAITING_PROSPECTIVE_NO_SUBMIT",
            "jointPolicy": "AWAITING_PROSPECTIVE_NO_SUBMIT",
        },
        "prospectiveImitationRequiredNext": True,
        "exactProfitabilityPermittedNow": False,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(joint_path, joint)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    fit_parser = subparsers.add_parser("fit")
    fit_parser.add_argument("--development", type=pathlib.Path, required=True)
    fit_parser.add_argument("--cohort-contract", type=pathlib.Path, required=True)
    fit_parser.add_argument("--output", type=pathlib.Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--validation", type=pathlib.Path, required=True)
    validate_parser.add_argument("--policy", type=pathlib.Path, required=True)
    validate_parser.add_argument("--transition-policy", type=pathlib.Path, required=True)
    validate_parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = fit(args) if args.command == "fit" else validate(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
