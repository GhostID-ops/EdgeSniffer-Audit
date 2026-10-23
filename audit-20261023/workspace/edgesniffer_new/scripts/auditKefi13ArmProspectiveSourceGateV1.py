#!/usr/bin/env python3
"""Reconcile the current receive-only source into a promotion-review gate.

The older source-compatibility audit describes a different candidate stream.
This gate consumes the current finalized-RPC source audit, exact materializer
audit, and fresh-cohort audit so final review cannot accidentally use stale
source metadata.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_PROSPECTIVE_SOURCE_GATE_V1"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(source: dict[str, Any], materialization: dict[str, Any], cohort: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if source.get("status") != "PASS_RAW_SOURCE_AUDIT":
        blockers.append("RAW_SOURCE_AUDIT_FAILED")
    source_meta = source.get("source") or {}
    if source_meta.get("transport_clean") is not True:
        blockers.append("SOURCE_TRANSPORT_NOT_CLEAN")
    if int(source_meta.get("state_errors", 0)) != 0:
        blockers.append("SOURCE_STATE_ERRORS")
    if materialization.get("status") != "PASS_OUTCOME_BLIND_FAIL_CLOSED":
        blockers.append("EXACT_MATERIALIZATION_AUDIT_FAILED")
    if cohort.get("status") != "PASS_CLEAN_FRESH_COHORT":
        blockers.append("FRESH_COHORT_AUDIT_FAILED")
    if int(cohort.get("accepted_rows", 0)) <= 0:
        blockers.append("NO_ACCEPTED_FRESH_ROWS")
    observed = sorted((source.get("coverage") or {}).get("observed_arm_ids") or [])
    admitted = (materialization.get("by_arm") or {})
    admitted_arm_ids = sorted(arm_id for arm_id, count in admitted.items() if int(count) > 0)
    return {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_blind": True,
        "cohort_id": source.get("cohort_id") or cohort.get("cohort_id"),
        "raw_source_status": source.get("status"),
        "materialization_status": materialization.get("status"),
        "fresh_cohort_status": cohort.get("status"),
        "source": {
            "transport_clean": source_meta.get("transport_clean") is True,
            "source_fresh": source_meta.get("source_fresh") is True,
            "raw_rows": int(source_meta.get("raw_rows", 0)),
            "unique_signatures": int(source_meta.get("unique_signatures", 0)),
            "observed_arm_ids": observed,
            "admitted_arm_ids": admitted_arm_ids,
            "materialized_observations": int(materialization.get("materialized_observations", 0)),
            "fresh_accepted_rows": int(cohort.get("accepted_rows", 0)),
        },
        "blockers": blockers,
        "global_comparison_complete": len(admitted_arm_ids) == 13,
        "status": "PASS_PROSPECTIVE_SOURCE_GATE" if not blockers else "BLOCKED_PROSPECTIVE_SOURCE_GATE",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--materialization-audit", type=Path, required=True)
    parser.add_argument("--cohort-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(load(args.source_audit), load(args.materialization_audit), load(args.cohort_audit))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, sort_keys=True))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
