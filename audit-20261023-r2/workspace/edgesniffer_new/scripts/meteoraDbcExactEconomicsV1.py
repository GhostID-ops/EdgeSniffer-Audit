#!/usr/bin/env python3
"""Exact, fail-closed Meteora Dynamic Bonding Curve swap normalization."""

from __future__ import annotations

import dataclasses
import hashlib
import struct
from typing import Any, Iterable

from exactCurveEconomicsV1 import ExactCurveError, base58_decode
from launchLabExactEconomicsV1 import b58encode


SCHEMA = "METEORA_DBC_EXACT_ECONOMICS_V1"
PROGRAM_ID = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
SWAP = hashlib.sha256(b"global:swap").digest()[:8]
SWAP2 = hashlib.sha256(b"global:swap2").digest()[:8]
SWAP2_WITH_TRANSFER_HOOK = hashlib.sha256(
    b"global:swap2_with_transfer_hook"
).digest()[:8]
EVT_SWAP2 = hashlib.sha256(b"event:EvtSwap2").digest()[:8]
EVT_SWAP2_WITH_TRANSFER_HOOK = hashlib.sha256(
    b"event:EvtSwap2WithTransferHook"
).digest()[:8]
EVENT_CPI = bytes([228, 69, 165, 46, 81, 203, 154, 29])

SWAP_MODES = {0: "EXACT_INPUT", 1: "PARTIAL_FILL", 2: "EXACT_OUTPUT"}


class MeteoraDbcExactError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class SwapInstruction:
    version: str
    amount_0_raw: int
    amount_1_raw: int
    swap_mode: int
    pool: str
    config: str
    input_account: str
    output_account: str
    base_vault: str
    quote_vault: str
    base_mint: str
    quote_mint: str
    payer: str
    coordinate: str
    outer_index: int


@dataclasses.dataclass(frozen=True)
class SwapEvent:
    pool: str
    config: str
    trade_direction: int
    has_referral: bool
    amount_0_raw: int
    amount_1_raw: int
    swap_mode: int
    included_fee_input_amount_raw: int
    excluded_fee_input_amount_raw: int
    amount_left_raw: int
    output_amount_raw: int
    next_sqrt_price: int
    trading_fee_raw: int
    protocol_fee_raw: int
    referral_fee_raw: int
    quote_reserve_amount_raw: int
    migration_threshold_raw: int
    current_timestamp: int
    coordinate: str
    outer_index: int
    transfer_hook: bool


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


def compiled_instructions(tx: dict[str, Any]) -> Iterable[tuple[str, int, dict[str, Any]]]:
    message = (tx.get("transaction") or {}).get("message") or {}
    for index, instruction in enumerate(message.get("instructions") or []):
        yield f"outer:{index}", index, instruction
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        outer = int(group.get("index") or 0)
        for index, instruction in enumerate(group.get("instructions") or []):
            yield f"outer:{outer}:inner:{index}", outer, instruction


def instruction_program(
    instruction: dict[str, Any], keys: list[str],
) -> str | None:
    program = instruction.get("programId")
    if program is not None:
        return str(program)
    index = instruction.get("programIdIndex")
    if index is None or int(index) >= len(keys):
        return None
    return keys[int(index)]


def instruction_accounts(
    instruction: dict[str, Any], keys: list[str],
) -> list[str]:
    accounts: list[str] = []
    for value in instruction.get("accounts") or []:
        if isinstance(value, str):
            accounts.append(value)
        elif int(value) < len(keys):
            accounts.append(keys[int(value)])
        else:
            raise MeteoraDbcExactError("DBC account index outside resolved keys")
    return accounts


