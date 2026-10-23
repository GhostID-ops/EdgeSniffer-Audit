import auditKefi13ArmProgramCompletionV1 as subject


def test_completion_audit_does_not_call_one_arm_global_complete():
    registry = {"arms": [{"id": str(i)} for i in range(13)]}
    result = subject.audit(
        registry,
        {"status": "PASS", "exact_component_missing_paths": [], "frozen_policy_count": 13},
        {"outcome_blind": True, "arms": [{} for _ in range(13)], "economic_validation_performed": False},
        {"policies": [{} for _ in range(13)]},
        {"partitions": [{} for _ in range(13)], "live_trading_enabled": False},
        {"summary": {"module_coverage_pass": 12}},
        {"status": "PASS_PROSPECTIVE_SOURCE_GATE"},
        {"status": "PASS_CLEAN_FRESH_COHORT"},
        {"fresh_only": True, "winner": "ARM07", "min_actionable_n": 50},
        {"global_comparison_complete": False, "metrics": {"actionable_n": 50}},
        {"global_comparison_complete": False},
    )
    assert result["overall_status"] == "INCOMPLETE_GLOBAL_COMPARISON"
    assert result["global_winner_claim"] is False
    assert result["passed_requirement_count"] < result["requirement_count"]
