#!/usr/bin/env python3
"""Freeze the non-negotiable mechanics contract for future kEfi paper replay.

This is deliberately a contract, not a strategy evaluator.  It makes two
requirements explicit before any H3 paper result can exist: source-order must
be deterministic at equal receipt timestamps, and PumpSwap execution must be
reconstructed from raw instruction/event transitions rather than approximate
fee-BPS arithmetic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


SCHEMA = "KEFI_PUMPSWAP_PAPER_CONTRACT_V1"
REQUIRED_SOURCE_ORDER = "durable_output_sequence"
REQUIRED_RAW_TRANSITION_FIELDS = {
    "base_amount", "pool_base_raw", "pool_quote_raw", "pool_quote_delta",
    "user_quote_amount", "lp_fee", "protocol_fee", "coin_creator_fee",
    "cashback", "buyback_fee", "virtual_quote_reserves", "side", "quote_mint",
    "amount_mode", "instruction_amount_raw", "instruction_limit_raw",
    "curve_quote_amount", "exact_transition_validated",
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w") as handle:
        handle.write(canonical(payload) + "\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def contract_for(sample_row: dict) -> dict:
    missing_order = (REQUIRED_SOURCE_ORDER not in sample_row or sample_row.get("continuity_epoch") is None
                     or not sample_row.get("source_instance_id"))
    missing_transition = sorted(REQUIRED_RAW_TRANSITION_FIELDS - sample_row.keys())
    blockers = []
    if missing_order:
        blockers.append("MISSING_DETERMINISTIC_SOURCE_ORDER")
    if missing_transition:
        blockers.append("MISSING_RAW_TRANSITION_FIELDS")
    elif sample_row.get("exact_transition_validated") is not True:
        blockers.append("UNVALIDATED_RAW_PUMPSWAP_TRANSITION")
    return {
        "schema": SCHEMA,
        "availability_clock": "received_at_unix_ns",
        "decision_order": ["received_at_unix_ns", "source_instance_id", "continuity_epoch", REQUIRED_SOURCE_ORDER, "event_index"],
        "same_event_visibility": "EXCLUDED: decision state is strictly before the entry event",
        "entry_delay": "POSITIVE_PREREGISTERED_NS; first supported same-epoch state at or after delay",
        "entry_state": "RECONSTRUCT_FROM_RAW_PUMPSWAP_PRE_STATE",
        "fee_slippage": "EXACT_RAW_PUMPSWAP_TRANSITIONS_ONLY; NO_SUMMED_BPS_APPROXIMATION",
        "buy_semantics": "VALIDATE_RAW_INSTRUCTION_EVENT_AND_QUOTE_EXACT_INPUT_OR_EXACT_BASE",
        "sell_semantics": "VALIDATE_RAW_INSTRUCTION_EVENT_AND_QUOTE_EXACT_TOKEN_SELL",
        "non_tradeable": "FAIL_CLOSED; unsupported layout, unvalidated transition, missing mark, or cross-epoch state is excluded",
        "exit": "SAME_EPOCH_OBSERVED_STATE_MARK_ONLY; no unvalidated hypothetical fill",
        "controls": "DETERMINISTIC_SAME_EPOCH_SAME_QUOTE_LAYOUT_DIFFERENT_MINT",
        "clustering": "EPOCH_POOL_FIXED_TIME_BUCKET",
        "outcome_blind": True,
        "pnl_accessed": False,
        "ready_for_h3_paper": not blockers,
        "blockers": blockers,
        "sample_row_fields": sorted(sample_row.keys()),
    }


def scientific_receipt_for(receipt_path: Path, sample_row: Path) -> tuple[dict, list[str]]:
    """Bind paper mechanics to a receipt produced by strict epoch admission.

    A row file alone is not scientific evidence: the receipt is the durable
    proof that every row was checked against the frozen boundary/epoch policy.
    """
    blockers: list[str] = []
    try:
        receipt = json.loads(receipt_path.read_text())
        validation = receipt.get("scientific_validation")
        recorded_rows = Path(str(receipt.get("market_rows_path", "")))
        if not isinstance(validation, dict) or int(validation.get("rows", 0)) <= 0:
            blockers.append("MISSING_SCIENTIFIC_EPOCH_VALIDATION")
        if not recorded_rows.is_file() or recorded_rows.resolve() != sample_row.resolve():
            blockers.append("SCIENTIFIC_RECEIPT_ROW_PATH_MISMATCH")
        elif receipt.get("market_rows_sha256") != sha256(sample_row):
            blockers.append("SCIENTIFIC_RECEIPT_ROW_HASH_MISMATCH")
        if receipt.get("outcome_blind") is not True or receipt.get("pnl_accessed") is not False:
            blockers.append("INVALID_SCIENTIFIC_RECEIPT_SAFETY_FLAGS")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        receipt = {}
        blockers.append("INVALID_SCIENTIFIC_RECEIPT")
    return receipt, blockers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-row", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scientific-receipt", type=Path, required=True)
    args = parser.parse_args()
    with args.sample_row.open() as handle:
        line = next((item for item in handle if item.strip()), "")
    if not line:
        raise SystemExit("FAIL_CLOSED: empty sample row file")
    result = contract_for(json.loads(line))
    receipt, scientific_blockers = scientific_receipt_for(args.scientific_receipt, args.sample_row)
    result["blockers"] = sorted(set(result["blockers"] + scientific_blockers))
    result["ready_for_h3_paper"] = not result["blockers"]
    result["sample_row_sha256"] = sha256(args.sample_row)
    result["scientific_receipt_sha256"] = sha256(args.scientific_receipt)
    result["scientific_validation"] = receipt.get("scientific_validation")
    atomic_json(args.out, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
