import json
from pathlib import Path
from unittest.mock import patch

import kefi13ArmPumpSwapTapeSourceV1 as subject


def transition(side="BUY", user="teacher-wallet"):
    return {
        "side": side,
        "user": user,
        "amountMode": "EXACT_BASE",
        "amountRaw": 100,
        "baseMint": "mint",
        "pool": "pool",
        "quoteMint": "So11111111111111111111111111111111111111112",
        "postState": {"base": 900, "quote": 1100, "virtual_quote": 0},
        "limitQuoteAmount": 100,
        "minBaseAmount": 100,
        "lpFeeBps": 20,
        "poolQuoteDeltaRaw": 100,
        "curveQuoteAmountRaw": 101,
        "userQuoteAmountRaw": 100,
        "lpFeeRaw": 1,
        "protocolFeeBps": 5,
        "protocolFeeRaw": 0,
        "coinCreatorBps": 95,
        "creatorFeeRaw": 0,
        "cashbackBps": 0,
        "cashbackRaw": 0,
        "buybackBps": 0,
        "buybackFeeRaw": 0,
    }


def test_tape_row_is_exact_and_outcome_free():
    with patch.object(subject.pumpswap, "validate_transaction", return_value=[transition()]):
        rows = subject.tape_rows(
            {}, "sig", 1792379000, 123, "teacher-wallet",
            source_instance_id="source-a", continuity_epoch=2,
            durable_output_sequence_start=10,
        )
    assert len(rows) == 1
    assert rows[0]["is_tracked_teacher"] is True
    assert rows[0]["exact_transition_validated"] is True
    assert rows[0]["source_instance_id"] == "source-a"
    assert rows[0]["continuity_epoch"] == 2
    assert rows[0]["durable_output_sequence"] == 10
    assert rows[0]["instruction_amount_raw"] is None
    assert "pnl" not in json.dumps(rows[0]).lower()


def test_pool_discovery_uses_only_canonical_raw_source(tmp_path: Path):
    source = tmp_path / "raw.jsonl"
    source.write_text(json.dumps({"schema": "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1", "transaction": {"x": 1}}) + "\n")
    with patch.object(subject.pumpswap, "validate_transaction", return_value=[transition()]):
        assert subject.discover_pools(source) == {"pool"}


def test_existing_output_signatures_are_reused_for_restart_dedup(tmp_path: Path):
    output = tmp_path / "tape.jsonl"
    output.write_text(json.dumps({"schema": subject.SCHEMA, "candidate_signature": "already"}) + "\n")
    assert subject.existing_signatures(output) == {"already"}


def test_mixed_curve_route_is_not_admitted_to_direct_tape():
    transaction = {
        "meta": {"logMessages": [
            f"Program {subject.PUMP_CURVE_PROGRAM_ID} invoke [3]",
            "Program data: not-used-by-mock",
        ]},
    }
    with patch.object(subject.pumpswap, "decode_events", return_value=[object()]), \
         patch.object(subject.pumpswap, "decode_instructions", return_value=[]):
        assert subject.is_mixed_noncanonical_route(transaction) is True
