import hashlib
import json
from datetime import datetime, timezone

import auditKefi13ArmFinalizedRpcSourceV1 as subject


def test_clean_raw_source_audit_records_partial_arm_coverage():
    tx = {"slot": 1, "meta": {"err": None}}
    row = {
        "schema": subject.RAW_SCHEMA,
        "signature": "sig",
        "captured_at_utc": "2026-10-19T03:00:00Z",
        "finalized": True,
        "tracked_wallet": "teacher",
        "transaction": tx,
        "transaction_sha256": hashlib.sha256(json.dumps(tx, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "observed_program_families": ["PUMPSWAP_PROGRAM"],
        "candidate_arm_ids": ["ARM07_PUMPSWAP_DIRECT"],
    }
    manifest = {"cohort_id": "c", "cohort_started_at_utc": "2026-10-19T02:00:00Z", "boundary_utc": "2026-10-19T02:00:00Z", "tracked_wallet": "teacher"}
    registry = {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}, {"id": "ARM10_ROUTER_ORCA_WHIRLPOOL"}]}
    result = subject.audit([row], {"errors": 0}, manifest, registry, datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc))
    assert result["status"] == "PASS_RAW_SOURCE_AUDIT"
    assert result["coverage"]["all_13_arms_observed"] is False


def test_cumulative_raw_source_ignores_rows_before_new_cohort_boundary():
    tx = {"slot": 1, "meta": {"err": None}}
    row = {
        "schema": subject.RAW_SCHEMA,
        "signature": "old-sig",
        "captured_at_utc": "2026-10-19T02:00:00Z",
        "finalized": True,
        "tracked_wallet": "teacher",
        "transaction": tx,
        "transaction_sha256": hashlib.sha256(json.dumps(tx, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "observed_program_families": ["PUMPSWAP_PROGRAM"],
        "candidate_arm_ids": ["ARM07_PUMPSWAP_DIRECT"],
    }
    manifest = {"cohort_id": "c2", "cohort_started_at_utc": "2026-10-19T03:00:00Z", "boundary_utc": "2026-10-19T03:00:00Z", "tracked_wallet": "teacher"}
    registry = {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}]}
    result = subject.audit([row], {"errors": 0}, manifest, registry, datetime(2026, 10, 19, 3, 1, tzinfo=timezone.utc))
    assert result["status"] == "FAIL_RAW_SOURCE_AUDIT"
    assert result["source"]["pre_boundary_rows_ignored"] == 1
    assert result["source"]["window_rows"] == 0
