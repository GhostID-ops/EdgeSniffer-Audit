#!/usr/bin/env python3
"""Audit the isolated post-freeze observation partitions before economics."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_FRESH_COHORT_AUDIT_V1"
FORBIDDEN = {"pnl", "roi", "profit_factor", "mfe", "mae", "drawdown", "outcome", "exit", "win_rate", "return"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def nested_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key).casefold() for key in value] + [child for item in value.values() for child in nested_keys(item)]
    if isinstance(value, list):
        return [child for item in value for child in nested_keys(item)]
    return []


def run(manifest: dict[str, Any], registry: dict[str, Any], policies: dict[str, Any], root: Path) -> dict[str, Any]:
    by_arm = {arm["id"]: arm for arm in registry["arms"]}
    partitions = {row["arm_id"]: row for row in manifest["partitions"]}
    boundary = parse_utc(str(manifest["cohort_started_at_utc"]))
    counts: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    ids: set[str] = set()
    rows_seen = 0
    for arm_id, partition in partitions.items():
        path = Path(partition["raw_observations"])
        if not path.is_absolute():
            path = root / path
        if not path.exists():
            errors.append({"arm_id": arm_id, "reason": "PARTITION_FILE_MISSING"})
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            rows_seen += 1
            try:
                row = json.loads(line)
                if row.get("schema") != "KEFI_13_ARM_OBSERVATION_V1":
                    raise ValueError("INVALID_SCHEMA")
                if row.get("arm_id") != arm_id or arm_id not in by_arm:
                    raise ValueError("ARM_PARTITION_MISMATCH")
                if row.get("cohort_id") != manifest["cohort_id"]:
                    raise ValueError("COHORT_ID_MISMATCH")
                if row.get("registry_sha256") != manifest["registry_sha256"] or row.get("policies_sha256") != manifest["policies_sha256"]:
                    raise ValueError("SEALED_HASH_MISMATCH")
                if parse_utc(str(row["captured_at_utc"])) < boundary:
                    raise ValueError("PRE_COHORT_OBSERVATION")
                if row["observation_id"] in ids:
                    raise ValueError("DUPLICATE_GLOBAL_OBSERVATION_ID")
                ids.add(row["observation_id"])
                if any(any(part in key for part in FORBIDDEN) for key in nested_keys(row)):
                    raise ValueError("OUTCOME_FIELD_PRESENT")
                for field in ("venue", "venue_class", "policy_id", "decoder_status", "evaluator_status"):
                    if row[field] != partition[field]:
                        raise ValueError(f"{field.upper()}_MISMATCH")
                if not isinstance(row.get("finalized_slot"), int) or row["finalized_slot"] < 0:
                    raise ValueError("INVALID_FINALIZED_SLOT")
                counts[arm_id] += 1
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append({"arm_id": arm_id, "line": line_number, "reason": str(exc)})
    return {
        "schema": SCHEMA,
        "outcome_blind": True,
        "cohort_id": manifest["cohort_id"],
        "cohort_started_at_utc": manifest["cohort_started_at_utc"],
        "registry_arm_count": len(by_arm),
        "rows_seen": rows_seen,
        "accepted_rows_by_arm": dict(sorted(counts.items())),
        "accepted_rows": sum(counts.values()),
        "errors": errors,
        "status": "PASS_CLEAN_FRESH_COHORT" if not errors else "FAIL_COHORT_AUDIT",
        "economics_readable": not errors and sum(counts.values()) > 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--policies", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    policies = json.loads(args.policies.read_text(encoding="utf-8"))
    if manifest["registry_sha256"] != sha256(args.registry) or manifest["policies_sha256"] != sha256(args.policies):
        raise SystemExit("FAIL_CLOSED: stale manifest hashes")
    result = run(manifest, registry, policies, args.root)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "rows": result["accepted_rows"], "errors": len(result["errors"])}, sort_keys=True))
    if result["status"] != "PASS_CLEAN_FRESH_COHORT":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
