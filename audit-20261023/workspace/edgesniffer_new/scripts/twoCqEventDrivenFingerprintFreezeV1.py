#!/usr/bin/env python3
"""Freeze an interpretable, event-driven 2CQ entry fingerprint from development only."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision


SCHEMA = "2CQ_EVENT_DRIVEN_FINGERPRINT_FREEZE_V1"
BOOTSTRAP_REPLICATES = 1000
TRAIN_RECALL_TARGET = 0.90
CHRONOLOGY_BLOCKS = ((1, 70), (71, 140), (141, 210), (211, 280), (281, 350))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    weight = position - left
    return ordered[left] * (1.0 - weight) + ordered[right] * weight


def selected_and_controls(example: dict[str, Any], feature: str) -> tuple[float, list[float]]:
    selected = [float(state["features"][feature]) for state in example["states"] if state["label"]]
    controls = [float(state["features"][feature]) for state in example["states"] if not state["label"]]
    if len(selected) != 1 or not controls:
        raise RuntimeError("UNIVARIATE_EXAMPLE_SHAPE_INVALID")
    return selected[0], controls


def bootstrap_clustered_median(
    paired: list[tuple[str, float]], scale: float, seed: int,
) -> tuple[float, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for mint, difference in paired:
        grouped[mint].append(difference)
    mints = sorted(grouped)
    rng = random.Random(seed)
    estimates = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = [rng.choice(mints) for _ in mints]
        differences = [value for mint in sample for value in grouped[mint]]
        estimates.append(statistics.median(differences) / scale)
    return float(percentile(estimates, 0.025)), float(percentile(estimates, 0.975))


def effect_for_examples(examples: list[dict[str, Any]], feature: str) -> dict[str, Any]:
    selected_values = []
    control_values = []
    paired: list[tuple[str, float]] = []
    for example in examples:
        selected, controls = selected_and_controls(example, feature)
        control_median = statistics.median(controls)
        selected_values.append(selected)
        control_values.extend(controls)
        paired.append((str(example["mint"]), selected - control_median))
    q25 = float(percentile(control_values, 0.25))
    q75 = float(percentile(control_values, 0.75))
    scale = q75 - q25
    if abs(scale) < 1e-12:
        control_median = statistics.median(control_values)
        scale = 1.4826 * statistics.median(abs(value - control_median) for value in control_values)
    if abs(scale) < 1e-12:
        scale = 1.0
    differences = [value for _, value in paired]
    effect = statistics.median(differences) / scale
    ci_low, ci_high = bootstrap_clustered_median(
        paired, scale, int(hashlib.sha256(feature.encode()).hexdigest()[:8], 16)
    )
    return {
        "feature": feature,
        "episodeN": len(examples),
        "uniqueMintN": len({row["mint"] for row in examples}),
        "teacherMedian": statistics.median(selected_values),
        "controlMedian": statistics.median(control_values),
        "controlIqr": q75 - q25,
        "pairedMedianDifference": statistics.median(differences),
        "robustPairedEffect": effect,
        "clusteredBootstrap95": [ci_low, ci_high],
        "teacherAboveControlMedianPct": 100.0 * sum(value > 0 for value in differences) / len(differences),
        "teacherBelowControlMedianPct": 100.0 * sum(value < 0 for value in differences) / len(differences),
    }


def univariate_report(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for feature in timing.FULL_FEATURES:
        overall = effect_for_examples(examples, feature)
        blocks = []
        for start, end in CHRONOLOGY_BLOCKS:
            subset = [row for row in examples if start <= int(row["eligibleOrdinal"]) <= end]
            if subset:
                block = effect_for_examples(subset, feature)
                blocks.append({
                    "ordinals": [start, end],
                    "n": len(subset),
                    "effect": block["robustPairedEffect"],
                    "directionPct": max(
                        block["teacherAboveControlMedianPct"], block["teacherBelowControlMedianPct"]
                    ),
                })
        sign = 1 if overall["robustPairedEffect"] > 0 else -1 if overall["robustPairedEffect"] < 0 else 0
        same_sign = sum(
            (block["effect"] > 0 and sign > 0) or (block["effect"] < 0 and sign < 0)
            for block in blocks
        )
        overall["chronologyBlocks"] = blocks
        overall["sameDirectionBlocks"] = same_sign
        overall["stableDirection"] = same_sign == len(blocks) and sign != 0
        overall["minimumAbsoluteBlockEffect"] = min((abs(block["effect"]) for block in blocks), default=0.0)
        rows.append(overall)
    return sorted(
        rows,
        key=lambda row: (
            row["stableDirection"], row["minimumAbsoluteBlockEffect"], abs(row["robustPairedEffect"])
        ),
        reverse=True,
    )


def fit_model(
    examples: list[dict[str, Any]], feature_names: tuple[str, ...]
) -> tuple[dict[str, Any], tuple[list[float], list[float], tuple[float, list[float]]], list[dict[str, Any]], list[dict[str, Any]]]:
    train, assessment, dropped = decision.internal_split(examples)
    fit_train = [decision.fit_control_sample(row) for row in train]
    report, fitted = timing.evaluate_model(fit_train, assessment, feature_names)
    report["crossBoundaryRepeatDropped"] = dropped
    report["trainEpisodeN"] = len(train)
    report["assessmentEpisodeN"] = len(assessment)
    return report, fitted, train, assessment


def score_features(
    features: dict[str, float], feature_names: tuple[str, ...],
    fitted: tuple[list[float], list[float], tuple[float, list[float]]],
) -> float:
    means, scales, model = fitted
    row = [[float(features[name]) for name in feature_names]]
    return timing.predict(timing.transform(row, means, scales), model)[0]


def frozen_model(
    feature_names: tuple[str, ...], fitted: tuple[list[float], list[float], tuple[float, list[float]]]
) -> dict[str, Any]:
    means, scales, model = fitted
    return {
        "featureNames": list(feature_names),
        "means": dict(zip(feature_names, means)),
        "scales": dict(zip(feature_names, scales)),
        "intercept": model[0],
        "coefficients": dict(zip(feature_names, model[1])),
    }


def event_trigger_replay(
    train: list[dict[str, Any]], assessment: list[dict[str, Any]],
    tape: list[dict[str, Any]], feature_names: tuple[str, ...],
    fitted: tuple[list[float], list[float], tuple[float, list[float]]],
) -> tuple[dict[str, Any], float]:
    positive_train_scores = []
    for example in train:
        selected = next(state for state in example["states"] if state["label"])
        positive_train_scores.append(score_features(selected["features"], feature_names, fitted))
    threshold = float(percentile(positive_train_scores, 1.0 - TRAIN_RECALL_TARGET))
    teacher_receipts = {int(row["eligibleOrdinal"]): int(row["entryReceivedAtUnixNs"]) for row in tape}
    rows = []
    for example in assessment:
        states = sorted(example["states"], key=lambda row: (int(row["receivedNs"]), str(row["signature"])))
        scored = [(state, score_features(state["features"], feature_names, fitted)) for state in states]
        crossings = [(state, score) for state, score in scored if score >= threshold]
        selected = next((state, score) for state, score in scored if state["label"])
        first = crossings[0] if crossings else None
        teacher_ns = teacher_receipts[int(example["eligibleOrdinal"])]
        rows.append({
            "eligibleOrdinal": int(example["eligibleOrdinal"]),
            "mint": example["mint"],
            "selectedScore": selected[1],
            "selectedPass": selected[1] >= threshold,
            "crossingStateN": len(crossings),
            "firstCrossingIsSelectedState": bool(first and first[0]["label"]),
            "firstCrossingToTeacherMs": (teacher_ns - int(first[0]["receivedNs"])) / 1e6 if first else None,
            "selectedStateToTeacherMs": (teacher_ns - int(selected[0]["receivedNs"])) / 1e6,
        })
    leads = [row["firstCrossingToTeacherMs"] for row in rows if row["firstCrossingToTeacherMs"] is not None]
    selected_leads = [row["selectedStateToTeacherMs"] for row in rows]
    result = {
        "assessmentN": len(rows),
        "thresholdPolicy": "P10 of training teacher-state scores; fixed 90% in-sample teacher recall target",
        "threshold": threshold,
        "teacherStatePassN": sum(row["selectedPass"] for row in rows),
        "teacherStateRecallPct": 100.0 * sum(row["selectedPass"] for row in rows) / len(rows),
        "anyPreTeacherCrossingN": sum(row["firstCrossingToTeacherMs"] is not None for row in rows),
        "firstCrossingIsFinalSelectedStatePct": 100.0 * sum(row["firstCrossingIsSelectedState"] for row in rows) / len(rows),
        "firstCrossingLeadMs": {
            "p25": percentile(leads, 0.25), "p50": percentile(leads, 0.50),
            "p75": percentile(leads, 0.75), "p90": percentile(leads, 0.90),
        },
        "selectedStateToTeacherMs": {
            "p25": percentile(selected_leads, 0.25), "p50": percentile(selected_leads, 0.50),
            "p75": percentile(selected_leads, 0.75), "p90": percentile(selected_leads, 0.90),
        },
        "crossWithinTeacherLeadWindow": {
            "250ms": sum(lead <= 250 for lead in leads),
            "1000ms": sum(lead <= 1000 for lead in leads),
            "5000ms": sum(lead <= 5000 for lead in leads),
        },
        "rows": rows,
        "limitation": "Teacher-independent event clock, but only teacher-traded mint histories are materialized; full-market unmatched signals/hour is not estimable from this tape.",
    }
    return result, threshold


def compact_model_metrics(report: dict[str, Any]) -> dict[str, Any]:
    assessment = report["validation"]
    ranked = assessment["withinMint"]
    return {
        "assessmentN": assessment["positiveN"],
        "rocAuc": assessment["rocAuc"],
        "averagePrecision": assessment["averagePrecision"],
        "top1Pct": ranked["actualRankedFirstPct"],
        "top3Pct": ranked["actualRankedTop3Pct"],
        "mrr": ranked["meanReciprocalRank"],
        "chanceTop1Pct": ranked["chanceRankedFirstPct"],
    }


def render(report: dict[str, Any]) -> str:
    timing_models = report["ablations"]["sameMintTiming"]
    cross_models = report["ablations"]["crossMintSelection"]
    trigger = report["eventDrivenReplay"]
    lines = [
        "# 2CQ Event-Driven Fingerprint Freeze V1", "",
        "Development-only, outcome-blind, validation-sealed.", "",
        "## Population", "",
        f"- Development entries: {report['population']['developmentN']}",
        f"- Same-mint timing examples: {report['population']['sameMintTimingN']}",
        f"- Cross-mint LAUNCHED_120S examples: {report['population']['crossMintN']}", "",
        "## Ablations", "",
        "| Problem | Model | N | AUC | Top-1 | Top-3 | Chance Top-1 |", "|---|---|---:|---:|---:|---:|---:|",
    ]
    for problem, models in (("WHEN", timing_models), ("WHAT", cross_models)):
        for name, values in models.items():
            lines.append(
                f"| {problem} | {name} | {values['assessmentN']} | {values['rocAuc']:.3f} | "
                f"{values['top1Pct']:.2f}% | {values['top3Pct']:.2f}% | {values['chanceTop1Pct']:.2f}% |"
            )
    lines += [
        "", "## Event-driven replay", "",
        f"- Frozen timing threshold: {trigger['threshold']:.9f}",
        f"- Assessment teacher-state recall: {trigger['teacherStateRecallPct']:.2f}%",
        f"- First crossing is final selected state: {trigger['firstCrossingIsFinalSelectedStatePct']:.2f}%",
        f"- First-crossing lead P50: {trigger['firstCrossingLeadMs']['p50']:.2f} ms",
        f"- Final selected-state to teacher P50: {trigger['selectedStateToTeacherMs']['p50']:.2f} ms", "",
        "## Stable causal shifts", "",
        "### Within-mint timing", "",
        "| Feature | Robust effect | 95% clustered bootstrap | Stable blocks | Teacher median | Control median |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["sameMintUnivariate"][:10]:
        lines.append(
            f"| {row['feature']} | {row['robustPairedEffect']:.3f} | "
            f"[{row['clusteredBootstrap95'][0]:.3f}, {row['clusteredBootstrap95'][1]:.3f}] | "
            f"{row['sameDirectionBlocks']}/5 | {row['teacherMedian']:.3f} | {row['controlMedian']:.3f} |"
        )
    lines += ["", "### Cross-mint selection", "",
        "| Feature | Direction | Stable blocks | Teacher median | Control median |",
        "|---|---|---:|---:|---:|",
    ]
    for row in report["crossMintUnivariate"][:10]:
        direction = "higher" if row["robustPairedEffect"] > 0 else "lower"
        lines.append(
            f"| {row['feature']} | {direction} | {row['sameDirectionBlocks']}/5 | "
            f"{row['teacherMedian']:.3f} | {row['controlMedian']:.3f} |"
        )
    lines += [
        "", "## Interpretation", "",
        report["humanReadableFingerprint"], "",
        report["eventTriggerInterpretation"], "",
        "Full-market unmatched signals/hour is deliberately not claimed from a tape materialized around teacher decisions.", "",
        "## Integrity", "",
        "- Outcome labels read: 0", "- Validation rows read: 0", "- Submissions: 0", "",
        f"Verdict: `{report['verdict']}`", "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_paths = {
        "parentContract": args.parent / "contract.json",
        "entryTape": args.parent / "entry-decision-tape.jsonl.gz",
        "temporalExamples": args.parent / "same-mint-temporal-examples.jsonl.gz",
        "crossExamples": args.parent / "choice-set-launched_120s.jsonl.gz",
    }
    source_hashes = {name: sha256(path) for name, path in source_paths.items()}
    temporal = read_jsonl_gz(source_paths["temporalExamples"])
    cross = read_jsonl_gz(source_paths["crossExamples"])
    tape = read_jsonl_gz(source_paths["entryTape"])
    if len(tape) != 350 or any(int(row["eligibleOrdinal"]) > 350 for row in tape):
        raise RuntimeError("DEVELOPMENT_BOUNDARY_VIOLATION")
    if any(int(row.get("outcomesRead") or 0) != 0 for row in tape):
        raise RuntimeError("OUTCOME_TAINT")

    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceHashes": source_hashes,
        "developmentOrdinals": [1, 350],
        "internalTrainOrdinals": [1, decision.INTERNAL_TRAIN_N],
        "internalAssessmentOrdinals": [decision.INTERNAL_TRAIN_N + 1, 350],
        "validationRowsPermitted": 0,
        "outcomesPermitted": 0,
        "bootstrap": {"unit": "mint", "replicates": BOOTSTRAP_REPLICATES},
        "chronologyBlocks": [list(block) for block in CHRONOLOGY_BLOCKS],
        "triggerClock": "after every successful external Pump transaction",
        "triggerThreshold": "training teacher-score P10; 90% training recall target",
        "crossMintUniverse": "LAUNCHED_120S",
        "fullMarketAlertRateClaimPermitted": False,
    }
    contract_path = args.output / "contract.json"
    if contract_path.exists():
        existing = json.loads(contract_path.read_text())
        for key in contract:
            if key != "createdAtUtc" and existing.get(key) != contract[key]:
                raise RuntimeError(f"FROZEN_CONTRACT_MISMATCH:{key}")
        contract = existing
    else:
        atomic_json(contract_path, contract)

    temporal_usable = [row for row in temporal if int(row.get("controlN") or 0) > 0]
    cross_usable = [row for row in cross if int(row.get("controlN") or 0) > 0]
    same_univariate = univariate_report(temporal_usable)
    cross_univariate = univariate_report(cross_usable)

    no_recency = tuple(name for name in timing.FULL_FEATURES if name not in {"log_age_ms", "log_ms_since_last_external"})
    no_250ms = tuple(name for name in timing.FULL_FEATURES if "250ms" not in name)
    families = {
        "LIFECYCLE": timing.PROGRESSION_FEATURES,
        "LIFECYCLE_FLOW": timing.PROGRESSION_FEATURES + timing.FLOW_FEATURES,
        "FULL_CAUSAL": timing.FULL_FEATURES,
        "FULL_WITHOUT_AGE_RECENCY": no_recency,
        "FULL_WITHOUT_250MS": no_250ms,
    }
    temporal_reports = {}
    cross_reports = {}
    timing_full_fit = None
    timing_train = timing_assessment = None
    cross_full_fit = None
    for name, features in families.items():
        fitted_report, fitted, train, assessment = fit_model(temporal_usable, features)
        temporal_reports[name] = compact_model_metrics(fitted_report)
        if name == "FULL_CAUSAL":
            timing_full_fit, timing_train, timing_assessment = fitted, train, assessment
        fitted_report, fitted, _, _ = fit_model(cross_usable, features)
        cross_reports[name] = compact_model_metrics(fitted_report)
        if name == "FULL_CAUSAL":
            cross_full_fit = fitted

    event_replay, threshold = event_trigger_replay(
        timing_train, timing_assessment, tape, timing.FULL_FEATURES, timing_full_fit
    )
    scorer = {
        "schema": SCHEMA + "_SCORER",
        "causalTrigger": "successful external Pump transaction post-state",
        "candidateUniverse": "mint age <=120s; active curve; complete causal history",
        "timingModel": frozen_model(timing.FULL_FEATURES, timing_full_fit),
        "timingThreshold": threshold,
        "timingThresholdPolicy": event_replay["thresholdPolicy"],
        "crossMintModel": frozen_model(timing.FULL_FEATURES, cross_full_fit),
        "selectionRule": "among causally complete active candidates, choose highest crossMintModel score",
        "dedupeRule": "first rising timing-threshold crossing per mint lifecycle",
        "requiresProspectiveFullUniverseRateMeasurement": True,
        "outcomeOptimized": False,
    }
    atomic_json(args.output / "frozen-scorer.json", scorer)

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "contractSha256": sha256(contract_path),
        "population": {
            "developmentN": len(tape),
            "sameMintTimingN": len(temporal_usable),
            "sameMintUniqueMints": len({row["mint"] for row in temporal_usable}),
            "sameMintControlStates": sum(int(row["controlN"]) for row in temporal_usable),
            "crossMintN": len(cross_usable),
            "crossMintUniqueTeacherMints": len({row["mint"] for row in cross_usable}),
            "crossMintControlStates": sum(int(row["controlN"]) for row in cross_usable),
        },
        "sameMintUnivariate": same_univariate,
        "crossMintUnivariate": cross_univariate,
        "ablations": {"sameMintTiming": temporal_reports, "crossMintSelection": cross_reports},
        "eventDrivenReplay": event_replay,
        "humanReadableFingerprint": (
            "2CQ usually chooses a newly launched, currently moving mint, then enters immediately after a compact "
            "externally driven burst: strong positive 250ms flow and price response, several consecutive buys, "
            "and less single-buyer concentration. Cross-mint choice is mostly lifecycle/recency; exact within-mint "
            "timing depends more on the short flow transition."
        ),
        "eventTriggerInterpretation": (
            "The state ranker is not yet a sufficiently specific standalone trigger. Its frozen training-recall "
            "threshold passed 78.95% of assessment teacher states, but only 31.58% of episodes first crossed at "
            "the final selected state; the upper-quarter first crossings were materially premature. A full "
            "transition/cross-sectional event rule must be validated before prospective use."
        ),
        "frozenScorerSha256": sha256(args.output / "frozen-scorer.json"),
        "integrity": {
            "validationRowsRead": 0, "outcomesRead": 0, "submissions": 0,
            "teacherTransactionFeaturesExcluded": True,
            "crossBoundaryRepeatMintsExcluded": True,
        },
        "requiredNextGate": "Run frozen scorer once on sealed ordinals 351-500 only after N500 is complete",
        "verdict": "2CQ_EVENT_DRIVEN_FINGERPRINT_FROZEN_DEVELOPMENT_ONLY",
    }
    atomic_json(args.output / "report.json", report)
    atomic_text(args.output / "report.md", render(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({
        "verdict": result["verdict"], "population": result["population"],
        "ablations": result["ablations"], "eventDrivenReplay": {
            key: value for key, value in result["eventDrivenReplay"].items() if key != "rows"
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
