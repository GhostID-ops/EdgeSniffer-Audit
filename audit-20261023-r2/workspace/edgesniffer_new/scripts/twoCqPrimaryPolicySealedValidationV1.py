#!/usr/bin/env python3
"""One-time outcome-blind teacher-imitation validation on sealed V3 ordinals 351-500."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqChoiceSetCoverageRepairV1 as coverage
import twoCqDecisionChoiceSetV1 as choices
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqEventDrivenFingerprintFreezeV1 as event_v1
import twoCqTransitionHazardFreezeV1 as hazard


SCHEMA = "2CQ_PRIMARY_POLICY_SEALED_VALIDATION_V1"


def fitted_model(payload: dict[str, Any]) -> tuple[tuple[str, ...], Any]:
    names = tuple(payload["featureNames"])
    means = [float(payload["means"][name]) for name in names]
    scales = [float(payload["scales"][name]) for name in names]
    model = (
        float(payload["intercept"]),
        [float(payload["coefficients"][name]) for name in names],
    )
    return names, (means, scales, model)


def fixed_trigger_replay(
    examples: list[dict[str, Any]], tape: list[dict[str, Any]], names: tuple[str, ...],
    fitted: Any, threshold: float,
) -> dict[str, Any]:
    teacher_ns = {int(row["eligibleOrdinal"]): int(row["entryReceivedAtUnixNs"]) for row in tape}
    details = []
    for example in examples:
        states = sorted(
            example["states"], key=lambda row: (int(row["receivedNs"]), str(row["signature"]))
        )
        scored = [(state, hazard.score(state["features"], names, fitted)) for state in states]
        rising = []
        prior_score = None
        for state, value in scored:
            if value >= threshold and (prior_score is None or prior_score < threshold):
                rising.append((state, value))
            prior_score = value
        selected = next(item for item in scored if item[0]["label"])
        first = rising[0] if rising else None
        ordinal = int(example["eligibleOrdinal"])
        details.append({
            "eligibleOrdinal": ordinal,
            "mint": example["mint"],
            "teacherStatePass": selected[1] >= threshold,
            "firstRisingIsTeacherState": bool(first and first[0]["label"]),
            "firstRisingToTeacherMs": (
                (teacher_ns[ordinal] - int(first[0]["receivedNs"])) / 1_000_000 if first else None
            ),
        })
    leads = [row["firstRisingToTeacherMs"] for row in details if row["firstRisingToTeacherMs"] is not None]
    n = len(details)
    return {
        "n": n,
        "teacherStateRecallPct": 100 * sum(row["teacherStatePass"] for row in details) / n if n else None,
        "anyRisingCrossingPct": 100 * len(leads) / n if n else None,
        "firstRisingIsTeacherStatePct": 100 * sum(row["firstRisingIsTeacherState"] for row in details) / n if n else None,
        "firstRisingLeadMs": {
            "p50": event_v1.percentile(leads, 0.50),
            "p75": event_v1.percentile(leads, 0.75),
            "p90": event_v1.percentile(leads, 0.90),
        },
        "rows": details,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    status = json.loads(args.census_status.read_text(encoding="utf-8"))
    if int(status.get("currentEligibleN") or 0) < 500:
        raise RuntimeError("N500_NOT_READY")
    if status.get("validationSealed") is not True or int(status.get("outcomesRead") or 0) != 0:
        raise RuntimeError("SEALED_VALIDATION_INTEGRITY_BLOCKER")
    validation = decision.read_jsonl(args.validation)
    if len(validation) != 150:
        raise RuntimeError(f"SEALED_VALIDATION_NOT_N150:{len(validation)}")
    if [int(row["eligibleOrdinal"]) for row in validation] != list(range(351, 501)):
        raise RuntimeError("SEALED_VALIDATION_ORDINALS_NOT_351_TO_500")
    if any(row.get("split") != "VALIDATION_SEALED" for row in validation):
        raise RuntimeError("VALIDATION_SPLIT_IDENTITY_INVALID")
    if any(int(row.get("outcomesRead") or 0) != 0 for row in validation):
        raise RuntimeError("VALIDATION_OUTCOME_TAINT")

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if policy.get("sealedValidationAuthorized") is not True:
        raise RuntimeError("PRIMARY_POLICY_NOT_AUTHORIZED_FOR_SEALED_VALIDATION")
    timing_names, timing_fitted = fitted_model(policy["timingModel"])
    cross_names, cross_fitted = fitted_model(policy["crossMintModel"])

    events, create_ns, entries = decision.selected_mint_events(
        validation, args.rpc_cache, args.compact_arrivals
    )
    temporal, tape = decision.build_temporal_examples(validation, events, create_ns, entries)
    temporal = hazard.add_transition_features(temporal)
    timing_usable = [row for row in temporal if int(row.get("controlN") or 0) > 0]

    boundaries = sorted(int(row["decisionStateReceivedAtUnixNs"]) for row in tape)
    hours = choices.receive_hours(
        boundaries[0] - decision.MAX_AGE_MS * 1_000_000, boundaries[-1]
    )
    evidence_paths, globally_inexact = choices.finalized_evidence_paths(args.exact_root, hours)
    full_events, full_creates, evidence = coverage.scan_repaired_universe(
        evidence_paths, args.compact_arrivals, boundaries, validation
    )
    cross, omissions = coverage.build_repaired_examples(tape, full_events, full_creates)

    timing_ranking = timing.ranking_metrics(
        timing_usable, timing_names, timing_fitted[0], timing_fitted[1], timing_fitted[2]
    )
    cross_ranking = timing.ranking_metrics(
        cross, cross_names, cross_fitted[0], cross_fitted[1], cross_fitted[2]
    )
    trigger = fixed_trigger_replay(
        timing_usable, tape, timing_names, timing_fitted, float(policy["timingThreshold"])
    )
    trigger_by_ordinal = {int(row["eligibleOrdinal"]): row for row in trigger["rows"]}
    cross_top1 = {}
    for example in cross:
        values = [
            hazard.score(state["features"], cross_names, cross_fitted)
            for state in example["states"]
        ]
        selected = next(index for index, state in enumerate(example["states"]) if state["label"])
        cross_top1[int(example["eligibleOrdinal"])] = (
            values[selected] > max(
                (value for index, value in enumerate(values) if index != selected),
                default=-float("inf"),
            )
        )
    joint_ordinals = sorted(set(trigger_by_ordinal) & set(cross_top1))
    joint_accepted = sum(
        trigger_by_ordinal[ordinal]["teacherStatePass"] and cross_top1[ordinal]
        for ordinal in joint_ordinals
    )

    args.output.mkdir(parents=True, exist_ok=True)
    decision.write_jsonl_gz(args.output / "same-mint-temporal-validation.jsonl.gz", temporal)
    decision.write_jsonl_gz(args.output / "choice-set-launched_120s-validation.jsonl.gz", cross)
    decision.write_jsonl_gz(args.output / "choice-set-omissions-validation.jsonl.gz", omissions)
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": decision.now_utc(),
        "sealedValidationN": 150,
        "timingIdentifiableN": len(timing_usable),
        "choiceSetComparableN": len(cross),
        "timing": {"ranking": timing_ranking, "trigger": trigger},
        "crossMint": {"ranking": cross_ranking},
        "jointPolicy": {
            "comparableN": len(joint_ordinals),
            "teacherDecisionAcceptedN": joint_accepted,
            "teacherDecisionRecallPct": 100 * joint_accepted / len(joint_ordinals) if joint_ordinals else None,
        },
        "omissionN": len(omissions),
        "evidence": evidence,
        "globalReconciliationExceptions": globally_inexact,
        "integrity": {
            "policySha256": decision.sha256_file(args.policy),
            "validationSha256": decision.sha256_file(args.validation),
            "validationOrdinals": [351, 500],
            "modelRefitOnValidation": False,
            "thresholdChanged": False,
            "outcomesRead": 0,
            "submissions": 0,
        },
        "verdict": "2CQ_PRIMARY_POLICY_ONE_TIME_SEALED_BEHAVIORAL_VALIDATION_COMPLETE",
    }
    decision.atomic_json(args.output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census-status", type=pathlib.Path, required=True)
    parser.add_argument("--validation", type=pathlib.Path, required=True)
    parser.add_argument("--policy", type=pathlib.Path, required=True)
    parser.add_argument("--rpc-cache", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
