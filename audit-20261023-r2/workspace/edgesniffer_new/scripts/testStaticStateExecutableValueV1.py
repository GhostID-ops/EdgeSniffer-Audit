#!/usr/bin/env python3

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import staticStateExecutableValueV1 as study


def row(mint: str, timestamp: int, pnl: float, outcome: str = "FLOW_DECAY_TIMEOUT", score: float = 0.5):
    return {
        "mint": mint, "ordinal": timestamp, "stateIndex": 1, "decisionUnixMs": timestamp,
        "pnlSol": pnl, "returnPct": pnl * 200, "timeToOutcomeMs": 1000,
        "outcome": outcome, "staticScore": score, "fold": 2,
        "counterfactualFailedTransactions": 0,
    }


class StaticStateExecutableValueTests(unittest.TestCase):
    def test_first_by_mint_is_chronological(self):
        rows = [row("A", 20, 0.1), row("A", 10, -0.1), row("B", 15, 0.2)]
        picked = study.first_by_mint(rows)
        self.assertEqual({item["mint"]: item["decisionUnixMs"] for item in picked}, {"A": 10, "B": 15})

    def test_metrics_profit_factor_and_drawdown(self):
        value = study.metrics([row("A", 1, 0.1), row("B", 2, -0.05), row("C", 3, 0.025)])
        self.assertAlmostEqual(value["profitFactor"], 2.5)
        self.assertAlmostEqual(value["expectancySol"], 0.025)
        self.assertAlmostEqual(value["sequentialMaxDrawdownSol"], 0.05)
        self.assertEqual(value["uniqueMintN"], 3)

    def test_policy_uses_fold_threshold_and_first_crossing(self):
        rows = [row("A", 1, 0.0, score=0.7), row("A", 2, 0.0, score=0.9), row("B", 3, 0.0, score=0.79)]
        policies = study.select_policies(rows, {2: {"top20pct": 0.8}}, [0.2])
        self.assertEqual([item["decisionUnixMs"] for item in policies["top20pct"]], [2])

    def test_p75_join_never_falls_back(self):
        selected = [row("A", 1, 0.1), row("B", 2, 0.1)]
        joined, missing = study.join_sensitivity(selected, {("A", 1): row("A", 1, -0.1)})
        self.assertEqual(len(joined), 1)
        self.assertEqual(missing, 1)
        self.assertEqual(joined[0]["pnlSol"], -0.1)


if __name__ == "__main__":
    unittest.main()
