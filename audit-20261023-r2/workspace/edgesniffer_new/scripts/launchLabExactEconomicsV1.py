#!/usr/bin/env python3
"""Exact, fail-closed Raydium LaunchLab trade-event economics.

The canonical LaunchLab TradeEvent is authoritative for curve reserve
transitions. Wallet token-balance deltas remain authoritative for inventory,
because Token-2022 transfer fees can make received inventory smaller than the
curve's amount_out.
"""

from __future__ import annotations

import base64
import dataclasses
import struct
from typing import Any, Iterable


SCHEMA = "LAUNCHLAB_EXACT_ECONOMICS_V1"
PROGRAM_ID = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
TRADE_EVENT_DISCRIMINATOR = bytes.fromhex("bddb7fd34ee661ee")
BUY_EXACT_IN = bytes.fromhex("faea0d7bd59c13ec")
BUY_EXACT_OUT = bytes.fromhex("18d3742869039938")
SELL_EXACT_IN = bytes.fromhex("9527de9bd37c981a")
SELL_EXACT_OUT = bytes.fromhex("5fc8472208090ba6")
TRADE_INSTRUCTIONS = {
    BUY_EXACT_IN: ("BUY", "EXACT_IN"),
    BUY_EXACT_OUT: ("BUY", "EXACT_OUT"),
    SELL_EXACT_IN: ("SELL", "EXACT_IN"),
    SELL_EXACT_OUT: ("SELL", "EXACT_OUT"),
}
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class LaunchLabExactError(ValueError):
    pass


def b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + ALPHABET.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def b58encode(value: bytes) -> str:
    zeroes = len(value) - len(value.lstrip(b"\0"))
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = ALPHABET[remainder] + encoded
    return "1" * zeroes + encoded


@dataclasses.dataclass(frozen=True)
class LaunchLabState:
    virtual_base: int
    virtual_quote: int
    real_base: int
    real_quote: int

    def validate(self) -> None:
        if min(dataclasses.astuple(self)) < 0:
            raise LaunchLabExactError("negative LaunchLab reserve")
        if self.virtual_base <= self.real_base:
            raise LaunchLabExactError("constant-product base reserve exhausted")


@dataclasses.dataclass(frozen=True)
class LaunchLabTradeEvent:
    pool_state: str
    total_base_sell: int
    virtual_base: int
    virtual_quote: int
    real_base_before: int
    real_quote_before: int
    real_base_after: int
    real_quote_after: int
    amount_in: int
    amount_out: int
    protocol_fee: int
    platform_fee: int
    creator_fee: int
    share_fee: int
    direction: str
    pool_status: int
    exact_in: bool

    @property
    def total_fee(self) -> int:
        return self.protocol_fee + self.platform_fee + self.creator_fee + self.share_fee

    @property
    def pre_state(self) -> LaunchLabState:
        return LaunchLabState(
            self.virtual_base,
            self.virtual_quote,
            self.real_base_before,
            self.real_quote_before,
        )

    @property
    def post_state(self) -> LaunchLabState:
        return LaunchLabState(
            self.virtual_base,
            self.virtual_quote,
            self.real_base_after,
            self.real_quote_after,
        )


@dataclasses.dataclass(frozen=True)
class LaunchLabInstruction:
    side: str
    amount_mode: str
    amount: int
    limit: int
    share_fee_rate: int
    accounts: tuple[str, ...]

    @property
    def payer(self) -> str:
        return self.accounts[0]

    @property
    def pool_state(self) -> str:
        return self.accounts[4]

    @property
    def base_mint(self) -> str:
        return self.accounts[9]

    @property
    def quote_mint(self) -> str:
        return self.accounts[10]

    @property
    def base_token_program(self) -> str:
        return self.accounts[11]

    @property
    def quote_token_program(self) -> str:
        return self.accounts[12]


def decode_trade_event(payload: bytes) -> LaunchLabTradeEvent:
    if len(payload) != 147 or payload[:8] != TRADE_EVENT_DISCRIMINATOR:
        raise LaunchLabExactError("not a canonical 147-byte LaunchLab TradeEvent")
    pool_state = b58encode(payload[8:40])
    values = struct.unpack_from("<13Q", payload, 40)
    direction_raw, status, exact_in_raw = payload[144:147]
    if direction_raw not in (0, 1) or exact_in_raw not in (0, 1):
        raise LaunchLabExactError("invalid LaunchLab event enum/bool")
    event = LaunchLabTradeEvent(
        pool_state,
        *values,
        "BUY" if direction_raw == 0 else "SELL",
        status,
        bool(exact_in_raw),
    )
    event.pre_state.validate()
    event.post_state.validate()
    return event


