#!/usr/bin/env python3
"""Audit whether a fresh arm report can enter quote/live-parity paper review.

This is a readiness gate, not an execution launcher.  It keeps the paper-stage
promotion separate from the stronger deterministic-source contract required
for quote/live-parity replay.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_TAPE_FIELDS = {
    "received_at_unix_ns",
    "source_instance_id",
    "continuity_epoch",
    "durable_output_sequence",
    "instruction_amount_raw",
    "instruction_limit_raw",
    "exact_transition_validated",
}


def audit(
    registry: dict[str, Any],
    policy: dict[str, Any],
    final_review: dict[str, Any],
    economics: dict[str, Any],
    tape_rows: list[dict[str, Any]],
    tape_audit: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    arm_id = "ARM07_PUMPSWAP_DIRECT"
    arm = next((row for row in registry.get("arms", []) if row.get("id") == arm_id), None)
    if arm is None:
        blockers.append("ARM07_REGISTRY_ROW_MISSING")
    if policy.get("arm_id") != arm_id or policy.get("policy_id") != "kefi-pumpswap-fixed-30s-challenge-v1":
        blockers.append("FROZEN_ARM07_POLICY_MISMATCH")
    if final_review.get("decision") != "PROMOTE_ARM07_TO_QUOTE_LIVE_PARITY_PAPER":
        blockers.append("PAPER_PROMOTION_DECISION_NOT_PRESENT")
    metrics = economics.get("metrics") or {}
    if economics.get("cohort") != "FRESH_PROSPECTIVE" or economics.get("outcome_source") != "POST_FREEZE":
        blockers.append("ECONOMICS_NOT_FRESH_POST_FREEZE")
    if int(metrics.get("actionable_n", 0)) < 50:
        blockers.append("ACTIONABLE_SAMPLE_BELOW_GATE")
    if metrics.get("controls_outperformed") is not True:
        blockers.append("CONTROLS_GATE_FAILED")
    if not isinstance(metrics.get("pnl_quote"), (int, float)) or metrics["pnl_quote"] <= 0:
        blockers.append("NON_POSITIVE_PNL")
    if not isinstance(metrics.get("profit_factor"), (int, float)) or metrics["profit_factor"] <= 1:
        blockers.append("PROFIT_FACTOR_GATE_FAILED")
    if not tape_rows:
        blockers.append("EMPTY_EXACT_TAPE")
    else:
        missing = sorted(REQUIRED_TAPE_FIELDS - set(tape_rows[0]))
        if missing:
            blockers.append("TAPE_FIELDS_MISSING:" + ",".join(missing))
        if any(row.get("source_instance_id") is None for row in tape_rows):
            blockers.append("MISSING_SOURCE_INSTANCE_ID")
        if any(row.get("continuity_epoch") is None for row in tape_rows):
            blockers.append("MISSING_CONTINUITY_EPOCH")
        if any(row.get("durable_output_sequence") is None for row in tape_rows):
            blockers.append("MISSING_DURABLE_OUTPUT_SEQUENCE")
    if int(tape_audit.get("decoder_error_count", 0)):
        blockers.append("EXACT_TAPE_DECODER_ERRORS_PRESENT")
    if tape_audit.get("schema_errors"):
        blockers.append("EXACT_TAPE_SCHEMA_ERRORS_PRESENT")
    if tape_audit.get("parity_contract") is not True:
        blockers.append("PARITY_TAPE_CONTRACT_NOT_CERTIFIED")
    return {
        "schema": "KEFI_ARM_QUOTE_LIVE_PARITY_READINESS_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "arm_id": arm_id,
        "frozen_policy_id": policy.get("policy_id"),
        "paper_promotion_decision": final_review.get("decision"),
        "outcome_source": economics.get("outcome_source"),
        "tape_rows": len(tape_rows),
        "tape_decoder_errors": int(tape_audit.get("decoder_error_count", 0)),
        "live_trading_authorized": False,
        "dust_execution_authorized": False,
        "blockers": sorted(set(blockers)),
        "status": "READY_FOR_QUOTE_LIVE_PARITY_PAPER" if not blockers else "BLOCKED_QUOTE_LIVE_PARITY_READINESS",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--final-review", type=Path, required=True)
    parser.add_argument("--economics", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--tape-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.tape.read_text(encoding="utf-8").splitlines() if line.strip()]
    policy_payload = json.loads(args.policy.read_text(encoding="utf-8"))
    if isinstance(policy_payload, dict) and isinstance(policy_payload.get("policies"), list):
        policy_payload = next(
            row for row in policy_payload["policies"] if row.get("arm_id") == "ARM07_PUMPSWAP_DIRECT"
        )
    result = audit(
        json.loads(args.registry.read_text(encoding="utf-8")),
        policy_payload,
        json.loads(args.final_review.read_text(encoding="utf-8")),
        json.loads(args.economics.read_text(encoding="utf-8")),
        rows,
        json.loads(args.tape_audit.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, sort_keys=True))


if __name__ == "__main__":
    main()
