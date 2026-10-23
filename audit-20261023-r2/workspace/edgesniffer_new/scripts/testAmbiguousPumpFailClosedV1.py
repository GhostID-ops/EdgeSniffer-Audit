import ambiguousPumpFailClosedV1 as subject


def test_unresolved_pump_evidence_is_preserved_and_not_admitted():
    result = subject.resolve({"candidate_arm_ids": ["ARM13"], "observed_program_ids": ["pump-program"]})
    assert result["arm_id"] == subject.ARM_ID
    assert result["status"] == "UNRESOLVED_FAIL_CLOSED"
    assert result["economic_entry"] is False


def test_resolved_venue_cannot_be_absorbed_by_ambiguous_arm():
    result = subject.resolve({"canonical_venue": "PUMPSWAP", "observed_program_ids": ["pumpswap"]})
    assert result["status"] == "REJECTED_CANONICAL_VENUE_PRESENT"
    assert result["economic_entry"] is False
