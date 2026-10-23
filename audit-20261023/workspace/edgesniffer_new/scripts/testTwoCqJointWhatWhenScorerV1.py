#!/usr/bin/env python3

import copy
import unittest

import twoCqJointWhatWhenScorerV1 as subject


def model(feature: str) -> dict:
    return {
        "featureNames": [feature],
        "means": {feature: 0.0},
        "scales": {feature: 1.0},
        "intercept": 0.0,
        "coefficients": {feature: 1.0},
    }


def policy() -> dict:
    return {
        "schema": subject.JOINT_SCHEMA,
        "timingModel": model("hazard"),
        "timingThreshold": 0.7,
        "timingScoreDomain": "LOGISTIC_PROBABILITY",
        "crossMintModel": model("choice"),
        "crossMintScoreDomain": "STANDARDIZED_LOGISTIC_LOGIT_FOR_RANKING",
        "prospectiveImitationRequiredNext": True,
        "exactProfitabilityPermittedNow": False,
        "outcomesRead": 0,
        "submissions": 0,
    }


def event() -> dict:
    return {
        "eventId": "event-1",
        "changedMint": "teacher-mint",
        "canonicalOrderProven": True,
        "priorTimingFeatures": {"hazard": 0.0},
        "currentTimingFeatures": {"hazard": 1.5},
        "riskSet": [
            {"mint": "control", "features": {"choice": 1.0}},
            {"mint": "teacher-mint", "features": {"choice": 2.0}},
        ],
    }


class JointWhatWhenTests(unittest.TestCase):
    def test_combined_gate_passes_only_when_both_components_pass(self):
        result = subject.evaluate_event(policy(), event())
        self.assertTrue(result["qualified"])
        self.assertFalse(result["submit"])
        self.assertEqual("WHAT_AND_WHEN_PASS", result["reason"])

    def test_timing_non_crossing_rejects(self):
        row = event()
        row["currentTimingFeatures"]["hazard"] = 0.5
        self.assertEqual("NO_FRESH_TIMING_CROSSING", subject.evaluate_event(policy(), row)["reason"])

    def test_non_top_mint_rejects(self):
        row = event()
        row["riskSet"][0]["features"]["choice"] = 3.0
        self.assertEqual("CHANGED_MINT_NOT_TOP_RANKED", subject.evaluate_event(policy(), row)["reason"])

    def test_missing_prior_state_fails_closed(self):
        row = event()
        row.pop("priorTimingFeatures")
        self.assertEqual("PRIOR_TIMING_STATE_MISSING", subject.evaluate_event(policy(), row)["reason"])

    def test_top_score_tie_fails_closed(self):
        row = event()
        row["riskSet"][0]["features"]["choice"] = 2.0
        self.assertEqual("CROSS_SECTIONAL_TOP_SCORE_TIE", subject.evaluate_event(policy(), row)["reason"])

    def test_tainted_policy_fails(self):
        bad = copy.deepcopy(policy())
        bad["submissions"] = 1
        with self.assertRaisesRegex(RuntimeError, "JOINT_POLICY_TAINTED"):
            subject.evaluate_event(bad, event())


if __name__ == "__main__":
    unittest.main()
