import unittest

from twoCqCausalEntryTimingFingerprintV1 import average_precision, ranking_metrics


class TieSafeMetricTests(unittest.TestCase):
    def test_average_precision_groups_equal_scores(self):
        self.assertAlmostEqual(average_precision([1, 0], [0.5, 0.5]), 0.5)
        self.assertAlmostEqual(average_precision([1, 0], [0.6, 0.5]), 1.0)

    def test_constant_model_is_not_reported_as_unique_top_one(self):
        examples = [{
            "states": [
                {"label": True, "features": {"x": 1.0}},
                {"label": False, "features": {"x": 1.0}},
            ]
        }]
        metrics = ranking_metrics(examples, ("x",), [0.0], [1.0], (0.0, [0.0]))
        self.assertEqual(metrics["actualRankedFirstPct"], 0.0)
        self.assertEqual(metrics["actualRankedFirstInclusivePct"], 100.0)
        self.assertEqual(metrics["randomizedTop1CreditPct"], 50.0)
        self.assertEqual(metrics["topScoreTieEpisodePct"], 100.0)
        self.assertEqual(metrics["meanTopScoreTieCount"], 2.0)


if __name__ == "__main__":
    unittest.main()
