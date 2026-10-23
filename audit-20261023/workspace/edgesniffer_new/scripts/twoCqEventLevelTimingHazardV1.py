#!/usr/bin/env python3
"""Fit one outcome-blind event-level hazard model for 2CQ entry timing."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import pathlib
from typing import Any

import numpy as np

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqEventDrivenFingerprintFreezeV1 as event_v1
import twoCqTransitionHazardFreezeV1 as transition_v1


SCHEMA = "2CQ_EVENT_LEVEL_TIMING_HAZARD_V1"
TRAIN_RECALL_FLOOR_PCT = 75.0
ASSESSMENT_RECALL_FLOOR_PCT = 60.0
MIN_EXACT_FIRST_IMPROVEMENT_PCT = 5.0
PROXIMITY_POWER = 1.0
FEATURES = tuple(timing.FULL_FEATURES) + tuple(transition_v1.TRANSITION_FEATURES)
WINDOWS_MS = (50, 100, 250, 500, 1_000)


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def enrich_canonical_transitions(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add transition features without reordering canonical source states."""
    enriched_examples = []
    for example in examples:
        states = list(example["states"])
        positive_indexes = [index for index, state in enumerate(states) if state["label"]]
        if positive_indexes != [len(states) - 1]:
            raise RuntimeError("FINAL_TEACHER_BOUNDARY_NOT_LAST_CANONICAL_STATE")
        enriched = []
        prior_features: dict[str, float] | None = None
        prior_received_ns: int | None = None
        price_high = flow_high = count_high = -math.inf
        for index, state in enumerate(states):
            features = {name: float(value) for name, value in state["features"].items()}
            previous = prior_features or features
            received_ns = int(state["receivedNs"])
            interarrival_ms = (
                max(0.0, (received_ns - prior_received_ns) / 1_000_000)
                if prior_received_ns is not None else 0.0
            )
            features.update({
                "transition_log_interarrival_ms": math.log1p(interarrival_ms),
                "transition_is_first_observed_state": 1.0 if index == 0 else 0.0,
                "transition_log_event_number": math.log1p(index + 1),
                "transition_new_price_high": (
                    1.0 if features["distance_from_launch_log_price"] > price_high else 0.0
                ),
                "transition_new_flow250_high": (
                    1.0 if features["net_flow_sol_250ms"] > flow_high else 0.0
                ),
                "transition_new_buy_count250_high": (
                    1.0 if features["buy_count_250ms"] > count_high else 0.0
                ),
            })
            for name in transition_v1.DELTA_BASES:
                features[f"delta_{name}"] = features[name] - previous[name]
            price_high = max(price_high, features["distance_from_launch_log_price"])
            flow_high = max(flow_high, features["net_flow_sol_250ms"])
            count_high = max(count_high, features["buy_count_250ms"])
            enriched.append(state | {"features": features, "canonicalStateIndex": index})
            prior_features = features
            prior_received_ns = received_ns
        enriched_examples.append(example | {"states": enriched, "controlN": len(enriched) - 1})
    return enriched_examples


def flatten_proximal(
    examples: list[dict[str, Any]], feature_names: tuple[str, ...],
) -> tuple[list[list[float]], list[int], list[float]]:
    x: list[list[float]] = []
    y: list[int] = []
    weights: list[float] = []
    for example in examples:
        states = example["states"]
        positive_index = len(states) - 1
        raw_negative = [
            1.0 / ((positive_index - index) ** PROXIMITY_POWER)
            for index in range(positive_index)
        ]
        negative_total = sum(raw_negative) or 1.0
        for index, state in enumerate(states):
            x.append([float(state["features"][name]) for name in feature_names])
            label = int(bool(state["label"]))
            y.append(label)
            weights.append(
                0.5 if label else 0.5 * raw_negative[index] / negative_total
            )
    return x, y, weights


def fit_model(
    examples: list[dict[str, Any]], proximal: bool,
) -> tuple[list[float], list[float], tuple[float, list[float]]]:
    if proximal:
        x, y, weights = flatten_proximal(examples, FEATURES)
    else:
        x, y, weights, _ = timing.flatten(examples, FEATURES)
    means, scales = timing.standardizer(x)
    model = fit_logistic_vectorized(timing.transform(x, means, scales), y, weights)
    return means, scales, model


