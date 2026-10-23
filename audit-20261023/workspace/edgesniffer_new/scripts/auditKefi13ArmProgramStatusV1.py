#!/usr/bin/env python3
"""Reconcile the canonical 13-arm program into one evidence-based status.

This is a reporting gate, not a selector.  It keeps raw candidate references,
exactly admitted observations, fresh economics, adapter coverage, and final
decisions separate so a no-data arm cannot become a losing-arm claim.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def audit(
    registry: dict[str, Any],
    source: dict[str, Any],
    materialization: dict[str, Any],
    cohort: dict[str, Any],
    ranking: dict[str, Any],
    final_review: dict[str, Any],
    exact_coverage: dict[str, Any],
) -> dict[str, Any]:
    arms = registry.get("arms") or []
    by_rank = {row["arm_id"]: row for row in ranking.get("arms", [])}
    by_exact = {row["arm_id"]: row for row in exact_coverage.get("arms", [])}
    candidate_counts = ((source.get("source") or {}).get("candidate_arm_counts") or {})
    admitted_counts = materialization.get("by_arm") or {}
    rows: list[dict[str, Any]] = []
    for arm in arms:
        arm_id = arm["id"]
        rank = by_rank.get(arm_id, {})
        exact = by_exact.get(arm_id, {})
        rank_status = rank.get("status", "NOT_IN_RANKING")
        if rank_status == "NOT_EVALUATED_NO_FRESH_COHORT":
            decision = "DEFER_NO_DATA"
        elif rank.get("kill_decision") == "KILL_RESEARCH_ONLY":
            decision = "KILL_RESEARCH_ONLY"
        elif rank.get("promotion_eligible") is True:
            decision = "PROMOTE_CANDIDATE"
        elif exact.get("exact_module_ready") is not True:
            decision = "DEFER_EXACT_ADAPTER"
        else:
            decision = "DEFER_GATE"
        rows.append({
            "arm_id": arm_id,
            "venue": arm.get("venue"),
            "registry_readiness": arm.get("readiness"),
            "exact_module_ready": bool(exact.get("exact_module_ready")),
            "raw_candidate_references": int(candidate_counts.get(arm_id, 0)),
            "exact_admitted_observations": int(admitted_counts.get(arm_id, 0)),
            "ranking_status": rank_status,
            "economics_metrics": rank.get("metrics"),
            "decision": decision,
        })

    evaluated_count = int(final_review.get("evaluated_arm_count", 0))
    global_complete = evaluated_count == len(arms) == 13
    invariants: list[str] = []
    if len(arms) != 13:
        invariants.append("REGISTRY_NOT_EXACTLY_13")
    if cohort.get("status") != "PASS_CLEAN_FRESH_COHORT":
        invariants.append("FRESH_COHORT_AUDIT_FAILED")
    if source.get("status") != "PASS_RAW_SOURCE_AUDIT":
        invariants.append("RAW_SOURCE_AUDIT_FAILED")
    if materialization.get("status") != "PASS_OUTCOME_BLIND_FAIL_CLOSED":
        invariants.append("MATERIALIZATION_AUDIT_FAILED")
    if final_review.get("global_winner_claim") and not global_complete:
        invariants.append("GLOBAL_WINNER_CLAIM_WITH_INCOMPLETE_COMPARISON")
    if final_review.get("live_trading_authorized") is not False:
        invariants.append("LIVE_AUTHORIZATION_NOT_FALSE")
    if final_review.get("dust_execution_authorized") is not False:
        invariants.append("DUST_AUTHORIZATION_NOT_FALSE")
    no_data = [row["arm_id"] for row in rows if row["decision"] == "DEFER_NO_DATA"]
    kills = [row["arm_id"] for row in rows if row["decision"] == "KILL_RESEARCH_ONLY"]
    promoted = [row["arm_id"] for row in rows if row["decision"] == "PROMOTE_CANDIDATE"]
    return {
        "schema": "KEFI_CANONICAL_13_ARM_PROGRAM_STATUS_AUDIT_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "arm_count": len(arms),
        "evaluated_arm_count": evaluated_count,
        "global_comparison_complete": global_complete,
        "global_winner_claim": bool(final_review.get("global_winner_claim")),
        "final_decision": final_review.get("decision"),
        "live_trading_authorized": final_review.get("live_trading_authorized"),
        "dust_execution_authorized": final_review.get("dust_execution_authorized"),
        "no_data_arms": no_data,
        "losing_arm_kills": kills,
        "promoted_candidates": promoted,
        "invariants": sorted(set(invariants)),
        "arms": rows,
        "status": (
            "PASS_GLOBAL_COMPARISON_COMPLETE"
            if global_complete and not invariants
            else "PASS_STRUCTURAL_AUDIT_GLOBAL_COMPARISON_INCOMPLETE"
            if not invariants
            else "FAIL_PROGRAM_STATUS_AUDIT"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("registry", "source", "materialization", "cohort", "ranking", "final-review", "exact-coverage", "output"):
        parser.add_argument("--" + name, dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    result = audit(
        *(json.loads(path.read_text(encoding="utf-8")) for path in (
            args.registry, args.source, args.materialization, args.cohort,
            args.ranking, args.final_review, args.exact_coverage,
        ))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "arm_count", "evaluated_arm_count", "global_comparison_complete", "no_data_arms", "losing_arm_kills")}, sort_keys=True))
    if result["status"] == "FAIL_PROGRAM_STATUS_AUDIT":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
