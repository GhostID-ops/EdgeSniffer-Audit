#!/usr/bin/env python3

import unittest

import twoCqPrimaryPolicySealedValidationV1 as subject


class SealedValidationTests(unittest.TestCase):
    def test_fitted_model_preserves_frozen_feature_order(self):
        names, fitted = subject.fitted_model({
            "featureNames": ["b", "a"],
            "means": {"a": 1, "b": 2},
            "scales": {"a": 3, "b": 4},
            "intercept": 5,
            "coefficients": {"a": 6, "b": 7},
        })
        self.assertEqual(("b", "a"), names)
        self.assertEqual([2, 1], fitted[0])
        self.assertEqual([7, 6], fitted[2][1])

    def test_source_has_hard_n500_gate_and_no_model_fit(self):
        source = subject.pathlib.Path(subject.__file__).read_text(encoding="utf-8")
        self.assertIn('if int(status.get("currentEligibleN") or 0) < 500', source)
        self.assertIn('policy.get("sealedValidationAuthorized") is not True', source)
        self.assertNotIn("fit_logistic", source)
        self.assertNotIn("--outcome", source)


if __name__ == "__main__":
    unittest.main()
