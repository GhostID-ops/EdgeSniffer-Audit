#!/usr/bin/env python3
"""Run one outcome-blind discovery readiness analysis across all registry arms.

This intentionally measures evidence coverage and evaluator readiness, not PnL.
It emits one row for every arm, including blocked arms, so missing adapters cannot
disappear from the research denominator.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY = Path("research/kefi-13-arm-program-v1/registry.json")
POLICIES = Path("research/kefi-13-arm-program-v1/frozen-policies.json")
OUTCOME_KEYS = {"pnl", "roi", "profit_factor", "win_rate", "mfe", "mae", "drawdown", "exit", "outcome"}

BASELINES: dict[str, dict[str, Any]] = {
    "ARM01_SOL_PUMP_CURVE_2CQ": {"source_population_n": 204, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
    "ARM02_SOL_PUMP_CPI_2CQ": {"source_population_n": 88, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
    "ARM03_ACTIVE_SOL_PUMP_5000MS": {"source_population_n": 124, "valid_symmetric_n": 23, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
    "ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE": {"source_population_n": 121, "quote_mint_family_n": 18, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
    "ARM05_RAYDIUM_LAUNCHLAB": {"source_population_n": 246, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
    "ARM06_STONKFUN_LAUNCHLAB": {"source_population_n": None, "parent_population_n": 246, "source": "route-init registry classification; subset not sealed"},
    "ARM07_PUMPSWAP_DIRECT": {"source_population_n": 17, "sealed_actionable_n": 22, "source": "sealed PumpSwap discovery/economics artifacts"},
    "ARM08_PUMPSWAP_CPI": {"source_population_n": None, "parent_population_n": 17, "source": "PUMPSWAP_CPI route policy; subset not sealed"},
    "ARM09_ROUTER_RAYDIUM_CLMM": {"source_population_n": None, "parent_population_n": 50, "source": "router underlying-venue inventory"},
    "ARM10_ROUTER_ORCA_WHIRLPOOL": {"source_population_n": None, "parent_population_n": 50, "source": "router underlying-venue inventory"},
    "ARM11_ROUTER_METEORA_DLMM": {"source_population_n": None, "parent_population_n": 50, "source": "router underlying-venue inventory"},
    "ARM12_OTHER_DEX_PROTOCOL_NATIVE": {"source_population_n": 32, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
    "ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED": {"source_population_n": 1, "source": "2cq-multi-archetype-research-v1 REPORT.md"},
}


def assert_outcome_blind(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in OUTCOME_KEYS:
                raise ValueError(f"outcome key {path}.{key} is not allowed in discovery input")
            assert_outcome_blind(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_outcome_blind(child, f"{path}[{index}]")


def run(registry: dict[str, Any], policies: dict[str, Any]) -> dict[str, Any]:
    policy_by_arm = {row["arm_id"]: row for row in policies["policies"]}
    if len(policy_by_arm) != 13:
        raise ValueError("frozen policy file must contain exactly one policy per arm")
    rows = []
    for arm in registry["arms"]:
        arm_id = arm["id"]
        baseline = dict(BASELINES.get(arm_id, {}))
        policy = policy_by_arm[arm_id]
        if policy["status"].startswith("FROZEN") is False:
            raise ValueError(f"{arm_id}: policy is not frozen")
        rows.append({
            "arm_id": arm_id,
            "venue": arm["venue"],
            "venue_class": arm["venue_class"],
            "readiness": arm["readiness"],
            "source_provenance": arm["source_provenance"],
            "baseline": baseline,
            "decoder_status": arm["decoder"]["status"],
            "evaluator_status": arm["evaluator"]["status"],
            "policy_id": policy["policy_id"],
            "policy_status": policy["status"],
            "action": policy["action"],
            "outcome_blind": True,
            "discovery_result": "COVERAGE_RECORDED_NOT_ECONOMIC_VALIDATION",
        })
    return {
        "schema": "KEFI_CANONICAL_13_ARM_DISCOVERY_ANALYSIS_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_schema": registry["schema"],
        "outcome_blind": True,
        "outcome_keys_permitted": sorted(OUTCOME_KEYS),
        "analysis_definition": "same fields for every arm: venue identity, source population, decoder status, evaluator status, frozen policy, and prospective action",
        "arms": rows,
        "summary": {
            "arm_count": len(rows),
            "ready_for_discovery": sum(row["readiness"] == "READY_FOR_DISCOVERY" for row in rows),
            "exact_evaluator": sum(row["evaluator_status"] == "EXACT" for row in rows),
            "paper_only_or_no_trade": sum(row["action"] != "PAPER_ONLY" for row in rows),
            "economic_validation_performed": False,
            "status": "PASS_OUTCOME_BLIND_COVERAGE_AUDIT",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--policies", type=Path, default=POLICIES)
    parser.add_argument("--output", type=Path, default=Path("research/kefi-13-arm-program-v1/discovery-analysis.json"))
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    policies = json.loads(args.policies.read_text(encoding="utf-8"))
    assert_outcome_blind(registry)
    report = run(registry, policies)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
