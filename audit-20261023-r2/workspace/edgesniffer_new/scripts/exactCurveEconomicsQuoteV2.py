#!/usr/bin/env python3
"""Exact quote-native Pump economics extension for non-SOL CreateV2 markets.

The validated V1 evaluator remains SOL-only. This extension validates finalized
non-SOL TradeEvent transitions in their native quote units and refuses to
normalize results to SOL without causal conversion evidence.
"""

from __future__ import annotations

import base64
import dataclasses
from typing import Any, Iterable

from exactCurveEconomicsV1 import (
    BorshReader,
    BuyQuote,
    CurveState,
    ExactCurveError,
    PumpInstruction,
    SellQuote,
    SOL_QUOTE_MINTS,
    TRADE_EVENT_DISCRIMINATOR,
    apply_instruction,
    fee_ceil,
)


SCHEMA = "EXACT_CURVE_ECONOMICS_QUOTE_V2"


@dataclasses.dataclass(frozen=True)
class QuoteTradeEvent:
    mint: str
    token_amount: int
    is_buy: bool
    user: str
    timestamp: int
    virtual_token_reserves: int
    real_token_reserves: int
    fee_recipient: str
    fee_basis_points: int
    fee: int
    creator: str
    creator_fee_basis_points: int
    creator_fee: int
    ix_name: str
    mayhem_mode: bool
    cashback_fee_basis_points: int
    cashback: int
    buyback_fee_basis_points: int
    buyback_fee: int
    quote_mint: str
    quote_amount: int
    virtual_quote_reserves: int
    real_quote_reserves: int
    holder_rewards_basis_points: int = 0
    holder_rewards: int = 0

    @property
    def post_state(self) -> CurveState:
        return CurveState(
            self.virtual_quote_reserves,
            self.virtual_token_reserves,
            self.real_quote_reserves,
            self.real_token_reserves,
        )

    @property
    def pre_state(self) -> CurveState:
        if self.is_buy:
            return CurveState(
                self.virtual_quote_reserves - self.quote_amount,
                self.virtual_token_reserves + self.token_amount,
                self.real_quote_reserves - self.quote_amount,
                self.real_token_reserves + self.token_amount,
            )
        return CurveState(
            self.virtual_quote_reserves + self.quote_amount,
            self.virtual_token_reserves - self.token_amount,
            self.real_quote_reserves + self.quote_amount,
            self.real_token_reserves - self.token_amount,
        )


def decode_quote_trade_event(payload: bytes) -> QuoteTradeEvent:
    reader = BorshReader(payload)
    if reader.take(8) != TRADE_EVENT_DISCRIMINATOR:
        raise ExactCurveError("not a Pump TradeEvent")
    mint = reader.pubkey()
    reader.u64()  # legacy SOL amount; zero for the observed non-SOL schema
    token_amount = reader.u64()
    is_buy = reader.boolean()
    user = reader.pubkey()
    timestamp = reader.i64()
    reader.u64()  # legacy virtual SOL reserves
    virtual_token = reader.u64()
    reader.u64()  # legacy real SOL reserves
    real_token = reader.u64()
    fee_recipient = reader.pubkey()
    fee_bps = reader.u64()
    fee = reader.u64()
    creator = reader.pubkey()
    creator_fee_bps = reader.u64()
    creator_fee = reader.u64()
    reader.boolean()  # track_volume
    reader.u64()  # total_unclaimed_tokens
    reader.u64()  # total_claimed_tokens
    reader.u64()  # current_sol_volume
    reader.i64()  # last_update_timestamp
    ix_name = reader.string()
    mayhem_mode = reader.boolean()
    cashback_bps = reader.u64()
    cashback = reader.u64()
    buyback_bps = reader.u64()
    buyback_fee = reader.u64()
    for _ in range(reader.u32()):
        reader.pubkey()
        reader.u16()
    quote_mint = reader.pubkey()
    quote_amount = reader.u64()
    virtual_quote = reader.u64()
    real_quote = reader.u64()
    holder_rewards_bps = 0
    holder_rewards = 0
    remaining = len(payload) - reader.offset
    if remaining == 16:
        holder_rewards_bps = reader.u64()
        holder_rewards = reader.u64()
    if reader.offset != len(payload):
        raise ExactCurveError(
            f"TradeEvent schema mismatch: decoded={reader.offset} payload={len(payload)}"
        )
    event = QuoteTradeEvent(
        mint,
        token_amount,
        is_buy,
        user,
        timestamp,
        virtual_token,
        real_token,
        fee_recipient,
        fee_bps,
        fee,
        creator,
        creator_fee_bps,
        creator_fee,
        ix_name,
        mayhem_mode,
        cashback_bps,
        cashback,
        buyback_bps,
        buyback_fee,
        quote_mint,
        quote_amount,
        virtual_quote,
        real_quote,
        holder_rewards_bps,
        holder_rewards,
    )
    event.post_state.validate()
    event.pre_state.validate()
    if quote_mint in SOL_QUOTE_MINTS:
        raise ExactCurveError("quote V2 extension received a SOL-quoted event")
    return event


