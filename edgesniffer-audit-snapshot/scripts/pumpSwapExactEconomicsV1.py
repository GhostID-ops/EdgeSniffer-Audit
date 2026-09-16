#!/usr/bin/env python3
"""Exact, fail-closed PumpSwap event-state normalization.

The current PumpSwap BuyEvent/SellEvent is authoritative for the realized
reserve transition and fees.  This module deliberately does not infer a
missing event from token balances or an RPC quote.
"""

from __future__ import annotations

import base64
import dataclasses
from typing import Any, Iterable

from exactCurveEconomicsV1 import BorshReader, ExactCurveError, base58_decode


SCHEMA = "PUMPSWAP_EXACT_ECONOMICS_V1"
PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
BUY = bytes([102, 6, 61, 18, 1, 218, 235, 234])
BUY_EXACT_QUOTE_IN = bytes([198, 46, 21, 82, 180, 217, 232, 112])
SELL = bytes([51, 230, 133, 164, 1, 127, 131, 173])
BUY_EVENT = bytes([103, 244, 82, 31, 44, 245, 119, 119])
SELL_EVENT = bytes([62, 47, 55, 10, 165, 3, 220, 42])


class PumpSwapExactError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class PoolState:
    base: int
    quote: int
    virtual_quote: int = 0

    @property
    def effective_quote(self) -> int:
        return self.quote + self.virtual_quote

    def validate(self) -> None:
        if self.base < 0 or self.quote < 0 or self.effective_quote <= 0:
            raise PumpSwapExactError(f"invalid PumpSwap reserve state: {self}")


@dataclasses.dataclass(frozen=True)
class PumpSwapInstruction:
    side: str
    amount_mode: str
    amount_raw: int
    limit_raw: int
    pool: str
    user: str
    base_mint: str
    quote_mint: str
    coordinate: str


@dataclasses.dataclass(frozen=True)
class PumpSwapEvent:
    side: str
    timestamp: int
    base_amount: int
    limit_quote_amount: int
    user_base_reserves: int
    user_quote_reserves: int
    pool_base_reserves: int
    pool_quote_reserves: int
    curve_quote_amount: int
    lp_fee_basis_points: int
    lp_fee: int
    protocol_fee_basis_points: int
    protocol_fee: int
    pool_quote_delta: int
    user_quote_amount: int
    pool: str
    user: str
    user_base_token_account: str
    user_quote_token_account: str
    protocol_fee_recipient: str
    protocol_fee_recipient_token_account: str
    coin_creator: str
    coin_creator_fee_basis_points: int
    coin_creator_fee: int
    cashback_fee_basis_points: int
    cashback: int
    buyback_fee_basis_points: int
    buyback_fee: int
    virtual_quote_reserves: int
    can_boost: bool
    base_supply: int
    ix_name: str = ""
    min_base_amount: int = 0
    holder_rewards_basis_points: int = 0
    holder_rewards: int = 0

    @property
    def post_state(self) -> PoolState:
        return PoolState(
            self.pool_base_reserves,
            self.pool_quote_reserves,
            self.virtual_quote_reserves,
        )

    @property
    def pre_state(self) -> PoolState:
        if self.side == "BUY":
            return PoolState(
                self.pool_base_reserves + self.base_amount,
                self.pool_quote_reserves - self.pool_quote_delta,
                self.virtual_quote_reserves,
            )
        return PoolState(
            self.pool_base_reserves - self.base_amount,
            self.pool_quote_reserves + self.pool_quote_delta,
            self.virtual_quote_reserves,
        )


def _i128(reader: BorshReader) -> int:
    return int.from_bytes(reader.take(16), "little", signed=True)


