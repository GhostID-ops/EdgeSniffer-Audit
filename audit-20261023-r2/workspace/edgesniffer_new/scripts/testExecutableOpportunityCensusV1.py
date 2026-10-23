#!/usr/bin/env python3

import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from buildPumpExactAlphaResearchV1 import EventRow
from exactCurveEconomicsV1 import CurveState, Fees, PumpInstruction, TradeEvent, quote_exact_quote_in_buy
from executableOpportunityCensusV1 import build_decisions, evaluate_cell, group_events


FEES = Fees(protocol_bps=95, creator_bps=30)
MINT = "mint"
QUOTE = "11111111111111111111111111111111"


def buy_row(state, gross, arrival, user, signature, slot, creator="creator", min_tokens=None):
    quote = quote_exact_quote_in_buy(state, gross, FEES)
    event = TradeEvent(
        MINT, quote.curve_quote_in, quote.token_out, True, user, arrival // 1000,
        quote.post_state, "fee", FEES.protocol_bps, quote.protocol_fee, creator,
        FEES.creator_bps, quote.creator_fee, False, "buy_exact_sol_in", False,
        0, 0, 0, 0, (), QUOTE, gross, quote.post_state.virtual_sol,
        quote.post_state.real_sol,
    )
    source = SimpleNamespace(
        instruction=PumpInstruction(
            "buy_exact_sol_in", gross,
            quote.token_out if min_tokens is None else min_tokens, True,
        )
    )
    return EventRow(
        signature, f"raw-{signature}", f"canonical-{signature}", arrival,
        slot, 0, 0, source, event,
    )


class ExecutableOpportunityCensusTests(unittest.TestCase):
    def setUp(self):
        self.initial = CurveState(
            virtual_sol=30_000_000_000,
            virtual_token=1_073_000_000_000_000,
            real_sol=10_000_000_000,
            real_token=793_100_000_000_000,
        )
        self.contract = json.loads(
            (pathlib.Path(__file__).parents[1] / "research" / "executable-opportunity-census-v1" / "contract.json").read_text()
        )

    def test_event_states_dedupe_same_transaction(self):
        first = buy_row(self.initial, 600_000_000, 1000, "buyer-1", "sig-1", 1)
        groups = group_events([first])
        decisions = build_decisions(groups, 900)
        after = next(item for item in decisions if "AFTER_EXTERNAL_BUY_1" in item["labels"])
        self.assertIn("AFTER_EXTERNAL_GROSS_BUY_0P5_SOL", after["labels"])
        self.assertEqual(2, len(decisions))  # Initial pre-state plus one deduped post-state.

    def test_initial_state_is_only_prefirst_at_zero_delay(self):
        first = buy_row(self.initial, 100_000_000, 1000, "buyer-1", "sig-1", 1)
        groups = group_events([first])
        initial = build_decisions(groups, 900)[0]
        zero = evaluate_cell(initial, groups, 0.5, "ZERO_DELAY_ORACLE", 0, 0, 0, [0.001], [10])
        delayed = evaluate_cell(initial, groups, 0.5, "P50", 37, 261, 351, [0.001], [10])
        self.assertEqual(self.initial.virtual_sol, zero["entryState"]["virtualSol"])
        self.assertEqual(first.event.post_state.virtual_sol, delayed["entryState"]["virtualSol"])

    def test_exact_cell_includes_fee_impact_and_frozen_policies(self):
        first = buy_row(self.initial, 100_000_000, 1000, "buyer-1", "sig-1", 1)
        groups = group_events([first])
        decision = next(item for item in build_decisions(groups, 900) if "AFTER_EXTERNAL_BUY_1" in item["labels"])
        cell = evaluate_cell(decision, groups, 0.5, "P50", 37, 261, 351, [0.001], [5, 10, 20])
        self.assertEqual("EXACT", cell["status"])
        self.assertGreater(cell["protocolFeeLamports"], 0)
        self.assertGreater(cell["creatorFeeLamports"], 0)
        self.assertGreater(cell["tokensReceivedRaw"], 0)
        self.assertEqual(
            {"TP20_SL20_MAX120", "FIXED_5S_SL20", "TRAIL20_12_SL20_120S"},
            set(cell["policies"]),
        )

    def test_our_order_can_invalidate_later_original_min_out(self):
        first = buy_row(self.initial, 100_000_000, 1000, "buyer-1", "sig-1", 1)
        second = buy_row(first.event.post_state, 100_000_000, 1100, "buyer-2", "sig-2", 2)
        groups = group_events([first, second])
        decision = next(item for item in build_decisions(groups, 900) if "AFTER_EXTERNAL_BUY_1" in item["labels"])
        cell = evaluate_cell(decision, groups, 1.0, "ZERO_DELAY_ORACLE", 0, 0, 0, [0.001], [10])
        self.assertEqual("EXACT", cell["status"])
        self.assertEqual(1, cell["counterfactualAttemptedTransactions"])
        self.assertEqual(1, cell["counterfactualFailedTransactions"])


if __name__ == "__main__":
    unittest.main()
