#!/usr/bin/env python3
"""Audit whether ARM08 is ready for a frozen-policy prospective gate.

This gate is intentionally stricter than discovery.  It does not choose a
policy.  It checks identity, causal coverage, lineage quality, independent
controls, and fresh-cohort requirements, while emitting descriptive ARM08
fingerprint clues that cannot use exit outcomes for selection.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from pathlib import Path
from typing import Any


ARM07 = "ARM07_PUMPSWAP_DIRECT"
ARM08 = "ARM08_PUMPSWAP_CPI"
MIN_GENUINE = 50
MIN_CAUSAL = 50
MAX_CAUSAL_FAILURE_RATE = 0.20
MIN_CONTEMPORANEOUS_CONTROLS = 50
CONTROL_WINDOW_NS = 300 * 1_000_000_000


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return values[index]


def stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "max": max(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
    }


def pre_quote_sol(event: dict[str, Any]) -> float:
    return (int(event["pool_quote_raw"]) - int(event["pool_quote_delta"])) / 1_000_000_000


def clue_summary(observations: list[dict[str, Any]]) -> dict[str, Any]:
    arm07 = [row for row in observations if row.get("arm_id") == ARM07]
    arm08 = [row for row in observations if row.get("arm_id") == ARM08]
    times = {
        arm: sorted(int(row["state"]["decoded_event"]["received_at_unix_ns"]) for row in rows)
        for arm, rows in ((ARM07, arm07), (ARM08, arm08))
    }
    arm07_times = times[ARM07]
    arm08_times = times[ARM08]
    nearest_gaps: list[float] = []
    if arm07_times:
        for timestamp in arm08_times:
            index = bisect.bisect_left(arm07_times, timestamp)
            candidates = []
            if index < len(arm07_times):
                candidates.append(abs(arm07_times[index] - timestamp))
            if index:
                candidates.append(abs(timestamp - arm07_times[index - 1]))
            nearest_gaps.append(min(candidates) / 1_000_000_000)
    arm08_events = [row["state"]["decoded_event"] for row in arm08]
    liquidity = [pre_quote_sol(event) for event in arm08_events]
    size_bps = {
        str(notional): stats([notional * 10_000 / (value * 1_000_000_000) for value in liquidity if value > 0])
        for notional in (100_000_000, 500_000_000, 1_000_000_000)
    }
    return {
        "arm07_count": len(arm07),
        "arm08_count": len(arm08),
        "arm07_time_window_unix_ns": [arm07_times[0], arm07_times[-1]] if arm07_times else None,
        "arm08_time_window_unix_ns": [arm08_times[0], arm08_times[-1]] if arm08_times else None,
        "nearest_arm07_control_gap_seconds": stats(nearest_gaps),
        "arm08_pre_trade_liquidity_quote_sol": stats(liquidity),
        "notional_to_pre_trade_liquidity_bps": size_bps,
        "interpretation": [
            "ARM07 and ARM08 are temporally separated in this snapshot; direct ARM07 is not a contemporaneous control for most ARM08 trades.",
            "The size-to-liquidity surface should be treated as a sizing-risk clue, not a selected policy.",
        ],
    }


def audit(args: argparse.Namespace) -> dict[str, Any]:
    admission = load_json(args.admission_report)
    materialization = load_json(args.materialization_report)
    discovery = load_json(args.discovery_report)
    normalization = load_json(args.tape_normalization_report)
    observations = load_jsonl(args.observations)
    receipt = load_json(args.snapshot_receipt)

    admitted = int(admission.get("admitted_arm08_count", 0))
    materialized = int(materialization.get("by_arm", {}).get(ARM08, 0))
    genuine = int(discovery.get("cohort", {}).get("genuine_arm08_observations", 0))
    causal = int(discovery.get("cohort", {}).get("causal_entries", 0))
    failures = sum(int(value) for value in (discovery.get("cohort", {}).get("entry_failures", {}) or {}).values())
    causal_total = causal + failures
    failure_rate = failures / causal_total if causal_total else 1.0
    synthetic_lineage = bool(normalization.get("lineage_is_synthetic", True))
    clues = clue_summary(observations)
    contemporaneous = int(sum(
        1 for gap in [clues["nearest_arm07_control_gap_seconds"]["min"]]
        if gap is not None and gap <= 300
    ))
    # Count every ARM08 event within the control window, not only the minimum.
    arm07_times = sorted(
        int(row["state"]["decoded_event"]["received_at_unix_ns"])
        for row in observations if row.get("arm_id") == ARM07
    )
    contemporaneous = 0
    for row in observations:
        if row.get("arm_id") != ARM08 or not arm07_times:
            continue
        timestamp = int(row["state"]["decoded_event"]["received_at_unix_ns"])
        index = bisect.bisect_left(arm07_times, timestamp)
        gap = min(
            [abs(arm07_times[index] - timestamp) if index < len(arm07_times) else 10**30,
             abs(timestamp - arm07_times[index - 1]) if index else 10**30]
        )
        contemporaneous += int(gap <= CONTROL_WINDOW_NS)

    checks = {
        "strict_admissions_minimum": admitted >= MIN_GENUINE,
        "materializer_matches_gate": admitted == materialized,
        "discovery_matches_materializer": materialized == genuine,
        "causal_entries_minimum": causal >= MIN_CAUSAL,
        "causal_failure_rate_bounded": failure_rate <= MAX_CAUSAL_FAILURE_RATE,
        "lineage_complete": not synthetic_lineage,
        "contemporaneous_controls_minimum": contemporaneous >= MIN_CONTEMPORANEOUS_CONTROLS,
        "fresh_prospective_validation": False,
        "policy_selection_disabled": discovery.get("policy_selection") == "DISABLED_DISCOVERY_ONLY",
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return {
        "schema": "KEFI_ARM08_PROSPECTIVE_GATE_AUDIT_V1",
        "status": "READY_FOR_FRESH_PROSPECTIVE_VALIDATION" if not reasons else "BLOCKED_NOT_READY",
        "read_only": True,
        "arm_id": ARM08,
        "thresholds": {
            "minimum_genuine_admissions": MIN_GENUINE,
            "minimum_causal_entries": MIN_CAUSAL,
            "maximum_causal_failure_rate": MAX_CAUSAL_FAILURE_RATE,
            "minimum_contemporaneous_controls": MIN_CONTEMPORANEOUS_CONTROLS,
            "control_window_seconds": 300,
        },
        "counts": {
            "strict_admitted": admitted,
            "materialized_arm08": materialized,
            "discovery_genuine": genuine,
            "causal_entries": causal,
            "causal_failures": failures,
            "contemporaneous_arm07_controls": contemporaneous,
        },
        "causal_failure_rate": failure_rate,
        "checks": checks,
        "blocking_reasons": reasons,
        "clues": clues,
        "snapshot_id": receipt.get("snapshot_id"),
        "no_policy_frozen": True,
        "no_trading_enabled": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-receipt", type=Path, required=True)
    parser.add_argument("--admission-report", type=Path, required=True)
    parser.add_argument("--materialization-report", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--discovery-report", type=Path, required=True)
    parser.add_argument("--tape-normalization-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "blocking_reasons": report["blocking_reasons"]}, sort_keys=True))


if __name__ == "__main__":
    main()
