#!/usr/bin/env python3
"""Build a read-only priority audit for the KEFI 13-arm coverage program."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PRIORITY = {
    "ARM07_PUMPSWAP_DIRECT": {
        "tier": "BENCHMARK_KEEP_ACTIVE",
        "reason": "Only arm with admitted causal economics; retain as unchanged benchmark while coverage expands.",
        "next_gate": "Continue receive-only causal collection; do not change frozen policy or promote.",
    },
    "ARM08_PUMPSWAP_CPI": {
        "tier": "P0_SAME_VENUE_COVERAGE",
        "reason": "95 raw candidate references share the PumpSwap tape, but the CPI-linked route adapter has not admitted executable entries.",
        "next_gate": "Resolve CPI-linked acquisition to the canonical PumpSwap pool, then run decoder/fee/causal tape parity.",
    },
    "ARM01_SOL_PUMP_CURVE_2CQ": {
        "tier": "P1_EXISTING_ACTIVITY",
        "reason": "Five raw references exist and the exact curve module is present, but no qualifying entry was admitted in the current boundary.",
        "next_gate": "Capture a dedicated finalized Pump curve transition tape and prove causal pre-action state plus exit marks.",
    },
    "ARM02_SOL_PUMP_CPI_2CQ": {
        "tier": "P1_EXISTING_ACTIVITY",
        "reason": "Five raw references exist and the exact CPI/curve modules are present, but no qualifying entry was admitted in the current boundary.",
        "next_gate": "Resolve CPI-linked curve state from finalized evidence and run the venue-native causal economics evaluator.",
    },
    "ARM03_ACTIVE_SOL_PUMP_5000MS": {
        "tier": "P1_EXISTING_ACTIVITY",
        "reason": "Five raw references exist and the exact Pump modules are present, but no qualifying entry was admitted in the current boundary.",
        "next_gate": "Prove the 5-second activity timing from finalized transitions before evaluating economics.",
    },
    "ARM09_ROUTER_RAYDIUM_CLMM": {
        "tier": "P1_ROUTER_RESOLUTION",
        "reason": "Two router references exist, but the underlying Raydium CLMM acquisition/venue join is not promotion-ready.",
        "next_gate": "Prove tracked-wallet acquisition and canonical CLMM pool state before admitting entries.",
    },
    "ARM10_ROUTER_ORCA_WHIRLPOOL": {
        "tier": "P1_ROUTER_RESOLUTION",
        "reason": "Two router references exist, but the underlying Orca Whirlpool acquisition/venue join is not promotion-ready.",
        "next_gate": "Prove tracked-wallet acquisition and canonical Whirlpool state/fee reconstruction before admitting entries.",
    },
    "ARM11_ROUTER_METEORA_DLMM": {
        "tier": "P1_ROUTER_RESOLUTION",
        "reason": "Two router references exist, but the captured route lacks a canonical admitted Meteora swap event/state.",
        "next_gate": "Add exact DLMM event/state reconstruction and reject any route without a proven underlying pool.",
    },
    "ARM12_OTHER_DEX_PROTOCOL_NATIVE": {
        "tier": "P2_ADAPTER_REQUIRED",
        "reason": "Two raw references exist, but the protocol-specific exact adapter is currently blocked.",
        "next_gate": "Identify the real protocol and build a fail-closed native decoder/evaluator with fixture parity.",
    },
    "ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE": {
        "tier": "P2_SOURCE_REQUIRED",
        "reason": "No raw references were observed and the adapter is partial; there is no current activity evidence to evaluate.",
        "next_gate": "Extend source coverage for non-SOL quote markets before strategy conclusions.",
    },
    "ARM05_RAYDIUM_LAUNCHLAB": {
        "tier": "P2_SOURCE_REQUIRED",
        "reason": "No raw references were observed and the LaunchLab adapter is partial; this arm is unmeasured.",
        "next_gate": "Capture canonical LaunchLab initialization/trade state before economics evaluation.",
    },
    "ARM06_STONKFUN_LAUNCHLAB": {
        "tier": "P2_SOURCE_REQUIRED",
        "reason": "No raw references were observed and the LaunchLab/stonkfun overlay is partial; this arm is unmeasured.",
        "next_gate": "Prove the Stonkfun platform configuration and venue-native LaunchLab economics.",
    },
    "ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED": {
        "tier": "P3_FAIL_CLOSED",
        "reason": "No raw references exist and the arm intentionally has no admitted real venue.",
        "next_gate": "Do not invest in economics until a real venue and exact route are proven; retain as a safety classification.",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    registry = load(args.registry)
    status = load(args.status)
    coverage = load(args.coverage)
    source_audit = load(args.source_audit)
    status_by_id = {row["arm_id"]: row for row in status["arms"]}
    coverage_by_id = {row["arm_id"]: row for row in coverage["arms"]}
    rows = []
    for arm in registry["arms"]:
        arm_id = arm["id"]
        current = status_by_id[arm_id]
        module = coverage_by_id[arm_id]
        priority = PRIORITY[arm_id]
        rows.append({
            "arm_id": arm_id,
            "name": arm["name"],
            "venue": arm["venue"],
            "priority_tier": priority["tier"],
            "priority_reason": priority["reason"],
            "next_gate": priority["next_gate"],
            "raw_candidate_references": current["raw_candidate_references"],
            "exact_admitted_observations": current["exact_admitted_observations"],
            "registry_readiness": current["registry_readiness"],
            "decision": current["decision"],
            "module_status": module["status"],
            "exact_module_ready": module["exact_module_ready"],
            "economic_promotion_ready": module["economic_promotion_ready"],
        })
    output = {
        "schema": "KEFI_13_ARM_COVERAGE_PRIORITY_AUDIT_V1",
        "read_only": True,
        "research_only": True,
        "no_collector_or_policy_change": True,
        "purpose": "Prioritize venue coverage before interpreting zero-admitted arms as negative strategy evidence.",
        "source_evidence": {
            "registry_sha256": sha256_file(args.registry),
            "status_sha256": sha256_file(args.status),
            "coverage_sha256": sha256_file(args.coverage),
            "source_audit_sha256": sha256_file(args.source_audit),
            "observed_arm_ids": source_audit["coverage"]["observed_arm_ids"],
            "unobserved_arm_ids": source_audit["coverage"]["unobserved_arm_ids"],
        },
        "interpretation": {
            "arms_are": "named fingerprint/venue/evaluator research partitions, not thirteen proven independent strategies",
            "zero_admitted_means": "not measured under the exact executable contract; it is not a negative PnL result",
            "promotion_rule": "coverage, canonical venue resolution, causal tape, realistic economics, then fresh prospective validation",
        },
        "priority_order": [
            "ARM08_PUMPSWAP_CPI",
            "ARM01_SOL_PUMP_CURVE_2CQ",
            "ARM02_SOL_PUMP_CPI_2CQ",
            "ARM03_ACTIVE_SOL_PUMP_5000MS",
            "ARM09_ROUTER_RAYDIUM_CLMM",
            "ARM10_ROUTER_ORCA_WHIRLPOOL",
            "ARM11_ROUTER_METEORA_DLMM",
            "ARM12_OTHER_DEX_PROTOCOL_NATIVE",
            "ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE",
            "ARM05_RAYDIUM_LAUNCHLAB",
            "ARM06_STONKFUN_LAUNCHLAB",
            "ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED",
        ],
        "arms": rows,
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COVERAGE_PRIORITY_AUDIT_COMPLETE_READ_ONLY", "arms": len(rows)}))


if __name__ == "__main__":
    main()
