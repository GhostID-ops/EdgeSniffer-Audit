#!/usr/bin/env python3
"""Requirement-level completion audit for the canonical 13-arm program.

This report is deliberately allowed to remain incomplete.  It prevents a
passing registry/source check from being mistaken for a completed global
out-of-sample comparison.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_CANONICAL_13_ARM_COMPLETION_AUDIT_V1"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def requirement(requirement_id: str, evidence: list[str], status: str, reason: str) -> dict[str, Any]:
    return {"requirement_id": requirement_id, "evidence": evidence, "status": status, "reason": reason}


def audit(registry: dict[str, Any], registry_audit: dict[str, Any], discovery: dict[str, Any], policies: dict[str, Any],
          manifest: dict[str, Any], exact_coverage: dict[str, Any], source_gate: dict[str, Any], cohort: dict[str, Any],
          ranking: dict[str, Any], final_review: dict[str, Any], program_status: dict[str, Any],
          evidence_labels: dict[str, str] | None = None) -> dict[str, Any]:
    labels = {
        "registry": "registry.json",
        "registry_audit": "registry-audit.json",
        "discovery": "discovery-analysis.json",
        "policies": "frozen-policies.json",
        "manifest": "collection-manifest.json",
        "exact_coverage": "exact-coverage-audit.json",
        "source_gate": "prospective-source-gate.json",
        "cohort": "fresh-cohort-audit.json",
        "ranking": "prospective-ranking.json",
        "final_review": "final-review.json",
        "program_status": "program-status-audit.json",
    }
    if evidence_labels:
        labels.update(evidence_labels)
    arms = registry.get("arms") or []
    ids = [arm.get("id") for arm in arms]
    policy_rows = policies.get("policies") or policies.get("arms") or []
    discovery_rows = discovery.get("arms") or discovery.get("results") or []
    manifest_rows = manifest.get("partitions") or []
    exact_summary = exact_coverage.get("summary") or {}
    requirements = [
        requirement("REGISTRY_13_UNIQUE", [labels["registry"], labels["registry_audit"]],
                     "PASS" if len(arms) == 13 and len(set(ids)) == 13 and registry_audit.get("status") == "PASS" else "INCOMPLETE",
                     "13 unique registry arms are required."),
        requirement("REAL_VENUE_AND_COMPONENT_BINDINGS", [labels["registry"], labels["registry_audit"]],
                     "PASS" if registry_audit.get("exact_component_missing_paths") == [] else "INCOMPLETE",
                     "Every declared component path must resolve."),
        requirement("OUTCOME_BLIND_COMMON_DISCOVERY", [labels["discovery"]],
                     "PASS" if discovery.get("outcome_blind") is True and len(discovery_rows) == 13 and not discovery.get("economic_validation_performed", False) else "INCOMPLETE",
                     "Discovery must emit one outcome-blind row for every arm."),
        requirement("FROZEN_POLICY_PER_ARM", [labels["policies"], labels["registry_audit"]],
                     "PASS" if len(policy_rows) == 13 and registry_audit.get("frozen_policy_count") == 13 else "INCOMPLETE",
                     "Exactly one frozen policy slot is required per arm."),
        requirement("EXACT_DECODER_EVALUATOR_MODULE_AUDIT", [labels["exact_coverage"]],
                     "PASS" if exact_summary.get("module_coverage_pass") == 13 else "INCOMPLETE",
                     "All 13 arms need exact module coverage; fail-closed ARM13 remains a deliberate gap."),
        requirement("PARALLEL_RECEIVE_ONLY_COLLECTION", [labels["manifest"], labels["source_gate"]],
                     "PASS" if source_gate.get("status") == "PASS_PROSPECTIVE_SOURCE_GATE" and len(manifest_rows) == 13 and manifest.get("live_trading_enabled") is False else "INCOMPLETE",
                     "The collection boundary must be 13-way, receive-only, and transport-clean."),
        requirement("FRESH_COHORT_AUDIT", [labels["cohort"]],
                     "PASS" if cohort.get("status") == "PASS_CLEAN_FRESH_COHORT" else "INCOMPLETE",
                     "Fresh observations must pass hash, boundary, partition, and outcome-blind checks."),
        requirement("OUT_OF_SAMPLE_EXECUTABLE_RANKING", [labels["ranking"], labels["economics"] if "economics" in labels else "ARM07_PUMPSWAP_DIRECT/economics-report.json"],
                     "PASS" if ranking.get("fresh_only") is True and ranking.get("winner") and int((final_review.get("metrics") or {}).get("actionable_n", 0)) >= int(ranking.get("min_actionable_n", 50)) else "INCOMPLETE",
                     "At least one exact arm currently passes the executable paper gate."),
        requirement("GLOBAL_13_ARM_COMPARISON", [labels["ranking"], labels["program_status"]],
                     "PASS" if final_review.get("global_comparison_complete") is True and program_status.get("global_comparison_complete") is True else "INCOMPLETE",
                     "All 13 arms need comparable fresh executable economics; current evidence evaluates one arm."),
        requirement("EXPLICIT_KILL_PROMOTE_DECISIONS", [labels["final_review"], labels["program_status"]],
                     "PASS" if final_review.get("global_comparison_complete") is True else "INCOMPLETE",
                     "Global kill/promote decisions are not supportable while 12 arms have no data; ARM07 is only an evaluated-arm candidate."),
    ]
    passed = sum(row["status"] == "PASS" for row in requirements)
    return {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_arm_count": len(arms),
        "requirements": requirements,
        "passed_requirement_count": passed,
        "requirement_count": len(requirements),
        "global_comparison_complete": final_review.get("global_comparison_complete") is True,
        "global_winner_claim": final_review.get("global_winner_claim") is True,
        "overall_status": "COMPLETE" if all(row["status"] == "PASS" for row in requirements) else "INCOMPLETE_GLOBAL_COMPARISON",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("registry", "registry-audit", "discovery", "policies", "manifest", "exact-coverage", "source-gate", "cohort", "ranking", "final-review", "program-status"):
        parser.add_argument("--" + name, dest=name.replace("-", "_"), type=Path, required=True)
    parser.add_argument("--economics", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = ("registry", "registry-audit", "discovery", "policies", "manifest", "exact-coverage", "source-gate", "cohort", "ranking", "final-review", "program-status")
    loaded = [load(getattr(args, name.replace("-", "_"))) for name in names]
    evidence_labels = {
        key: str(getattr(args, key.replace("-", "_")))
        for key in ("registry", "registry-audit", "discovery", "policies", "manifest", "exact-coverage", "source-gate", "cohort", "ranking", "final-review", "program-status")
    }
    economics_path = args.economics or (args.final_review.parent / "ARM07_PUMPSWAP_DIRECT/economics-report.json")
    evidence_labels["economics"] = str(economics_path)
    result = audit(*loaded, evidence_labels=evidence_labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["overall_status"], "passed": result["passed_requirement_count"], "total": result["requirement_count"]}, sort_keys=True))


if __name__ == "__main__":
    main()
