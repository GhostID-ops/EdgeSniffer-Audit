import hashlib
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "kefi_pumpswap_paper_contract_v1.py"


def module():
    spec = importlib.util.spec_from_file_location("contract", SCRIPT); assert spec and spec.loader
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


def row():
    return {"received_at_unix_ns": 1, "source_instance_id": "source-a", "continuity_epoch": 1, "durable_output_sequence": 2, "event_index": 0,
            "base_amount": 1, "pool_base_raw": 2, "pool_quote_raw": 3, "pool_quote_delta": 1,
            "user_quote_amount": 2, "lp_fee": 1, "protocol_fee": 1, "coin_creator_fee": 0,
            "cashback": 0, "buyback_fee": 0, "virtual_quote_reserves": 0, "side": "buy",
            "quote_mint": "So11111111111111111111111111111111111111112", "amount_mode": "EXACT_BASE",
            "instruction_amount_raw": 1, "instruction_limit_raw": 2, "curve_quote_amount": 1,
            "exact_transition_validated": True}


def test_contract_requires_deterministic_order_and_exact_transition_fields():
    contract = module()
    assert contract.contract_for(row())["ready_for_h3_paper"] is True
    incomplete = contract.contract_for({"received_at_unix_ns": 1})
    assert incomplete["ready_for_h3_paper"] is False
    assert "MISSING_DETERMINISTIC_SOURCE_ORDER" in incomplete["blockers"]
    assert "MISSING_RAW_TRANSITION_FIELDS" in incomplete["blockers"]


def test_scientific_receipt_binding_rejects_unverified_or_replaced_rows(tmp_path):
    contract = module()
    rows = tmp_path / "rows.jsonl"; rows.write_text(json.dumps(row()) + "\n")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"market_rows_path": str(rows),
                                   "market_rows_sha256": hashlib.sha256(rows.read_bytes()).hexdigest(),
                                   "scientific_validation": {"rows": 1},
                                   "outcome_blind": True, "pnl_accessed": False}))
    _, blockers = contract.scientific_receipt_for(receipt, rows)
    assert blockers == []
    rows.write_text(json.dumps({**row(), "pool_quote_raw": 999}) + "\n")
    _, blockers = contract.scientific_receipt_for(receipt, rows)
    assert "SCIENTIFIC_RECEIPT_ROW_HASH_MISMATCH" in blockers
