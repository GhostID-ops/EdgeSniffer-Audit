#!/usr/bin/env python3

import unittest

import twoCqChoiceSetRepresentativenessV1 as subject


class ChoiceSetRepresentativenessTests(unittest.TestCase):
    def test_identical_numeric_populations_have_zero_shift(self):
        result = subject.numeric_comparison("x", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        self.assertEqual(0.0, result["absoluteStandardizedMeanDifference"])
        self.assertEqual(0.0, result["ksDistance"])

    def test_separated_numeric_populations_are_detected(self):
        result = subject.numeric_comparison("x", [10.0, 11.0], [1.0, 2.0])
        self.assertGreater(result["absoluteStandardizedMeanDifference"], 0.5)
        self.assertEqual(1.0, result["ksDistance"])

    def test_categorical_total_variation(self):
        rows = [
            {"covered": True, "route": "A"}, {"covered": True, "route": "A"},
            {"covered": False, "route": "B"}, {"covered": False, "route": "B"},
        ]
        result = subject.categorical_comparison("route", rows)
        self.assertEqual(1.0, result["totalVariation"])

    def test_gate_is_fail_closed(self):
        self.assertFalse(subject.gate("x", 0.7, "<=", 0.65)["passed"])
        self.assertFalse(subject.gate("x", 0.4, ">=", 0.55)["passed"])

    def test_source_has_no_validation_or_economic_inputs(self):
        source = subject.pathlib.Path(subject.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--validation", source)
        self.assertNotIn("--outcome", source)
        self.assertNotIn("future_return", source)
        self.assertIn("DEVELOPMENT_POPULATION_NOT_EXACT_ORDINALS_1_350", source)


if __name__ == "__main__":
    unittest.main()
