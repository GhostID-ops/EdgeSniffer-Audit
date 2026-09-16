#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import twoCqEventDrivenFingerprintFreezeV1 as subject


def example(ordinal, mint, selected, controls):
    states = [
        {"label": False, "features": {"x": value}, "receivedNs": ordinal * 10 + index, "signature": f"c{index}"}
        for index, value in enumerate(controls)
    ]
    states.append({"label": True, "features": {"x": selected}, "receivedNs": ordinal * 10 + 9, "signature": "s"})
    return {"eligibleOrdinal": ordinal, "mint": mint, "controlN": len(controls), "states": states}


class TestEventDrivenFingerprintFreeze(unittest.TestCase):
    def test_percentile_interpolates(self):
        self.assertEqual(subject.percentile([0, 10], 0.25), 2.5)

    def test_effect_uses_paired_control_median(self):
        rows = [example(1, "a", 5, [1, 3]), example(2, "b", 6, [2, 4])]
        result = subject.effect_for_examples(rows, "x")
        self.assertEqual(result["pairedMedianDifference"], 3)
        self.assertEqual(result["teacherAboveControlMedianPct"], 100)

    def test_compact_metrics_uses_internal_assessment(self):
        report = {"validation": {"positiveN": 2, "rocAuc": 0.7, "averagePrecision": 0.5, "withinMint": {
            "actualRankedFirstPct": 60, "actualRankedTop3Pct": 80,
            "meanReciprocalRank": 0.7, "chanceRankedFirstPct": 20,
        }}}
        self.assertEqual(subject.compact_model_metrics(report)["top1Pct"], 60)

    def test_frozen_model_preserves_feature_order(self):
        frozen = subject.frozen_model(("a", "b"), ([1, 2], [3, 4], (0.5, [0.1, 0.2])))
        self.assertEqual(frozen["featureNames"], ["a", "b"])
        self.assertEqual(frozen["coefficients"]["b"], 0.2)


if __name__ == "__main__":
    unittest.main()
