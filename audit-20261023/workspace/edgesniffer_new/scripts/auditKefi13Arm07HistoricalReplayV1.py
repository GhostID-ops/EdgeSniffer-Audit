#!/usr/bin/env python3
"""Audit a preserved ARM07 cohort against the current evaluator bundle.

This is a read-only research audit.  It never edits the preserved cohort,
registry, frozen policies, or collection service.  The output is a new
audit artifact containing the current code fingerprints and an exact replay
comparison against the sealed historical economics report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluateKefi13Arm07FreshEconomicsV1 import evaluate


DEFAULT_CODE_PATHS = (
    "scripts/pumpSwapExactEconomicsV1.py",
    "scripts/pumpSwapInitDecoderV1.py",
    "kefi_frozen_pumpswap_exit_surface_v1.py",
    "kefi_frozen_selection_actionability_evaluator_v1.py",
    "scripts/evaluateKefi13Arm07FreshEconomicsV1.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metric_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    controls = report["control_comparison"]
    return {
        "observations_n": metrics["observations_n"],
        "actionable_n": metrics["actionable_n"],
        "pnl_quote": metrics["pnl_quote"],
        "profit_factor": metrics["profit_factor"],
        "win_rate_pct": metrics["win_rate_pct"],
        "mfe_quote": metrics["mfe_quote"],
        "mae_quote": metrics["mae_quote"],
        "max_drawdown_quote": metrics["max_drawdown_quote"],
        "controls_n": metrics["controls_n"],
        "controls_actionable_n": metrics["controls_actionable_n"],
        "controls_pnl_quote": controls["pnl_quote"],
        "controls_profit_factor": controls["profit_factor"],
        "controls_win_rate_pct": controls["win_rate_pct"],
        "controls_outperformed": metrics["controls_outperformed"],
    }


def build_audit(
    *,
    root: Path,
    manifest_path: Path,
    observations_path: Path,
    tape_path: Path,
    expected_report_path: Path,
    registry_path: Path,
    policies_path: Path,
    policy_artifact_path: Path | None = None,
    quote_in_lamports: int = 100_000_000,
    exit_seconds: int = 30,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    expected = load_json(expected_report_path)
    observations = load_jsonl(observations_path)
    tape = load_jsonl(tape_path)
    registry = load_json(registry_path)
    policies = load_json(policies_path)

    replay = evaluate(observations, tape, quote_in_lamports, exit_seconds * 1_000_000_000)
    current_registry_sha256 = sha256_file(registry_path)
    current_policies_sha256 = sha256_file(policies_path)

    arm07 = next(arm for arm in registry["arms"] if arm["id"] == "ARM07_PUMPSWAP_DIRECT")
    arm07_policy = next(policy for policy in policies["policies"] if policy["arm_id"] == "ARM07_PUMPSWAP_DIRECT")

    observation_registry_hashes = sorted({row.get("registry_sha256") for row in observations})
    observation_policy_hashes = sorted({row.get("policies_sha256") for row in observations})
    observation_policy_ids = sorted({row.get("policy_id") for row in observations})

    expected_metrics = metric_snapshot(expected)
    replay_metrics = metric_snapshot(replay)
    checks = {
        "historical_manifest_matches_observations_registry": (
            observation_registry_hashes == [manifest["registry_sha256"]]
        ),
        "historical_manifest_matches_observations_policies": (
            observation_policy_hashes == [manifest["policies_sha256"]]
        ),
        "historical_observations_are_arm07": all(
            row.get("arm_id") == "ARM07_PUMPSWAP_DIRECT" for row in observations
        ),
        "historical_policy_id_is_frozen_arm07_policy": (
            observation_policy_ids == [arm07_policy["policy_id"]]
        ),
        "replay_uses_expected_notional": replay["paper_quote_in_lamports"] == quote_in_lamports,
        "replay_uses_expected_exit": replay["fixed_exit_seconds"] == float(exit_seconds),
        "replay_metrics_match_sealed_report": replay_metrics == expected_metrics,
        "current_registry_hash_matches_current_file": (
            current_registry_sha256
            == sha256_file(registry_path)
        ),
        "current_policy_bundle_hash_matches_current_file": (
            current_policies_sha256 == sha256_file(policies_path)
        ),
        "current_arm07_policy_is_same_named_contract": (
            arm07["frozen_policy"]["policy_id"] == arm07_policy["policy_id"]
            == expected["policy_id"]
        ),
        "current_arm07_decoder_is_exact": arm07["decoder"]["status"] == "EXACT_CANONICAL_POOL_STATE",
        "current_arm07_evaluator_is_exact": arm07["evaluator"]["status"] == "EXACT",
    }

    code_hashes = {}
    for relative in DEFAULT_CODE_PATHS:
        path = root / relative
        code_hashes[relative] = sha256_file(path)

    artifact_hashes = {
        "manifest_sha256": sha256_file(manifest_path),
        "observations_sha256": sha256_file(observations_path),
        "tape_sha256": sha256_file(tape_path),
        "sealed_economics_report_sha256": sha256_file(expected_report_path),
        "historical_registry_sha256": manifest["registry_sha256"],
        "historical_policies_sha256": manifest["policies_sha256"],
        "current_registry_sha256": current_registry_sha256,
        "current_policies_sha256": current_policies_sha256,
        "current_arm07_code_sha256": code_hashes,
    }
    if policy_artifact_path is not None and policy_artifact_path.exists():
        artifact_hashes["arm07_policy_artifact_sha256"] = sha256_file(policy_artifact_path)

    return {
        "schema": "KEFI_ARM07_HISTORICAL_REPLAY_AUDIT_V1",
        "status": "PASS_CURRENT_EVALUATOR_REPLAY" if all(checks.values()) else "FAIL_CURRENT_EVALUATOR_REPLAY",
        "read_only": True,
        "historical_cohort_id": manifest["cohort_id"],
        "historical_cohort_started_at_utc": manifest["cohort_started_at_utc"],
        "historical_registry_sha256": manifest["registry_sha256"],
        "current_registry_sha256": current_registry_sha256,
        "historical_policies_sha256": manifest["policies_sha256"],
        "current_policies_sha256": current_policies_sha256,
        "policy_id": expected["policy_id"],
        "notional_lamports": quote_in_lamports,
        "fixed_exit_seconds": exit_seconds,
        "sealed_metrics": expected_metrics,
        "replay_metrics": replay_metrics,
        "sealed_promotion_blocker": expected["promotion_blocker"],
        "replay_promotion_blocker": replay["promotion_blocker"],
        "checks": checks,
        "hashes": artifact_hashes,
        "input_paths": {
            "manifest": str(manifest_path),
            "observations": str(observations_path),
            "tape": str(tape_path),
            "sealed_economics_report": str(expected_report_path),
            "registry": str(registry_path),
            "policies": str(policies_path),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--sealed-report", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--policies", type=Path, required=True)
    parser.add_argument("--policy-artifact", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    audit = build_audit(
        root=args.root,
        manifest_path=args.manifest,
        observations_path=args.observations,
        tape_path=args.tape,
        expected_report_path=args.sealed_report,
        registry_path=args.registry,
        policies_path=args.policies,
        policy_artifact_path=args.policy_artifact,
    )
    args.out.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "checks": audit["checks"]}, sort_keys=True))


if __name__ == "__main__":
    main()
