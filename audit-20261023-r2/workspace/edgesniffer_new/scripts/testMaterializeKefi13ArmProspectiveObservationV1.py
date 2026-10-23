import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("materializer", ROOT / "scripts/materializeKefi13ArmProspectiveObservationV1.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_materializes_direct_pumpswap_without_outcomes():
    row = {
        "is_tracked_teacher": True,
        "candidate_signature": "event-sig",
        "source_frame_sha256": "frame",
        "source_partition": "2026/10/19/02",
        "event_index": 0,
        "received_at_unix_ns": 1792375406218539235,
        "slot": 123,
        "exact_transition_validated": True,
    }
    registry = {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}]}
    policies = {"policies": [{"arm_id": "ARM07_PUMPSWAP_DIRECT", "policy_id": "policy"}]}
    manifest = {
        "cohort_id": "cohort",
        "cohort_started_at_utc": "2026-10-19T02:00:00Z",
        "registry_sha256": "registry",
        "policies_sha256": "policies",
        "partitions": [{
            "arm_id": "ARM07_PUMPSWAP_DIRECT", "venue": "PumpSwap direct pool", "venue_class": "PUMPSWAP",
            "policy_id": "policy", "decoder_status": "EXACT_CANONICAL_POOL_STATE", "evaluator_status": "EXACT",
        }],
    }
    receipt = {"window_id": "window", "window_manifest_sha256": "manifest"}
    resolutions = {"event-sig": {"arm_id": "ARM07_PUMPSWAP_DIRECT", "program_id": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "direct_invocation": True}}
    report, rows = module.run([row], receipt, manifest, registry, policies, resolutions)
    assert report["materialized_observations"] == 1
    assert report["rejected_rows"] == 0
    assert rows[0]["arm_id"] == "ARM07_PUMPSWAP_DIRECT"
    assert "pnl" not in str(rows[0]).lower()


def test_observation_id_is_stable_across_overlapping_windows():
    row = {
        "is_tracked_teacher": True, "candidate_signature": "event-sig", "source_frame_sha256": "frame",
        "source_partition": "2026/10/19/02", "event_index": 0, "received_at_unix_ns": 1792375406218539235,
        "slot": 123, "exact_transition_validated": True,
    }
    registry = {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}]}
    policies = {"policies": [{"arm_id": "ARM07_PUMPSWAP_DIRECT", "policy_id": "policy"}]}
    manifest = {
        "cohort_id": "cohort", "cohort_started_at_utc": "2026-10-19T00:00:00Z", "registry_sha256": "r", "policies_sha256": "p",
        "partitions": [{"arm_id": "ARM07_PUMPSWAP_DIRECT", "venue": "PumpSwap direct pool", "venue_class": "PUMPSWAP", "policy_id": "policy", "decoder_status": "EXACT_CANONICAL_POOL_STATE", "evaluator_status": "EXACT"}],
    }
    receipt = {"window_id": "w1", "window_manifest_sha256": "m"}
    resolutions = {"event-sig": {"arm_id": "ARM07_PUMPSWAP_DIRECT", "program_id": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "direct_invocation": True}}
    _, first = module.run([row], receipt, manifest, registry, policies, resolutions)
    receipt["window_id"] = "w2"
    _, second = module.run([row], receipt, manifest, registry, policies, resolutions)
    assert first[0]["observation_id"] == second[0]["observation_id"]
