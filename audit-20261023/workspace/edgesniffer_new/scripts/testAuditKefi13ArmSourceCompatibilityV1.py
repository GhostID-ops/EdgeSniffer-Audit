import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_audit", ROOT / "scripts/auditKefi13ArmSourceCompatibilityV1.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_metadata_only_source_is_blocked():
    snapshot = {
        "health": {
            "latestReceivedAtUtc": "2026-10-19T02:00:00Z",
            "continuityGaps": 0,
            "receiveErrors": 0,
            "reconnects": 0,
            "framesMetaIncomplete": 0,
        },
        "candidates": [{"schema": "KEFI_FULL_META_WALLET_PUMP_CANDIDATE_V1", "signature": "s"}],
        "latest_persisted_window_at_utc": "2026-10-18T07:18:13Z",
        "latest_persisted_window_raw_truth": True,
        "latest_persisted_window_complete": True,
        "canonical_observation_rows": 0,
        "fresh_canonical_observation_rows": 0,
    }
    manifest = {"cohort_id": "cohort", "cohort_started_at_utc": "2026-10-19T01:52:30Z"}
    result = MODULE.run(snapshot, {"arms": [{}] * 13}, manifest, datetime(2026, 10, 19, 2, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "BLOCKED_SOURCE_OR_MATERIALIZATION"
    assert "CANDIDATES_METADATA_ONLY_NOT_CANONICAL_OBSERVATIONS" in result["blockers"]
    assert "NO_POST_FREEZE_SEALED_WINDOW" in result["blockers"]


def test_clean_post_freeze_canonical_source_passes():
    fields = {
        "observation_id": "o",
        "cohort_id": "c",
        "arm_id": "ARM07_PUMPSWAP_DIRECT",
        "venue": "PUMPSWAP",
        "frozen_policy_id": "p",
        "registry_sha256": "r",
        "policy_sha256": "p",
        "finalized_slot": 10,
        "state": {"pool": "x"},
    }
    snapshot = {
        "health": {
            "latestReceivedAtUtc": "2026-10-19T02:00:00Z",
            "continuityGaps": 0,
            "receiveErrors": 0,
            "reconnects": 0,
            "framesMetaIncomplete": 0,
        },
        "candidates": [fields],
        "latest_persisted_window_at_utc": "2026-10-19T01:59:00Z",
        "latest_persisted_window_raw_truth": True,
        "latest_persisted_window_complete": True,
        "canonical_observation_rows": 1,
        "fresh_canonical_observation_rows": 1,
    }
    manifest = {"cohort_id": "c", "cohort_started_at_utc": "2026-10-19T01:52:30Z"}
    result = MODULE.run(snapshot, {"arms": [{}] * 13}, manifest, datetime(2026, 10, 19, 2, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "READY_FOR_CANONICAL_INGEST"
    assert result["blockers"] == []
