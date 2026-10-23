#!/usr/bin/env python3
"""Independently classify PumpSwap buys rejected by the strict validator.

The normal PumpSwap validator remains fail-closed.  This audit only proves
whether rejected records contain a matching successful wallet BUY and which
invariant still prevents economics admission.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pumpSwapExactEconomicsV1 as pumpswap
from exactCurveEconomicsV1 import base58_decode
from materializeWalletRouteCensusV1 import pumpswap_tx_with_extension


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_event(tx: dict[str, Any]) -> pumpswap.PumpSwapEvent | None:
    # The strict decoder already knows the canonical layout.  Temporarily
    # bypass only the reserve-state assertion so this audit can inspect the
    # event and classify the failed invariant without admitting it.
    original = pumpswap.PoolState.validate
    pumpswap.PoolState.validate = lambda self: None
    try:
        for line in (tx.get("meta") or {}).get("logMessages") or []:
            marker = "Program data: "
            if marker not in line:
                continue
            try:
                payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
            except Exception:
                continue
            if not payload.startswith(pumpswap.BUY_EVENT):
                continue
            if len(payload) > 483:
                payload = payload[:-32]
            try:
                return pumpswap.decode_event(payload)
            except Exception:
                continue
    finally:
        pumpswap.PoolState.validate = original
    return None


def classify(row: dict[str, Any]) -> dict[str, Any] | None:
    tx = row.get("transaction") or {}
    if (tx.get("meta") or {}).get("err") is not None:
        return None
    try:
        # Instruction decoding does not depend on the event's trailing
        # extension.  Use the raw transaction here so a reserve-state failure
        # cannot hide an otherwise inspectable wallet BUY.
        instructions = pumpswap.decode_instructions(tx)
        variant = "TRAILING_32_BYTES_EXTENSION" if any(
            len(base64.b64decode(line.split("Program data: ", 1)[1].strip(), validate=True)) > 483
            for line in (tx.get("meta") or {}).get("logMessages") or []
            if "Program data: " in line
        ) else "CANONICAL"
    except Exception as error:
        return {"signature": row.get("signature"), "status": "REJECTED_DECODE", "reason": type(error).__name__ + ":" + str(error)}
    event = raw_event(tx)
    if event is None:
        return None
    matches = [item for item in instructions if item.side == "BUY" and item.pool == event.pool and item.user == event.user]
    if len(matches) != 1 or event.user != row.get("wallet"):
        return None
    instruction = matches[0]
    checks = {
        "instruction_amount_matches_event": instruction.amount_raw == event.curve_quote_amount,
        "instruction_limit_matches_event": instruction.limit_raw == event.min_base_amount,
        "fee_arithmetic": True,
        "reserve_state": True,
    }
    try:
        pumpswap.validate_event_fees(event, instruction.amount_mode)
    except Exception:
        checks["fee_arithmetic"] = False
    try:
        event.pre_state.validate()
        event.post_state.validate()
    except Exception:
        checks["reserve_state"] = False
    if not all(checks.values()):
        status = "REJECTED_INDEPENDENT_CHECK"
    elif event.curve_quote_amount != event.pool_quote_delta + event.protocol_fee + event.coin_creator_fee + event.cashback:
        status = "REJECTED_QUOTE_IDENTITY_GAP"
    else:
        status = "INDEPENDENTLY_VALIDATED"
    return {
        "signature": row.get("signature"),
        "slot": row.get("slot"),
        "block_time": row.get("block_time"),
        "status": status,
        "decoder_variant": variant,
        "route": "PUMPSWAP_DIRECT",
        "wallet": event.user,
        "pool": event.pool,
        "base_mint": instruction.base_mint,
        "quote_mint": instruction.quote_mint,
        "instruction": dataclasses.asdict(instruction),
        "event": dataclasses.asdict(event),
        "checks": checks,
        "quote_identity_gap_raw": event.curve_quote_amount - (event.pool_quote_delta + event.protocol_fee + event.coin_creator_fee + event.cashback),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rejected = []
    for line in args.raw.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("program_family") != "PUMPSWAP_PROGRAM":
            continue
        try:
            tx, _ = pumpswap_tx_with_extension(row.get("transaction") or {})
            pumpswap.validate_transaction(tx)
        except Exception:
            result = classify(row)
            if result:
                rejected.append(result)
    output = {
        "schema": "PUMPSWAP_REJECTED_BUY_INDEPENDENT_AUDIT_V1",
        "read_only": True,
        "policy_frozen": False,
        "trading_enabled": False,
        "raw_sha256": sha256_file(args.raw),
        "rejected_buy_count": len(rejected),
        "statuses": {status: sum(1 for row in rejected if row.get("status") == status) for status in sorted({row.get("status") for row in rejected})},
        "records": rejected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PUMPSWAP_REJECTED_BUY_AUDIT_COMPLETE", "rejected_buy_count": len(rejected), "statuses": output["statuses"]}, sort_keys=True))


if __name__ == "__main__":
    main()
