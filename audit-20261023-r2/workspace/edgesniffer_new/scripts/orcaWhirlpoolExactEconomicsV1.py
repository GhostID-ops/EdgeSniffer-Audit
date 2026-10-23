#!/usr/bin/env python3
"""Canonical Orca Whirlpool Traded-event decoder and cash-flow evaluator.

This module is deliberately event based: it evaluates only a pair of
canonical Whirlpool swap events supplied by a resolver. It does not infer a
router's underlying venue and it does not simulate missing tick arrays.
"""

from __future__ import annotations

import dataclasses
import hashlib
import struct
from typing import Any

from launchLabExactEconomicsV1 import b58encode


SCHEMA = "ORCA_WHIRLPOOL_EXACT_ECONOMICS_V1"
PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
TRADED_EVENT = hashlib.sha256(b"event:Traded").digest()[:8]


class OrcaWhirlpoolExactError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class TradedEvent:
    whirlpool: str
    a_to_b: bool
    pre_sqrt_price_x64: int
    post_sqrt_price_x64: int
    input_amount: int
    output_amount: int
    input_transfer_fee: int
    output_transfer_fee: int
    lp_fee: int
    protocol_fee: int


@dataclasses.dataclass(frozen=True)
class FixedExitCashflow:
    quote_spent_raw: int
    quote_received_raw: int
    pnl_quote_raw: int
    roi: float


def decode_event(payload: bytes) -> TradedEvent:
    if len(payload) != 121 or payload[:8] != TRADED_EVENT:
        raise OrcaWhirlpoolExactError("not a canonical 121-byte Whirlpool Traded event")
    offset = 8
    whirlpool = b58encode(payload[offset:offset + 32])
    offset += 32
    direction = payload[offset]
    if direction not in (0, 1):
        raise OrcaWhirlpoolExactError("invalid Whirlpool a_to_b bool")
    offset += 1
    pre_price = int.from_bytes(payload[offset:offset + 16], "little")
    offset += 16
    post_price = int.from_bytes(payload[offset:offset + 16], "little")
    offset += 16
    values = struct.unpack_from("<6Q", payload, offset)
    if pre_price <= 0 or post_price <= 0 or values[0] <= 0 or values[1] <= 0:
        raise OrcaWhirlpoolExactError("invalid Whirlpool trade amounts or price")
    input_amount, output_amount, input_fee, output_fee, lp_fee, protocol_fee = values
    if input_fee > input_amount or output_fee > output_amount:
        raise OrcaWhirlpoolExactError("Whirlpool transfer fee exceeds transfer amount")
    return TradedEvent(
        whirlpool, bool(direction), pre_price, post_price,
        input_amount, output_amount, input_fee, output_fee, lp_fee, protocol_fee,
    )


def evaluate_fixed_exit(
    entry: TradedEvent,
    exit: TradedEvent,
    *,
    quote_is_token_a: bool,
    notional_quote_raw: int,
) -> FixedExitCashflow:
    """Evaluate a quote->token entry followed by token->quote exit.

    `Traded.output_amount` is the protocol event amount and the explicit
    transfer-fee fields are applied to the wallet cash flow. The caller must
    provide an already-resolved same-pool pair; this function never trusts a
    router/source account meta as pool identity.
    """
    if entry.whirlpool != exit.whirlpool:
        raise OrcaWhirlpoolExactError("entry and exit Whirlpool mismatch")
    entry_is_buy = entry.a_to_b == quote_is_token_a
    exit_is_sell = exit.a_to_b != quote_is_token_a
    if not entry_is_buy or not exit_is_sell:
        raise OrcaWhirlpoolExactError("event directions are not quote-buy then token-sell")
    quote_spent = entry.input_amount + entry.input_transfer_fee
    if quote_spent != notional_quote_raw:
        raise OrcaWhirlpoolExactError("entry notional does not match fixed paper notional")
    quote_received = exit.output_amount - exit.output_transfer_fee
    pnl = quote_received - quote_spent
    return FixedExitCashflow(quote_spent, quote_received, pnl, pnl / quote_spent)


def adapter_contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "program_id": PROGRAM_ID,
        "decoder": "canonical Whirlpool Traded event, 121 bytes",
        "evaluator": "same-pool quote-buy/token-sell fixed-notional event cash flow",
        "requires": ["resolved underlying Whirlpool", "entry/exit event pair", "quote mint orientation"],
        "does_not_do": ["generic router attribution", "missing tick-array simulation"],
    }
