import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cohort_audit", ROOT / "scripts/auditKefi13ArmFreshCohortV1.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_clean_cohort_audit():
    manifest = {
        "cohort_id": "c", "cohort_started_at_utc": "2026-10-19T00:00:00Z",
        "registry_sha256": "r", "policies_sha256": "p",
        "partitions": [{"arm_id": "A", "venue": "v", "venue_class": "c", "policy_id": "p", "decoder_status": "d", "evaluator_status": "e", "raw_observations": "a.jsonl"}],
    }
    registry = {"arms": [{"id": "A"}]}
    policies = {"policies": [{"arm_id": "A"}]}
    with __import__("tempfile").TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "a.jsonl").write_text('{"schema":"KEFI_13_ARM_OBSERVATION_V1","observation_id":"o","cohort_id":"c","captured_at_utc":"2026-10-19T00:01:00Z","arm_id":"A","venue":"v","venue_class":"c","policy_id":"p","registry_sha256":"r","policies_sha256":"p","decoder_status":"d","evaluator_status":"e","finalized_slot":1,"state":{"raw_evidence":{}}}\n')
        result = module.run(manifest, registry, policies, root)
    assert result["status"] == "PASS_CLEAN_FRESH_COHORT"
    assert result["accepted_rows"] == 1
