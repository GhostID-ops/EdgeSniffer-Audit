#!/usr/bin/env python3
"""Small pure regressions for direct kEFi wallet-action extraction."""
from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("kefi_wallet_action_index_v1.py")
SPEC = importlib.util.spec_from_file_location("wallet_index", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def transaction(program: str, pre: str, post: str) -> dict:
    return {
        "transaction": {"message": {"accountKeys": [{"pubkey": "wallet"}], "instructions": [{"programId": program}]}},
        "meta": {
            "preBalances": [100], "postBalances": [90],
            "preTokenBalances": [{"owner": "wallet", "mint": "mint", "uiTokenAmount": {"amount": pre}}],
            "postTokenBalances": [{"owner": "wallet", "mint": "mint", "uiTokenAmount": {"amount": post}}],
            "innerInstructions": [],
        },
    }


def main() -> None:
    buy = MODULE.records_for_transaction("wallet", {"signature": "buy", "slot": 1, "blockTime": 1, "err": None}, transaction(next(iter(MODULE.PUMP_PROGRAMS)), "0", "10"))[0]
    assert buy["side"] == "BUY" and buy["action_type"] == "DIRECT_SWAP_BUY"
    sell = MODULE.records_for_transaction("wallet", {"signature": "sell", "slot": 2, "blockTime": 2, "err": None}, transaction(next(iter(MODULE.PUMP_PROGRAMS)), "10", "0"))[0]
    assert sell["side"] == "SELL" and sell["action_type"] == "DIRECT_SWAP_SELL"
    transfer = MODULE.records_for_transaction("wallet", {"signature": "transfer", "slot": 3, "blockTime": 3, "err": None}, transaction("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "10", "0"))[0]
    assert transfer["side"] == "TRANSFER_OUT" and transfer["action_type"] == "DIRECT_TOKEN_TRANSFER_OUT"
    print("PASS")


if __name__ == "__main__":
    main()
