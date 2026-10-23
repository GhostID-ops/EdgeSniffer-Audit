#!/usr/bin/env python3
"""Fail-closed review of whether discovery evidence permits a policy freeze."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def find(rows: list[dict[str, Any]], policy: str, slippage: int) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("policy") == policy and row.get("slippage_bps") == slippage), None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surface", type=Path, required=True)
    parser.add_argument("--hypotheses", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    surface = json.loads(args.surface.read_text(encoding="utf-8"))
    hypotheses = json.loads(args.hypotheses.read_text(encoding="utf-8"))
    reviews: list[dict[str, Any]] = []
    for route, data in surface["routes"].items():
        fixed = find(data["selected"]["sizes"]["0.5"]["fixed"], "FIXED_30S", 25)
        controls = find(data["controls"]["sizes"]["0.5"]["fixed"], "FIXED_30S", 25)
        reviews.append({
            "route": route,
            "policy": "FIXED_30S",
            "notional_sol": 0.5,
            "slippage_bps": 25,
            "selected": fixed,
            "venue_level_control": controls,
            "passes_discovery_economics": bool(
                fixed
                and fixed.get("actionable", 0) >= 50
                and (fixed.get("profit_factor") or 0) > 1
                and (fixed.get("roi_bps_mean") or 0) > 0
            ),
            "control_route_certainty": data.get("controls_route_certainty"),
        })
    dynamic_incomplete: list[dict[str, Any]] = []
    for route, data in surface["routes"].items():
        for row in data["selected"]["sizes"]["0.5"]["dynamic"]:
            if row.get("actionable", 0) < row.get("attempted", 0) * 0.8:
                dynamic_incomplete.append({"route": route, **row})
    output = {
        "schema": "KEFI_ROUTE_POLICY_FREEZE_REVIEW_V1",
        "read_only": True,
        "research_only": True,
        "policy_selection_performed": False,
        "policy_frozen": False,
        "freeze_status": "BLOCKED_NO_DISCOVERY_POLICY_PASSES",
        "reason": "No route-specific candidate passes the predeclared realistic economics gate at 0.5 SOL and 25 bps. Dynamic TP results are incomplete and cannot be promoted from trigger-only observations.",
        "reviews": reviews,
        "dynamic_incomplete_count": len(dynamic_incomplete),
        "dynamic_incomplete_examples": dynamic_incomplete[:20],
        "validation": {
            "fresh_cohort_required": True,
            "fresh_cohort_started": False,
            "trading_enabled": False,
            "existing_receive_only_collector_unchanged": True,
        },
        "inputs": {
            "surface": str(args.surface),
            "hypotheses": str(args.hypotheses),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["freeze_status"], "policy_frozen": False}, sort_keys=True))


if __name__ == "__main__":
    main()
