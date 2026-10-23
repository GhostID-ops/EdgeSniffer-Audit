from __future__ import annotations

from pathlib import Path

from auditKefi13Arm07HistoricalReplayV1 import build_audit


ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "research/kefi-13-arm-program-v1/prospective-multivenue-20261019T025634Z"


def test_preserved_arm07_replays_exactly_under_current_bundle() -> None:
    audit = build_audit(
        root=ROOT,
        manifest_path=COHORT / "collection-manifest.json",
        observations_path=COHORT / "ARM07_PUMPSWAP_DIRECT/observations.jsonl",
        tape_path=COHORT / "source/pumpswap-cumulative-tape-current-v3.jsonl",
        expected_report_path=COHORT / "ARM07_PUMPSWAP_DIRECT/economics-report-cumulative-v4.json",
        registry_path=ROOT / "research/kefi-13-arm-program-v1/registry.json",
        policies_path=ROOT / "research/kefi-13-arm-program-v1/frozen-policies.json",
        policy_artifact_path=ROOT / "artifacts/kefi-pumpswap-prospective-policy-v1.json",
    )

    assert audit["status"] == "PASS_CURRENT_EVALUATOR_REPLAY"
    assert all(audit["checks"].values())
    assert audit["sealed_metrics"] == audit["replay_metrics"]
    assert audit["sealed_metrics"]["actionable_n"] == 161
    assert audit["sealed_metrics"]["pnl_quote"] == 1.61727519
