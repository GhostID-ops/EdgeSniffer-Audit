#!/usr/bin/env python3
"""Rank only fresh, out-of-sample, exact-evaluator arm reports.

Missing reports are explicit NOT_EVALUATED rows. This prevents the historical
PumpSwap surface or any development sample from being misreported as prospective
validation for the 13-arm program.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY = Path("research/kefi-13-arm-program-v1/registry.json")


def evaluate_row(arm: dict[str, Any], report: dict[str, Any] | None, min_n: int) -> dict[str, Any]:
    base = {"arm_id": arm["id"], "venue": arm["venue"], "evaluator_status": arm["evaluator"]["status"]}
    if report is None:
        return {**base, "status": "NOT_EVALUATED_NO_FRESH_COHORT", "promotion_eligible": False, "kill_decision": "DEFER_NO_DATA"}
    if report.get("cohort") != "FRESH_PROSPECTIVE" or report.get("outcome_source") != "POST_FREEZE":
        return {**base, "status": "REJECTED_NOT_FRESH_OUT_OF_SAMPLE", "promotion_eligible": False, "kill_decision": "KILL_REPORT"}
    if arm["evaluator"]["status"] != "EXACT":
        return {**base, "status": "REJECTED_EXACT_EVALUATOR_MISSING", "promotion_eligible": False, "kill_decision": "DEFER_ADAPTER"}
    metrics = report.get("metrics") or {}
    n = int(metrics.get("actionable_n", 0))
    pf = metrics.get("profit_factor")
    pnl = metrics.get("pnl_quote", metrics.get("pnl_sol"))
    controls = metrics.get("controls_outperformed")
    if n < min_n:
        return {**base, "status": "DEFER_INSUFFICIENT_FRESH_SAMPLE", "promotion_eligible": False, "kill_decision": "DEFER_SAMPLE", "metrics": metrics}
    if controls is None:
        return {**base, "status": "DEFER_CONTROLS_MISSING", "promotion_eligible": False, "kill_decision": "DEFER_CONTROLS", "metrics": metrics}
    if controls is not True:
        return {**base, "status": "FAIL_PROMOTION_GATE_CONTROLS", "promotion_eligible": False, "kill_decision": "KILL_RESEARCH_ONLY", "metrics": metrics}
    eligible = n >= min_n and isinstance(pf, (int, float)) and pf > 1 and isinstance(pnl, (int, float)) and pnl > 0 and controls is True
    return {**base, "status": "PASS_GATE" if eligible else "FAIL_PROMOTION_GATE", "promotion_eligible": eligible, "kill_decision": "PROMOTE_CANDIDATE" if eligible else "KILL_RESEARCH_ONLY", "metrics": metrics}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--reports-dir", type=Path, default=Path("research/kefi-13-arm-program-v1/prospective"))
    parser.add_argument("--output", type=Path, default=Path("research/kefi-13-arm-program-v1/prospective-ranking.json"))
    parser.add_argument("--min-actionable-n", type=int, default=50)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    rows = []
    for arm in registry["arms"]:
        report_path = args.reports_dir / arm["id"] / "economics-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        rows.append(evaluate_row(arm, report, args.min_actionable_n))
    promoted = [row for row in rows if row["promotion_eligible"]]
    promoted.sort(key=lambda row: (row["metrics"].get("profit_factor", float("-inf")), row["metrics"].get("pnl_quote", row["metrics"].get("pnl_sol", float("-inf")))), reverse=True)
    result = {
        "schema": "KEFI_CANONICAL_13_ARM_PROSPECTIVE_RANKING_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fresh_only": True,
        "min_actionable_n": args.min_actionable_n,
        "promotion_rule": "actionable_n >= threshold, positive PnL, PF > 1, and controls_outperformed=true",
        "arms": rows,
        "ranking": promoted,
        "promote_only_winner": True,
        "winner": promoted[0]["arm_id"] if promoted else None,
        "status": "WINNER_CANDIDATE_REQUIRES_FINAL_REVIEW" if promoted else "NO_WINNER_DATA_OR_GATE_PASS",
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "arm_count": len(rows), "winner": result["winner"]}, indent=2))


if __name__ == "__main__":
    main()
