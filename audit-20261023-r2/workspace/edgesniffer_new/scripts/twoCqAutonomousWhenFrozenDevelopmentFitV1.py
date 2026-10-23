#!/usr/bin/env python3
"""Run the sealed AUTONOMOUS_WHEN_V1 development protocol exactly once.

This runner deliberately accepts only the certified development intervals and
creates immutable output artifacts. It has no path for quarantine, validation,
economic outcomes, or live-service writes.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import shutil
import sqlite3
import stat
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

SCHEMA = "AUTONOMOUS_WHEN_V1_FROZEN_DEVELOPMENT_FIT_V1"
HORIZON_NS = 2_000_000_000
EMBARGO_NS = 1_000_000_000
LAMBDA = 0.03
ITERATIONS = 800
RATE_BASE = 0.08
RATE_DECAY = 300.0


@dataclass(frozen=True)
class Segment:
    identifier: str
    root_name: str
    start_utc: str
    end_utc: str | None
    duration_hours: str | None
    expected_labels: int


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        temporary = pathlib.Path(stream.name)
    os.replace(temporary, path)


def seal(path: pathlib.Path) -> str:
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return sha256_file(path)


def parse_ns(value: str) -> int:
    if not value.endswith("Z"):
        raise RuntimeError(f"UTC_Z_REQUIRED:{value}")
    date_part, time_part = value[:-1].split("T", 1)
    if "." in time_part:
        clock, fraction = time_part.split(".", 1)
    else:
        clock, fraction = time_part, ""
    base = dt.datetime.fromisoformat(f"{date_part}T{clock}+00:00")
    fraction_ns = int((fraction + "0" * 9)[:9])
    return int(base.timestamp()) * 1_000_000_000 + fraction_ns


def add_hours(start_ns: int, hours: str) -> int:
    value = Decimal(hours) * Decimal(3600) * Decimal(1_000_000_000)
    return start_ns + int(value.to_integral_value(rounding=ROUND_HALF_UP))


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise RuntimeError("EMPTY_QUANTILE")
    position = (len(ordered) - 1) * q
    left = int(math.floor(position))
    right = min(left + 1, len(ordered) - 1)
    weight = position - left
    return float(ordered[left] * (1.0 - weight) + ordered[right] * weight)


def write_fit_input(path: pathlib.Path, x: list[list[float]], y: list[int]) -> None:
    with path.open("wb") as stream:
        stream.write(b"AWHFIT1\0")
        stream.write(struct.pack("<QQ", len(x), len(x[0])))
        for row in x:
            stream.write(struct.pack(f"<{len(row)}d", *row))
        stream.write(bytes(y))


def write_predict_input(path: pathlib.Path, x: list[list[float]]) -> None:
    with path.open("wb") as stream:
        stream.write(b"AWHPRE1\0")
        stream.write(struct.pack("<QQ", len(x), len(x[0])))
        for row in x:
            stream.write(struct.pack(f"<{len(row)}d", *row))


def read_model(path: pathlib.Path) -> tuple[list[float], list[float], float, list[float]]:
    with path.open("rb") as stream:
        if stream.read(8)[:7] != b"AWHMOD1":
            raise RuntimeError("NATIVE_MODEL_MAGIC_MISMATCH")
        (width,) = struct.unpack("<Q", stream.read(8))
        means = list(struct.unpack(f"<{width}d", stream.read(width * 8)))
        scales = list(struct.unpack(f"<{width}d", stream.read(width * 8)))
        (intercept,) = struct.unpack("<d", stream.read(8))
        coefficients = list(struct.unpack(f"<{width}d", stream.read(width * 8)))
    return means, scales, intercept, coefficients


def read_scores(path: pathlib.Path) -> list[float]:
    with path.open("rb") as stream:
        if stream.read(8)[:7] != b"AWHPRE1":
            raise RuntimeError("NATIVE_SCORE_MAGIC_MISMATCH")
        (count,) = struct.unpack("<Q", stream.read(8))
        return list(struct.unpack(f"<{count}d", stream.read(count * 8)))


def native_fit_predict(helper: pathlib.Path, work: pathlib.Path, tag: str, x_train: list[list[float]], y_train: list[int], x_test: list[list[float]]) -> tuple[list[float], list[float], float, list[float], list[float], list[float]]:
    fit_input = work / f"{tag}.fit.bin"
    model = work / f"{tag}.model.bin"
    test_input = work / f"{tag}.test.bin"
    test_scores = work / f"{tag}.test.scores.bin"
    train_input = work / f"{tag}.train.bin"
    train_scores = work / f"{tag}.train.scores.bin"
    write_fit_input(fit_input, x_train, y_train)
    subprocess.run([str(helper), "fit", str(fit_input), str(model)], check=True, capture_output=True, text=True)
    write_predict_input(test_input, x_test)
    write_predict_input(train_input, x_train)
    subprocess.run([str(helper), "predict", str(model), str(test_input), str(test_scores)], check=True, capture_output=True, text=True)
    subprocess.run([str(helper), "predict", str(model), str(train_input), str(train_scores)], check=True, capture_output=True, text=True)
    means, scales, intercept, coefficients = read_model(model)
    return means, scales, intercept, coefficients, read_scores(test_scores), read_scores(train_scores)


def average_precision(y: list[int], scores: list[float]) -> float | None:
    positives = sum(y)
    if positives == 0:
        return None
    labels = [label for _, _, label in sorted((-score, index, label) for index, (score, label) in enumerate(zip(scores, y)))]
    seen = 0
    total = 0.0
    for index, label in enumerate(labels, 1):
        if label:
            seen += 1
            total += seen / index
    return total / positives


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return quantile(values, q)


def utc_from_ns(value: int) -> str:
    seconds, nanos = divmod(value, 1_000_000_000)
    stamp = dt.datetime.fromtimestamp(seconds, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{nanos:09d}Z"


def segments(base: pathlib.Path) -> list[Segment]:
    # Every interval and label count is copied from the sealed manifest/receipts.
    return [
        Segment("SEGMENT_A", "autonomous-when-v1-run-20260925T053500Z", "2026-09-25T05:34:48.219247Z", "2026-09-26T01:59:59.850971982Z", None, 1175),
        # The formal closure's authoritative A+B aggregate is 34.413h. Segment A
        # has an exact sealed end, so B is the conservative residual. This omits
        # the 17.718-second tail present in an earlier B operational receipt.
        Segment("SEGMENT_B", "autonomous-when-v1-segment-b-20260926T0459Z", "2026-09-26T05:00:06.105855Z", None, "13.99310229895111", 538),
        Segment("SEGMENT_C", "autonomous-when-v1-segment-c-20260926T220421Z", "2026-09-26T22:06:27.031593Z", None, "3.51507", 168),
        Segment("SEGMENT_050", "autonomous-when-v1-segment-auto-050-20260927T030742Z", "2026-09-27T03:07:42.289256Z", None, "11.27995266527778", 231),
        Segment("SEGMENT_051_PREFIX", "autonomous-when-v1-segment-auto-051-20260927T142554Z", "2026-09-27T14:25:54.459189Z", "2026-09-27T14:59:51.595829Z", None, 10),
        Segment("SEGMENT_052_PREFIX", "autonomous-when-v1-segment-auto-052-20260927T150132Z", "2026-09-27T15:01:32.684099Z", "2026-09-27T15:09:28.175792Z", None, 6),
        Segment("SEGMENT_053_PREFIX", "autonomous-when-v1-segment-auto-053-20260927T151051Z", "2026-09-27T15:10:51.716047Z", "2026-09-27T16:22:22.672818Z", None, 92),
        Segment("SEGMENT_054", "autonomous-when-v1-segment-auto-054-20260927T162313Z", "2026-09-27T16:23:13.740397Z", None, "0.4475579588888889", 27),
        Segment("SEGMENT_055_PREFIX", "autonomous-when-v1-segment-auto-055-20260927T165035Z", "2026-09-27T16:50:35.652506Z", "2026-09-27T22:40:41.767491Z", None, 480),
        Segment("SEGMENT_056_PREFIX", "autonomous-when-v1-segment-auto-056-20260927T224232Z", "2026-09-27T22:42:32.442989Z", "2026-09-27T23:59:42.162729Z", None, 100),
        Segment("SEGMENT_057_PREFIX", "autonomous-when-v1-segment-auto-057-20260928T000134Z", "2026-09-28T00:01:34.773869Z", "2026-09-28T13:21:35.263793Z", None, 725),
    ]


def segment_bounds(segment: Segment) -> tuple[int, int]:
    start = parse_ns(segment.start_utc)
    end = parse_ns(segment.end_utc) if segment.end_utc else add_hours(start, str(segment.duration_hours))
    return start, end


def require_hash(path: pathlib.Path, expected: str, name: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(f"{name}_HASH_MISMATCH:{actual}")


def load_segment(
    base: pathlib.Path, segment: Segment, feature_names: list[str], coordinate_offset: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], int]:
    start, end = segment_bounds(segment)
    database = base / segment.root_name / "autonomous-when-v1.sqlite3"
    if not database.exists():
        raise RuntimeError(f"SEGMENT_DATABASE_MISSING:{segment.identifier}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    try:
        connection.execute("PRAGMA query_only=ON")
        labels = [
            {"signature": str(row[0]), "mint": str(row[1]), "receiptNs": int(row[2])}
            for row in connection.execute(
                "SELECT sig,mint,receipt_ns FROM teacher_labels WHERE finality='FINAL' AND receipt_ns>=? AND receipt_ns<=? ORDER BY receipt_ns,sig,mint",
                (start, end),
            )
        ]
        if len(labels) != segment.expected_labels:
            raise RuntimeError(f"SEGMENT_LABEL_COUNT_MISMATCH:{segment.identifier}:{len(labels)}:{segment.expected_labels}")
        by_mint: dict[str, list[dict[str, Any]]] = {}
        for label in labels:
            by_mint.setdefault(label["mint"], []).append(label)
        decisions = []
        exclusions: dict[str, int] = {}
        for row in connection.execute(
            "SELECT tick_ns,top_mint,top_score,second_score,margin,universe_n,persistence,features_json,provenance_sha FROM decisions WHERE finality='FINAL' AND tick_ns>=? AND tick_ns<=? ORDER BY tick_ns,provenance_sha",
            (start, end),
        ):
            tick, mint, top_score, second_score, margin, universe_n, persistence, features_json, provenance = row
            tick = int(tick)
            reason = None
            if tick + HORIZON_NS > end:
                reason = "HORIZON_CROSSES_CERTIFIED_SEGMENT_END"
            elif not mint or top_score is None or universe_n is None or persistence is None:
                reason = "DECISION_FIELD_MISSING"
            elif not math.isfinite(float(top_score)) or int(universe_n) < 1:
                reason = "DECISION_FIELD_INVALID"
            elif second_score is not None and float(top_score) <= float(second_score):
                reason = "TOP_SCORE_NOT_UNIQUE"
            else:
                try:
                    raw = json.loads(features_json)
                    base_features = [float(raw[name]) for name in feature_names]
                    if any(isinstance(raw[name], bool) or not math.isfinite(value) for name, value in zip(feature_names, base_features)):
                        raise ValueError("nonfinite")
                    margin_value = 0.0 if second_score is None else float(top_score) - float(second_score)
                    values = [float(top_score), margin_value, math.log1p(int(universe_n)), float(min(int(persistence), 5))] + base_features
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("derived")
                except Exception:
                    reason = "FEATURE_MISSING_OR_INVALID"
            if reason:
                exclusions[reason] = exclusions.get(reason, 0) + 1
                continue
            candidates = by_mint.get(str(mint), [])
            receipts = [entry["receiptNs"] for entry in candidates]
            index = bisect.bisect_right(receipts, tick)
            positive_event = candidates[index] if index < len(candidates) and candidates[index]["receiptNs"] <= tick + HORIZON_NS else None
            decisions.append({
                "segment": segment.identifier,
                "tickNs": tick,
                "coordinateNs": coordinate_offset + tick - start,
                "topMint": str(mint),
                "provenanceSha": str(provenance),
                "features": values,
                "label": 1 if positive_event else 0,
                "positiveEvent": None if positive_event is None else f"{positive_event['signature']}:{positive_event['mint']}",
            })
    finally:
        connection.close()
    return decisions, labels, exclusions, end - start


def match_events(rows: list[dict[str, Any]], scores: list[float], threshold: float, all_events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_rows = sorted(zip(rows, scores), key=lambda item: (item[0]["tickNs"], item[0]["topMint"], item[0]["provenanceSha"]))
    refractory_end = -1
    fires: list[dict[str, Any]] = []
    for row, value in ordered_rows:
        if value < threshold or row["tickNs"] <= refractory_end:
            continue
        fires.append({"tickNs": row["tickNs"], "mint": row["topMint"], "score": value, "segment": row["segment"]})
        refractory_end = row["tickNs"] + HORIZON_NS
    by_mint: dict[str, list[dict[str, Any]]] = {}
    for event in all_events:
        by_mint.setdefault(event["mint"], []).append(event)
    used: set[str] = set()
    matches = []
    for fire in fires:
        for event in by_mint.get(fire["mint"], []):
            event_id = f"{event['signature']}:{event['mint']}"
            if event_id in used or event["segment"] != fire["segment"]:
                continue
            if fire["tickNs"] < event["receiptNs"] <= fire["tickNs"] + HORIZON_NS:
                used.add(event_id)
                matches.append({"fire": fire, "event": event, "leadMs": (event["receiptNs"] - fire["tickNs"]) / 1_000_000})
                break
    return {
        "fires": fires,
        "matches": matches,
        "matchedEventIds": used,
        "falseFires": len(fires) - len(matches),
    }


def fold_rows(rows: list[dict[str, Any]], start_hours: int, end_hours: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train_boundary = start_hours * 3600 * 1_000_000_000
    test_end = end_hours * 3600 * 1_000_000_000
    train = [row for row in rows if row["coordinateNs"] < train_boundary - HORIZON_NS]
    test = [row for row in rows if row["coordinateNs"] > train_boundary + EMBARGO_NS and row["coordinateNs"] < test_end - HORIZON_NS]
    return train, test


def arrays(rows: list[dict[str, Any]]) -> tuple[list[list[float]], list[int]]:
    return [row["features"] for row in rows], [int(row["label"]) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--helper", type=pathlib.Path, required=True)
    parser.add_argument("--helper-source", type=pathlib.Path, required=True)
    args = parser.parse_args()

    base = args.base
    output = args.output
    protocol_path = base / "autonomous-when-v1/model-selection-protocol-v1/AUTONOMOUS_WHEN_V1_MODEL_SELECTION_PROTOCOL.json"
    manifest_path = base / "autonomous-when-v1/development-closure-v1/autonomous-when-v1-development-manifest-20260928T143126Z.json"
    receipt_path = base / "autonomous-when-v1/development-closure-v1/autonomous-when-v1-development-closure-receipt-20260928T143126Z.json"
    v2_path = base / "fingerprint-v2-development-v1/frozen-fingerprint.json"
    require_hash(protocol_path, "a27bbb5c244e7bdeb1fa59ecc5bcfb1ea451aaadb16a90feb426fbbd847ac081", "PROTOCOL")
    require_hash(manifest_path, "6bcb153ee88c600173bd6bc48f1588f977865add81a8622b5c3d7b77f24e83b7", "MANIFEST")
    require_hash(receipt_path, "b5c61b004b052d8fe720cc54aa4b93c036c04018298d558f667eb6a548e50d3e", "CLOSURE_RECEIPT")
    require_hash(v2_path, "7606a3a138591516f503e7f7b56501f1828b92947a2b38707bff166fbb233b64", "V2")
    if (output / "AUTONOMOUS_WHEN_V1_FROZEN_MODEL.json").exists() or (output / "AUTONOMOUS_WHEN_V1_FROZEN_MODEL_FREEZE_RECEIPT.json").exists():
        raise RuntimeError("IMMUTABLE_FINAL_OUTPUT_ALREADY_EXISTS")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["protocolStatus"] != "FROZEN_PRE_ANALYSIS":
        raise RuntimeError("PROTOCOL_STATUS_INVALID")
    feature_names = list(json.loads(v2_path.read_text(encoding="utf-8"))["featureSet"])
    expected_names = list(protocol["featureContract"]["featureNamesInExactOrder"])
    if expected_names != ["when_top_score", "when_top_margin_or_zero", "when_log1p_universe_n", "when_persistence_capped_5"] + feature_names:
        raise RuntimeError("FEATURE_CONTRACT_MISMATCH")
    if not args.helper.exists() or not args.helper_source.exists():
        raise RuntimeError("NATIVE_HELPER_MISSING")

    coordinate_offset = 0
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    exclusions: dict[str, int] = {}
    source_segments = []
    for segment in segments(base):
        loaded, labels, rejected, duration = load_segment(base, segment, feature_names, coordinate_offset)
        rows.extend(loaded)
        events.extend([{**label, "segment": segment.identifier} for label in labels])
        for reason, count in rejected.items():
            exclusions[reason] = exclusions.get(reason, 0) + count
        start, end = segment_bounds(segment)
        source_segments.append({"id": segment.identifier, "root": str(base / segment.root_name), "startUtc": utc_from_ns(start), "endUtc": utc_from_ns(end), "durationNs": duration, "labels": len(labels)})
        coordinate_offset += duration
    if len(events) != 3552:
        raise RuntimeError(f"TOTAL_LABEL_COUNT_MISMATCH:{len(events)}")
    if abs(coordinate_offset - 72 * 3600 * 1_000_000_000) > 10_000:
        raise RuntimeError(f"CERTIFIED_TIME_MISMATCH:{coordinate_offset}")
    rows.sort(key=lambda row: (row["coordinateNs"], row["tickNs"], row["topMint"], row["provenanceSha"]))
    table_hasher = hashlib.sha256()
    for row in rows:
        table_hasher.update(canonical(row) + b"\n")
    table_hash = table_hasher.hexdigest()
    positive_events = {row["positiveEvent"] for row in rows if row["positiveEvent"]}

    fold_specs = [("F1", 36, 45), ("F2", 45, 54), ("F3", 54, 63), ("F4", 63, 72)]
    fold_results = []
    oos_rows: list[dict[str, Any]] = []
    oos_scores: list[float] = []
    fold_models = []
    with tempfile.TemporaryDirectory(prefix="autonomous-when-fit-v1-") as temporary_text:
        temporary = pathlib.Path(temporary_text)
        for identifier, start_hour, end_hour in fold_specs:
            train, test = fold_rows(rows, start_hour, end_hour)
            x_train, y_train = arrays(train)
            x_test, y_test = arrays(test)
            means, scales, intercept, coefficients, scores, train_scores = native_fit_predict(args.helper, temporary, identifier, x_train, y_train, x_test)
            positive_scores = [value for value, label in zip(train_scores, y_train) if label]
            fold_models.append({"id": identifier, "means": means, "scales": scales, "intercept": intercept, "coefficients": coefficients, "positiveScores": positive_scores, "testRows": test, "testScores": scores, "trainN": len(train), "testN": len(test), "trainPositive": sum(y_train), "testPositive": sum(y_test)})
            oos_rows.extend(test)
            oos_scores.extend(scores)
    q_values = [round(value / 100, 2) for value in range(0, 51)]
    selection = []
    segment_starts = {item["id"]: parse_ns(item["startUtc"]) for item in source_segments}
    segment_offsets = {}
    cumulative = 0
    for item in source_segments:
        segment_offsets[item["id"]] = cumulative
        cumulative += int(item["durationNs"])
    events_with_coordinate = [{
        **event,
        "coordinateNs": segment_offsets[event["segment"]] + event["receiptNs"] - segment_starts[event["segment"]],
    } for event in events]
    # Evaluation events are restricted to the mechanically defined OOS ranges.
    # The one-second embargo and two-second horizon purge exclude both ticks and
    # labels near fold boundaries, so no event can leak between folds.
    oos_events_by_fold: dict[str, list[dict[str, Any]]] = {}
    all_oos_events = []
    for identifier, start_hour, end_hour in fold_specs:
        lower = start_hour * 3600 * 1_000_000_000 + EMBARGO_NS
        upper = end_hour * 3600 * 1_000_000_000 - HORIZON_NS
        fold_events = [event for event in events_with_coordinate if lower < event["coordinateNs"] < upper]
        oos_events_by_fold[identifier] = fold_events
        all_oos_events.extend(fold_events)
    unique_oos_events = {f"{event['signature']}:{event['mint']}": event for event in all_oos_events}
    all_oos_events = sorted(unique_oos_events.values(), key=lambda event: (event["receiptNs"], event["signature"], event["mint"]))
    for q in q_values:
        per_fold_thresholds = {item["id"]: quantile(item["positiveScores"], q) for item in fold_models}
        # Refractory semantics reset at a test-fold boundary because the 3s purge
        # makes cross-fold firing impossible under the 2s horizon.
        fires = []
        matches = []
        used = set()
        cursor = 0
        for item in fold_models:
            local = match_events(item["testRows"], item["testScores"], per_fold_thresholds[item["id"]], oos_events_by_fold[item["id"]])
            fires.extend(local["fires"])
            for entry in local["matches"]:
                event_id = f"{entry['event']['signature']}:{entry['event']['mint']}"
                if event_id not in used:
                    used.add(event_id); matches.append(entry)
            cursor += item["testN"]
        # A teacher event is eligible for OOS recall only if it falls inside one
        # of the mechanically defined test ranges.
        oos_event_ids = {f"{event['signature']}:{event['mint']}" for event in all_oos_events}
        matched_ids = {f"{entry['event']['signature']}:{entry['event']['mint']}" for entry in matches}
        false_fires = len(fires) - len(matches)
        hours = sum((end - start) for _, start, end in fold_specs)
        precision = len(matches) / len(fires) if fires else 0.0
        recall = len(matched_ids) / len(oos_event_ids) if oos_event_ids else 0.0
        selection.append({"q": q, "thresholdsByFold": per_fold_thresholds, "fires": len(fires), "matches": len(matches), "falseFires": false_fires, "precision": precision, "recall": recall, "falseFiresPerHour": false_fires / hours, "firesPerHour": len(fires) / hours})
    eligible = [row for row in selection if row["recall"] >= 0.90]
    if not eligible:
        raise RuntimeError("NO_DEPLOYABLE_THRESHOLD")
    selected = max(eligible, key=lambda row: (row["precision"], -row["falseFiresPerHour"], -row["firesPerHour"], row["q"]))

    chosen_fold_reports = []
    all_fires = []
    all_matches = []
    for item in fold_models:
        threshold = selected["thresholdsByFold"][item["id"]]
        local_events = oos_events_by_fold[item["id"]]
        local = match_events(item["testRows"], item["testScores"], threshold, local_events)
        all_fires.extend(local["fires"]); all_matches.extend(local["matches"])
        local_event_ids = {f"{event['signature']}:{event['mint']}" for event in local_events}
        local_matched = {f"{entry['event']['signature']}:{entry['event']['mint']}" for entry in local["matches"]}
        y_test = [int(row["label"]) for row in item["testRows"]]
        chosen_fold_reports.append({"id": item["id"], "trainTicks": item["trainN"], "trainPositiveTicks": item["trainPositive"], "testTicks": item["testN"], "testPositiveTicks": item["testPositive"], "threshold": threshold, "fires": len(local["fires"]), "matches": len(local["matches"]), "falseFires": local["falseFires"], "precision": len(local["matches"]) / len(local["fires"]) if local["fires"] else 0.0, "teacherRecall": len(local_matched) / len(local_event_ids) if local_event_ids else None, "averagePrecision": average_precision(y_test, item["testScores"])})
    matched_ids = {f"{entry['event']['signature']}:{entry['event']['mint']}" for entry in all_matches}
    lead_values = [entry["leadMs"] for entry in all_matches]
    y_oos = [int(row["label"]) for row in oos_rows]

    x_all, y_all = arrays(rows)
    with tempfile.TemporaryDirectory(prefix="autonomous-when-final-fit-v1-") as temporary_text:
        temporary = pathlib.Path(temporary_text)
        means, scales, intercept, coefficients, final_scores, _ = native_fit_predict(args.helper, temporary, "FINAL", x_all, y_all, x_all)
    final_threshold = quantile([value for value, label in zip(final_scores, y_all) if label], selected["q"])
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": now,
        "developmentOnly": True,
        "developmentTable": {"eligibleTicks": len(rows), "positiveTicks": sum(y_all), "negativeTicks": len(rows) - sum(y_all), "qualifyingTeacherEvents": len(events), "teacherEventsWithEligiblePositiveTick": len(positive_events), "excludedTicksByReason": exclusions, "contentSha256": table_hash, "noCrossGapWindows": True, "postDevelopmentRowsUsed": 0, "validationRowsUsed": 0},
        "folds": chosen_fold_reports,
        "thresholdSelection": {"selectedQ": selected["q"], "selectedOosThresholdsByFold": selected["thresholdsByFold"], "grid": selection, "selected": selected},
        "developmentMetrics": {"eventPrecision": len(all_matches) / len(all_fires) if all_fires else 0.0, "teacherRecall": len(matched_ids) / len(all_oos_events) if all_oos_events else 0.0, "falseFiresPerHour": (len(all_fires) - len(all_matches)) / 36.0, "firesPerHour": len(all_fires) / 36.0, "leadTimeMs": {"p10": percentile(lead_values, .10), "p50": percentile(lead_values, .50), "p90": percentile(lead_values, .90)}, "tickAveragePrecision": average_precision(y_oos, oos_scores), "jointWhenWhichRecall": len(matched_ids) / len(all_oos_events) if all_oos_events else 0.0, "matchedTeacherEvents": len(matched_ids), "unmatchedTeacherEvents": len(all_oos_events) - len(matched_ids), "totalFires": len(all_fires), "falseFires": len(all_fires) - len(all_matches), "oosEligibleTeacherEvents": len(all_oos_events)},
        "integrity": {"protocolSha256": sha256_file(protocol_path), "developmentManifestSha256": sha256_file(manifest_path), "closureReceiptSha256": sha256_file(receipt_path), "frozenV2Sha256": sha256_file(v2_path), "outcomesRead": 0, "pnlRead": 0, "quarantineRowsUsed": 0, "validationRowsUsed": 0, "submissions": 0},
    }
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "AUTONOMOUS_WHEN_V1_FROZEN_MODEL.json"
    report_path = output / "AUTONOMOUS_WHEN_V1_DEVELOPMENT_MODELING_REPORT.json"
    source_path = output / "source" / args.source.name
    helper_source_path = output / "source" / args.helper_source.name
    source_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source, source_path)
    shutil.copyfile(args.helper_source, helper_source_path)
    source_hash = sha256_file(source_path)
    helper_source_hash = sha256_file(helper_source_path)
    model = {
        "schema": "AUTONOMOUS_WHEN_V1_FROZEN_MODEL_V1",
        "frozenAtUtc": now,
        "developmentOnly": True,
        "developmentManifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path), "endUtc": "2026-09-28T13:21:35.263793Z"},
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "fittingTable": {"contentSha256": table_hash, "eligibleTicks": len(rows), "positiveTicks": sum(y_all), "negativeTicks": len(rows) - sum(y_all)},
        "modelFamily": "L2_LOGISTIC_FULL_CAUSAL_TICK_V1",
        "featureNames": expected_names,
        "preprocessing": {"means": means, "scales": scales, "zeroScaleReplacement": 1.0},
        "model": {"intercept": intercept, "coefficients": coefficients, "lambda": LAMBDA, "iterations": ITERATIONS, "learningRate": "0.08 / (1 + iteration / 300)"},
        "threshold": {"selectedQ": selected["q"], "numericThreshold": final_threshold, "oosThresholdsByFold": selected["thresholdsByFold"], "rule": protocol["thresholdSelection"]},
        "decisionSemantics": protocol["fireSemantics"] | {"target": protocol["targetLabel"], "eventMatching": protocol["eventMatching"]},
        "developmentMetrics": report["developmentMetrics"],
        "code": {"fitRunnerSource": str(source_path), "fitRunnerSourceSha256": source_hash, "nativeKernelSource": str(helper_source_path), "nativeKernelSourceSha256": helper_source_hash, "nativeKernelBinarySha256": sha256_file(args.helper)},
        "immutableDuringValidation": True,
        "outcomesRead": 0, "pnlRead": 0, "quarantineRowsUsed": 0, "validationRowsUsed": 0, "submissions": 0,
    }
    atomic_json(report_path, report)
    atomic_json(model_path, model)
    for path in (report_path, model_path, source_path):
        seal(path)
    freeze_receipt = {
        "schema": "AUTONOMOUS_WHEN_V1_FROZEN_MODEL_FREEZE_RECEIPT_V1",
        "createdAtUtc": now,
        "model": {"path": str(model_path), "sha256": sha256_file(model_path)},
        "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "developmentManifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "fitRunner": {"path": str(source_path), "sha256": source_hash},
        "nativeKernel": {"sourcePath": str(helper_source_path), "sourceSha256": helper_source_hash, "binaryPath": str(args.helper), "binarySha256": sha256_file(args.helper)},
        "attestation": {"developmentOnly": True, "quarantineRowsUsed": 0, "validationRowsUsed": 0, "outcomesRead": 0, "pnlRead": 0, "submissions": 0},
        "status": "FROZEN",
    }
    receipt_out = output / "AUTONOMOUS_WHEN_V1_FROZEN_MODEL_FREEZE_RECEIPT.json"
    atomic_json(receipt_out, freeze_receipt)
    seal(receipt_out)
    print(json.dumps({"model": str(model_path), "modelSha256": sha256_file(model_path), "report": str(report_path), "reportSha256": sha256_file(report_path), "receipt": str(receipt_out), "receiptSha256": sha256_file(receipt_out)}, sort_keys=True))


if __name__ == "__main__":
    main()
