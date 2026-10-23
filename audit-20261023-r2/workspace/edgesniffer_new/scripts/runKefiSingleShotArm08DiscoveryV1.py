#!/usr/bin/env python3
"""Run the bounded single-shot discovery surface independently for ARM08.

This is a read-only consumer of strict ARM08 materialized observations and a
PumpSwap causal tape.  It reuses the same surface/economics functions as the
ARM07 discovery engine, but keeps ARM08 evidence, metadata, and the policy
decision namespace separate.  It never freezes or promotes a policy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runKefiSingleShotArm07DiscoveryV1 import (
    HORIZONS_SECONDS,
    POLICIES,
    SIZES,
    build_entries,
    dynamic_surface,
    fixed_surface,
    load_json,
    load_jsonl,
    sha256_file,
    usable_tape_row,
)


ARM_ID = "ARM08_PUMPSWAP_CPI"


def analyze(manifest_path: Path, observations_path: Path, tape_path: Path) -> dict[str, object]:
    manifest = load_json(manifest_path)
    observations = [
        row for row in load_jsonl(observations_path)
        if row.get("arm_id") == ARM_ID
    ]
    tape = [row for row in load_jsonl(tape_path) if usable_tape_row(row)]
    entries, failures = build_entries(observations, tape)
    surface: list[dict[str, object]] = []
    for size in SIZES:
        for bps in (0, 50):
            for horizon in HORIZONS_SECONDS:
                result = fixed_surface(entries, size, horizon, bps)
                surface.append({
                    **result,
                    "selection_gate": {
                        "status": "HOLD_NO_SELECTION",
                        "reason": "Controls and fresh prospective validation are required before freezing.",
                        "minimum_actionable_trades": 50,
                        "actionable_trades": result["n"],
                        "controls_available": False,
                    },
                })
            for policy in POLICIES:
                result = dynamic_surface(entries, size, policy, bps)
                surface.append({
                    **result,
                    "selection_gate": {
                        "status": "HOLD_NO_SELECTION",
                        "reason": "Controls and fresh prospective validation are required before freezing.",
                        "minimum_actionable_trades": 50,
                        "actionable_trades": result["n"],
                        "controls_available": False,
                    },
                })
    return {
        "cohort_id": manifest["cohort_id"],
        "manifest_sha256": sha256_file(manifest_path),
        "observations_sha256": sha256_file(observations_path),
        "tape_sha256": sha256_file(tape_path),
        "genuine_arm08_observations": len(observations),
        "causal_entries": len(entries),
        "entry_failures": failures,
        "surface": surface,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    contract = load_json(args.contract)
    discovery = analyze(args.manifest, args.observations, args.tape)
    output = {
        "schema": "KEFI_SINGLE_SHOT_ARM08_DISCOVERY_SURFACE_V1",
        "read_only": True,
        "arm_id": ARM_ID,
        "contract_sha256": sha256_file(args.contract),
        "contract_name": contract["hypothesis"]["name"],
        "policy_selection": "DISABLED_DISCOVERY_ONLY",
        "prospective_validation": "REQUIRED",
        "controls_available": False,
        "cohort": discovery,
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SINGLE_SHOT_ARM08_DISCOVERY_COMPLETE_READ_ONLY",
        "genuine_arm08_observations": discovery["genuine_arm08_observations"],
        "causal_entries": discovery["causal_entries"],
        "policy_selection": output["policy_selection"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
