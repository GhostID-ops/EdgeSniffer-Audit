import auditKefi13ArmQuoteLiveParityReadinessV1 as subject


def base():
    return (
        {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}]},
        {"arm_id": "ARM07_PUMPSWAP_DIRECT", "policy_id": "kefi-pumpswap-fixed-30s-challenge-v1"},
        {"decision": "PROMOTE_ARM07_TO_QUOTE_LIVE_PARITY_PAPER"},
        {
            "cohort": "FRESH_PROSPECTIVE",
            "outcome_source": "POST_FREEZE",
            "metrics": {"actionable_n": 68, "controls_outperformed": True, "pnl_quote": 0.3, "profit_factor": 1.4},
        },
        [{
            "received_at_unix_ns": 1,
            "source_instance_id": "source-a",
            "continuity_epoch": 1,
            "durable_output_sequence": 1,
            "instruction_amount_raw": 1,
            "instruction_limit_raw": 1,
            "exact_transition_validated": True,
        }],
        {"decoder_error_count": 0, "parity_contract": True},
    )


def test_ready_contract_has_no_blockers():
    result = subject.audit(*base())
    assert result["status"] == "READY_FOR_QUOTE_LIVE_PARITY_PAPER"
    assert result["live_trading_authorized"] is False


def test_current_tape_contract_fails_closed_on_missing_order_and_errors():
    args = list(base())
    args[4] = [{"received_at_unix_ns": 1, "exact_transition_validated": True}]
    args[5] = {"decoder_error_count": 2}
    result = subject.audit(*args)
    assert result["status"] == "BLOCKED_QUOTE_LIVE_PARITY_READINESS"
    assert "MISSING_DURABLE_OUTPUT_SEQUENCE" in result["blockers"]
    assert "EXACT_TAPE_DECODER_ERRORS_PRESENT" in result["blockers"]
