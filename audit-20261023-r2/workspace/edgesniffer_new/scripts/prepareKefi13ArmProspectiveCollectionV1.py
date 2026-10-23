#!/usr/bin/env python3
"""Prepare isolated receive-only prospective partitions for all 13 arms.

This does not start a systemd unit, submit a transaction, read PnL, or alter the
existing EdgeSniffer service surface. It produces a sealed collection contract
that a future source collector can append raw/outcome-blind observations to.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY = Path("research/kefi-13-arm-program-v1/registry.json")
POLICIES = Path("research/kefi-13-arm-program-v1/frozen-policies.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--policies", type=Path, default=POLICIES)
    parser.add_argument("--root", type=Path, default=Path("research/kefi-13-arm-program-v1/prospective"))
    parser.add_argument("--remote-service", default=None)
    parser.add_argument("--remote-source", default=None)
    parser.add_argument("--blocked-reason", default=None)
    parser.add_argument("--cohort-id", default=None)
    parser.add_argument("--service-restart-performed", action="store_true",
                        help="Record that the receive-only source was restarted during collection repair.")
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    policies = json.loads(args.policies.read_text(encoding="utf-8"))
    policy_by_arm = {row["arm_id"]: row for row in policies["policies"]}
    if len(registry["arms"]) != 13 or len(policy_by_arm) != 13:
        raise SystemExit("COLLECTION_PREP_INVALID_ARM_COUNT")
    args.root.mkdir(parents=True, exist_ok=True)
    partitions: list[dict[str, Any]] = []
    for arm in registry["arms"]:
        arm_id = arm["id"]
        policy = policy_by_arm[arm_id]
        partition = args.root / arm_id
        partition.mkdir(parents=True, exist_ok=True)
        raw_path = partition / "observations.jsonl"
        if not raw_path.exists():
            raw_path.write_text("", encoding="utf-8")
        partitions.append({
            "arm_id": arm_id,
            "venue": arm["venue"],
            "venue_class": arm["venue_class"],
            "partition": str(partition),
            "raw_observations": str(raw_path),
            "policy_id": policy["policy_id"],
            "policy_status": policy["status"],
            "collection_mode": "RECEIVE_ONLY_OUTCOME_BLIND",
            "allow_pnl_read": False,
            "allow_submissions": False,
            "allow_policy_changes": False,
            "allow_cross_arm_pooling": False,
            "decoder_status": arm["decoder"]["status"],
            "evaluator_status": arm["evaluator"]["status"],
            "capture_status": "READY_TO_APPEND" if arm["prospective"]["status"] in {"ELIGIBLE", "RECEIVE_ONLY_CANDIDATE"} else "QUARANTINED_RAW_ONLY",
        })
    cohort_started_at = datetime.now(timezone.utc)
    cohort_id = args.cohort_id or f"kefi-13-arm-prospective-{cohort_started_at.strftime('%Y%m%dT%H%M%SZ')}"
    if not cohort_id.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("COLLECTION_PREP_INVALID_COHORT_ID")
    collection_status = (
        "BLOCKED_SOURCE_NO_PACKETS" if args.blocked_reason else
        "RUNNING_RECEIVE_ONLY_SOURCE_NOT_YET_PARTITIONED" if args.remote_service else
        "PREPARED_NOT_STARTED"
    )
    manifest = {
        "schema": "KEFI_CANONICAL_13_ARM_PROSPECTIVE_COLLECTION_V1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cohort_id": cohort_id,
        "cohort_started_at_utc": cohort_started_at.isoformat(),
        "registry_sha256": sha256(args.registry),
        "policies_sha256": sha256(args.policies),
        "collection_mode": "RECEIVE_ONLY_OUTCOME_BLIND",
        "live_trading_enabled": False,
        "rust_only_buys": True,
        "rust_only_sells": True,
        "jupiter_fallback_enabled": False,
        "source_strategy": "one program-wide source feed with immutable arm-specific partitions",
        "no_service_restart_performed": not args.service_restart_performed,
        "service_start_performed": bool(args.remote_service),
        "remote_service": args.remote_service,
        "remote_source": args.remote_source,
        "source_blocker": args.blocked_reason,
        "partitions": partitions,
        "integrity_rules": [
            "append raw observations only; do not overwrite prior lines",
            "do not read PnL or exits while collecting the cohort",
            "do not move observations between arm partitions after receipt",
            "do not pool quote mints, venues, or controls",
            "blocked arms retain raw evidence but cannot create an economic decision",
        ],
        "status": collection_status,
    }
    output = args.root / "collection-manifest.json"
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "arm_count": len(partitions), "manifest": str(output)}, indent=2))


if __name__ == "__main__":
    main()
