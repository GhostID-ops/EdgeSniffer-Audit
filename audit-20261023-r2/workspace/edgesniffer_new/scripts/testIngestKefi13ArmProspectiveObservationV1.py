#!/usr/bin/env python3
import copy
import hashlib
import json
import tempfile
from pathlib import Path

from ingestKefi13ArmProspectiveObservationV1 import ingest


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    registry = json.loads((ROOT / "research/kefi-13-arm-program-v1/registry.json").read_text(encoding="utf-8"))
    policies = json.loads((ROOT / "research/kefi-13-arm-program-v1/frozen-policies.json").read_text(encoding="utf-8"))
    manifest = {
        "collection_mode": "RECEIVE_ONLY_OUTCOME_BLIND",
        "status": "BLOCKED_SOURCE_NO_PACKETS",
        "cohort_id": "test-cohort-v1",
        "cohort_started_at_utc": "2026-10-18T23:00:00Z",
        "registry_sha256": hashlib.sha256((ROOT / "research/kefi-13-arm-program-v1/registry.json").read_bytes()).hexdigest(),
        "policies_sha256": hashlib.sha256((ROOT / "research/kefi-13-arm-program-v1/frozen-policies.json").read_bytes()).hexdigest(),
        "partitions": [],
    }
    arm = registry["arms"][0]
    policy = next(row for row in policies["policies"] if row["arm_id"] == arm["id"])
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)
        raw = path / "arm01.jsonl"
        raw.write_text("", encoding="utf-8")
        manifest["partitions"] = [{
            "arm_id": arm["id"], "venue": arm["venue"], "venue_class": arm["venue_class"],
            "policy_id": policy["policy_id"], "decoder_status": arm["decoder"]["status"],
            "evaluator_status": arm["evaluator"]["status"], "raw_observations": str(raw),
        }]
        valid = {
            "schema": "KEFI_13_ARM_OBSERVATION_V1", "observation_id": "obs-1",
            "cohort_id": "test-cohort-v1", "captured_at_utc": "2026-10-19T00:00:00Z",
            "source": "fixture", "arm_id": arm["id"], "venue": arm["venue"],
            "venue_class": arm["venue_class"], "policy_id": policy["policy_id"],
            "registry_sha256": manifest["registry_sha256"], "policies_sha256": manifest["policies_sha256"],
            "decoder_status": arm["decoder"]["status"], "evaluator_status": arm["evaluator"]["status"],
            "finalized_slot": 123, "state": {"mint": "mint-1", "pre_entry": {"reserve": 1}},
        }
        result = ingest(manifest, [valid])
        assert result["status"] == "APPENDED_OUTCOME_BLIND", result
        assert len(raw.read_text(encoding="utf-8").splitlines()) == 1
        duplicate = ingest(manifest, [valid])
        assert duplicate["status"] == "REJECTED_NO_APPEND"
        pre_boundary = copy.deepcopy(valid)
        pre_boundary["observation_id"] = "obs-pre-boundary"
        pre_boundary["captured_at_utc"] = "2026-10-18T22:59:59Z"
        pre_result = ingest(manifest, [pre_boundary])
        assert pre_result["status"] == "REJECTED_NO_APPEND"
        assert pre_result["errors"][0]["reason"] == "OBSERVATION_PRE_COHORT_BOUNDARY"
        bad = copy.deepcopy(valid)
        bad["observation_id"] = "obs-2"
        bad["state"]["pnl_sol"] = 1
        rejected = ingest(manifest, [bad])
        assert rejected["status"] == "REJECTED_NO_APPEND"
        assert rejected["errors"][0]["reason"] == "OUTCOME_FIELD_FORBIDDEN"
        bad_arm = copy.deepcopy(valid)
        bad_arm["observation_id"] = "obs-3"
        bad_arm["venue_class"] = "WRONG"
        mismatch = ingest(manifest, [bad_arm])
        assert mismatch["errors"][0]["reason"] == "VENUE_CLASS_MISMATCH"
    print("PASS testIngestKefi13ArmProspectiveObservationV1")


if __name__ == "__main__":
    main()
