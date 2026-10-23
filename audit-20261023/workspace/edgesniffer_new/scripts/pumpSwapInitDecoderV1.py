#!/usr/bin/env python3
"""Fail-closed finalized PumpSwap pool-init / Pump-migration decoder.

The decoder is deliberately separate from PumpSwap trade economics.  A pool
becomes lifecycle eligible only when one finalized transaction contains both
the published ``create_pool`` instruction and a matching ``CreatePoolEvent``.
It does not infer an init from a first observed swap, token balance, or pool
account snapshot.

Published interface provenance:
https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json
"""

from __future__ import annotations

import base64
import dataclasses
from typing import Any, Iterable

from exactCurveEconomicsV1 import BorshReader, ExactCurveError, base58_decode
import launchLabExactEconomicsV1 as common
import pumpSwapExactEconomicsV1 as pumpswap


SCHEMA = "PUMPSWAP_FINALIZED_INIT_DECODER_V1"
IDL_SOURCE_URL = "https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json"
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM_ID = pumpswap.PROGRAM_ID
CREATE_POOL = bytes([233, 146, 209, 142, 207, 104, 64, 188])
CREATE_POOL_EVENT = bytes([177, 49, 12, 210, 160, 118, 167, 116])


class PumpSwapInitDecodeError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class CreatePoolInstruction:
    coordinate: str
    pool: str
    creator: str
    base_mint: str
    quote_mint: str
    index: int
    base_amount_in: int
    quote_amount_in: int
    coin_creator: str
    is_mayhem_mode: bool
    inner: bool


@dataclasses.dataclass(frozen=True)
class CreatePoolEvent:
    timestamp: int
    index: int
    creator: str
    base_mint: str
    quote_mint: str
    base_mint_decimals: int
    quote_mint_decimals: int
    base_amount_in: int
    quote_amount_in: int
    pool_base_amount: int
    pool_quote_amount: int
    minimum_liquidity: int
    initial_liquidity: int
    lp_token_amount_out: int
    pool_bump: int
    pool: str
    lp_mint: str
    user_base_token_account: str
    user_quote_token_account: str
    coin_creator: str
    is_mayhem_mode: bool
    creator_fee_bps: int
    can_edit_creator_fee: bool
    is_holder_reward: bool


@dataclasses.dataclass(frozen=True)
class VerifiedPumpSwapInit:
    pool: str
    mint: str
    quote_mint: str
    creator: str
    index: int
    base_amount_in: int
    quote_amount_in: int
    instruction_coordinate: str
    origin: str
    canonical_pump_migration: bool
    idl_source_url: str = IDL_SOURCE_URL