def decode_event(payload: bytes) -> PumpSwapEvent:
    reader = BorshReader(payload)
    discriminator = reader.take(8)
    if discriminator not in {BUY_EVENT, SELL_EVENT}:
        raise PumpSwapExactError("not a current PumpSwap buy/sell event")
    side = "BUY" if discriminator == BUY_EVENT else "SELL"
    timestamp = reader.i64()
    base_amount = reader.u64()
    limit_quote = reader.u64()
    user_base = reader.u64()
    user_quote = reader.u64()
    pool_base = reader.u64()
    pool_quote = reader.u64()
    curve_quote = reader.u64()
    lp_bps = reader.u64()
    lp_fee = reader.u64()
    protocol_bps = reader.u64()
    protocol_fee = reader.u64()
    pool_quote_delta = reader.u64()
    user_quote_amount = reader.u64()
    pool = reader.pubkey()
    user = reader.pubkey()
    user_base_account = reader.pubkey()
    user_quote_account = reader.pubkey()
    protocol_recipient = reader.pubkey()
    protocol_recipient_account = reader.pubkey()
    creator = reader.pubkey()
    creator_bps = reader.u64()
    creator_fee = reader.u64()
    ix_name = ""
    min_base_amount = 0
    if side == "BUY":
        reader.boolean()  # track_volume
        reader.u64()  # total_unclaimed_tokens
        reader.u64()  # total_claimed_tokens
        reader.u64()  # current_sol_volume
        reader.i64()  # last_update_timestamp
        min_base_amount = reader.u64()
        ix_name = reader.string()
    cashback_bps = reader.u64()
    cashback = reader.u64()
    buyback_bps = reader.u64()
    buyback_fee = reader.u64()
    virtual_quote = _i128(reader)
    can_boost = reader.boolean()
    base_supply = reader.u64()
    holder_rewards_bps = 0
    holder_rewards = 0
    if len(payload) - reader.offset == 16:
        holder_rewards_bps = reader.u64()
        holder_rewards = reader.u64()
    if reader.offset != len(payload):
        raise PumpSwapExactError(
            f"PumpSwap event schema mismatch: decoded={reader.offset} payload={len(payload)}"
        )
    event = PumpSwapEvent(
        side, timestamp, base_amount, limit_quote, user_base, user_quote,
        pool_base, pool_quote, curve_quote, lp_bps, lp_fee, protocol_bps,
        protocol_fee, pool_quote_delta, user_quote_amount, pool, user,
        user_base_account, user_quote_account, protocol_recipient,
        protocol_recipient_account, creator, creator_bps, creator_fee,
        cashback_bps, cashback, buyback_bps, buyback_fee, virtual_quote,
        can_boost, base_supply, ix_name, min_base_amount,
        holder_rewards_bps, holder_rewards,
    )
    event.pre_state.validate()
    event.post_state.validate()
    if event.base_amount <= 0 or event.curve_quote_amount <= 0:
        raise PumpSwapExactError("non-positive PumpSwap trade amount")
    if event.pool_quote_delta <= 0:
        raise PumpSwapExactError("non-positive PumpSwap pool quote delta")
    return event


def decode_events(log_messages: Iterable[str]) -> list[PumpSwapEvent]:
    events: list[PumpSwapEvent] = []
    for line in log_messages:
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if payload.startswith(BUY_EVENT) or payload.startswith(SELL_EVENT):
            events.append(decode_event(payload))
    return events


def _message_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(value) for value in loaded.get("writable") or [])
    keys.extend(str(value) for value in loaded.get("readonly") or [])
    return keys