def decode_quote_trade_events(log_messages: Iterable[str]) -> list[QuoteTradeEvent]:
    events = []
    for line in log_messages:
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if payload.startswith(TRADE_EVENT_DISCRIMINATOR):
            try:
                event = decode_quote_trade_event(payload)
            except ExactCurveError as error:
                if "SOL-quoted" in str(error):
                    continue
                raise
            events.append(event)
    return events


def validate_quote_transition(
    instruction: PumpInstruction,
    event: QuoteTradeEvent,
    *,
    transaction_successful: bool = True,
) -> dict[str, Any]:
    if not transaction_successful:
        return {"schema": SCHEMA, "classification": "FAILED_ZERO_STATE_MUTATION"}
    if event.quote_mint in SOL_QUOTE_MINTS:
        raise ExactCurveError("quote V2 extension received a SOL-quoted event")
    protocol_expected = fee_ceil(event.quote_amount, event.fee_basis_points)
    creator_bps = event.creator_fee_basis_points + event.cashback_fee_basis_points
    creator_expected = fee_ceil(event.quote_amount, creator_bps)
    if event.fee != protocol_expected:
        raise ExactCurveError(
            f"quote protocol fee mismatch {event.fee}/{protocol_expected}"
        )
    if event.creator_fee + event.cashback != creator_expected:
        raise ExactCurveError(
            "quote creator/cashback fee mismatch "
            f"{event.creator_fee + event.cashback}/{creator_expected}"
        )
    from exactCurveEconomicsV1 import Fees

    applied = apply_instruction(
        event.pre_state,
        instruction,
        Fees(event.fee_basis_points, creator_bps),
        cashback_bps=event.cashback_fee_basis_points,
        buyback_share_bps=event.buyback_fee_basis_points,
    )
    if applied.post_state != event.post_state:
        raise ExactCurveError("instruction does not reproduce quote reserve state")
    if isinstance(applied, BuyQuote):
        if applied.token_out != event.token_amount or applied.curve_quote_in != event.quote_amount:
            raise ExactCurveError("buy does not reproduce quote-native event amounts")
    elif isinstance(applied, SellQuote):
        if applied.token_in != event.token_amount or applied.gross_quote_out != event.quote_amount:
            raise ExactCurveError("sell does not reproduce quote-native event amounts")
    return {
        "schema": SCHEMA,
        "classification": "EXACT_QUOTE_NATIVE_TRANSITION_VALIDATED",
        "mint": event.mint,
        "quoteMint": event.quote_mint,
        "side": instruction.side,
        "instructionKind": instruction.kind,
        "quoteAmount": event.quote_amount,
        "tokenAmount": event.token_amount,
        "solNormalization": "BLOCKED_CAUSAL_CONVERSION_EVIDENCE_REQUIRED",
    }
