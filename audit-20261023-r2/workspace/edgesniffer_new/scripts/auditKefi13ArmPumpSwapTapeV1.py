#!/usr/bin/env python3
"""Audit the exact PumpSwap receive-only tape and its fail-closed errors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_PUMPSWAP_EXACT_TAPE_V1"
FORBIDDEN = ("pnl", "roi", "profit_factor", "win_rate", "mfe", "mae", "drawdown", "exit", "outcome")
REQUIRED = {"schema", "candidate_signature", "event_index", "received_at_unix_ns", "slot", "source_frame_sha256", "is_tracked_teacher", "exact_transition_validated", "side", "mint", "pool", "quote_mint", "pool_base_raw", "pool_quote_raw", "pool_quote_delta", "virtual_quote_reserves", "lp_fee_bps", "protocol_fee_bps", "coin_creator_bps"}
PARITY_REQUIRED = {"source_instance_id", "continuity_epoch", "durable_output_sequence", "instruction_amount_raw", "instruction_limit_raw"}


def audit(rows: list[dict[str, Any]], state: dict[str, Any], *, require_parity_fields: bool = False) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    ids: set[tuple[str, int]] = set()
    for index, row in enumerate(rows, 1):
        missing = sorted(REQUIRED - row.keys())
        if require_parity_fields:
            missing = sorted(set(missing) | (PARITY_REQUIRED - row.keys()))
        if row.get("schema") != SCHEMA or missing:
            errors.append({"line": index, "reason": "SCHEMA_OR_REQUIRED_FIELD", "missing": missing})
        identity = (str(row.get("candidate_signature")), int(row.get("event_index", -1)))
        if identity in ids:
            errors.append({"line": index, "reason": "DUPLICATE_EVENT_ID", "identity": identity})
        ids.add(identity)
        forbidden = [key for key in row if any(part in str(key).casefold() for part in FORBIDDEN)]
        if forbidden:
            errors.append({"line": index, "reason": "OUTCOME_FIELD_PRESENT", "fields": forbidden})
        if row.get("exact_transition_validated") is not True or row.get("side") not in {"BUY", "SELL"}:
            errors.append({"line": index, "reason": "EXACT_TRANSITION_CONTRACT_FAILED"})
        if require_parity_fields and any(row.get(field) is None for field in PARITY_REQUIRED):
            errors.append({"line": index, "reason": "PARITY_FIELD_NULL"})
    if require_parity_fields and rows:
        source_ids = {row.get("source_instance_id") for row in rows}
        epochs = {row.get("continuity_epoch") for row in rows}
        sequences = [row.get("durable_output_sequence") for row in rows]
        if len(source_ids) != 1 or None in source_ids:
            errors.append({"reason": "PARITY_SOURCE_INSTANCE_NOT_UNIQUE"})
        if len(epochs) != 1 or None in epochs:
            errors.append({"reason": "PARITY_CONTINUITY_EPOCH_NOT_UNIQUE"})
        if sequences != list(range(int(sequences[0]), int(sequences[0]) + len(sequences))) if all(isinstance(value, int) for value in sequences) else True:
            errors.append({"reason": "PARITY_DURABLE_SEQUENCE_NOT_CONTIGUOUS"})
    decoder_errors = int(state.get("errors", 0))
    return {
        "schema": "KEFI_13_ARM_PUMPSWAP_TAPE_AUDIT_V1",
        "outcome_blind": True,
        "rows": len(rows),
        "unique_event_ids": len(ids),
        "schema_errors": errors,
        "decoder_error_count": decoder_errors,
        "decoder_error_samples": state.get("error_samples", []),
        "ignored_noncanonical_count": int(state.get("ignored_noncanonical", 0)),
        "ignored_noncanonical_samples": state.get("ignored_noncanonical_samples", []),
        "parity_contract": bool(require_parity_fields and not errors),
        "status": "PASS_TAPE_SCHEMA_FAIL_CLOSED_DECODER_ERRORS" if not errors else "FAIL_TAPE_SCHEMA",
        "economics_ready": not errors and decoder_errors == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-parity-fields", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.tape.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = audit(rows, json.loads(args.state.read_text(encoding="utf-8")), require_parity_fields=args.require_parity_fields)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "rows": result["rows"], "decoder_error_count": result["decoder_error_count"]}, sort_keys=True))
    if result["status"] == "FAIL_TAPE_SCHEMA":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
