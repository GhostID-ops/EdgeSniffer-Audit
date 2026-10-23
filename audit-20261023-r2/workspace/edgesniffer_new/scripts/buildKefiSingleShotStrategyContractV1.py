#!/usr/bin/env python3
"""Build the single pre-registered, research-only KEFI strategy contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    arm_ids = [str(arm["id"]) for arm in registry["arms"]]
    output: dict[str, Any] = {
        "schema": "KEFI_SINGLE_SHOT_STRATEGY_DISCOVERY_CONTRACT_V1",
        "version": 1,
        "research_only": True,
        "live_enabled": False,
        "jupiter_fallback_enabled": False,
        "frozen_policies_changed": False,
        "hypothesis": {
            "name": "venue_native_early_wallet_impulse_with_liquidity_capped_size",
            "statement": "A genuine early tracked-wallet purchase can identify positive short-horizon opportunity, but only after venue-native causal quoting, realistic costs, and a liquidity/impact-capped notional.",
            "supporting_evidence": [
                "kEFi activity spans multiple venue families and the current registry preserves separate venue hypotheses.",
                "ARM07 has a preserved historical signal, but its causal fixed-30 result is not currently profitable.",
                "ARM07 fixed-size economics degrade materially as notional rises because of AMM impact and fees.",
            ],
            "invalidation_evidence": [
                "No arm produces positive out-of-sample net economics after venue-native costs and controls.",
                "Positive discovery results disappear under causal entry timing or modest slippage stress.",
                "Liquidity/impact-capped sizing does not improve realistic ROI or drawdown.",
            ],
        },
        "arm_scope": {
            "arm_ids": arm_ids,
            "rule": "Each arm retains its real venue, decoder, evaluator, policy slot, and evidence partition. No ARM07 result is reused as ARM08 or another arm evidence.",
            "admission": [
                "finalized successful transaction",
                "canonical venue route proven",
                "exact venue-native transition decoded",
                "genuine tracked-wallet purchase proven",
                "distinct event identity within and across arm partitions",
                "non-trading and ambiguous activity rejected",
            ],
        },
        "causal_execution_model": {
            "entry": "first usable venue-native observed state at or after signal plus one second",
            "exit_marks": "first usable same-venue state at or after the declared horizon; unavailable marks remain explicit failures/censoring",
            "fees": "exact venue-native LP/protocol/creator fees when available",
            "impact": "integer venue-native quote at each candidate notional",
            "slippage_stress_bps": [0, 25, 50],
            "network_and_priority_fees": "not yet observed; excluded from offline results and required before execution claims",
            "failed_or_missed": "never silently removed; reported separately and included in coverage gates",
        },
        "discovery_grid": {
            "notionals_sol": [0.1, 0.5, 1.0],
            "fixed_exit_seconds": [5, 10, 15, 30, 60, 120, 300],
            "dynamic_exits": [
                {"id": "TP100", "rule": "exit at +100 bps ROI, otherwise at 300 seconds"},
                {"id": "TP200", "rule": "exit at +200 bps ROI, otherwise at 300 seconds"},
                {"id": "PROTECT100_0", "rule": "arm at +100 bps ROI, exit at 0 bps, otherwise at 300 seconds"},
                {"id": "PARTIAL50_TP100", "rule": "sell 50% at +100 bps, remainder at the declared time horizon"},
                {"id": "PARTIAL50_TP200", "rule": "sell 50% at +200 bps, remainder at the declared time horizon"},
            ],
            "entry_filters": [
                "none",
                "effective_liquidity_and_impact_cap",
                "prior_30_second_net_flow_nonnegative",
                "entry_fee_burden_at_or_below_120_bps",
                "liquidity_plus_flow_plus_fee_conjunction",
            ],
            "sizing_hypotheses": [
                {"id": "FIXED_0_1", "rule": "0.1 SOL fixed paper notional"},
                {"id": "FIXED_0_5", "rule": "0.5 SOL fixed paper notional"},
                {"id": "FIXED_1_0", "rule": "1.0 SOL fixed paper notional"},
                {"id": "IMPACT_CAP_25", "rule": "largest notional at or below 25 bps modeled entry impact, hard capped at 1.0 SOL"},
                {"id": "IMPACT_CAP_50", "rule": "largest notional at or below 50 bps modeled entry impact, hard capped at 1.0 SOL"},
                {"id": "IMPACT_CAP_100", "rule": "largest notional at or below 100 bps modeled entry impact, hard capped at 1.0 SOL"},
            ],
        },
        "discovery_selection_gate": {
            "purpose": "Select a candidate for freezing without claiming validation.",
            "minimum_actionable_trades": 50,
            "requirements": [
                "positive net PnL after exact observed costs and 50 bps stress",
                "profit factor greater than 1",
                "positive mean ROI at the target notional",
                "does not underperform matched controls",
                "positive in both chronological discovery halves",
                "no single trade contributes more than 30 percent of total PnL",
                "missing/failed coverage is reported and below the arm-specific threshold",
            ],
            "selection_rule": "Choose at most one frozen candidate per arm using discovery data only; if no candidate passes, freeze nothing.",
        },
        "prospective_gate": {
            "partition": "fresh post-freeze evidence only",
            "no_same_data_validation": True,
            "requirements": [
                "policy hash unchanged from freeze",
                "genuine venue admission certificate on every row",
                "positive net PnL after costs",
                "profit factor greater than 1",
                "controls not beaten only by a single outlier",
                "realistic size remains profitable, preferably at 0.5 SOL",
            ],
            "promotion_order": [
                "offline discovery",
                "prospective paper",
                "quote/live-parity paper",
                "dust-size Rust-only calibration only after explicit approval",
                "larger live sizing only after further approval",
            ],
        },
        "source_registry_sha256": sha256_file(args.registry),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SINGLE_SHOT_CONTRACT_BUILT_READ_ONLY", "arms": len(arm_ids)}))


if __name__ == "__main__":
    main()
