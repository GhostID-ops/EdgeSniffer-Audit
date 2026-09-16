#!/usr/bin/env python3
"""Exact, fail-closed Raydium CPMM swap-event normalization."""

from __future__ import annotations

import base64
import dataclasses
import struct
from typing import Any, Iterable

from exactCurveEconomicsV1 import ExactCurveError, base58_decode
from launchLabExactEconomicsV1 import b58encode


SCHEMA = "RAYDIUM_CPMM_EXACT_ECONOMICS_V1"
PROGRAM_ID = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
SWAP_BASE_INPUT = bytes([143, 190, 90, 218, 196, 30, 51, 222])
SWAP_BASE_OUTPUT = bytes([55, 217, 98, 86, 163, 74, 180, 173])
SWAP_EVENT = bytes([64, 198, 205, 232, 38, 8, 113, 226])


class RaydiumCpmmExactError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class SwapInstruction:
    amount_mode: str
    amount_raw: int
    limit_raw: int
    payer: str
    pool: str
    input_account: str
    output_account: str
    input_mint: str
    output_mint: str
    coordinate: str


@dataclasses.dataclass(frozen=True)
class SwapEvent:
    pool: str
    input_vault_before: int
    output_vault_before: int
    input_amount: int
    output_amount: int
    input_transfer_fee: int
    output_transfer_fee: int
    base_input: bool
    input_mint: str
    output_mint: str
    trade_fee: int
    creator_fee: int
    creator_fee_on_input: bool


def decode_event(payload: bytes) -> SwapEvent:
    if len(payload) != 170 or payload[:8] != SWAP_EVENT:
        raise RaydiumCpmmExactError("not a canonical 170-byte CPMM SwapEvent")
    pool = b58encode(payload[8:40])
    values = struct.unpack_from("<6Q", payload, 40)
    base_input = payload[88]
    if base_input not in (0, 1):
        raise RaydiumCpmmExactError("invalid CPMM base_input bool")
    input_mint = b58encode(payload[89:121])
    output_mint = b58encode(payload[121:153])
    trade_fee, creator_fee = struct.unpack_from("<2Q", payload, 153)
    creator_fee_on_input = payload[169]
    if creator_fee_on_input not in (0, 1):
        raise RaydiumCpmmExactError("invalid CPMM creator_fee_on_input bool")
    event = SwapEvent(
        pool, *values, bool(base_input), input_mint, output_mint,
        trade_fee, creator_fee, bool(creator_fee_on_input),
    )
    if min(values) < 0 or event.input_amount <= 0 or event.output_amount <= 0:
        raise RaydiumCpmmExactError("invalid CPMM swap amounts")
    if event.input_transfer_fee > event.input_amount:
        raise RaydiumCpmmExactError("CPMM input transfer fee exceeds input")
    if event.output_transfer_fee > event.output_amount:
        raise RaydiumCpmmExactError("CPMM output transfer fee exceeds output")
    return event


def decode_events(log_messages: Iterable[str]) -> list[SwapEvent]:
    rows: list[SwapEvent] = []
    stack: list[str] = []
    for line in log_messages:
        if line.startswith("Program ") and " invoke [" in line:
            stack.append(line.split()[1])
            continue
        if line.startswith("Program ") and (
            line.endswith(" success") or " failed:" in line
        ):
            program = line.split()[1]
            if stack and stack[-1] == program:
                stack.pop()
            elif program in stack:
                del stack[len(stack) - 1 - stack[::-1].index(program):]
            continue
        marker = "Program data: "
        if marker not in line or not stack or stack[-1] != PROGRAM_ID:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if payload.startswith(SWAP_EVENT):
            rows.append(decode_event(payload))
    return rows


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(row) for row in loaded.get("writable") or [])
    keys.extend(str(row) for row in loaded.get("readonly") or [])
    return keys


