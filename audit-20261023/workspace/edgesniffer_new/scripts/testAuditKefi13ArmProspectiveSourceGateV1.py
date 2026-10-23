import auditKefi13ArmProspectiveSourceGateV1 as subject


def test_gate_passes_without_claiming_global_completeness():
    result = subject.audit(
        {
            "cohort_id": "c",
            "status": "PASS_RAW_SOURCE_AUDIT",
            "source": {"transport_clean": True, "source_fresh": True, "raw_rows": 10, "unique_signatures": 10},
            "coverage": {"observed_arm_ids": ["ARM07_PUMPSWAP_DIRECT"]},
        },
        {"status": "PASS_OUTCOME_BLIND_FAIL_CLOSED", "materialized_observations": 3, "by_arm": {"ARM07_PUMPSWAP_DIRECT": 3}},
        {"cohort_id": "c", "status": "PASS_CLEAN_FRESH_COHORT", "accepted_rows": 3},
    )
    assert result["status"] == "PASS_PROSPECTIVE_SOURCE_GATE"
    assert result["global_comparison_complete"] is False
    assert result["blockers"] == []


def test_gate_blocks_stale_transport():
    result = subject.audit(
        {"status": "PASS_RAW_SOURCE_AUDIT", "source": {"transport_clean": False}},
        {"status": "PASS_OUTCOME_BLIND_FAIL_CLOSED", "materialized_observations": 1, "by_arm": {}},
        {"status": "PASS_CLEAN_FRESH_COHORT", "accepted_rows": 1},
    )
    assert result["status"] == "BLOCKED_PROSPECTIVE_SOURCE_GATE"
    assert "SOURCE_TRANSPORT_NOT_CLEAN" in result["blockers"]
