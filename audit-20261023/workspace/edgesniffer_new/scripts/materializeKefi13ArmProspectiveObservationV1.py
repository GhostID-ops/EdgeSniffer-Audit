#!/usr/bin/env python3
"""Convert sealed, outcome-blind decoder rows into canonical arm observations.

The input is a materialized raw window plus an independently recorded venue
resolution.  This script does not read PnL, exits, controls, or any other
economic outcome.  It only binds a finalized event to a real arm and frozen
policy so the strict ingest boundary can append it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_PROSPECTIVE_OBSERVATION_MATERIALIZER_V1"
OBS_SCHEMA = "KEFI_13_ARM_OBSERVATION_V1"


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def captured_at(ns: int) -> str:
    return dt.datetime.fromtimestamp(ns / 1_000_000_000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def run(rows: list[dict[str, Any]], receipt: dict[str, Any], manifest: dict[str, Any], registry: dict[str, Any], policies: dict[str, Any], resolutions: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry_by_id = {row["id"]: row for row in registry["arms"]}
    policy_by_id = {row["arm_id"]: row for row in policies["policies"]}
    manifest_by_id = {row["arm_id"]: row for row in manifest["partitions"]}
    output: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    boundary = parse_utc(str(manifest["cohort_started_at_utc"]))
    for index, row in enumerate(rows, 1):
        if not row.get("is_tracked_teacher"):
            continue
        row_captured_at = captured_at(int(row["received_at_unix_ns"]))
        if parse_utc(row_captured_at) < boundary:
            rejected.append({"line": index, "reason": "OBSERVATION_PRE_COHORT_BOUNDARY", "captured_at_utc": row_captured_at})
            continue
        event_signature = str(row.get("candidate_signature", ""))
        resolution = resolutions.get(event_signature)
        if not resolution:
            rejected.append({"line": index, "reason": "VENUE_RESOLUTION_MISSING", "event_signature": event_signature})
            continue
        arm_id = str(resolution.get("arm_id", ""))
        arm = registry_by_id.get(arm_id)
        partition = manifest_by_id.get(arm_id)
        policy = policy_by_id.get(arm_id)
        if not arm or not partition or not policy:
            rejected.append({"line": index, "reason": "ARM_CONTRACT_MISSING", "arm_id": arm_id})
            continue
        if resolution.get("program_id") != "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA":
            rejected.append({"line": index, "reason": "UNEXPECTED_PROGRAM_ID", "event_signature": event_signature})
            continue
        if not resolution.get("direct_invocation"):
            rejected.append({"line": index, "reason": "DIRECT_VENUE_NOT_PROVEN", "event_signature": event_signature})
            continue
        state = {
            "raw_evidence": {
                "window_id": receipt["window_id"],
                "window_manifest_sha256": receipt["window_manifest_sha256"],
                "source_frame_sha256": row["source_frame_sha256"],
                "source_partition": row["source_partition"],
                "event_index": row["event_index"],
                "event_signature": event_signature,
                "venue_resolution": resolution,
            },
            "decoded_event": row,
            "exact_transition_validated": bool(row.get("exact_transition_validated")),
        }
        # Window IDs are deliberately excluded: overlapping pre-entry windows
        # must resolve to one observation for the same immutable event.
        identity = {"event_signature": event_signature, "source_frame_sha256": row["source_frame_sha256"], "event_index": row["event_index"]}
        output.append({
            "schema": OBS_SCHEMA,
            "observation_id": "obs-" + digest(identity)[:32],
            "cohort_id": manifest["cohort_id"],
            "captured_at_utc": row_captured_at,
            "source": "shyft-yellowstone-confirmed-full-meta",
            "arm_id": arm_id,
            "venue": partition["venue"],
            "venue_class": partition["venue_class"],
            "policy_id": policy["policy_id"],
            "registry_sha256": manifest["registry_sha256"],
            "policies_sha256": manifest["policies_sha256"],
            "decoder_status": partition["decoder_status"],
            "evaluator_status": partition["evaluator_status"],
            "finalized_slot": int(row["slot"]),
            "state": state,
        })
    return {
        "schema": SCHEMA,
        "outcome_blind": True,
        "window_id": receipt["window_id"],
        "tracked_teacher_rows": sum(1 for row in rows if row.get("is_tracked_teacher")),
        "materialized_observations": len(output),
        "rejected_rows": len(rejected),
        "rejections": rejected,
    }, output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--policies", type=Path, required=True)
    parser.add_argument("--resolutions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report, observations = run(
        [json.loads(line) for line in args.rows.read_text(encoding="utf-8").splitlines() if line.strip()],
        json.loads(args.receipt.read_text(encoding="utf-8")),
        json.loads(args.manifest.read_text(encoding="utf-8")),
        json.loads(args.registry.read_text(encoding="utf-8")),
        json.loads(args.policies.read_text(encoding="utf-8")),
        json.loads(args.resolutions.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in observations), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    if report["rejected_rows"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