def _compiled_instructions(tx: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    message = (tx.get("transaction") or {}).get("message") or {}
    for index, instruction in enumerate(message.get("instructions") or []):
        yield f"outer:{index}", instruction
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        outer = int(group.get("index") or 0)
        for index, instruction in enumerate(group.get("instructions") or []):
            yield f"outer:{outer}:inner:{index}", instruction


def decode_instructions(tx: dict[str, Any]) -> list[PumpSwapInstruction]:
    keys = _message_keys(tx)
    decoded: list[PumpSwapInstruction] = []
    for coordinate, instruction in _compiled_instructions(tx):
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
        if len(raw) < 24 or raw[:8] not in {BUY, BUY_EXACT_QUOTE_IN, SELL}:
            continue
        indices = [int(value) for value in instruction.get("accounts") or []]
        accounts = [keys[index] for index in indices if index < len(keys)]
        if len(accounts) < 5:
            raise PumpSwapExactError(f"PumpSwap instruction account layout incomplete: {coordinate}")
        decoded.append(PumpSwapInstruction(
            "BUY" if raw[:8] in {BUY, BUY_EXACT_QUOTE_IN} else "SELL",
            "EXACT_QUOTE_IN" if raw[:8] == BUY_EXACT_QUOTE_IN else "EXACT_BASE",
            int.from_bytes(raw[8:16], "little"),
            int.from_bytes(raw[16:24], "little"),
            accounts[0], accounts[1], accounts[3], accounts[4], coordinate,
        ))
    return decoded


def validate_event_fees(event: PumpSwapEvent, amount_mode: str) -> None:
    """Validate the asymmetric buy/sell fee bases emitted by PumpSwap."""
    # Exact-quote buys express curve_quote_amount as the gross fixed input and
    # user_quote_amount as its fee base. Exact-base buys and sells express
    # curve_quote_amount as the pre-fee curve quote and use that as the base.
    fee_base = (
        event.user_quote_amount
        if event.side == "BUY" and amount_mode == "EXACT_QUOTE_IN"
        else event.curve_quote_amount
    )
    expected_lp = (fee_base * event.lp_fee_basis_points + 9_999) // 10_000
    expected_protocol = (
        fee_base * event.protocol_fee_basis_points + 9_999
    ) // 10_000
    tolerance = 1 if event.side == "BUY" and amount_mode == "EXACT_QUOTE_IN" else 0
    if (
        abs(event.lp_fee - expected_lp) > tolerance
        or abs(event.protocol_fee - expected_protocol) > tolerance
    ):
        raise PumpSwapExactError("PumpSwap event fee arithmetic mismatch")


def validate_transaction(tx: dict[str, Any]) -> list[dict[str, Any]]:
    if (tx.get("meta") or {}).get("err") is not None:
        return []
    instructions = decode_instructions(tx)
    events = decode_events((tx.get("meta") or {}).get("logMessages") or [])
    if not instructions and not events:
        return []
    if len(instructions) != len(events):
        raise PumpSwapExactError(
            f"PumpSwap instruction/event cardinality mismatch {len(instructions)}/{len(events)}"
        )
    results: list[dict[str, Any]] = []
    unmatched = list(instructions)
    for event in events:
        candidates = [
            item for item in unmatched
            if item.side == event.side and item.pool == event.pool and item.user == event.user
        ]
        if len(candidates) != 1:
            raise PumpSwapExactError("PumpSwap event cannot be joined uniquely to instruction")
        instruction = candidates[0]
        unmatched.remove(instruction)
        if instruction.amount_mode == "EXACT_BASE" and instruction.amount_raw != event.base_amount:
            raise PumpSwapExactError("PumpSwap instruction/event base amount mismatch")
        if instruction.amount_mode == "EXACT_QUOTE_IN":
            if instruction.amount_raw != event.curve_quote_amount:
                raise PumpSwapExactError("PumpSwap exact-quote input mismatch")
            if instruction.limit_raw != event.min_base_amount:
                raise PumpSwapExactError("PumpSwap min-base mismatch")
        elif event.side == "BUY" and instruction.limit_raw != event.limit_quote_amount:
            raise PumpSwapExactError("PumpSwap buy max-quote mismatch")
        if event.side == "SELL" and instruction.limit_raw != event.limit_quote_amount:
            raise PumpSwapExactError("PumpSwap sell min-quote mismatch")
        validate_event_fees(event, instruction.amount_mode)
        creator_charge = event.coin_creator_fee + event.cashback
        if event.side == "BUY":
            if instruction.amount_mode == "EXACT_QUOTE_IN":
                if event.pool_quote_delta != event.user_quote_amount + event.lp_fee:
                    raise PumpSwapExactError("PumpSwap buy pool-delta identity mismatch")
                expected_gross = (
                    event.pool_quote_delta + event.protocol_fee + creator_charge
                )
                if abs(event.curve_quote_amount - expected_gross) > 1:
                    raise PumpSwapExactError("PumpSwap buy gross-quote identity mismatch")
            else:
                if event.pool_quote_delta != event.curve_quote_amount + event.lp_fee:
                    raise PumpSwapExactError("PumpSwap buy pool-delta identity mismatch")
                if event.user_quote_amount != (
                    event.pool_quote_delta + event.protocol_fee + creator_charge
                ):
                    raise PumpSwapExactError("PumpSwap buy gross-quote identity mismatch")
        else:
            if event.curve_quote_amount != event.pool_quote_delta + event.lp_fee:
                raise PumpSwapExactError("PumpSwap sell pool-delta identity mismatch")
            if event.user_quote_amount != (
                event.pool_quote_delta - event.protocol_fee - creator_charge
            ):
                raise PumpSwapExactError("PumpSwap sell net-quote identity mismatch")
        results.append({
            "schema": SCHEMA + "_TRANSITION",
            "programId": PROGRAM_ID,
            "pool": event.pool,
            "user": event.user,
            "baseMint": instruction.base_mint,
            "quoteMint": instruction.quote_mint,
            "side": event.side,
            "amountMode": instruction.amount_mode,
            "instructionCoordinate": instruction.coordinate,
            "amountRaw": event.base_amount,
            "curveQuoteAmountRaw": event.curve_quote_amount,
            "poolQuoteDeltaRaw": event.pool_quote_delta,
            "userQuoteAmountRaw": event.user_quote_amount,
            "lpFeeRaw": event.lp_fee,
            "protocolFeeRaw": event.protocol_fee,
            "creatorFeeRaw": event.coin_creator_fee,
            "cashbackRaw": event.cashback,
            "buybackFeeRaw": event.buyback_fee,
            "preState": dataclasses.asdict(event.pre_state),
            "postState": dataclasses.asdict(event.post_state),
            "effectiveQuoteReservesUsed": True,
            "exactObservedTransition": True,
        })
    return results
