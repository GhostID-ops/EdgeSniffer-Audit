#!/usr/bin/env python3

import unittest

from constrainedCausalRankerV1 import economics, ranking_summary, spearman, top_count


class ConstrainedCausalRankerV1Tests(unittest.TestCase):
    def test_spearman_complete_cases_and_direction(self):
        rows = [
            {"features": {"a": 1.0, "b": 2.0}},
            {"features": {"a": 2.0, "b": 4.0}},
            {"features": {"a": 3.0, "b": 6.0}},
            {"features": {"a": None, "b": 9.0}},
        ]
        result = spearman(rows, "a", "b")
        self.assertEqual(3, result["completeCaseN"])
        self.assertAlmostEqual(1.0, result["spearmanRho"])

    def test_economics_and_loss_concentration(self):
        result = economics([20.0, 10.0, -20.0, -5.0])
        self.assertAlmostEqual(1.2, result["profitFactor"])
        self.assertAlmostEqual(0.8, result["lossConcentration"]["largestLossShare"])
        self.assertAlmostEqual(0.05, result["normalizedPnlSolAt1Sol"])

    def test_top_count_uses_ceiling(self):
        self.assertEqual(11, top_count(101, 10))

    def test_ranking_summary_uses_scores_only(self):
        rows = []
        for index in range(20):
            rows.append({
                "mint": str(index),
                "ordinal": index,
                "score": float(index),
                "labels": {
                    "POLICY_GE_10": index >= 18,
                    "policyReturnPct": 20.0 if index >= 18 else -20.0,
                },
            })
        result = ranking_summary(rows, "POLICY_GE_10")
        self.assertEqual(2, result["buckets"]["TOP_10"]["N"])
        self.assertEqual(1.0, result["buckets"]["TOP_10"]["positiveRate"])
        self.assertEqual(0, result["buckets"]["REMAINING_80"]["positiveRate"])


if __name__ == "__main__":
    unittest.main()
