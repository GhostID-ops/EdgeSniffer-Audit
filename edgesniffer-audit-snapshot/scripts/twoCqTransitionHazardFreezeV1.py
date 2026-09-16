#!/usr/bin/env python3
"""Freeze one outcome-blind 2CQ transition-hazard imitation policy on N350."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import pathlib
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqEventDrivenFingerprintFreezeV1 as event_v1


SCHEMA = "2CQ_TRANSITION_HAZARD_FREEZE_V1"
TRAIN_RECALL_TARGET = 0.90
MIN_INTERNAL_ASSESSMENT_RECALL_PCT = 75.0
DELTA_BASES = (
    "net_flow_sol_250ms",
    "gross_buy_sol_250ms",
    "buy_count_250ms",
    "sell_count_250ms",
    "log_price_change_250ms",
    "net_acceleration_1s",
    "buy_count_acceleration_1s",
    "consecutive_buys",
    "top_buyer_share_1000ms",
    "curve_progress",
)
TRANSITION_FEATURES = (
    "transition_log_interarrival_ms",
    "transition_is_first_observed_state",
    "transition_log_event_number",
    "transition_new_price_high",
    "transition_new_flow250_high",
    "transition_new_buy_count250_high",
) + tuple(f"delta_{name}" for name in DELTA_BASES)


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def add_transition_features(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for example in examples:
        states = sorted(
            example["states"], key=lambda row: (int(row["receivedNs"]), str(row["signature"]))
        )
        enriched = []
        prior = None
        price_high = flow_high = count_high = -math.inf
        for index, state in enumerate(states):
            features = {name: float(value) for name, value in state["features"].items()}
            prior_features = prior["features"] if prior else features
            prior_received = int(prior["receivedNs"]) if prior else int(state["receivedNs"])
            features.update({
                "transition_log_interarrival_ms": math.log1p(
                    max(0.0, (int(state["receivedNs"]) - prior_received) / 1_000_000)
                ),
                "transition_is_first_observed_state": 1.0 if prior is None else 0.0,
                "transition_log_event_number": math.log1p(index + 1),
                "transition_new_price_high": 1.0 if features["distance_from_launch_log_price"] > price_high else 0.0,
                "transition_new_flow250_high": 1.0 if features["net_flow_sol_250ms"] > flow_high else 0.0,
                "transition_new_buy_count250_high": 1.0 if features["buy_count_250ms"] > count_high else 0.0,
            })
            for name in DELTA_BASES:
                features[f"delta_{name}"] = features[name] - float(prior_features[name])
            price_high = max(price_high, features["distance_from_launch_log_price"])
            flow_high = max(flow_high, features["net_flow_sol_250ms"])
            count_high = max(count_high, features["buy_count_250ms"])
            enriched.append(state | {"features": features})
            prior = state
        result.append(example | {"states": enriched, "controlN": len(enriched) - 1})
    return result


def fit(
    examples: list[dict[str, Any]], features: tuple[str, ...],
) -> tuple[dict[str, Any], Any, list[dict[str, Any]], list[dict[str, Any]]]:
    train, assessment, dropped = decision.internal_split(examples)
    fitted_train = [decision.fit_control_sample(row) for row in train]
    report, fitted = timing.evaluate_model(fitted_train, assessment, features)
    report["trainEpisodeN"] = len(train)
    report["assessmentEpisodeN"] = len(assessment)
    report["crossBoundaryRepeatDropped"] = dropped
    return report, fitted, train, assessment


def score(features: dict[str, float], names: tuple[str, ...], fitted: Any) -> float:
    means, scales, model = fitted
    return timing.predict(
        timing.transform([[float(features[name]) for name in names]], means, scales), model
    )[0]


def rising_crossing_replay(
    train: list[dict[str, Any]], assessment: list[dict[str, Any]],
    tape: list[dict[str, Any]], names: tuple[str, ...], fitted: Any,
) -> tuple[dict[str, Any], float]:
    positives = [
        score(next(state for state in row["states"] if state["label"])["features"], names, fitted)
        for row in train
    ]
    threshold = float(event_v1.percentile(positives, 1.0 - TRAIN_RECALL_TARGET))
    teacher_ns = {int(row["eligibleOrdinal"]): int(row["entryReceivedAtUnixNs"]) for row in tape}
    details = []
    for example in assessment:
        states = sorted(
            example["states"], key=lambda row: (int(row["receivedNs"]), str(row["signature"]))
        )
        scored = [(state, score(state["features"], names, fitted)) for state in states]
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
            "risingCrossingN": len(rising),
            "firstRisingIsTeacherState": bool(first and first[0]["label"]),
            "firstRisingToTeacherMs": (
                (teacher_ns[ordinal] - int(first[0]["receivedNs"])) / 1_000_000 if first else None
            ),
            "teacherStateToTeacherMs": (
                teacher_ns[ordinal] - int(selected[0]["receivedNs"])
            ) / 1_000_000,
        })
    leads = [row["firstRisingToTeacherMs"] for row in details if row["firstRisingToTeacherMs"] is not None]
    n = len(details)
    return {
        "assessmentN": n,
        "threshold": threshold,
        "thresholdPolicy": "P10 of internal-training teacher-state scores; fixed 90% teacher recall target",
        "teacherStateRecallPct": 100 * sum(row["teacherStatePass"] for row in details) / n,
        "anyRisingCrossingPct": 100 * len(leads) / n,
        "firstRisingIsTeacherStatePct": 100 * sum(row["firstRisingIsTeacherState"] for row in details) / n,
        "firstRisingLeadMs": {
            "p50": event_v1.percentile(leads, 0.50),
            "p75": event_v1.percentile(leads, 0.75),
            "p90": event_v1.percentile(leads, 0.90),
        },
        "rows": details,
    }, threshold


def choose_family(rows: dict[str, dict[str, Any]]) -> str:
    eligible = [
        name for name, row in rows.items()
        if row["trigger"]["teacherStateRecallPct"] >= MIN_INTERNAL_ASSESSMENT_RECALL_PCT
    ]
    if not eligible:
        raise RuntimeError("NO_TIMING_FAMILY_MEETS_PREDECLARED_RECALL_FLOOR")
    # Behavioral imitation priority: trigger the final teacher state, then reduce
    # premature lead, then use within-mint rank as the final deterministic tie-break.
    return max(eligible, key=lambda name: (
        rows[name]["trigger"]["firstRisingIsTeacherStatePct"],
        -(rows[name]["trigger"]["firstRisingLeadMs"]["p75"] or math.inf),
        rows[name]["model"]["validation"]["withinMint"]["actualRankedFirstPct"],
    ))


def fit_full(examples: list[dict[str, Any]], names: tuple[str, ...]) -> Any:
    capped = [decision.fit_control_sample(row) for row in examples]
    x, y, weights, _ = timing.flatten(capped, names)
    means, scales = timing.standardizer(x)
    model = timing.fit_logistic(timing.transform(x, means, scales), y, weights)
    return means, scales, model


def freeze_model(names: tuple[str, ...], fitted: Any) -> dict[str, Any]:
    return event_v1.frozen_model(names, fitted)


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    temporal = add_transition_features(
        read_jsonl_gz(args.parent / "same-mint-temporal-examples.jsonl.gz")
    )
    temporal = [row for row in temporal if int(row.get("controlN") or 0) > 0]
    tape = read_jsonl_gz(args.parent / "entry-decision-tape.jsonl.gz")
    cross = read_jsonl_gz(args.choice_repair / "choice-set-launched_120s.jsonl.gz")
    if len(tape) != 350 or any(int(row["eligibleOrdinal"]) > 350 for row in tape):
        raise RuntimeError("DEVELOPMENT_BOUNDARY_VIOLATION")
    if any(int(row.get("outcomesRead") or 0) != 0 for row in tape):
        raise RuntimeError("OUTCOME_TAINT")

    families = {
        "STATIC_FULL_CAUSAL_BASELINE": tuple(timing.FULL_FEATURES),
        "TRANSITION_ONLY": TRANSITION_FEATURES,
        "BASE_PLUS_TRANSITION": tuple(timing.FULL_FEATURES) + TRANSITION_FEATURES,
    }
    comparisons: dict[str, dict[str, Any]] = {}
    fitted_by_family = {}
    for name, names in families.items():
        model_report, fitted, train, assessment = fit(temporal, names)
        trigger, _ = rising_crossing_replay(train, assessment, tape, names, fitted)
        comparisons[name] = {"model": model_report, "trigger": trigger}
        fitted_by_family[name] = fitted
    selected_family = choose_family(comparisons)
    selected_names = families[selected_family]

    timing_full = fit_full(temporal, selected_names)
    full_teacher_scores = [
        score(next(state for state in row["states"] if state["label"])["features"], selected_names, timing_full)
        for row in temporal
    ]
    timing_threshold = float(event_v1.percentile(full_teacher_scores, 1.0 - TRAIN_RECALL_TARGET))
    cross_full = fit_full(cross, tuple(timing.FULL_FEATURES))

    policy = {
        "schema": SCHEMA + "_PRIMARY_POLICY",
        "teacherImitationOnly": True,
        "outcomeOptimized": False,
        "candidateUniverse": "canonical Pump launches age<=120s, active curve, complete causal history",
        "triggerClock": "after every successful external Pump transaction",
        "timingFamily": selected_family,
        "timingModel": freeze_model(selected_names, timing_full),
        "timingThreshold": timing_threshold,
        "timingThresholdPolicy": "P10 of all N350 development teacher-state scores",
        "crossMintModel": freeze_model(tuple(timing.FULL_FEATURES), cross_full),
        "crossMintRule": "at a fresh rising timing-threshold crossing, mint must rank first among complete LAUNCHED_120S candidates",
        "dedupeRule": "first accepted rising crossing per mint lifecycle",
        "maximumOpen": 1,
        "validationRule": "evaluate exactly once on sealed eligible ordinals 351-500 after N500",
        "profitabilityClaim": False,
    }
    decision.atomic_json(args.output / "primary-policy.json", policy)

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": decision.now_utc(),
        "developmentTimingN": len(temporal),
        "developmentChoiceSetN": len(cross),
        "families": comparisons,
        "predeclaredSelectionRule": {
            "minimumTeacherStateRecallPct": MIN_INTERNAL_ASSESSMENT_RECALL_PCT,
            "order": [
                "maximize first rising crossing equals final teacher state",
                "minimize P75 first-rising lead",
                "maximize within-mint Top-1",
            ],
        },
        "selectedFamily": selected_family,
        "finalTimingThreshold": timing_threshold,
        "integrity": {
            "developmentOrdinals": [1, 350],
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
            "choiceRepairSha256": decision.sha256_file(args.choice_repair / "report.json"),
            "policySha256": decision.sha256_file(args.output / "primary-policy.json"),
        },
        "nextGate": "ONE_TIME_SEALED_VALIDATION_AT_N500",
        "verdict": "2CQ_PRIMARY_TEACHER_IMITATION_POLICY_FROZEN_DEVELOPMENT_ONLY",
    }
    decision.atomic_json(args.output / "report.json", report)
    decision.atomic_json(args.output / "contract.json", {
        "schema": SCHEMA + "_CONTRACT",
        "developmentOrdinals": [1, 350],
        "sealedValidationOrdinals": [351, 500],
        "modelFamilies": {name: list(names) for name, names in families.items()},
        "selectionRule": report["predeclaredSelectionRule"],
        "outcomesPermitted": False,
        "validationPermittedBeforeN500": False,
        "submissionsPermitted": False,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=pathlib.Path, required=True)
    parser.add_argument("--choice-repair", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