def decode_instructions(tx: dict[str, Any]) -> list[SwapInstruction]:
    keys = resolved_keys(tx)
    rows: list[SwapInstruction] = []
    for coordinate, outer, instruction in compiled_instructions(tx):
        if instruction_program(instruction, keys) != PROGRAM_ID:
            continue
        try:
            raw = base58_decode(str(instruction.get("data") or ""))
        except ExactCurveError as error:
            raise MeteoraDbcExactError("invalid DBC instruction data") from error
        discriminator = raw[:8]
        if discriminator not in {SWAP, SWAP2, SWAP2_WITH_TRANSFER_HOOK}:
            continue
        minimum_length = 24 if discriminator == SWAP else 25
        if len(raw) < minimum_length:
            raise MeteoraDbcExactError(f"truncated DBC swap instruction: {coordinate}")
        accounts = instruction_accounts(instruction, keys)
        if len(accounts) < 15:
            raise MeteoraDbcExactError(f"DBC account layout incomplete: {coordinate}")
        amount_0, amount_1 = struct.unpack_from("<2Q", raw, 8)
        swap_mode = 0 if discriminator == SWAP else int(raw[24])
        if swap_mode not in SWAP_MODES:
            raise MeteoraDbcExactError("invalid DBC swap mode")
        version = {
            SWAP: "SWAP",
            SWAP2: "SWAP2",
            SWAP2_WITH_TRANSFER_HOOK: "SWAP2_WITH_TRANSFER_HOOK",
        }[discriminator]
        rows.append(SwapInstruction(
            version, amount_0, amount_1, swap_mode,
            accounts[2], accounts[1], accounts[3], accounts[4],
            accounts[5], accounts[6], accounts[7], accounts[8], accounts[9],
            coordinate, outer,
        ))
    return rows


def decode_event(payload: bytes, coordinate: str = "", outer_index: int = 0) -> SwapEvent:
    if len(payload) != 187 or payload[:8] not in {
        EVT_SWAP2, EVT_SWAP2_WITH_TRANSFER_HOOK,
    }:
        raise MeteoraDbcExactError("not a canonical DBC EvtSwap2")
    offset = 8
    pool = b58encode(payload[offset:offset + 32])
    offset += 32
    config = b58encode(payload[offset:offset + 32])
    offset += 32
    trade_direction = int(payload[offset])
    has_referral_raw = int(payload[offset + 1])
    offset += 2
    if trade_direction not in (0, 1) or has_referral_raw not in (0, 1):
        raise MeteoraDbcExactError("invalid DBC event enum/bool")
    amount_0, amount_1 = struct.unpack_from("<2Q", payload, offset)
    swap_mode = int(payload[offset + 16])
    offset += 17
    if swap_mode not in SWAP_MODES:
        raise MeteoraDbcExactError("invalid DBC event swap mode")
    included, excluded, amount_left, output = struct.unpack_from("<4Q", payload, offset)
    offset += 32
    next_sqrt_price = int.from_bytes(payload[offset:offset + 16], "little")
    offset += 16
    trading_fee, protocol_fee, referral_fee = struct.unpack_from("<3Q", payload, offset)
    offset += 24
    quote_reserve, migration_threshold, current_timestamp = struct.unpack_from(
        "<3Q", payload, offset
    )
    if included <= 0 or output <= 0 or next_sqrt_price <= 0:
        raise MeteoraDbcExactError("invalid DBC swap economics")
    return SwapEvent(
        pool, config, trade_direction, bool(has_referral_raw), amount_0, amount_1,
        swap_mode, included, excluded, amount_left, output, next_sqrt_price,
        trading_fee, protocol_fee, referral_fee, quote_reserve,
        migration_threshold, current_timestamp, coordinate, outer_index,
        payload[:8] == EVT_SWAP2_WITH_TRANSFER_HOOK,
    )


