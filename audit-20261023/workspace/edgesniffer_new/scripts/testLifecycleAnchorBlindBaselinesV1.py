#!/usr/bin/env python3

import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from exactCurveEconomicsV1 import CurveState
from lifecycleAnchorBlindBaselinesV1 import (
    pairwise_overlap,
    reconstruct_anchor_metadata,
    result_metric,
    select_max_open1,
)


QUOTE = "11111111111111111111111111111111"


def fake_row(signature, arrival, instruction_index, amount, user, state):
    event = SimpleNamespace(
        quote_mint=QUOTE,
        mayhem_mode=False,
        is_buy=True,
        user=user,
        creator="creator",
        immediate_quote_amount=amount,
        post_state=state,
    )
    return SimpleNamespace(
        signature=signature,
        arrival_ms=arrival,
        slot=arrival,
        tx_index=0,
        instruction_index=instruction_index,
        observation_evidence_id=f"raw-{signature}",
        canonical_evidence_id=f"canonical-{signature}",
        event=event,
    )


class LifecycleAnchorTests(unittest.TestCase):
    def test_first_crossing_uses_actual_overshoot_and_external_events(self):
        state = CurveState(30_000_000_000, 1_000_000_000, 10_000_000_000, 800_000_000)
        rows = [
            fake_row("one", 1000, 0, 400_000_000, "same", state),
            fake_row("two", 1100, 0, 700_000_000, "same", state),
        ]
        metadata = reconstruct_anchor_metadata(rows, 900)
        anchor = metadata["EXTERNAL_GROSS_BUY_GE_1_SOL"]
        self.assertAlmostEqual(1.1, anchor["actualCumulativeExternalGrossBuySol"])
        self.assertAlmostEqual(0.1, anchor["overshootSol"])
        self.assertEqual(2, anchor["externalBuyEventCount"])
        self.assertEqual(1, anchor["uniqueExternalBuyerCount"])

    def test_fifth_buy_event_is_not_fifth_unique_buyer(self):
        state = CurveState(30_000_000_000, 1_000_000_000, 10_000_000_000, 800_000_000)
        rows = [
            fake_row("one", 1000, index, 100_000_000, "repeat", state)
            for index in range(6)
        ]
        anchor = reconstruct_anchor_metadata(rows, 900)["EXTERNAL_BUY_EVENT_5"]
        self.assertEqual(6, anchor["actualExternalBuyEventCount"])
        self.assertEqual(1, anchor["buyEventOvershoot"])
        self.assertEqual(1, anchor["uniqueExternalBuyerCount"])

    def test_max_open_one_uses_exit_landing(self):
        rows = [
            {"mint": "a", "decisionUnixMs": 10, "exitUnixMs": 30},
            {"mint": "b", "decisionUnixMs": 20, "exitUnixMs": 25},
            {"mint": "c", "decisionUnixMs": 30, "exitUnixMs": 40},
        ]
        accepted, rejected = select_max_open1(rows)
        self.assertEqual(["a", "c"], [row["mint"] for row in accepted])
        self.assertEqual(1, rejected)

    def test_metric_uses_solana_pnl_for_pf_and_reports_loss_concentration(self):
        rows = [
            {"mint": "a", "decisionUnixMs": 1, "pnlSol": 0.2, "returnPct": 20, "outcome": "TP", "counterfactualDiverged": False},
            {"mint": "b", "decisionUnixMs": 2, "pnlSol": -0.1, "returnPct": -10, "outcome": "SL", "counterfactualDiverged": True},
        ]
        metric = result_metric(rows)
        self.assertAlmostEqual(2.0, metric["profitFactor"])
        self.assertAlmostEqual(0.05, metric["normalizedPnlSol"] / 2)
        self.assertEqual(1, metric["divergentN"])
        self.assertEqual(1.0, metric["largestLossShare"])

    def test_overlap_does_not_sum_populations(self):
        rows = pairwise_overlap({"a": {"one", "two"}, "b": {"two", "three"}})
        self.assertEqual(1, rows[0]["intersectionN"])
        self.assertAlmostEqual(1 / 3, rows[0]["jaccard"])


if __name__ == "__main__":
    unittest.main()
