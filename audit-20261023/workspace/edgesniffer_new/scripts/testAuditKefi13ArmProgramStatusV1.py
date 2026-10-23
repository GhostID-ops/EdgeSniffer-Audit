import auditKefi13ArmProgramStatusV1 as subject


def test_no_data_is_not_a_loss_and_global_winner_is_blocked():
    result = subject.audit(
        {"arms": [{"id": "A", "venue": "Venue", "readiness": "READY"}] * 13},
        {"status": "PASS_RAW_SOURCE_AUDIT", "source": {"candidate_arm_counts": {}}},
        {"status": "PASS_OUTCOME_BLIND_FAIL_CLOSED", "by_arm": {}},
        {"status": "PASS_CLEAN_FRESH_COHORT"},
        {"arms": [{"arm_id": "A", "status": "NOT_EVALUATED_NO_FRESH_COHORT", "kill_decision": "DEFER_NO_DATA"}]},
        {"evaluated_arm_count": 0, "global_winner_claim": False, "decision": "DO_NOT_PROMOTE", "live_trading_authorized": False, "dust_execution_authorized": False},
        {"arms": [{"arm_id": "A", "exact_module_ready": True}]},
    )
    assert result["status"] == "PASS_STRUCTURAL_AUDIT_GLOBAL_COMPARISON_INCOMPLETE"
    assert result["losing_arm_kills"] == []
    assert result["no_data_arms"] == ["A"] * 13


def test_incomplete_comparison_cannot_claim_global_winner():
    result = subject.audit(
        {"arms": [{"id": f"A{i}", "venue": "Venue", "readiness": "READY"} for i in range(13)]},
        {"status": "PASS_RAW_SOURCE_AUDIT", "source": {"candidate_arm_counts": {}}},
        {"status": "PASS_OUTCOME_BLIND_FAIL_CLOSED", "by_arm": {}},
        {"status": "PASS_CLEAN_FRESH_COHORT"},
        {"arms": []},
        {"evaluated_arm_count": 1, "global_winner_claim": True, "decision": "PROMOTE_GLOBAL_WINNER_TO_QUOTE_LIVE_PARITY_PAPER", "live_trading_authorized": False, "dust_execution_authorized": False},
        {"arms": []},
    )
    assert result["status"] == "FAIL_PROGRAM_STATUS_AUDIT"
    assert "GLOBAL_WINNER_CLAIM_WITH_INCOMPLETE_COMPARISON" in result["invariants"]