def compiled_instructions(tx: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    message = (tx.get("transaction") or {}).get("message") or {}
    for index, instruction in enumerate(message.get("instructions") or []):
        yield f"outer:{index}", instruction
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        outer = int(group.get("index") or 0)
        for index, instruction in enumerate(group.get("instructions") or []):
            yield f"outer:{outer}:inner:{index}", instruction


def decode_instructions(tx: dict[str, Any]) -> list[SwapInstruction]:
    keys = resolved_keys(tx)
    rows: list[SwapInstruction] = []
    for coordinate, instruction in compiled_instructions(tx):
        program = instruction.get("programId")
        if program is None and instruction.get("programIdIndex") is not None:
            index = int(instruction["programIdIndex"])
            program = keys[index] if index < len(keys) else None
        if program != PROGRAM_ID:
            continue
        try:
            raw = base58_decode(str(instruction.get("data") or ""))
        except ExactCurveError:
            continue
        if len(raw) != 24 or raw[:8] not in {SWAP_BASE_INPUT, SWAP_BASE_OUTPUT}:
            continue
        indices = [int(value) for value in instruction.get("accounts") or []]
        if len(indices) < 12 or any(index >= len(keys) for index in indices[:12]):
            raise RaydiumCpmmExactError(f"CPMM account layout incomplete: {coordinate}")
        accounts = [keys[index] for index in indices]
        rows.append(SwapInstruction(
            "EXACT_INPUT" if raw[:8] == SWAP_BASE_INPUT else "EXACT_OUTPUT",
            int.from_bytes(raw[8:16], "little"), int.from_bytes(raw[16:24], "little"),
            accounts[0], accounts[3], accounts[4], accounts[5],
            accounts[10], accounts[11], coordinate,
        ))
    return rows


def token_delta(tx: dict[str, Any], account: str, mint: str) -> tuple[int, str | None]:
    keys = resolved_keys(tx)
    if account not in keys:
        raise RaydiumCpmmExactError("CPMM token account absent from resolved keys")
    index = keys.index(account)
    owner: str | None = None
    values: dict[str, int] = {}
    for name in ("preTokenBalances", "postTokenBalances"):
        matches = [
            row for row in (tx.get("meta") or {}).get(name) or []
            if int(row.get("accountIndex") or -1) == index and str(row.get("mint") or "") == mint
        ]
        if len(matches) > 1:
            raise RaydiumCpmmExactError("ambiguous CPMM token balance")
        values[name] = int(((matches[0].get("uiTokenAmount") or {}).get("amount")) or 0) if matches else 0
        if matches and matches[0].get("owner"):
            owner = str(matches[0]["owner"])
    return values["postTokenBalances"] - values["preTokenBalances"], owner


def validate_transaction(tx: dict[str, Any]) -> list[dict[str, Any]]:
    if (tx.get("meta") or {}).get("err") is not None:
        return []
    instructions = decode_instructions(tx)
    events = decode_events((tx.get("meta") or {}).get("logMessages") or [])
    if not instructions and not events:
        return []
    if len(instructions) != len(events):
        raise RaydiumCpmmExactError(
            f"CPMM instruction/event cardinality mismatch {len(instructions)}/{len(events)}"
        )
    unmatched = list(instructions)
    results: list[dict[str, Any]] = []
    for event in events:
        candidates = [
            row for row in unmatched
            if row.pool == event.pool and row.input_mint == event.input_mint
            and row.output_mint == event.output_mint
        ]
        if len(candidates) != 1:
            raise RaydiumCpmmExactError("CPMM event cannot be joined uniquely")
        instruction = candidates[0]
        unmatched.remove(instruction)
        if instruction.amount_mode == "EXACT_INPUT" and instruction.amount_raw != (
            event.input_amount + event.input_transfer_fee
        ):
            raise RaydiumCpmmExactError("CPMM exact-input amount mismatch")
        if instruction.amount_mode == "EXACT_OUTPUT" and instruction.limit_raw != (
            event.output_amount - event.output_transfer_fee
        ):
            raise RaydiumCpmmExactError("CPMM exact-output amount mismatch")
        input_delta, input_owner = token_delta(
            tx, instruction.input_account, instruction.input_mint
        )
        output_delta, output_owner = token_delta(
            tx, instruction.output_account, instruction.output_mint
        )
        expected_input_delta = -(event.input_amount + event.input_transfer_fee)
        expected_output_delta = event.output_amount - event.output_transfer_fee
        if input_delta != expected_input_delta or output_delta != expected_output_delta:
            raise RaydiumCpmmExactError("CPMM wallet/event token delta mismatch")
        results.append({
            "schema": SCHEMA + "_TRANSITION",
            "classification": "EXACT_CPMM_TRANSITION_VALIDATED",
            "programId": PROGRAM_ID,
            "pool": event.pool,
            "payer": instruction.payer,
            "inputOwner": input_owner,
            "outputOwner": output_owner,
            "inputMint": event.input_mint,
            "outputMint": event.output_mint,
            "amountMode": instruction.amount_mode,
            "inputAmountRaw": event.input_amount,
            "outputAmountRaw": event.output_amount,
            "inputTransferFeeRaw": event.input_transfer_fee,
            "outputTransferFeeRaw": event.output_transfer_fee,
            "tradeFeeRaw": event.trade_fee,
            "creatorFeeRaw": event.creator_fee,
            "creatorFeeOnInput": event.creator_fee_on_input,
            "walletInputDeltaRaw": input_delta,
            "walletOutputDeltaRaw": output_delta,
            "preState": {
                "inputVaultRaw": event.input_vault_before,
                "outputVaultRaw": event.output_vault_before,
            },
            "postState": {
                "inputVaultRaw": event.input_vault_before + event.input_amount,
                "outputVaultRaw": event.output_vault_before - event.output_amount,
            },
            "instructionCoordinate": instruction.coordinate,
            "exactObservedTransition": True,
        })
    return results
