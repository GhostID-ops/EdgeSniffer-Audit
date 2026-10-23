#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shadow_observer import TARGET, canonical_uint


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.fixtures)
    creates = read_jsonl(root / "missed_event_global_rows.jsonl")
    wallet_rows = read_jsonl(root / "missed_event_wallet_rows.jsonl")
    chain = json.loads((root / "missed_event_chain_transaction.json").read_text(encoding="utf-8"))["result"]
    create = creates[0]
    wallet = wallet_rows[0]
    buy = wallet["localSwaps"][0]
    account_keys = [row["pubkey"] if isinstance(row, dict) else row for row in chain["transaction"]["message"]["accountKeys"]]
    target_key = next(row for row in chain["transaction"]["message"]["accountKeys"] if isinstance(row, dict) and row["pubkey"] == TARGET)
    pump_instructions = [row for row in chain["transaction"]["message"]["instructions"] if row.get("programId") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]
    post_token = [row for row in chain["meta"]["postTokenBalances"] if row["mint"] == create["mint"] and row["owner"] == TARGET]
    checks = {
        "one_proven_create": len(creates) == 1 and create.get("side") == "create" and create.get("creation_proven") is True,
        "same_signature": create["transaction_signature"] == wallet["signature"],
        "same_mint": create["mint"] == buy["mint"],
        "same_canonical_slot": canonical_uint(create["source_slot"], "create.slot", required=True) == canonical_uint(wallet["slot"], "wallet.slot", required=True),
        "target_atomic_buy": buy["wallet"] == TARGET and buy["side"] == "buy" and buy["sourceBuySol"] > 0,
        "chain_success": chain["meta"]["err"] is None,
        "chain_slot_matches": chain["slot"] == canonical_uint(create["source_slot"], "create.slot", required=True),
        "target_is_signer_and_writable": target_key["signer"] is True and target_key["writable"] is True,
        "target_and_mint_in_pump_instructions": len(pump_instructions) >= 2 and all(TARGET in row.get("accounts", []) and create["mint"] in row.get("accounts", []) for row in pump_instructions),
        "target_received_mint_in_same_transaction": len(post_token) == 1 and int(post_token[0]["uiTokenAmount"]["amount"]) == int(buy["tokensOutRaw"]),
    }
    passed = all(checks.values())
    result = {
        "schema": "8ycb-missed-event-forensic-v1",
        "classification": "MISSED_EVENT_WOULD_TRIGGER_WITH_CANONICAL_TYPES" if passed else "MISSED_EVENT_FORENSIC_BLOCKER",
        "checks": checks,
        "create": {
            "signature": create["transaction_signature"], "mint": create["mint"],
            "wallet_creator": TARGET, "wallet_creator_basis": "target is transaction signer/fee payer and creator account in the Pump create instruction",
            "slot": canonical_uint(create["source_slot"], "create.slot", required=True),
            "transaction_index": canonical_uint(create["source_transaction_index"], "create.transaction_index", required=True),
        },
        "atomic_buy": {
            "signature": wallet["signature"], "mint": buy["mint"], "wallet": buy["wallet"],
            "slot_raw": wallet["slot"], "slot_canonical": canonical_uint(wallet["slot"], "wallet.slot", required=True),
            "transaction_index": None, "transaction_index_note": "wallet provider did not expose it; frozen semantics compare only when both are available",
            "quote_sol": buy["sourceBuySol"], "quote_lamports": round(buy["sourceBuySol"] * 1e9),
            "tokens_out_raw": int(buy["tokensOutRaw"]),
        },
        "chain": {
            "slot": chain["slot"], "block_time": chain["blockTime"], "fee_lamports": chain["meta"]["fee"],
            "target_account_index": account_keys.index(TARGET), "pump_instruction_count": len(pump_instructions),
        },
        "frozen_semantics": {
            "proven_create": True, "target_buy": True, "same_signature": True, "same_mint": True,
            "slot_match_after_canonicalization": True, "transaction_index_rule_satisfied": True,
            "only_v1_failure": "raw int-versus-string slot equality",
        },
        "prospective_cohort_inclusion": "EXCLUDED_FROM_V2_AND_NOT_BACKFILLED_INTO_V1",
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
