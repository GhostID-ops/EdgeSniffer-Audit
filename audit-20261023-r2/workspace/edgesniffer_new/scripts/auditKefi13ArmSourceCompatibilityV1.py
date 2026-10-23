#!/usr/bin/env python3
"""Audit the receive-only source before canonical 13-arm ingestion.

This check is intentionally outcome-blind.  Candidate metadata is not an
observation, and a healthy stream is not equivalent to a usable scientific
cohort.  The source must provide a post-freeze sealed raw window and the
canonical arm materializer must be able to bind each row to an exact venue,
decoder, evaluator, and frozen policy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_SOURCE_COMPATIBILITY_AUDIT_V1"
REQUIRED_CANONICAL_FIELDS = {
    "observation_id",
    "cohort_id",
    "arm_id",
    "venue",
    "frozen_policy_id",
    "registry_sha256",
    "policy_sha256",
    "finalized_slot",
    "state",
}
FORBIDDEN_OUTCOME_FIELDS = {
    "pnl",
    "roi",
    "profit_factor",
    "mfe",
    "mae",
    "drawdown",
    "exit",
    "outcome",
}


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def candidate_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("candidate row is not an object")
            rows.append(value)
    return rows


def run(snapshot: dict[str, Any], registry: dict[str, Any], manifest: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    health = snapshot.get("health") or {}
    candidates = snapshot.get("candidates") or []
    summary = snapshot.get("candidate_summary") or {}
    boundary = parse_utc(str(manifest["cohort_started_at_utc"]))
    latest = health.get("latestReceivedAtUtc")
    source_fresh = False
    if latest:
        source_fresh = (now - parse_utc(str(latest))).total_seconds() <= 120

    healthy_counters = {
        field: int(health.get(field, 0))
        for field in ("continuityGaps", "receiveErrors", "reconnects", "framesMetaIncomplete")
    }
    clean_transport = all(value == 0 for value in healthy_counters.values())
    schema_counts: dict[str, int] = dict(summary.get("schema_counts") or {})
    canonical_field_missing: dict[str, int] = {field: 0 for field in sorted(REQUIRED_CANONICAL_FIELDS)}
    forbidden_seen: dict[str, int] = {field: 0 for field in sorted(FORBIDDEN_OUTCOME_FIELDS)}
    for row in candidates:
        schema = str(row.get("schema", ""))
        schema_counts[schema] = schema_counts.get(schema, 0) + 1
        for field in canonical_field_missing:
            if field not in row:
                canonical_field_missing[field] += 1
        for field in forbidden_seen:
            if field in row:
                forbidden_seen[field] += 1

    if not candidates and summary:
        for field, count in (summary.get("missing_field_counts") or {}).items():
            if field in canonical_field_missing:
                canonical_field_missing[field] = int(count)
        for field, count in (summary.get("forbidden_outcome_field_counts") or {}).items():
            if field in forbidden_seen:
                forbidden_seen[field] = int(count)
    candidate_count = len(candidates) if candidates else int(summary.get("rows", 0))
    latest_window_at = snapshot.get("latest_persisted_window_at_utc")
    post_freeze_window = bool(latest_window_at and parse_utc(str(latest_window_at)) >= boundary)
    raw_truth = bool(snapshot.get("latest_persisted_window_raw_truth"))
    complete_window = bool(snapshot.get("latest_persisted_window_complete"))
    canonical_rows = int(snapshot.get("canonical_observation_rows", 0))
    fresh_canonical_rows = int(snapshot.get("fresh_canonical_observation_rows", 0))
    blockers: list[str] = []
    if not source_fresh:
        blockers.append("SOURCE_STALE")
    if not clean_transport:
        blockers.append("TRANSPORT_NOT_CLEAN")
    if not post_freeze_window:
        blockers.append("NO_POST_FREEZE_SEALED_WINDOW")
    if not raw_truth:
        blockers.append("NO_RAW_TRUTH_WINDOW")
    if not complete_window:
        blockers.append("LATEST_WINDOW_NOT_COMPLETE")
    if candidate_count == 0:
        blockers.append("NO_CANDIDATE_METADATA")
    if candidate_count and any(canonical_field_missing.values()):
        blockers.append("CANDIDATES_METADATA_ONLY_NOT_CANONICAL_OBSERVATIONS")
    if fresh_canonical_rows == 0:
        blockers.append("NO_FRESH_CANONICAL_ARM_ROWS")

    return {
        "schema": SCHEMA,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "outcome_blind": True,
        "registry_arm_count": len(registry.get("arms", [])),
        "cohort_id": manifest.get("cohort_id"),
        "cohort_started_at_utc": manifest.get("cohort_started_at_utc"),
        "source": {
            "transport_clean": clean_transport,
            "source_fresh": source_fresh,
            "health_counters": healthy_counters,
            "candidate_rows": candidate_count,
            "candidate_schema_counts": schema_counts,
            "canonical_observation_rows": canonical_rows,
            "fresh_canonical_observation_rows": fresh_canonical_rows,
            "latest_persisted_window_at_utc": latest_window_at,
            "latest_persisted_window_post_freeze": post_freeze_window,
            "latest_persisted_window_raw_truth": raw_truth,
            "latest_persisted_window_complete": complete_window,
        },
        "metadata_compatibility": {
            "required_canonical_fields": sorted(REQUIRED_CANONICAL_FIELDS),
            "missing_field_counts": canonical_field_missing,
            "forbidden_outcome_field_counts": forbidden_seen,
        },
        "blockers": blockers,
        "canonical_ingest_ready": not blockers,
        "status": "READY_FOR_CANONICAL_INGEST" if not blockers else "BLOCKED_SOURCE_OR_MATERIALIZATION",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    reference_now = parse_utc(args.now) if args.now else None
    result = run(snapshot, registry, manifest, reference_now)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, sort_keys=True))


if __name__ == "__main__":
    main()
