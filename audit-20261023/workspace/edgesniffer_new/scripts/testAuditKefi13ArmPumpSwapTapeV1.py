import auditKefi13ArmPumpSwapTapeV1 as subject


def test_tape_audit_distinguishes_clean_schema_from_decoder_errors():
    row = {
        key: 1 for key in subject.REQUIRED
        if key not in {"schema", "candidate_signature", "side", "mint", "pool", "quote_mint", "source_frame_sha256", "is_tracked_teacher", "exact_transition_validated"}
    }
    row.update({"schema": subject.SCHEMA, "candidate_signature": "sig", "event_index": 0, "side": "BUY", "mint": "mint", "pool": "pool", "quote_mint": "quote", "source_frame_sha256": "hash", "is_tracked_teacher": False, "exact_transition_validated": True})
    result = subject.audit([row], {"errors": 2, "error_samples": [{"error": "unsupported"}]})
    assert result["status"] == "PASS_TAPE_SCHEMA_FAIL_CLOSED_DECODER_ERRORS"
    assert result["economics_ready"] is False


def test_parity_audit_requires_deterministic_fields_and_contiguous_sequence():
    row = {
        key: 1 for key in subject.REQUIRED
        if key not in {"schema", "candidate_signature", "side", "mint", "pool", "quote_mint", "source_frame_sha256", "is_tracked_teacher", "exact_transition_validated"}
    }
    row.update({
        "schema": subject.SCHEMA, "candidate_signature": "sig", "event_index": 0,
        "side": "BUY", "mint": "mint", "pool": "pool", "quote_mint": "quote",
        "source_frame_sha256": "hash", "is_tracked_teacher": False,
        "exact_transition_validated": True, "source_instance_id": "source-a",
        "continuity_epoch": 1, "durable_output_sequence": 0,
        "instruction_amount_raw": 10, "instruction_limit_raw": 9,
    })
    result = subject.audit([row], {"errors": 0}, require_parity_fields=True)
    assert result["parity_contract"] is True
