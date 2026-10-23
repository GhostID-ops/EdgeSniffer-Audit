#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import twoCqMultiArchetypeResearchV1 as subject
from exactCurveEconomicsV1 import CurveState
from twoCqDecisionSystemDecompositionV1 import CausalEvent


def event(mint: str, signature: str, received_ns: int, user: str, is_buy: bool = True) -> CausalEvent:
    before = CurveState(30_000_000_000, 1_000_000_000_000, 0, 900_000_000_000)
    after = CurveState(31_000_000_000, 970_000_000_000, 1_000_000_000, 870_000_000_000)
    return CausalEvent(
        mint=mint, signature=signature, slot=10, transaction_index=1,
        instruction_index=0, received_ns=received_ns, is_buy=is_buy, user=user,
        creator="creator", quote_lamports=1_000_000_000, token_raw=30_000_000_000,
        pre_state=before, post_state=after,
    )


def main() -> None:
    boundary = 10_000_000_000
    chosen = event("chosen", "a", boundary - 1_000_000, "external-a")
    control = event("control", "b", boundary - 2_000_000, "external-b")
    selected = {
        "signature": "teacher", "mint": "chosen", "route": "PUMP_BONDING_CURVE",
        "economicFamily": "SOL_PUMP_CURVE", "entryReceivedAtUnixNs": boundary,
        "entrySlot": 11, "entryTransactionIndex": 0,
    }
    row, reasons, detail = subject.build_active_sol_choice(
        selected, {"chosen": [chosen], "control": [control]}, {}
    )
    assert row is not None, reasons
    assert detail["controlN"] == 1
    assert sum(state["label"] for state in row["states"]) == 1
    assert "log_age_ms" not in row["states"][0]["features"]

    stale = event("chosen", "a", boundary - subject.ACTIVE_WINDOW_NS - 1, "external-a")
    row, reasons, _ = subject.build_active_sol_choice(
        selected, {"chosen": [stale], "control": [control]}, {}
    )
    assert row is None
    assert "SELECTED_MINT_ABSENT_FROM_ACTIVE_SOL_UNIVERSE" in reasons

    broken = event("chosen", "a", boundary - 1_000_000, "external-a")
    row, _, detail = subject.build_active_sol_choice(
        selected, {"chosen": [broken], "control": [control]},
        {"chosen": [((9, 0, -1, "x"), "TRADE_LOG_DECODE_FAILED")]},
    )
    assert row is None
    assert "TRADE_LOG_DECODE_FAILED" in detail["selectedExclusionReasons"]
    assert subject.ROUTE_CAPABILITIES["ROUTER"]["symmetricControls"] is False
    print("PASS")


if __name__ == "__main__":
    main()
