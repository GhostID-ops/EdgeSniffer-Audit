#!/usr/bin/env python3
"""Versioned Mayhem extension for validated Pump exact-curve economics V1.

This module deliberately leaves exactCurveEconomicsV1.py unchanged. It proves
that a finalized Mayhem TradeEvent is economically replayable only when its
SOL and quote reserve views agree and the existing integer transition model
reproduces the finalized event exactly.
"""

from __future__ import annotations

from typing import Any

from exactCurveEconomicsV1 import (
    SOL_QUOTE_MINTS,
    SourceObservation,
    TradeEvent,
    validate_actual_trade_transition,
)
from validatePumpExactEvidenceLedgerV1 import event_fees, verify_event_fee_amounts


SCHEMA = "EXACT_CURVE_ECONOMICS_MAYHEM_V2"


def validate_mayhem_transition(
    source: SourceObservation,
    event: TradeEvent,
    *,
    transaction_successful: bool = True,
) -> dict[str, Any]:
    if not transaction_successful:
        return {"schema": SCHEMA, "classification": "FAILED_ZERO_STATE_MUTATION"}
    if not event.mayhem_mode:
        raise ValueError("Mayhem V2 extension received a non-Mayhem event")
    if event.quote_mint not in SOL_QUOTE_MINTS:
        raise ValueError(f"unsupported Mayhem quote mint {event.quote_mint}")
    if event.virtual_quote_reserves != event.post_state.virtual_sol:
        raise ValueError("Mayhem virtual quote reserve disagrees with SOL reserve")
    if event.real_quote_reserves != event.post_state.real_sol:
        raise ValueError("Mayhem real quote reserve disagrees with SOL reserve")
    if event.quote_amount != event.sol_amount:
        raise ValueError("Mayhem quote amount disagrees with SOL amount")
    edge_cases = verify_event_fee_amounts(event)
    applied = validate_actual_trade_transition(source, event, event_fees(event))
    return {
        "schema": SCHEMA,
        "classification": "EXACT_MAYHEM_TRANSITION_VALIDATED",
        "mint": event.mint,
        "signature": source.signature,
        "side": source.side,
        "instructionKind": source.instruction.kind,
        "postState": {
            "virtualSol": applied.post_state.virtual_sol,
            "virtualToken": applied.post_state.virtual_token,
            "realSol": applied.post_state.real_sol,
            "realToken": applied.post_state.real_token,
        },
        "feeEdgeCases": edge_cases,
    }