def _instruction_groups(tx: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    message = (tx.get("transaction") or {}).get("message") or {}
    for index, instruction in enumerate(message.get("instructions") or []):
        yield f"outer:{index}", instruction
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        outer = int(group.get("index") or 0)
        for index, instruction in enumerate(group.get("instructions") or []):
            yield f"outer:{outer}:inner:{index}", instruction


def _raw_data(instruction: dict[str, Any]) -> bytes | None:
    try:
        return base58_decode(str(instruction.get("data") or ""))
    except ExactCurveError:
        return None


def decode_create_pool_instructions(tx: dict[str, Any]) -> list[CreatePoolInstruction]:
    """Decode only the published, current fixed prefix of ``create_pool``.

    Later optional arguments are intentionally ignored.  The immutable identity
    fields are all in the fixed prefix and each accepted instruction must have
    the complete published 18-account layout.
    """
    keys = common.resolved_keys(tx)
    decoded: list[CreatePoolInstruction] = []
    for coordinate, instruction in _instruction_groups(tx):
        if common.instruction_program(instruction, keys) != PUMPSWAP_PROGRAM_ID:
            continue
        raw = _raw_data(instruction)
        if raw is None or len(raw) < 59 or raw[:8] != CREATE_POOL:
            continue
        accounts = common.instruction_accounts(instruction, keys)
        if len(accounts) != 18:
            raise PumpSwapInitDecodeError(
                f"create_pool account layout mismatch {coordinate}: {len(accounts)} != 18"
            )
        coin_creator = common.b58encode(raw[26:58])
        mayhem_raw = raw[58]
        if mayhem_raw not in (0, 1):
            raise PumpSwapInitDecodeError(f"create_pool invalid mayhem bool at {coordinate}")
        decoded.append(CreatePoolInstruction(
            coordinate=coordinate,
            pool=accounts[0],
            creator=accounts[2],
            base_mint=accounts[3],
            quote_mint=accounts[4],
            index=int.from_bytes(raw[8:10], "little"),
            base_amount_in=int.from_bytes(raw[10:18], "little"),
            quote_amount_in=int.from_bytes(raw[18:26], "little"),
            coin_creator=coin_creator,
            is_mayhem_mode=bool(mayhem_raw),
            inner=":inner:" in coordinate,
        ))
    return decoded


def decode_create_pool_events(log_messages: Iterable[str]) -> list[CreatePoolEvent]:
    events: list[CreatePoolEvent] = []
    for line in log_messages:
        marker = "Program data: "
        if marker not in str(line):
            continue
        try:
            payload = base64.b64decode(str(line).split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if not payload.startswith(CREATE_POOL_EVENT):
            continue
        # Current published CreatePoolEvent is exactly discriminator + 336 Borsh bytes.
        if len(payload) != 344:
            raise PumpSwapInitDecodeError(
                f"CreatePoolEvent schema mismatch {len(payload)} != 344"
            )
        reader = BorshReader(payload)
        if reader.take(8) != CREATE_POOL_EVENT:
            raise PumpSwapInitDecodeError("CreatePoolEvent discriminator mismatch")
        event = CreatePoolEvent(
            timestamp=reader.i64(),
            index=reader.u16(),
            creator=reader.pubkey(),
            base_mint=reader.pubkey(),
            quote_mint=reader.pubkey(),
            base_mint_decimals=reader.u8(),
            quote_mint_decimals=reader.u8(),
            base_amount_in=reader.u64(),
            quote_amount_in=reader.u64(),
            pool_base_amount=reader.u64(),
            pool_quote_amount=reader.u64(),
            minimum_liquidity=reader.u64(),
            initial_liquidity=reader.u64(),
            lp_token_amount_out=reader.u64(),
            pool_bump=reader.u8(),
            pool=reader.pubkey(),
            lp_mint=reader.pubkey(),
            user_base_token_account=reader.pubkey(),
            user_quote_token_account=reader.pubkey(),
            coin_creator=reader.pubkey(),
            is_mayhem_mode=reader.boolean(),
            creator_fee_bps=reader.u64(),
            can_edit_creator_fee=reader.boolean(),
            is_holder_reward=reader.boolean(),
        )
        if reader.offset != len(payload):
            raise PumpSwapInitDecodeError("CreatePoolEvent trailing bytes")
        events.append(event)
    return events


def _matches(instruction: CreatePoolInstruction, event: CreatePoolEvent) -> bool:
    return (
        instruction.pool == event.pool
        and instruction.creator == event.creator
        and instruction.base_mint == event.base_mint
        and instruction.quote_mint == event.quote_mint
        and instruction.index == event.index
        and instruction.base_amount_in == event.base_amount_in
        and instruction.quote_amount_in == event.quote_amount_in
        and instruction.coin_creator == event.coin_creator
        and instruction.is_mayhem_mode == event.is_mayhem_mode
    )


def _has_pump_migrate_log(tx: dict[str, Any]) -> bool:
    logs = [str(line) for line in (tx.get("meta") or {}).get("logMessages") or []]
    return any("Instruction: Migrate" in line for line in logs)


def _has_pump_program_instruction(tx: dict[str, Any]) -> bool:
    keys = common.resolved_keys(tx)
    return any(
        common.instruction_program(instruction, keys) == PUMP_PROGRAM_ID
        for _, instruction in _instruction_groups(tx)
    )


def validate_finalized_init(tx: dict[str, Any]) -> VerifiedPumpSwapInit | None:
    """Return a pool init only when all finalized identity evidence agrees."""
    if (tx.get("meta") or {}).get("err") is not None:
        return None
    instructions = decode_create_pool_instructions(tx)
    events = decode_create_pool_events((tx.get("meta") or {}).get("logMessages") or [])
    if not instructions and not events:
        return None
    matches = [(instruction, event) for instruction in instructions for event in events if _matches(instruction, event)]
    if len(matches) != 1:
        raise PumpSwapInitDecodeError(
            f"create_pool/event proof cardinality mismatch instructions={len(instructions)} "
            f"events={len(events)} matches={len(matches)}"
        )
    instruction, _ = matches[0]
    canonical_migration = (
        instruction.inner and instruction.index == 0 and _has_pump_program_instruction(tx) and _has_pump_migrate_log(tx)
    )
    return VerifiedPumpSwapInit(
        pool=instruction.pool,
        mint=instruction.base_mint,
        quote_mint=instruction.quote_mint,
        creator=instruction.creator,
        index=instruction.index,
        base_amount_in=instruction.base_amount_in,
        quote_amount_in=instruction.quote_amount_in,
        instruction_coordinate=instruction.coordinate,
        origin="PUMP_MIGRATE_CREATE_POOL" if canonical_migration else "PUMPSWAP_CREATE_POOL",
        canonical_pump_migration=canonical_migration,
    )
