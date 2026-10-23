#!/usr/bin/env python3
"""Post-failure, development-only forensics for AUTONOMOUS_WHEN_V1.

This script deliberately reads only the sealed V1 development segments.  It is
not a retry of the V1 gate and cannot write a model, threshold, or validation
artifact.  It produces a separately namespaced exploratory report for V2
hypothesis discovery.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import shutil
import stat
import sys
import tempfile
from collections import Counter, defaultdict
from typing import Any

import twoCqAutonomousWhenFrozenDevelopmentFitV1 as v1


SCHEMA = "AUTONOMOUS_WHEN_V2_EXPLORATORY_FORENSICS_V2"
HORIZONS_S = (1, 2, 3, 5, 10, 20, 30)
LEAD_BUCKETS = ((0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 20), (20, 30))
FOLDS = (("F1", 36, 45), ("F2", 45, 54), ("F3", 54, 63), ("F4", 63, 72))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: pathlib.Path) -> str:
    return v1.sha256_file(path)


def canonical(value: Any) -> bytes:
    return v1.canonical(value)


def atomic_json(path: pathlib.Path, value: Any) -> None:
    v1.atomic_json(path, value)


def seal(path: pathlib.Path) -> str:
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return sha256_file(path)


def event_id(event: dict[str, Any]) -> str:
    return f"{event['signature']}:{event['mint']}"


def percentile(values: list[float], q: float) -> float | None:
    return v1.percentile(values, q)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = sum(values) / len(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def rate_per_hour(value: int | float, hours: float) -> float:
    return value / hours if hours else 0.0


def segment_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        output[row["segment"]].append(row)
    return output


def segment_events(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in events:
        output[item["segment"]].append(item)
    return output


def rows_before_event(rows: list[dict[str, Any]], ticks: list[int], event: dict[str, Any], horizon_s: int) -> list[dict[str, Any]]:
    lower = event["receiptNs"] - horizon_s * 1_000_000_000
    upper = event["receiptNs"]
    return rows[bisect.bisect_left(ticks, lower):bisect.bisect_left(ticks, upper)]


def maximum_refractory_matches(per_event_candidates: dict[str, list[int]], events: list[dict[str, Any]], refractory_ns: int) -> tuple[set[str], list[dict[str, Any]]]:
    """Maximum matches from real candidate ticks under a global refractory.

    Process teacher events chronologically.  For an event, the earliest candidate
    tick outside the previous refractory interval is optimal: choosing a later
    tick can only make future events harder to serve.  This is an upper bound for
    the frozen semantics because it assumes a perfect classifier that fires only
    at true target-positive ticks.
    """
    matched: set[str] = set()
    selections: list[dict[str, Any]] = []
    previous_fire = -1
    active_segment: str | None = None
    # `events` is constructed from the sealed segment sequence and then each
    # segment's receipt-ordered label query, so it is canonical chronology.
    for event in events:
        if event["segment"] != active_segment:
            active_segment = event["segment"]
            previous_fire = -1
        identifier = event_id(event)
        choices = per_event_candidates.get(identifier, [])
        tick = next((value for value in choices if value > previous_fire + refractory_ns), None)
        if tick is None:
            continue
        matched.add(identifier)
        previous_fire = tick
        selections.append({"eventId": identifier, "tickNs": tick, "eventReceiptNs": event["receiptNs"], "segment": event["segment"]})
    return matched, selections


def structural_by_horizon(rows: list[dict[str, Any]], events: list[dict[str, Any]], horizon_s: int) -> dict[str, Any]:
    by_segment_rows = segment_rows(rows)
    by_segment_events = segment_events(events)
    eligible_ids: set[str] = set()
    top1_ids: set[str] = set()
    per_event_candidates: dict[str, list[int]] = {}
    per_event: dict[str, dict[str, Any]] = {}
    for segment, segment_event_rows in by_segment_events.items():
        candidates = by_segment_rows.get(segment, [])
        candidate_ticks = [row["tickNs"] for row in candidates]
        for item in segment_event_rows:
            identifier = event_id(item)
            eligible = rows_before_event(candidates, candidate_ticks, item, horizon_s)
            matching = [row for row in eligible if row["topMint"] == item["mint"]]
            if eligible:
                eligible_ids.add(identifier)
            if matching:
                top1_ids.add(identifier)
                per_event_candidates[identifier] = [row["tickNs"] for row in matching]
            per_event[identifier] = {
                "eligible": bool(eligible),
                "top1": bool(matching),
                "closestTop1LeadMs": min(((item["receiptNs"] - row["tickNs"]) / 1_000_000 for row in matching), default=None),
            }
    matched_ids, selections = maximum_refractory_matches(per_event_candidates, events, horizon_s * 1_000_000_000)
    count = len(events)
    return {
        "horizonSeconds": horizon_s,
        "teacherEvents": count,
        "eligibleTickEvents": len(eligible_ids),
        "top1Events": len(top1_ids),
        "perfectRefractoryMatchedEvents": len(matched_ids),
        "eligibleTickFraction": len(eligible_ids) / count if count else 0.0,
        "top1Fraction": len(top1_ids) / count if count else 0.0,
        "jointTop1RefractoryCeiling": len(matched_ids) / count if count else 0.0,
        "candidateUniverseMembership": "NOT_MATERIALIZED_IN_SEALED_V1_DECISIONS",
        "top3": "NOT_MATERIALIZED_IN_SEALED_V1_DECISIONS",
        "perEvent": per_event,
        "perfectMatching": {"matchedEventIds": sorted(matched_ids), "selections": selections},
    }


def oos_events(rows: list[dict[str, Any]], events: list[dict[str, Any]], offsets: dict[str, int], starts: dict[str, int]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    converted = [{**item, "coordinateNs": offsets[item["segment"]] + item["receiptNs"] - starts[item["segment"]]} for item in events]
    all_events: list[dict[str, Any]] = []
    per_fold: dict[str, list[dict[str, Any]]] = {}
    for identifier, start_hour, end_hour in FOLDS:
        lower = start_hour * 3_600_000_000_000 + v1.EMBARGO_NS
        upper = end_hour * 3_600_000_000_000 - v1.HORIZON_NS
        items = [item for item in converted if lower < item["coordinateNs"] < upper]
        per_fold[identifier] = items
        all_events.extend(items)
    unique = {event_id(item): item for item in all_events}
    return sorted(unique.values(), key=lambda item: (item["coordinateNs"], item["signature"], item["mint"])), per_fold


def baseline_models(rows: list[dict[str, Any]], helper: pathlib.Path, feature_indexes: list[int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float]]:
    models: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    all_scores: list[float] = []
    with tempfile.TemporaryDirectory(prefix="autonomous-when-v2-forensics-") as directory:
        work = pathlib.Path(directory)
        for identifier, start_hour, end_hour in FOLDS:
            train, test = v1.fold_rows(rows, start_hour, end_hour)
            def x(items: list[dict[str, Any]]) -> list[list[float]]:
                return [[row["features"][index] for index in feature_indexes] for row in items] if feature_indexes is not None else [row["features"] for row in items]
            x_train, y_train = x(train), [int(row["label"]) for row in train]
            x_test, y_test = x(test), [int(row["label"]) for row in test]
            values = v1.native_fit_predict(helper, work, identifier + ("-meta" if feature_indexes else "-full"), x_train, y_train, x_test)
            means, scales, intercept, coefficients, scores, train_scores = values
            models.append({
                "id": identifier,
                "testRows": test,
                "testScores": scores,
                "positiveScores": [score for score, label in zip(train_scores, y_train) if label],
                "trainTicks": len(train), "testTicks": len(test),
                "trainPositiveTicks": sum(y_train), "testPositiveTicks": sum(y_test),
                "averagePrecision": v1.average_precision(y_test, scores),
                "coefficients": coefficients, "means": means, "scales": scales, "intercept": intercept,
            })
            all_rows.extend(test)
            all_scores.extend(scores)
    return models, all_rows, all_scores


def threshold_grid(models: list[dict[str, Any]], per_fold_events: dict[str, list[dict[str, Any]]], all_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = []
    event_ids = {event_id(item) for item in all_events}
    for q_percent in range(51):
        q = q_percent / 100.0
        fires: list[dict[str, Any]] = []
        matches: list[dict[str, Any]] = []
        matched: set[str] = set()
        thresholds: dict[str, float] = {}
        for model in models:
            threshold = v1.quantile(model["positiveScores"], q)
            thresholds[model["id"]] = threshold
            local = v1.match_events(model["testRows"], model["testScores"], threshold, per_fold_events[model["id"]])
            fires.extend(local["fires"])
            for match in local["matches"]:
                identifier = event_id(match["event"])
                if identifier not in matched:
                    matched.add(identifier)
                    matches.append(match)
        false_fires = len(fires) - len(matches)
        values.append({
            "q": q, "thresholdsByFold": thresholds, "fires": len(fires), "matches": len(matches),
            "falseFires": false_fires, "precision": len(matches) / len(fires) if fires else 0.0,
            "recall": len(matched) / len(event_ids) if event_ids else 0.0,
            "falseFiresPerHour": false_fires / 36.0, "firesPerHour": len(fires) / 36.0,
            "matchedEventIds": sorted(matched), "matchesDetail": matches,
        })
    return values


def failure_decomposition(rows: list[dict[str, Any]], all_events: list[dict[str, Any]], horizon: dict[str, Any], closest: dict[str, Any]) -> dict[str, Any]:
    by_segment = segment_rows(rows)
    threshold_matched = set(closest["matchedEventIds"])
    perfect_matched = set(horizon["perfectMatching"]["matchedEventIds"])
    counts: Counter[str] = Counter()
    details: dict[str, str] = {}
    for item in all_events:
        identifier = event_id(item)
        status = horizon["perEvent"].get(identifier, {"eligible": False, "top1": False})
        if not status["eligible"]:
            reason = "NO_ELIGIBLE_AUTONOMOUS_TICK"
        elif not status["top1"]:
            reason = "FROZEN_WHICH_TOP1_NOT_EVENTUAL_TEACHER_MINT"
        elif identifier not in perfect_matched:
            reason = "REFRACTORY_OR_EVENT_MATCHING_SUPPRESSION"
        elif identifier not in threshold_matched:
            reason = "CLASSIFIER_OR_THRESHOLD_MISS_AT_CLOSEST_OPERATING_POINT"
        else:
            reason = "MATCHED_AT_CLOSEST_OPERATING_POINT"
        counts[reason] += 1
        details[identifier] = reason
    total = len(all_events)
    return {"counts": dict(sorted(counts.items())), "fractions": {key: value / total if total else 0.0 for key, value in sorted(counts.items())}, "perEvent": details}


def timing_discovery(rows: list[dict[str, Any]], events: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    by_segment = segment_rows(rows)
    ticks_by_segment = {segment: [row["tickNs"] for row in items] for segment, items in by_segment.items()}
    # Ordinary ticks are those with no same-top teacher acquisition in the next
    # 30 seconds.  This avoids treating imminent teacher ticks as controls.
    imminently_positive: set[tuple[str, int]] = set()
    bucket_rows: dict[str, list[dict[str, Any]]] = {f"{left}-{right}s": [] for left, right in LEAD_BUCKETS}
    for event in events:
        segment_items = by_segment.get(event["segment"], [])
        segment_ticks = ticks_by_segment.get(event["segment"], [])
        for row in rows_before_event(segment_items, segment_ticks, event, 30):
            lead = (event["receiptNs"] - row["tickNs"]) / 1_000_000_000
            if row["topMint"] == event["mint"]:
                imminently_positive.add((row["segment"], row["tickNs"]))
                for left, right in LEAD_BUCKETS:
                    if left <= lead < right:
                        bucket_rows[f"{left}-{right}s"].append(row)
                        break
    ordinary = [row for row in rows if (row["segment"], row["tickNs"]) not in imminently_positive]
    # Deterministic chronological thinning prevents a massive ordinary class from
    # dominating descriptive mean comparisons.
    ordinary = ordinary[::max(1, len(ordinary) // max(1, sum(len(items) for items in bucket_rows.values())))]
    ordinary = ordinary[:max(1, sum(len(items) for items in bucket_rows.values()))]

    def profile(items: list[dict[str, Any]]) -> dict[str, Any]:
        columns = list(zip(*(row["features"] for row in items))) if items else []
        return {feature_names[index]: {"mean": mean(list(column)), "p50": percentile(list(column), .5)} for index, column in enumerate(columns)}

    ordinary_profile = profile(ordinary)
    bucket_profiles: dict[str, Any] = {}
    separation: list[dict[str, Any]] = []
    # Rank the near-entry (0-2s) feature mean shifts in pooled standard deviation
    # units.  This is descriptive only and cannot be a feature-selection result.
    near = bucket_rows["0-1s"] + bucket_rows["1-2s"]
    for index, name in enumerate(feature_names):
        a = [row["features"][index] for row in near]
        b = [row["features"][index] for row in ordinary]
        pooled = math.sqrt((stddev(a) ** 2 + stddev(b) ** 2) / 2)
        delta = (mean(a) or 0.0) - (mean(b) or 0.0)
        separation.append({"feature": name, "nearEntryMean": mean(a), "ordinaryMean": mean(b), "standardizedMeanDifference": delta / pooled if pooled else 0.0, "nNear": len(a), "nOrdinary": len(b)})
    for label, items in bucket_rows.items():
        bucket_profiles[label] = {"n": len(items), "profile": profile(items)}
    return {
        "definition": "same frozen-WHICH Top-1 mint as a teacher event; controls have no same-top teacher event in next 30 seconds",
        "ordinaryTicks": len(ordinary), "buckets": bucket_profiles,
        "strongestNearEntrySeparations": sorted(separation, key=lambda item: abs(item["standardizedMeanDifference"]), reverse=True)[:15],
    }


def safe_copy_source(source: pathlib.Path, destination: pathlib.Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return sha256_file(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--helper", type=pathlib.Path, required=True)
    parser.add_argument("--v1-runner", type=pathlib.Path, required=True)
    args = parser.parse_args()
    base, output = args.base, args.output
    if output.exists():
        raise RuntimeError("EXPLORATORY_OUTPUT_PATH_ALREADY_EXISTS")
    v1_failure = base / "autonomous-when-v1/frozen-model-v1/AUTONOMOUS_WHEN_V1_FROZEN_DEVELOPMENT_FIT_FAIL_CLOSED_RECEIPT.json"
    v1_log = base / "autonomous-when-v1/frozen-model-v1/development-fit-run.log"
    manifest = base / "autonomous-when-v1/development-closure-v1/autonomous-when-v1-development-manifest-20260928T143126Z.json"
    v2 = base / "fingerprint-v2-development-v1/frozen-fingerprint.json"
    v1.require_hash(v1_failure, "1e2b4d203b816a2c67483d5c2c3805a311f26fe70d7d2d38c1d9baf63d04b1d3", "V1_FAILURE_RECEIPT")
    v1.require_hash(v1_log, "233944b1ce6369490f054e75f15a92ad52b421a9da7b90ea7c0af61e3f9880c4", "V1_EXECUTION_LOG")
    v1.require_hash(manifest, "6bcb153ee88c600173bd6bc48f1588f977865add81a8622b5c3d7b77f24e83b7", "DEVELOPMENT_MANIFEST")
    v1.require_hash(v2, "7606a3a138591516f503e7f7b56501f1828b92947a2b38707bff166fbb233b64", "FROZEN_V2")
    if not args.helper.exists():
        raise RuntimeError("NATIVE_LOGISTIC_HELPER_MISSING")

    feature_set = list(json.loads(v2.read_text(encoding="utf-8"))["featureSet"])
    feature_names = ["when_top_score", "when_top_margin_or_zero", "when_log1p_universe_n", "when_persistence_capped_5"] + feature_set
    coordinate_offset = 0
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    segment_meta = []
    for segment in v1.segments(base):
        loaded, labels, rejected, duration = v1.load_segment(base, segment, feature_set, coordinate_offset)
        rows.extend(loaded)
        events.extend([{**label, "segment": segment.identifier} for label in labels])
        exclusions.update(rejected)
        start, end = v1.segment_bounds(segment)
        segment_meta.append({"id": segment.identifier, "startNs": start, "endNs": end, "durationNs": duration, "labels": len(labels)})
        coordinate_offset += duration
    if len(events) != 3552 or abs(coordinate_offset - 72 * 3_600_000_000_000) > 10_000:
        raise RuntimeError(f"SEALED_CORPUS_INTEGRITY_MISMATCH:labels={len(events)} duration={coordinate_offset}")
    rows.sort(key=lambda row: (row["coordinateNs"], row["tickNs"], row["topMint"], row["provenanceSha"]))
    table_hash = hashlib.sha256(b"".join(canonical(row) + b"\n" for row in rows)).hexdigest()
    starts = {item["id"]: item["startNs"] for item in segment_meta}
    offsets: dict[str, int] = {}
    running = 0
    for item in segment_meta:
        offsets[item["id"]] = running
        running += item["durationNs"]
    all_oos, per_fold_oos = oos_events(rows, events, offsets, starts)

    horizons = {f"{value}s": structural_by_horizon(rows, events, value) for value in HORIZONS_S}
    full_models, full_oos_rows, full_oos_scores = baseline_models(rows, args.helper)
    q_grid = threshold_grid(full_models, per_fold_oos, all_oos)
    best_recall = max(q_grid, key=lambda item: (item["recall"], item["precision"], -item["falseFiresPerHour"], -item["q"]))
    best_precision = max(q_grid, key=lambda item: (item["precision"], item["recall"], -item["falseFiresPerHour"], -item["q"]))
    closest = min(q_grid, key=lambda item: (abs(item["recall"] - .90), -item["recall"], -item["precision"], item["q"]))
    decomposition = failure_decomposition(full_oos_rows, all_oos, horizons["2s"], closest)
    oos_h2 = horizons["2s"]
    oos_structural = {"teacherEvents": len(all_oos), "eligibleTickEvents": 0, "top1Events": 0, "perfectRefractoryMatchedEvents": 0}
    oos_ids = {event_id(item) for item in all_oos}
    oos_perfect = set(oos_h2["perfectMatching"]["matchedEventIds"])
    for identifier in oos_ids:
        state = oos_h2["perEvent"].get(identifier, {"eligible": False, "top1": False})
        oos_structural["eligibleTickEvents"] += int(state["eligible"])
        oos_structural["top1Events"] += int(state["top1"])
        oos_structural["perfectRefractoryMatchedEvents"] += int(identifier in oos_perfect)
    for field in ("eligibleTickEvents", "top1Events", "perfectRefractoryMatchedEvents"):
        oos_structural[field + "Fraction"] = oos_structural[field] / len(all_oos) if all_oos else 0.0
    timing = timing_discovery(rows, events, feature_names)
    meta_models, meta_rows, meta_scores = baseline_models(rows, args.helper, [0, 1, 2, 3])
    meta_grid = threshold_grid(meta_models, per_fold_oos, all_oos)
    meta_best = max(meta_grid, key=lambda item: (item["recall"], item["precision"], -item["falseFiresPerHour"], -item["q"]))
    dependency_check = {name: False for name in ("numpy", "sklearn", "xgboost", "lightgbm")}
    for name in list(dependency_check):
        try:
            __import__(name)
            dependency_check[name] = True
        except Exception:
            pass

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "status": "POST_FAILURE_EXPLORATORY_DIAGNOSTICS_NOT_V1_CONFIRMATORY",
        "scope": {"sealedDevelopmentOnly": True, "developmentEndUtc": "2026-09-28T13:21:35.263793Z", "quarantineRowsUsed": 0, "validationRowsUsed": 0, "outcomesRead": 0, "pnlRead": 0, "submissions": 0},
        "integrity": {"v1FailureReceiptSha256": sha256_file(v1_failure), "v1ExecutionLogSha256": sha256_file(v1_log), "developmentManifestSha256": sha256_file(manifest), "frozenV2Sha256": sha256_file(v2), "developmentTableSha256": table_hash, "v1RunnerSha256": sha256_file(args.v1_runner), "forensicsRunnerSha256": sha256_file(pathlib.Path(__file__)), "nativeKernelBinarySha256": sha256_file(args.helper)},
        "sealedCorpus": {"segments": segment_meta, "eligibleTicks": len(rows), "positiveTicks": sum(int(row["label"]) for row in rows), "negativeTicks": len(rows) - sum(int(row["label"]) for row in rows), "teacherEvents": len(events), "eligibleTickExclusions": dict(sorted(exclusions.items())), "oosTeacherEvents": len(all_oos), "oosTicks": len(full_oos_rows), "oosPositiveTicks": sum(int(row["label"]) for row in full_oos_rows)},
        "v1FailureReconstruction": {"horizon2Structural": {key: value for key, value in horizons["2s"].items() if key not in ("perEvent", "perfectMatching")}, "horizon2OosStructural": oos_structural, "thresholdGrid": [{key: value for key, value in row.items() if key not in ("matchedEventIds", "matchesDetail")} for row in q_grid], "bestRecall": {key: value for key, value in best_recall.items() if key not in ("matchedEventIds", "matchesDetail")}, "bestPrecision": {key: value for key, value in best_precision.items() if key not in ("matchedEventIds", "matchesDetail")}, "closestTo90RecallGate": {key: value for key, value in closest.items() if key not in ("matchedEventIds", "matchesDetail")}, "structurallyAttainableAt90": oos_structural["perfectRefractoryMatchedEventsFraction"] >= .90},
        "failureDecompositionOos": {"counts": decomposition["counts"], "fractions": decomposition["fractions"]},
        "horizonFeasibility": {key: {field: value for field, value in item.items() if field not in ("perEvent", "perfectMatching")} for key, item in horizons.items()},
        "timingDiscovery": timing,
        "modelability": {"frozenV1FullLogistic": {"pooledTickAveragePrecision": v1.average_precision([int(row["label"]) for row in full_oos_rows], full_oos_scores), "bestRecallOperatingPoint": {key: value for key, value in best_recall.items() if key not in ("matchedEventIds", "matchesDetail")}}, "metaOnlyLogisticAblation": {"pooledTickAveragePrecision": v1.average_precision([int(row["label"]) for row in meta_rows], meta_scores), "bestRecallOperatingPoint": {key: value for key, value in meta_best.items() if key not in ("matchedEventIds", "matchesDetail")}}, "nonlinearAlternative": {"status": "NOT_RUN_NO_SUPPORTED_NUMERICAL_ML_DEPENDENCY", "dependencyAvailability": dependency_check}},
        "limits": {"candidateSetMembershipAndTop3": "V1 compact decision schema retains only Top-1 mint plus universe_n; exact non-Top-1 membership/rank was not sealed and is not reconstructed here.", "interpretation": "All results are post-failure exploratory development diagnostics and cannot validate or rehabilitate V1."},
        "v2Hypothesis": {"status": "NOT_FROZEN", "recommendation": "Do not declare V2 yet. Use the observed horizon ceilings and aligned pre-entry feature trajectories to pre-register a fresh development protocol. Any new target horizon or candidate-set rank semantics requires fresh development data because this corpus has been opened for discovery.", "freshDevelopmentRequired": True, "freshValidationRequired": True},
    }
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "AUTONOMOUS_WHEN_V2_EXPLORATORY_FORENSICS_REPORT.json"
    receipt_path = output / "AUTONOMOUS_WHEN_V2_EXPLORATORY_FORENSICS_RECEIPT.json"
    source_path = output / "source" / args.v1_runner.name
    source_hash = safe_copy_source(args.v1_runner, source_path)
    forensics_source_path = output / "source" / pathlib.Path(__file__).name
    forensics_source_hash = safe_copy_source(pathlib.Path(__file__), forensics_source_path)
    atomic_json(report_path, report)
    seal(report_path); seal(source_path); seal(forensics_source_path)
    receipt = {"schema": SCHEMA + "_RECEIPT", "createdAtUtc": report["generatedAtUtc"], "report": {"path": str(report_path), "sha256": sha256_file(report_path)}, "v1RunnerSource": {"path": str(source_path), "sha256": source_hash}, "forensicsRunnerSource": {"path": str(forensics_source_path), "sha256": forensics_source_hash}, "attestation": report["scope"], "v1Status": "FAILED_PRESERVED_UNCHANGED", "v2Status": "EXPLORATORY_NOT_FROZEN"}
    atomic_json(receipt_path, receipt)
    seal(receipt_path)
    print(json.dumps({"report": str(report_path), "reportSha256": sha256_file(report_path), "receipt": str(receipt_path), "receiptSha256": sha256_file(receipt_path)}, sort_keys=True))


if __name__ == "__main__":
    main()
