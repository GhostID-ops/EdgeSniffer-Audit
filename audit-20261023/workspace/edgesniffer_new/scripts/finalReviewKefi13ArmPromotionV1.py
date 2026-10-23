#!/usr/bin/env python3
"""Perform the final paper-stage review for the canonical 13-arm ranking.

This review does not submit transactions and does not authorize live sizing.
It separates an evaluated-arm OOS winner from a global 13-arm winner when
other arms have no comparable fresh cohort.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cohort-audit", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--economics", type=Path, required=True)
    parser.add_argument("--source-compatibility", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    registry = load(args.registry)
    manifest = load(args.manifest)
    cohort = load(args.cohort_audit)
    ranking = load(args.ranking)
    economics = load(args.economics)
    source = load(args.source_compatibility)

    metrics = economics.get("metrics") or {}
    errors: list[str] = []
    if len(registry.get("arms", [])) != 13:
        errors.append("REGISTRY_NOT_13_ARMS")
    if cohort.get("status") != "PASS_CLEAN_FRESH_COHORT":
        errors.append("FRESH_COHORT_AUDIT_FAILED")
    if economics.get("cohort") != "FRESH_PROSPECTIVE" or economics.get("outcome_source") != "POST_FREEZE":
        errors.append("ECONOMICS_NOT_FRESH_POST_FREEZE")
    if economics.get("exact_evaluator") is not True:
        errors.append("EXACT_EVALUATOR_NOT_CONFIRMED")
    if source.get("status") not in {"READY_FOR_CANONICAL_INGEST", "PASS_PROSPECTIVE_SOURCE_GATE"}:
        errors.append("SOURCE_COMPATIBILITY_GATE_FAILED")
    if (source.get("source") or {}).get("transport_clean") is not True:
        errors.append("SOURCE_TRANSPORT_NOT_CLEAN")
    if int(metrics.get("actionable_n", 0)) < int(ranking.get("min_actionable_n", 50)):
        errors.append("ACTIONABLE_SAMPLE_BELOW_GATE")
    if metrics.get("controls_outperformed") is not True:
        errors.append("CONTROLS_GATE_FAILED")
    if not isinstance(metrics.get("pnl_quote"), (int, float)) or metrics["pnl_quote"] <= 0:
        errors.append("NON_POSITIVE_PNL")
    if not isinstance(metrics.get("profit_factor"), (int, float)) or metrics["profit_factor"] <= 1:
        errors.append("PROFIT_FACTOR_GATE_FAILED")
    ranked_winner = ranking.get("winner")
    if ranked_winner is not None and ranked_winner != economics.get("arm_id"):
        errors.append("RANKING_WINNER_MISMATCH")

    evaluated = [row for row in ranking.get("arms", []) if row.get("status") != "NOT_EVALUATED_NO_FRESH_COHORT"]
    evaluated_winner = ranking.get("winner") if not errors else None
    global_comparison_complete = len(evaluated) == len(registry.get("arms", []))
    result = {
        "schema": "KEFI_CANONICAL_13_ARM_FINAL_REVIEW_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_arm_count": len(registry.get("arms", [])),
        "cohort_id": manifest.get("cohort_id"),
        "source_compatibility_status": source.get("status"),
        "source_transport_clean": bool((source.get("source") or {}).get("transport_clean")),
        "evaluated_arm_count": len(evaluated),
        "global_comparison_complete": global_comparison_complete,
        "evaluated_arm_winner": evaluated_winner,
        "global_winner_claim": bool(evaluated_winner and global_comparison_complete),
        "paper_gate_pass": not errors,
        "errors": errors,
        "decision": (
            "PROMOTE_ARM07_TO_QUOTE_LIVE_PARITY_PAPER"
            if not errors and not global_comparison_complete
            else "PROMOTE_GLOBAL_WINNER_TO_QUOTE_LIVE_PARITY_PAPER"
            if not errors
            else "DO_NOT_PROMOTE"
        ),
        "live_trading_authorized": False,
        "dust_execution_authorized": False,
        "deferred_no_data_arms": [
            row["arm_id"] for row in ranking.get("arms", [])
            if row.get("status") == "NOT_EVALUATED_NO_FRESH_COHORT"
        ],
        "losing_arm_kills": [
            row["arm_id"] for row in ranking.get("arms", [])
            if row.get("kill_decision") == "KILL_RESEARCH_ONLY"
        ],
        "metrics": metrics,
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"decision": result["decision"], "errors": result["errors"]}, sort_keys=True))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