def fit_logistic_vectorized(
    x: list[list[float]], y: list[int], weights: list[float],
) -> tuple[float, list[float]]:
    """Vectorized equivalent of the repository's deterministic logistic fitter."""
    matrix = np.asarray(x, dtype=np.float64)
    labels = np.asarray(y, dtype=np.float64)
    sample_weights = np.asarray(weights, dtype=np.float64)
    coefficients = np.zeros(matrix.shape[1], dtype=np.float64)
    intercept = 0.0
    total_weight = float(sample_weights.sum())
    for iteration in range(800):
        logits = np.clip(intercept + matrix @ coefficients, -40.0, 40.0)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        errors = (probabilities - labels) * sample_weights
        rate = 0.08 / (1.0 + iteration / 300.0)
        intercept -= rate * float(errors.sum()) / total_weight
        coefficients -= rate * (
            (matrix.T @ errors) / total_weight + 0.03 * coefficients
        )
    return intercept, coefficients.tolist()


def score_states(
    examples: list[dict[str, Any]], fitted: tuple[list[float], list[float], tuple[float, list[float]]],
) -> list[dict[str, Any]]:
    means, scales, model = fitted
    result = []
    for example in examples:
        raw = [[float(state["features"][name]) for name in FEATURES] for state in example["states"]]
        scores = timing.predict(timing.transform(raw, means, scales), model)
        result.append(example | {
            "states": [state | {"hazardScore": score} for state, score in zip(example["states"], scores)]
        })
    return result


def trigger_metrics(
    scored: list[dict[str, Any]], teacher_ns: dict[int, int], threshold: float,
    include_rows: bool = False,
) -> dict[str, Any]:
    rows = []
    for example in scored:
        states = example["states"]
        selected = states[-1]
        first_index = next(
            (index for index, state in enumerate(states) if float(state["hazardScore"]) >= threshold),
            None,
        )
        first = states[first_index] if first_index is not None else None
        ordinal = int(example["eligibleOrdinal"])
        teacher_received_ns = teacher_ns[ordinal]
        lead_ms = (
            (teacher_received_ns - int(first["receivedNs"])) / 1_000_000
            if first is not None else None
        )
        row = {
            "eligibleOrdinal": ordinal,
            "mint": example["mint"],
            "stateN": len(states),
            "teacherStatePass": float(selected["hazardScore"]) >= threshold,
            "firstTriggerStateIndex": first_index,
            "firstTriggerIsTeacherState": first_index == len(states) - 1,
            "prematureFirstTrigger": first_index is not None and first_index < len(states) - 1,
            "firstTriggerToTeacherMs": lead_ms,
            "teacherStateToTeacherMs": (
                teacher_received_ns - int(selected["receivedNs"])
            ) / 1_000_000,
            "teacherStateScore": float(selected["hazardScore"]),
            "firstTriggerScore": float(first["hazardScore"]) if first is not None else None,
        }
        rows.append(row)
    n = len(rows)
    triggered = [row for row in rows if row["firstTriggerStateIndex"] is not None]
    leads = [float(row["firstTriggerToTeacherMs"]) for row in triggered]
    exact = sum(bool(row["firstTriggerIsTeacherState"]) for row in rows)
    metrics: dict[str, Any] = {
        "episodeN": n,
        "threshold": threshold,
        "teacherStateRecallPct": 100.0 * sum(bool(row["teacherStatePass"]) for row in rows) / n,
        "anyTriggerPct": 100.0 * len(triggered) / n,
        "exactFirstTriggerPct": 100.0 * exact / n,
        "exactFirstGivenTriggeredPct": 100.0 * exact / len(triggered) if triggered else 0.0,
        "prematureFirstTriggerPct": 100.0 * sum(bool(row["prematureFirstTrigger"]) for row in rows) / n,
        "noTriggerPct": 100.0 * (n - len(triggered)) / n,
        "firstTriggerLeadMs": {
            "p50": event_v1.percentile(leads, 0.50),
            "p75": event_v1.percentile(leads, 0.75),
            "p90": event_v1.percentile(leads, 0.90),
        },
        "firstTriggerWithinTeacherMsPct": {
            str(window): 100.0 * sum(0 <= lead <= window for lead in leads) / n
            for window in WINDOWS_MS
        },
    }
    if include_rows:
        metrics["rows"] = rows
    return metrics


def select_threshold(
    scored: list[dict[str, Any]], teacher_ns: dict[int, int], recall_floor_pct: float,
) -> tuple[float, dict[str, Any]]:
    candidates = sorted({
        float(state["hazardScore"])
        for example in scored for state in example["states"]
    })
    eligible = []
    for threshold in candidates:
        metrics = trigger_metrics(scored, teacher_ns, threshold)
        if metrics["teacherStateRecallPct"] + 1e-12 < recall_floor_pct:
            continue
        p75 = metrics["firstTriggerLeadMs"]["p75"]
        key = (
            metrics["exactFirstTriggerPct"],
            -metrics["prematureFirstTriggerPct"],
            metrics["teacherStateRecallPct"],
            -(p75 if p75 is not None else math.inf),
            threshold,
        )
        eligible.append((key, threshold, metrics))
    if not eligible:
        raise RuntimeError("NO_THRESHOLD_MEETS_TRAIN_RECALL_FLOOR")
    _, threshold, metrics = max(eligible, key=lambda row: row[0])
    return threshold, metrics