def decode_trade_events(log_messages: Iterable[str]) -> list[LaunchLabTradeEvent]:
    events: list[LaunchLabTradeEvent] = []
    for line in log_messages:
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if payload.startswith(TRADE_EVENT_DISCRIMINATOR) and len(payload) == 147:
            events.append(decode_trade_event(payload))
    return events


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    static = [str(row.get("pubkey")) if isinstance(row, dict) else str(row)
              for row in message.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return static + [str(row) for row in loaded.get("writable") or []] + [
        str(row) for row in loaded.get("readonly") or []
    ]


def instruction_program(ix: dict[str, Any], keys: list[str]) -> str | None:
    if ix.get("programId"):
        return str(ix["programId"])
    index = ix.get("programIdIndex")
    return keys[int(index)] if index is not None and int(index) < len(keys) else None


def instruction_accounts(ix: dict[str, Any], keys: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    for raw in ix.get("accounts") or []:
        if isinstance(raw, int) or str(raw).isdigit():
            index = int(raw)
            if index >= len(keys):
                raise LaunchLabExactError("instruction account index out of range")
            values.append(keys[index])
        else:
            values.append(str(raw))
    return tuple(values)


def decode_instruction(data: bytes, accounts: tuple[str, ...]) -> LaunchLabInstruction:
    shape = TRADE_INSTRUCTIONS.get(data[:8])
    if shape is None:
        raise LaunchLabExactError("unsupported LaunchLab trade instruction")
    if len(data) != 32 or len(accounts) < 15:
        raise LaunchLabExactError("unexpected LaunchLab trade instruction layout")
    amount, limit, share_fee_rate = struct.unpack_from("<3Q", data, 8)
    return LaunchLabInstruction(shape[0], shape[1], amount, limit, share_fee_rate, accounts)


def transaction_trade_instructions(tx: dict[str, Any]) -> list[LaunchLabInstruction]:
    keys = resolved_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}
    groups = [message.get("instructions") or []]
    groups.extend(row.get("instructions") or [] for row in (tx.get("meta") or {}).get("innerInstructions") or [])
    decoded: list[LaunchLabInstruction] = []
    for group in groups:
        for ix in group:
            if instruction_program(ix, keys) != PROGRAM_ID:
                continue
            try:
                data = b58decode(str(ix.get("data") or ""))
            except Exception as error:
                raise LaunchLabExactError("invalid base58 instruction data") from error
            if data[:8] in TRADE_INSTRUCTIONS:
                decoded.append(decode_instruction(data, instruction_accounts(ix, keys)))
    return decoded


def token_balance_by_owner(tx: dict[str, Any], key: str, owner: str, mint: str) -> int:
    total = 0
    for row in (tx.get("meta") or {}).get(key) or []:
        if str(row.get("owner") or "") == owner and str(row.get("mint") or "") == mint:
            total += int(((row.get("uiTokenAmount") or {}).get("amount")) or 0)
    return total


def wallet_deltas(
    tx: dict[str, Any], instruction: LaunchLabInstruction
) -> tuple[int, int]:
    base_pre = token_balance_by_owner(tx, "preTokenBalances", instruction.payer, instruction.base_mint)
    base_post = token_balance_by_owner(tx, "postTokenBalances", instruction.payer, instruction.base_mint)
    quote_pre = token_balance_by_owner(tx, "preTokenBalances", instruction.payer, instruction.quote_mint)
    quote_post = token_balance_by_owner(tx, "postTokenBalances", instruction.payer, instruction.quote_mint)
    return base_post - base_pre, quote_post - quote_pre


def validate_constant_product_transition(
    event: LaunchLabTradeEvent,
    instruction: LaunchLabInstruction,
    tx: dict[str, Any] | None = None,
    require_wallet_consistency: bool = False,
) -> dict[str, Any]:
    if event.pool_state != instruction.pool_state or event.direction != instruction.side:
        raise LaunchLabExactError("event/instruction identity mismatch")
    if event.exact_in != (instruction.amount_mode == "EXACT_IN"):
        raise LaunchLabExactError("event/instruction amount-mode mismatch")
    if instruction.amount_mode == "EXACT_IN" and event.amount_in > instruction.amount:
        raise LaunchLabExactError("exact-in event exceeds signed input")
    if instruction.amount_mode == "EXACT_OUT" and event.amount_out != instruction.amount:
        raise LaunchLabExactError("exact-out amount mismatch")

    fees = event.total_fee
    if event.direction == "BUY":
        if fees > event.amount_in:
            raise LaunchLabExactError("buy fees exceed input")
        net_quote = event.amount_in - fees
        expected_out = (
            (event.virtual_base - event.real_base_before) * net_quote
            // (event.virtual_quote + event.real_quote_before + net_quote)
        )
        if expected_out != event.amount_out:
            raise LaunchLabExactError("constant-product buy amount mismatch")
        if event.real_base_after - event.real_base_before != event.amount_out:
            raise LaunchLabExactError("buy base reserve transition mismatch")
        if event.real_quote_after - event.real_quote_before != net_quote:
            raise LaunchLabExactError("buy quote reserve transition mismatch")
    else:
        gross_quote = (
            (event.virtual_quote + event.real_quote_before) * event.amount_in
            // (event.virtual_base - event.real_base_before + event.amount_in)
        )
        if gross_quote < fees or gross_quote - fees != event.amount_out:
            raise LaunchLabExactError("constant-product sell amount mismatch")
        if event.real_base_before - event.real_base_after != event.amount_in:
            raise LaunchLabExactError("sell base reserve transition mismatch")
        if event.real_quote_before - event.real_quote_after != gross_quote:
            raise LaunchLabExactError("sell quote reserve transition mismatch")

    base_delta = quote_delta = None
    inventory_transfer_fee = quote_transfer_fee = None
    if tx is not None:
        base_delta, quote_delta = wallet_deltas(tx, instruction)
        if event.direction == "BUY":
            inventory_transfer_fee = event.amount_out - max(base_delta, 0)
            quote_transfer_fee = max(-quote_delta, 0) - event.amount_in
        else:
            inventory_transfer_fee = max(-base_delta, 0) - event.amount_in
            quote_transfer_fee = event.amount_out - max(quote_delta, 0)
        if require_wallet_consistency and (inventory_transfer_fee < 0 or quote_transfer_fee < 0):
            raise LaunchLabExactError("wallet deltas contradict TradeEvent")

    return {
        "schema": SCHEMA,
        "classification": "EXACT_CONSTANT_PRODUCT_TRANSITION_VALIDATED",
        "poolState": event.pool_state,
        "payer": instruction.payer,
        "baseMint": instruction.base_mint,
        "quoteMint": instruction.quote_mint,
        "baseTokenProgram": instruction.base_token_program,
        "quoteTokenProgram": instruction.quote_token_program,
        "side": event.direction,
        "amountMode": instruction.amount_mode,
        "curveAmountInRaw": event.amount_in,
        "curveAmountOutRaw": event.amount_out,
        "signedInstructionAmountRaw": instruction.amount,
        "inputTransferFeeRaw": (
            instruction.amount - event.amount_in
            if instruction.amount_mode == "EXACT_IN" else None
        ),
        "curveFeeRaw": fees,
        "walletBaseDeltaRaw": base_delta,
        "walletQuoteDeltaRaw": quote_delta,
        "baseTransferFeeRaw": inventory_transfer_fee,
        "quoteTransferFeeRaw": quote_transfer_fee,
        "preState": dataclasses.asdict(event.pre_state),
        "postState": dataclasses.asdict(event.post_state),
    }


def validate_transaction(tx: dict[str, Any]) -> list[dict[str, Any]]:
    if (tx.get("meta") or {}).get("err") is not None:
        return []
    instructions = transaction_trade_instructions(tx)
    events = decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
    if len(instructions) != len(events):
        raise LaunchLabExactError(
            f"trade instruction/event count mismatch {len(instructions)}/{len(events)}"
        )
    return [
        validate_constant_product_transition(event, instruction, tx)
        for instruction, event in zip(instructions, events)
    ]
