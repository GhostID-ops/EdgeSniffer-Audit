#!/usr/bin/env python3
"""Structural and policy-safety validator for the canonical KEFI registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_CANONICAL_13_ARM_REGISTRY_V1"
ARM_REQUIRED = {
    "id", "name", "venue", "venue_class", "real_venue_required",
    "source_provenance", "readiness", "decoder", "evaluator", "discovery",
    "frozen_policy", "prospective", "promotion_gate",
}
DECODER_REQUIRED = {"status", "paths", "contract"}
EVALUATOR_REQUIRED = {"status", "paths", "contract"}


def fail(message: str) -> None:
    raise SystemExit(f"REGISTRY_INVALID: {message}")


def validate(registry: dict[str, Any], root: Path) -> dict[str, Any]:
    if registry.get("schema") != SCHEMA:
        fail("wrong schema")
    arms = registry.get("arms")
    if not isinstance(arms, list) or len(arms) != 13:
        fail(f"expected exactly 13 arms, got {len(arms) if isinstance(arms, list) else 'non-list'}")
    ids = [arm.get("id") for arm in arms]
    if len(set(ids)) != 13 or any(not isinstance(value, str) or not value for value in ids):
        fail("arm ids must be unique non-empty strings")
    statuses = set(registry.get("readiness_values", []))
    if not statuses:
        fail("missing readiness values")
    referenced_paths: list[str] = []
    for arm in arms:
        missing = ARM_REQUIRED - set(arm)
        if missing:
            fail(f"{arm.get('id')}: missing fields {sorted(missing)}")
        if arm["real_venue_required"] is not True:
            fail(f"{arm['id']}: real_venue_required must be true")
        if arm["readiness"] not in statuses:
            fail(f"{arm['id']}: unknown readiness {arm['readiness']}")
        for label, required in (("decoder", DECODER_REQUIRED), ("evaluator", EVALUATOR_REQUIRED)):
            block = arm[label]
            if not isinstance(block, dict) or required - set(block):
                fail(f"{arm['id']}: malformed {label}")
            if not isinstance(block["paths"], list):
                fail(f"{arm['id']}: {label}.paths must be a list")
            referenced_paths.extend(str(path) for path in block["paths"])
        if arm["frozen_policy"]["status"] not in {"FROZEN", "FROZEN_RESEARCH_CONTRACT", "FROZEN_FAIL_CLOSED"}:
            fail(f"{arm['id']}: policy slot is not frozen")
        if not arm["frozen_policy"].get("policy_id"):
            fail(f"{arm['id']}: frozen policy has no policy_id")
    missing_paths = sorted({path for path in referenced_paths if not (root / path).exists()})
    # A missing path is an audit failure only when the registry claims the component is exact.
    exact_missing: list[str] = []
    for arm in arms:
        for component in (arm["decoder"], arm["evaluator"]):
            if component["status"].startswith("EXACT") or component["status"] == "EXACT":
                exact_missing.extend(path for path in component["paths"] if not (root / path).exists())
    if exact_missing:
        fail(f"exact component paths do not exist: {sorted(set(exact_missing))}")
    return {
        "schema": "KEFI_CANONICAL_13_ARM_REGISTRY_AUDIT_V1",
        "arm_count": len(arms),
        "unique_arm_ids": True,
        "exact_component_missing_paths": sorted(set(exact_missing)),
        "non_exact_referenced_missing_paths": missing_paths,
        "frozen_policy_count": sum(arm["frozen_policy"]["status"] in {"FROZEN", "FROZEN_RESEARCH_CONTRACT", "FROZEN_FAIL_CLOSED"} for arm in arms),
        "ready_for_discovery_count": sum(arm["readiness"] == "READY_FOR_DISCOVERY" for arm in arms),
        "blocked_or_partial_count": sum(arm["readiness"] in {"BLOCKED_EXACT_ADAPTER", "FAIL_CLOSED", "PARTIAL_ADAPTER"} for arm in arms),
        "live_trading_enabled": registry.get("live_trading_enabled"),
        "jupiter_fallback_enabled": registry.get("global_execution_posture", {}).get("jupiter_fallback_enabled"),
        "status": "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=Path("research/kefi-13-arm-program-v1/registry.json"))
    parser.add_argument("--policies", type=Path, default=Path("research/kefi-13-arm-program-v1/frozen-policies.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    policies = json.loads(args.policies.read_text(encoding="utf-8"))
    policy_by_arm = {row.get("arm_id"): row for row in policies.get("policies", [])}
    if len(policy_by_arm) != 13:
        fail("frozen policy file must contain exactly 13 unique arm IDs")
    for arm in registry["arms"]:
        policy = policy_by_arm.get(arm["id"])
        if not policy or arm["frozen_policy"].get("policy_id") != policy.get("policy_id"):
            fail(f"{arm['id']}: registry policy slot does not match frozen policy file")
    report = validate(registry, root)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
