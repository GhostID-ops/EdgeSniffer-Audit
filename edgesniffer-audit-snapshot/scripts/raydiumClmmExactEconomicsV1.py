#!/usr/bin/env python3
"""Exact, fail-closed Raydium CLMM swap normalization."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import struct
from collections import defaultdict
from typing import Any, Iterable

from exactCurveEconomicsV1 import ExactCurveError, base58_decode
from launchLabExactEconomicsV1 import b58encode


SCHEMA = "RAYDIUM_CLMM_EXACT_ECONOMICS_V1"
PROGRAM_ID = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
SWAP = hashlib.sha256(b"global:swap").digest()[:8]
SWAP_V2 = hashlib.sha256(b"global:swap_v2").digest()[:8]
SWAP_EVENT = hashlib.sha256(b"event:SwapEvent").digest()[:8]


class RaydiumClmmExactError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class SwapInstruction:
    version: str
    amount_specified_raw: int
    other_amount_threshold_raw: int
    sqrt_price_limit_x64: int
    is_base_input: bool
    payer: str
    pool: str
    input_account: str
    output_account: str
    input_vault: str
    output_vault: str
    input_mint: str
    output_mint: str
    coordinate: str


@dataclasses.dataclass(frozen=True)
class SwapEvent:
    pool: str
    sender: str
    token_account_0: str
    token_account_1: str
    amount_0_raw: int
    transfer_fee_0_raw: int
    amount_1_raw: int
    transfer_fee_1_raw: int
    zero_for_one: bool
    sqrt_price_x64: int
    liquidity: int
    tick: int
    trade_fee_0_raw: int | None
    trade_fee_1_raw: int | None


def decode_event(payload: bytes) -> SwapEvent:
    if len(payload) not in {205, 221} or payload[:8] != SWAP_EVENT:
        raise RaydiumClmmExactError("not a canonical CLMM SwapEvent")
    offset = 8
    addresses = []
    for _ in range(4):
        addresses.append(b58encode(payload[offset:offset + 32]))
        offset += 32
    amount_0, transfer_0, amount_1, transfer_1 = struct.unpack_from("<4Q", payload, offset)
    offset += 32
    direction = payload[offset]
    offset += 1
    if direction not in (0, 1):
        raise RaydiumClmmExactError("invalid CLMM zero_for_one bool")
    sqrt_price = int.from_bytes(payload[offset:offset + 16], "little")
    offset += 16
    liquidity = int.from_bytes(payload[offset:offset + 16], "little")
    offset += 16
    tick = struct.unpack_from("<i", payload, offset)[0]
    offset += 4
    trade_fee_0 = trade_fee_1 = None
    if len(payload) == 221:
        trade_fee_0, trade_fee_1 = struct.unpack_from("<2Q", payload, offset)
    if amount_0 <= 0 or amount_1 <= 0 or sqrt_price <= 0:
        raise RaydiumClmmExactError("invalid CLMM swap economics")
    return SwapEvent(
        *addresses, amount_0, transfer_0, amount_1, transfer_1,
        bool(direction), sqrt_price, liquidity, tick, trade_fee_0, trade_fee_1,
    )


def decode_program_events(log_messages: Iterable[str]) -> list[SwapEvent]:
    """Decode only data emitted while CLMM is the active invocation frame."""
    stack: list[str] = []
    events: list[SwapEvent] = []
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
        if not stack or stack[-1] != PROGRAM_ID or marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if payload.startswith(SWAP_EVENT):
            events.append(decode_event(payload))
    return events


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


def token_account_identity(
    tx: dict[str, Any], account: str,
) -> tuple[str | None, str | None]:
    keys = resolved_keys(tx)
    if account not in keys:
        return None, None
    index = keys.index(account)
    identities = {
        (str(row.get("mint") or ""), str(row.get("owner") or ""))
        for name in ("preTokenBalances", "postTokenBalances")
        for row in (tx.get("meta") or {}).get(name) or []
        if int(row.get("accountIndex") or -1) == index
    }
    identities.discard(("", ""))
    if len(identities) != 1:
        return None, None
    mint, owner = next(iter(identities))
    return mint or None, owner or None


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
        except ExactCurveError as error:
            raise RaydiumClmmExactError("invalid CLMM instruction data") from error
        if raw[:8] not in {SWAP, SWAP_V2}:
            continue
        if len(raw) != 41:
            raise RaydiumClmmExactError(f"unexpected CLMM swap data length: {coordinate}")
        accounts = [int(value) for value in instruction.get("accounts") or []]
        minimum = 13 if raw[:8] == SWAP_V2 else 9
        if len(accounts) < minimum or any(value >= len(keys) for value in accounts[:minimum]):
            raise RaydiumClmmExactError(f"CLMM account layout incomplete: {coordinate}")
        values = [keys[value] for value in accounts]
        amount, threshold = struct.unpack_from("<2Q", raw, 8)
        sqrt_limit = int.from_bytes(raw[24:40], "little")
        is_base_input = raw[40]
        if is_base_input not in (0, 1):
            raise RaydiumClmmExactError("invalid CLMM is_base_input bool")
        if raw[:8] == SWAP_V2:
            input_mint, output_mint = values[11], values[12]
        else:
            input_mint, _ = token_account_identity(tx, values[3])
            output_mint, _ = token_account_identity(tx, values[4])
            if not input_mint:
                input_mint, _ = token_account_identity(tx, values[5])
            if not output_mint:
                output_mint, _ = token_account_identity(tx, values[6])
            if not input_mint or not output_mint:
                raise RaydiumClmmExactError("legacy CLMM mint identity unavailable")
        rows.append(SwapInstruction(
            "SWAP_V2" if raw[:8] == SWAP_V2 else "SWAP",
            amount, threshold, sqrt_limit, bool(is_base_input), values[0], values[2],
            values[3], values[4], values[5], values[6], input_mint, output_mint,
            coordinate,
        ))
    return rows


def token_account_delta(tx: dict[str, Any], account: str, mint: str) -> tuple[int | None, str | None]:
    keys = resolved_keys(tx)
    if account not in keys:
        return None, None
    index = keys.index(account)
    values: dict[str, int | None] = {}
    owner: str | None = None
    for name in ("preTokenBalances", "postTokenBalances"):
        matches = [
            row for row in (tx.get("meta") or {}).get(name) or []
            if int(row.get("accountIndex") or -1) == index
            and str(row.get("mint") or "") == mint
        ]
        if len(matches) > 1:
            raise RaydiumClmmExactError("ambiguous CLMM token balance")
        values[name] = (
            int(((matches[0].get("uiTokenAmount") or {}).get("amount")) or 0)
            if matches else None
        )
        if matches and matches[0].get("owner"):
            owner = str(matches[0]["owner"])
    if values["preTokenBalances"] is None or values["postTokenBalances"] is None:
        return None, owner
    return int(values["postTokenBalances"]) - int(values["preTokenBalances"]), owner


def wallet_mint_deltas(tx: dict[str, Any], wallet: str) -> dict[str, int]:
    keys = resolved_keys(tx)
    by_side: dict[str, dict[str, int]] = {"preTokenBalances": {}, "postTokenBalances": {}}
    for name in by_side:
        for row in (tx.get("meta") or {}).get(name) or []:
            if str(row.get("owner") or "") != wallet:
                continue
            mint = str(row.get("mint") or "")
            if not mint:
                continue
            by_side[name][mint] = by_side[name].get(mint, 0) + int(
                ((row.get("uiTokenAmount") or {}).get("amount")) or 0
            )
    return {
        mint: by_side["postTokenBalances"].get(mint, 0)
        - by_side["preTokenBalances"].get(mint, 0)
        for mint in set(by_side["preTokenBalances"]) | set(by_side["postTokenBalances"])
    }


def validate_transaction(tx: dict[str, Any]) -> list[dict[str, Any]]:
    if (tx.get("meta") or {}).get("err") is not None:
        return []
    instructions = decode_instructions(tx)
    events = decode_program_events((tx.get("meta") or {}).get("logMessages") or [])
    if not instructions and not events:
        return []
    if len(instructions) != len(events):
        raise RaydiumClmmExactError(
            f"CLMM instruction/event cardinality mismatch {len(instructions)}/{len(events)}"
        )
    unmatched = list(instructions)
    results: list[dict[str, Any]] = []
    for event in events:
        expected_input_account = event.token_account_0 if event.zero_for_one else event.token_account_1
        expected_output_account = event.token_account_1 if event.zero_for_one else event.token_account_0
        candidates = [
            row for row in unmatched
            if row.pool == event.pool and row.payer == event.sender
            and row.input_account == expected_input_account
            and row.output_account == expected_output_account
        ]
        if len(candidates) != 1:
            raise RaydiumClmmExactError("CLMM event cannot be joined uniquely")
        instruction = candidates[0]
        unmatched.remove(instruction)
        input_amount = event.amount_0_raw if event.zero_for_one else event.amount_1_raw
        input_transfer_fee = (
            event.transfer_fee_0_raw if event.zero_for_one else event.transfer_fee_1_raw
        )
        output_amount = event.amount_1_raw if event.zero_for_one else event.amount_0_raw
        output_transfer_fee = (
            event.transfer_fee_1_raw if event.zero_for_one else event.transfer_fee_0_raw
        )
        gross_input = input_amount + input_transfer_fee
        if instruction.is_base_input:
            valid_amount = (
                instruction.amount_specified_raw == gross_input
                if instruction.sqrt_price_limit_x64 == 0
                else gross_input <= instruction.amount_specified_raw
            )
            if not valid_amount:
                raise RaydiumClmmExactError("CLMM exact-input signed amount mismatch")
        else:
            valid_amount = (
                instruction.amount_specified_raw == output_amount
                if instruction.sqrt_price_limit_x64 == 0
                else output_amount <= instruction.amount_specified_raw
            )
            if not valid_amount:
                raise RaydiumClmmExactError("CLMM exact-output signed amount mismatch")
        input_delta, input_owner = token_account_delta(
            tx, instruction.input_account, instruction.input_mint
        )
        output_delta, output_owner = token_account_delta(
            tx, instruction.output_account, instruction.output_mint
        )
        results.append({
            "schema": SCHEMA + "_TRANSITION",
            "classification": "EXACT_CLMM_TRANSITION_VALIDATED",
            "programId": PROGRAM_ID,
            "instructionVersion": instruction.version,
            "instructionCoordinate": instruction.coordinate,
            "pool": event.pool,
            "payer": instruction.payer,
            "inputAccount": instruction.input_account,
            "outputAccount": instruction.output_account,
            "inputOwner": input_owner,
            "outputOwner": output_owner,
            "inputMint": instruction.input_mint,
            "outputMint": instruction.output_mint,
            "amountMode": "EXACT_INPUT" if instruction.is_base_input else "EXACT_OUTPUT",
            "signedAmountRaw": instruction.amount_specified_raw,
            "otherAmountThresholdRaw": instruction.other_amount_threshold_raw,
            "sqrtPriceLimitX64": instruction.sqrt_price_limit_x64,
            "inputAmountRaw": input_amount,
            "inputTransferFeeRaw": input_transfer_fee,
            "grossInputAmountRaw": gross_input,
            "outputAmountRaw": output_amount,
            "outputTransferFeeRaw": output_transfer_fee,
            "tradeFeeInputRaw": (
                event.trade_fee_0_raw if event.zero_for_one else event.trade_fee_1_raw
            ),
            "walletInputAccountDeltaRaw": input_delta,
            "walletOutputAccountDeltaRaw": output_delta,
            "zeroForOne": event.zero_for_one,
            "preState": None,
            "postState": {
                "sqrtPriceX64": event.sqrt_price_x64,
                "liquidity": event.liquidity,
                "tick": event.tick,
            },
            "exactObservedTransition": True,
        })
    return results


def selected_wallet_acquisition(
    tx: dict[str, Any], mint: str, wallet: str,
) -> dict[str, Any]:
    rows = [
        row for row in validate_transaction(tx)
        if row["outputMint"] == mint
        and (row["payer"] == wallet or row["outputOwner"] == wallet)
    ]
    if not rows:
        raise RaydiumClmmExactError("selected mint has no exact CLMM acquisition")
    deltas = wallet_mint_deltas(tx, wallet)
    acquired = int(deltas.get(mint, 0))
    if acquired <= 0:
        raise RaydiumClmmExactError("selected wallet inventory did not increase")
    expected = sum(int(row["outputAmountRaw"]) for row in rows)
    adjustment = acquired - expected
    # A router may deduct a small output-side platform fee after the CLMM leg.
    # A positive adjustment means another economic venue supplied inventory and
    # the route is not a CLMM-only selected transition.
    if adjustment > 0 or -adjustment * 100 > expected:
        raise RaydiumClmmExactError(
            f"CLMM selected output/wallet delta mismatch {expected}/{acquired}"
        )
    spent = [(asset, -amount) for asset, amount in deltas.items() if amount < 0]
    input_mints = {str(row["inputMint"]) for row in rows}
    wrapped_sol = "So11111111111111111111111111111111111111112"
    if wrapped_sol in input_mints:
        spent_mint = wrapped_sol
    elif len(spent) == 1:
        spent_mint = spent[0][0]
    else:
        raise RaydiumClmmExactError(f"CLMM route spend asset cardinality {len(spent)}")
    spent_amount = next((amount for asset, amount in spent if asset == spent_mint), None)
    return {
        "schema": SCHEMA + "_SELECTED_ACQUISITION",
        "classification": "EXACT_CLMM_SELECTED_ACQUISITION_VALIDATED",
        "underlyingVenue": "RAYDIUM_CLMM",
        "selectedAssetPerspective": True,
        "acquiredMint": mint,
        "spentMint": spent_mint,
        "acquiredAmountRaw": acquired,
        "spentAmountRaw": spent_amount,
        "knownClmmOutputRaw": expected,
        "postClmmRouterAdjustmentRaw": adjustment,
        "routeLegN": len(rows),
        "routeLegs": rows,
        "walletMintDeltasRaw": deltas,
        "exactObservedTransition": True,
    }
