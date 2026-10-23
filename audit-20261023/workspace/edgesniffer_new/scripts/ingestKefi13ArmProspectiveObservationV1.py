#!/usr/bin/env python3
"""Append outcome-blind observations to the canonical 13-arm partitions.

The collector boundary is intentionally strict.  It accepts only rows whose
arm, venue, decoder/evaluator status, frozen policy, and registry/policy hashes
match the sealed collection manifest.  It rejects outcome/economics fields so
the prospective selection cohort cannot be contaminated by its own results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST = Path("research/kefi-13-arm-program-v1/prospective/collection-manifest.json")
REGISTRY = Path("research/kefi-13-arm-program-v1/registry.json")
POLICIES = Path("research/kefi-13-arm-program-v1/frozen-policies.json")

REQUIRED = {
    "schema",
    "observation_id",
    "cohort_id",
    "captured_at_utc",
    "source",
    "arm_id",
    "venue",
    "venue_class",
    "policy_id",
    "registry_sha256",
    "policies_sha256",
    "decoder_status",
    "evaluator_status",
    "finalized_slot",
    "state",
}
FORBIDDEN_KEY_PARTS = {
    "pnl",
    "roi",
    "profit_factor",
    "drawdown",
    "mfe",
    "mae",
    "win_rate",
    "controls_outperformed",
    "exit",
    "outcome",
    "return",
}


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def nested_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append(str(key).casefold())
            found.extend(nested_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(nested_keys(child))
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject_reason(row: dict[str, Any], manifest: dict[str, Any], by_arm: dict[str, dict[str, Any]]) -> str | None:
    if row.get("schema") != "KEFI_13_ARM_OBSERVATION_V1":
        return "INVALID_SCHEMA"
    missing = sorted(REQUIRED - row.keys())
    if missing:
        return "MISSING_REQUIRED:" + ",".join(missing)
    if any(any(part in key for part in FORBIDDEN_KEY_PARTS) for key in nested_keys(row)):
        return "OUTCOME_FIELD_FORBIDDEN"
    if row["cohort_id"] != manifest.get("cohort_id"):
        return "COHORT_ID_MISMATCH"
    try:
        if parse_utc(str(row["captured_at_utc"])) < parse_utc(str(manifest["cohort_started_at_utc"])):
            return "OBSERVATION_PRE_COHORT_BOUNDARY"
    except (KeyError, TypeError, ValueError):
        return "INVALID_CAPTURED_AT_UTC"
    if row["registry_sha256"] != manifest.get("registry_sha256"):
        return "REGISTRY_HASH_MISMATCH"
    if row["policies_sha256"] != manifest.get("policies_sha256"):
        return "POLICY_HASH_MISMATCH"
    arm_id = row["arm_id"]
    if arm_id not in by_arm:
        return "UNKNOWN_ARM"
    arm = by_arm[arm_id]
    partition = next(item for item in manifest["partitions"] if item["arm_id"] == arm_id)
    for field in ("venue", "venue_class", "policy_id", "decoder_status", "evaluator_status"):
        if row[field] != partition[field]:
            return f"{field.upper()}_MISMATCH"
    if not isinstance(row["observation_id"], str) or not row["observation_id"].strip():
        return "INVALID_OBSERVATION_ID"
    if not isinstance(row["source"], str) or not row["source"].strip():
        return "INVALID_SOURCE"
    if not isinstance(row["finalized_slot"], int) or row["finalized_slot"] < 0:
        return "INVALID_FINALIZED_SLOT"
    if not isinstance(row["state"], dict) or not row["state"]:
        return "INVALID_STATE"
    if arm["id"] != arm_id:
        return "ARM_REGISTRY_MISMATCH"
    if arm["readiness"] == "FAIL_CLOSED" and "raw_evidence" not in row["state"]:
        return "FAIL_CLOSED_REQUIRES_RAW_EVIDENCE"
    return None


def ingest(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    registry_path: Path = REGISTRY,
    policies_path: Path = POLICIES,
) -> dict[str, Any]:
    if manifest.get("registry_sha256") != sha256(registry_path) or manifest.get("policies_sha256") != sha256(policies_path):
        raise ValueError("MANIFEST_HASHES_STALE")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    by_arm = {arm["id"]: arm for arm in registry["arms"]}
    if manifest.get("collection_mode") != "RECEIVE_ONLY_OUTCOME_BLIND":
        raise ValueError("MANIFEST_NOT_OUTCOME_BLIND")
    if manifest.get("status") == "PREPARED_NOT_STARTED":
        raise ValueError("MANIFEST_SOURCE_NOT_STARTED")
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append({"line": index, "reason": "ROW_NOT_OBJECT"})
            continue
        reason = reject_reason(row, manifest, by_arm)
        if reason:
            errors.append({"line": index, "observation_id": row.get("observation_id"), "reason": reason})
        elif row["observation_id"] in seen:
            errors.append({"line": index, "observation_id": row["observation_id"], "reason": "DUPLICATE_INPUT_ID"})
        else:
            seen.add(row["observation_id"])
    if errors:
        return {"status": "REJECTED_NO_APPEND", "accepted": 0, "rejected": len(errors), "errors": errors}

    existing_ids: dict[str, set[str]] = {}
    existing_global: set[str] = set()
    for partition in manifest["partitions"]:
        path = Path(partition["raw_observations"])
        ids: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    saved = json.loads(line)
                    ids.add(saved["observation_id"])
                    existing_global.add(saved["observation_id"])
        existing_ids[partition["arm_id"]] = ids
    for row in rows:
        if row["observation_id"] in existing_global:
            return {"status": "REJECTED_NO_APPEND", "accepted": 0, "rejected": 1, "errors": [{"observation_id": row["observation_id"], "reason": "DUPLICATE_EXISTING_ID"}]}

    for row in rows:
        partition = next(item for item in manifest["partitions"] if item["arm_id"] == row["arm_id"])
        path = Path(partition["raw_observations"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return {"status": "APPENDED_OUTCOME_BLIND", "accepted": len(rows), "rejected": 0, "errors": []}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--policies", type=Path, default=POLICIES)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = ingest(manifest, rows, args.registry, args.policies)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "APPENDED_OUTCOME_BLIND":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