def decode_events(tx: dict[str, Any]) -> list[SwapEvent]:
    """Decode Anchor self-CPI events, scoped to the Meteora DBC program."""
    keys = resolved_keys(tx)
    rows: list[SwapEvent] = []
    for coordinate, outer, instruction in compiled_instructions(tx):
        if instruction_program(instruction, keys) != PROGRAM_ID:
            continue
        try:
            raw = base58_decode(str(instruction.get("data") or ""))
        except ExactCurveError as error:
            raise MeteoraDbcExactError("invalid DBC event instruction data") from error
        if raw[:8] != EVENT_CPI or raw[8:16] not in {
            EVT_SWAP2, EVT_SWAP2_WITH_TRANSFER_HOOK,
        }:
            continue
        rows.append(decode_event(raw[8:], coordinate, outer))
    return rows


def token_account_delta(
    tx: dict[str, Any], account: str, mint: str,
) -> tuple[int | None, str | None]:
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
            raise MeteoraDbcExactError("ambiguous DBC token balance")
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
    by_side: dict[str, dict[str, int]] = {"preTokenBalances": {}, "postTokenBalances": {}}
    for name in by_side:
        for row in (tx.get("meta") or {}).get(name) or []:
            if str(row.get("owner") or "") != wallet:
                continue
            mint = str(row.get("mint") or "")
            if mint:
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
    events = decode_events(tx)
    if not instructions and not events:
        return []
    if len(instructions) != len(events):
        raise MeteoraDbcExactError(
            f"DBC instruction/event cardinality mismatch {len(instructions)}/{len(events)}"
        )
    unmatched = list(instructions)
    results: list[dict[str, Any]] = []
    account_effects: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    for event in events:
        candidates = [
            row for row in unmatched
            if row.outer_index == event.outer_index and row.pool == event.pool
            and row.config == event.config and row.amount_0_raw == event.amount_0_raw
            and row.amount_1_raw == event.amount_1_raw and row.swap_mode == event.swap_mode
        ]
        if len(candidates) != 1:
            raise MeteoraDbcExactError("DBC event cannot be joined uniquely")
        instruction = candidates[0]
        unmatched.remove(instruction)
        if event.transfer_hook != (instruction.version == "SWAP2_WITH_TRANSFER_HOOK"):
            raise MeteoraDbcExactError("DBC transfer-hook instruction/event mismatch")
        if event.trade_direction == 0:
            input_mint, output_mint = instruction.base_mint, instruction.quote_mint
        else:
            input_mint, output_mint = instruction.quote_mint, instruction.base_mint
        input_delta, input_owner = token_account_delta(
            tx, instruction.input_account, input_mint
        )
        output_delta, output_owner = token_account_delta(
            tx, instruction.output_account, output_mint
        )
        result_index = len(results)
        account_effects.setdefault((instruction.input_account, input_mint), []).append(
            (result_index, -event.included_fee_input_amount_raw, "input")
        )
        account_effects.setdefault((instruction.output_account, output_mint), []).append(
            (result_index, event.output_amount_raw, "output")
        )
        results.append({
            "schema": SCHEMA + "_TRANSITION",
            "classification": "EXACT_DBC_TRANSITION_VALIDATED",
            "programId": PROGRAM_ID,
            "instructionVersion": instruction.version,
            "instructionCoordinate": instruction.coordinate,
            "eventCoordinate": event.coordinate,
            "pool": event.pool,
            "config": event.config,
            "payer": instruction.payer,
            "inputAccount": instruction.input_account,
            "outputAccount": instruction.output_account,
            "inputOwner": input_owner,
            "outputOwner": output_owner,
            "baseMint": instruction.base_mint,
            "quoteMint": instruction.quote_mint,
            "inputMint": input_mint,
            "outputMint": output_mint,
            "tradeDirection": "BASE_TO_QUOTE" if event.trade_direction == 0 else "QUOTE_TO_BASE",
            "swapMode": SWAP_MODES[event.swap_mode],
            "signedAmount0Raw": event.amount_0_raw,
            "signedAmount1Raw": event.amount_1_raw,
            "includedFeeInputAmountRaw": event.included_fee_input_amount_raw,
            "excludedFeeInputAmountRaw": event.excluded_fee_input_amount_raw,
            "amountLeftRaw": event.amount_left_raw,
            "outputAmountRaw": event.output_amount_raw,
            "tradingFeeRaw": event.trading_fee_raw,
            "protocolFeeRaw": event.protocol_fee_raw,
            "referralFeeRaw": event.referral_fee_raw,
            "walletInputAccountDeltaRaw": input_delta,
            "walletOutputAccountDeltaRaw": output_delta,
            "inputAccountDeltaValidationMode": "PENDING_TRANSACTION_AGGREGATE",
            "outputAccountDeltaValidationMode": "PENDING_TRANSACTION_AGGREGATE",
            "preState": None,
            "postState": {
                "sqrtPrice": event.next_sqrt_price,
                "quoteReserveAmountRaw": event.quote_reserve_amount_raw,
                "migrationThresholdRaw": event.migration_threshold_raw,
                "currentTimestamp": event.current_timestamp,
            },
            "exactObservedTransition": True,
        })
    for (account, mint), effects in account_effects.items():
        actual, _ = token_account_delta(tx, account, mint)
        expected = sum(effect for _, effect, _ in effects)
        matches = actual is not None and actual == expected
        if actual is None:
            mode = "EVENT_EXACT_ACCOUNT_BALANCE_UNAVAILABLE"
        elif not matches:
            # Router transactions commonly fund and consume an intermediate
            # account in other protocol legs, so its whole-transaction net is
            # not the DBC leg delta. The finalized DBC self-CPI event remains
            # authoritative for the DBC transition itself.
            mode = "EVENT_EXACT_ACCOUNT_NET_NOT_ISOLATED"
        else:
            mode = "SINGLE_LEG" if len(effects) == 1 else "TRANSACTION_AGGREGATE"
        for result_index, effect, side in effects:
            results[result_index][f"{side}AccountDeltaValidationMode"] = mode
            results[result_index][f"transaction{side.title()}AccountDeltaRaw"] = actual
            results[result_index][f"transaction{side.title()}AccountDeltaMatchesDbcEffects"] = (
                None if actual is None else matches
            )
            if len(effects) > 1 or not matches:
                results[result_index][f"wallet{side.title()}AccountDeltaRaw"] = None
            elif actual is not None:
                results[result_index][f"wallet{side.title()}AccountDeltaRaw"] = effect
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
        raise MeteoraDbcExactError("selected mint has no exact DBC acquisition")
    deltas = wallet_mint_deltas(tx, wallet)
    acquired = int(deltas.get(mint, 0))
    if acquired <= 0:
        raise MeteoraDbcExactError("selected wallet inventory did not increase")
    expected = sum(int(row["outputAmountRaw"]) for row in rows)
    adjustment = acquired - expected
    if adjustment > 0 or -adjustment * 100 > expected:
        raise MeteoraDbcExactError(
            f"DBC selected output/wallet delta mismatch {expected}/{acquired}"
        )
    input_mints = {str(row["inputMint"]) for row in rows}
    if len(input_mints) != 1:
        raise MeteoraDbcExactError("DBC selected route spend asset cardinality")
    spent_mint = next(iter(input_mints))
    spent_amount = sum(int(row["includedFeeInputAmountRaw"]) for row in rows)
    return {
        "schema": SCHEMA + "_SELECTED_ACQUISITION",
        "classification": "EXACT_DBC_SELECTED_ACQUISITION_VALIDATED",
        "underlyingVenue": "METEORA_DBC",
        "selectedAssetPerspective": True,
        "acquiredMint": mint,
        "spentMint": spent_mint,
        "acquiredAmountRaw": acquired,
        "spentAmountRaw": spent_amount,
        "knownDbcOutputRaw": expected,
        "postDbcRouteAdjustmentRaw": adjustment,
        "routeLegN": len(rows),
        "routeLegs": rows,
        "walletMintDeltasRaw": deltas,
        "exactObservedTransition": True,
    }
