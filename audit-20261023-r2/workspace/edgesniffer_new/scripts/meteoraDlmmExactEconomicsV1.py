#!/usr/bin/env python3
"""Canonical Meteora DLMM Swap2Evt decoder and cash-flow evaluator."""

from __future__ import annotations

import dataclasses
import struct
from typing import Any

from launchLabExactEconomicsV1 import b58encode


SCHEMA = "METEORA_DLMM_EXACT_ECONOMICS_V1"
PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
SWAP2_EVENT = bytes([46, 116, 82, 215, 148, 27, 84, 77])


class MeteoraDlmmExactError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Swap2Event:
    lb_pair: str
    sender: str
    start_bin_id: int
    end_bin_id: int
    swap_for_y: bool
    fee_bps: int
    amount_in: int
    amount_left: int
    amount_out: int
    mm_fee: int
    protocol_fee: int
    limit_order_fee: int
    host_fee: int
    fees_on_input: bool
    fees_on_token_x: bool

    @property
    def consumed_input(self) -> int:
        return self.amount_in - self.amount_left


@dataclasses.dataclass(frozen=True)
class FixedExitCashflow:
    quote_spent_raw: int
    quote_received_raw: int
    pnl_quote_raw: int
    roi: float


def decode_swap2_event(payload: bytes) -> Swap2Event:
    # IDL order: pubkey, pubkey, i32, i32, bool, u128, 7*u64, bool, bool.
    if len(payload) != 155 or payload[:8] != SWAP2_EVENT:
        raise MeteoraDlmmExactError("not a canonical 155-byte DLMM Swap2Evt")
    offset = 8
    lb_pair = b58encode(payload[offset:offset + 32])
    offset += 32
    sender = b58encode(payload[offset:offset + 32])
    offset += 32
    start_bin, end_bin = struct.unpack_from("<2i", payload, offset)
    offset += 8
    direction = payload[offset]
    if direction not in (0, 1):
        raise MeteoraDlmmExactError("invalid DLMM swap_for_y bool")
    offset += 1
    fee_bps = int.from_bytes(payload[offset:offset + 16], "little")
    offset += 16
    values = struct.unpack_from("<7Q", payload, offset)
    offset += 56
    fees_on_input = payload[offset]
    fees_on_token_x = payload[offset + 1]
    if fees_on_input not in (0, 1) or fees_on_token_x not in (0, 1):
        raise MeteoraDlmmExactError("invalid DLMM fee-side bool")
    amount_in, amount_left, amount_out, mm_fee, protocol_fee, limit_fee, host_fee = values
    if amount_in <= 0 or amount_out <= 0 or amount_left > amount_in:
        raise MeteoraDlmmExactError("invalid DLMM swap amounts")
    return Swap2Event(
        lb_pair, sender, start_bin, end_bin, bool(direction), fee_bps,
        amount_in, amount_left, amount_out, mm_fee, protocol_fee, limit_fee,
        host_fee, bool(fees_on_input), bool(fees_on_token_x),
    )


def evaluate_fixed_exit(
    entry: Swap2Event,
    exit: Swap2Event,
    *,
    quote_is_token_x: bool,
    notional_quote_raw: int,
) -> FixedExitCashflow:
    if entry.lb_pair != exit.lb_pair:
        raise MeteoraDlmmExactError("entry and exit DLMM pair mismatch")
    entry_is_buy = entry.swap_for_y == quote_is_token_x
    exit_is_sell = exit.swap_for_y != quote_is_token_x
    if not entry_is_buy or not exit_is_sell:
        raise MeteoraDlmmExactError("event directions are not quote-buy then token-sell")
    quote_spent = entry.consumed_input
    if quote_spent != notional_quote_raw:
        raise MeteoraDlmmExactError("entry notional does not match fixed paper notional")
    quote_received = exit.amount_out
    pnl = quote_received - quote_spent
    return FixedExitCashflow(quote_spent, quote_received, pnl, pnl / quote_spent)


def adapter_contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "program_id": PROGRAM_ID,
        "decoder": "published DLMM IDL Swap2Evt, 155 bytes",
        "evaluator": "same-pair quote-buy/token-sell fixed-notional event cash flow",
        "requires": ["resolved underlying DLMM pair", "entry/exit Swap2Evt pair", "quote mint orientation"],
        "does_not_do": ["generic router attribution", "quote reconstruction without bin arrays"],
    }