def model_metrics(
    examples: list[dict[str, Any]], fitted: tuple[list[float], list[float], tuple[float, list[float]]],
) -> dict[str, Any]:
    means, scales, model = fitted
    x, y, _, _ = timing.flatten(examples, FEATURES)
    scores = timing.predict(timing.transform(x, means, scales), model)
    return {
        "rowN": len(y),
        "positiveN": sum(y),
        "rocAuc": timing.auc(y, scores),
        "averagePrecision": timing.average_precision(y, scores),
        "withinMint": timing.ranking_metrics(examples, FEATURES, means, scales, model),
    }


def frozen_model(fitted: tuple[list[float], list[float], tuple[float, list[float]]]) -> dict[str, Any]:
    means, scales, model = fitted
    return {
        "featureNames": list(FEATURES),
        "means": dict(zip(FEATURES, means)),
        "scales": dict(zip(FEATURES, scales)),
        "intercept": model[0],
        "coefficients": dict(zip(FEATURES, model[1])),
    }


def render_report(report: dict[str, Any]) -> str:
    baseline = report["baselineUniform90Recall"]["assessmentTrigger"]
    candidate = report["proximalHazardCandidate"]["assessmentTrigger"]
    model = report["proximalHazardCandidate"]["assessmentModel"]
    lines = [
        "# 2CQ Event-Level Timing Hazard V1",
        "",
        "Development-only, outcome-blind, sealed validation untouched.",
        "",
        "## Assessment",
        "",
        "| Policy | N | AUC | Teacher recall | Exact first trigger | Premature first trigger | P75 lead |",
        "|---|---:|---:|---:|---:|---:|---:|",
        (
            f"| Uniform / 90% train recall | {baseline['episodeN']} | "
            f"{report['baselineUniform90Recall']['assessmentModel']['rocAuc']:.3f} | "
            f"{baseline['teacherStateRecallPct']:.2f}% | {baseline['exactFirstTriggerPct']:.2f}% | "
            f"{baseline['prematureFirstTriggerPct']:.2f}% | {baseline['firstTriggerLeadMs']['p75']:.1f}ms |"
        ),
        (
            f"| Proximal hazard / exact-first threshold | {candidate['episodeN']} | {model['rocAuc']:.3f} | "
            f"{candidate['teacherStateRecallPct']:.2f}% | {candidate['exactFirstTriggerPct']:.2f}% | "
            f"{candidate['prematureFirstTriggerPct']:.2f}% | {candidate['firstTriggerLeadMs']['p75']:.1f}ms |"
        ),
        "",
        "## Near-Teacher Capture",
        "",
    ]
    for window, value in candidate["firstTriggerWithinTeacherMsPct"].items():
        lines.append(f"- First trigger within {window}ms of teacher: **{value:.2f}%**")
    lines += [
        "",
        "## Integrity",
        "",
        f"- Development ordinals: **1-350 only**",
        f"- Internal train/assessment timing episodes: **{report['population']['trainN']} / {report['population']['assessmentN']}**",
        "- Validation rows read: **0**",
        "- Outcomes read: **0**",
        "- Submissions: **0**",
        "- Canonical state order preserved: **yes**",
        "",
        f"Verdict: `{report['verdict']}`",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    raw_examples = read_jsonl_gz(args.parent / "same-mint-temporal-examples.jsonl.gz")
    tape = read_jsonl_gz(args.parent / "entry-decision-tape.jsonl.gz")
    if len(tape) != 350 or any(int(row["eligibleOrdinal"]) > 350 for row in tape):
        raise RuntimeError("DEVELOPMENT_BOUNDARY_VIOLATION")
    if any(int(row.get("outcomesRead") or 0) != 0 for row in tape):
        raise RuntimeError("OUTCOME_TAINT")
    examples = enrich_canonical_transitions(
        [row for row in raw_examples if int(row.get("controlN") or 0) > 0]
    )
    train, assessment, repeat_dropped = decision.internal_split(examples)
    teacher_ns = {int(row["eligibleOrdinal"]): int(row["entryReceivedAtUnixNs"]) for row in tape}

    baseline_fit = fit_model(train, proximal=False)
    baseline_train = score_states(train, baseline_fit)
    baseline_scores = [
        float(example["states"][-1]["hazardScore"]) for example in baseline_train
    ]
    baseline_threshold = float(event_v1.percentile(baseline_scores, 0.10))
    baseline_assessment = score_states(assessment, baseline_fit)

    candidate_fit = fit_model(train, proximal=True)
    candidate_train = score_states(train, candidate_fit)
    candidate_threshold, candidate_train_trigger = select_threshold(
        candidate_train, teacher_ns, TRAIN_RECALL_FLOOR_PCT
    )
    candidate_assessment = score_states(assessment, candidate_fit)

    baseline_trigger = trigger_metrics(
        baseline_assessment, teacher_ns, baseline_threshold, include_rows=True
    )
    candidate_trigger = trigger_metrics(
        candidate_assessment, teacher_ns, candidate_threshold, include_rows=True
    )
    baseline_model = model_metrics(assessment, baseline_fit)
    candidate_model = model_metrics(assessment, candidate_fit)
    gate = {
        "assessmentRecallFloorMet": (
            candidate_trigger["teacherStateRecallPct"] >= ASSESSMENT_RECALL_FLOOR_PCT
        ),
        "exactFirstImprovementMet": (
            candidate_trigger["exactFirstTriggerPct"]
            >= baseline_trigger["exactFirstTriggerPct"] + MIN_EXACT_FIRST_IMPROVEMENT_PCT
        ),
        "p75LeadNotWorse": (
            candidate_trigger["firstTriggerLeadMs"]["p75"]
            <= baseline_trigger["firstTriggerLeadMs"]["p75"]
        ),
    }
    passed = all(gate.values())
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": decision.now_utc(),
        "population": {
            "sourceDevelopmentN": len(raw_examples),
            "timingIdentifiableN": len(examples),
            "trainN": len(train),
            "assessmentN": len(assessment),
            "crossBoundaryRepeatDropped": repeat_dropped,
        },
        "contract": {
            "featureFamily": "FULL_CAUSAL_PLUS_TRANSITION_FIXED",
            "negativeWeighting": "per episode: positive=0.5; negatives share 0.5 proportional to 1/distance-from-teacher-state",
            "thresholdSelection": "training-only maximize exact first trigger with >=75% teacher-state recall",
            "assessmentPromotionGate": {
                "minimumTeacherStateRecallPct": ASSESSMENT_RECALL_FLOOR_PCT,
                "minimumExactFirstImprovementPct": MIN_EXACT_FIRST_IMPROVEMENT_PCT,
                "p75LeadMustNotWorsen": True,
            },
        },
        "baselineUniform90Recall": {
            "threshold": baseline_threshold,
            "assessmentModel": baseline_model,
            "assessmentTrigger": baseline_trigger,
        },
        "proximalHazardCandidate": {
            "threshold": candidate_threshold,
            "trainingTrigger": candidate_train_trigger,
            "assessmentModel": candidate_model,
            "assessmentTrigger": candidate_trigger,
        },
        "gate": gate,
        "limitations": {
            "standaloneTimingSolved": False,
            "reason": "premature first-trigger rate and upper-tail lead remain too high for standalone use",
            "combinedWhatWhenEvaluation": "PENDING_REPRESENTATIVE_SYMMETRIC_CHOICE_COHORT",
        },
        "nextGate": (
            "combine this timing candidate with the frozen cross-sectional selector after the "
            "representative symmetric SOL-Pump development cohort reaches its frozen gate"
        ),
        "integrity": {
            "parentTemporalSha256": decision.sha256_file(args.parent / "same-mint-temporal-examples.jsonl.gz"),
            "parentTapeSha256": decision.sha256_file(args.parent / "entry-decision-tape.jsonl.gz"),
            "developmentOrdinals": [1, 350],
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
            "canonicalStateOrderPreserved": True,
        },
        "verdict": (
            "2CQ_EVENT_LEVEL_TIMING_HAZARD_DEVELOPMENT_CANDIDATE_IMPROVED"
            if passed else "2CQ_EVENT_LEVEL_TIMING_HAZARD_NO_IMPROVEMENT"
        ),
    }
    decision.atomic_json(args.output / "report.json", report)
    event_v1.atomic_text(args.output / "REPORT.md", render_report(report))
    decision.atomic_json(args.output / "contract.json", report["contract"] | {
        "schema": SCHEMA + "_CONTRACT",
        "developmentOrdinals": [1, 350],
        "sealedValidationOrdinals": [351, 500],
        "validationPermitted": False,
        "outcomesPermitted": False,
        "submissionsPermitted": False,
    })
    if passed:
        decision.atomic_json(args.output / "timing-policy-candidate.json", {
            "schema": SCHEMA + "_TIMING_POLICY_CANDIDATE",
            "teacherImitationOnly": True,
            "model": frozen_model(candidate_fit),
            "threshold": candidate_threshold,
            "triggerClock": "after every canonically ordered successful external Pump transaction",
            "dedupeRule": "first threshold crossing per mint lifecycle",
            "sealedValidationRequired": True,
            "profitabilityClaim": False,
        })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
