import json
import tempfile
import unittest
from pathlib import Path

from adapt2CQFrozenScorerNativeV1 import metadata_for, post_event_boundary, scorer_logit, scorer_scores, sigmoid
from twoCqCausalEntryTimingFingerprintV1 import CurveState, Event


class FrozenScorerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.scorer = {
            "timingThreshold": 0.4622968637150163,
            "timingModel": {
                "intercept": -0.1,
                "featureNames": ["x"],
                "coefficients": {"x": 2.0},
                "means": {"x": 0.0},
                "scales": {"x": 1.0},
            },
        }

    def test_score_is_probability_and_threshold_is_not_compared_to_logit(self):
        logit, probability = scorer_scores({"x": 0.05}, self.scorer)
        self.assertAlmostEqual(logit, 0.0)
        self.assertAlmostEqual(probability, 0.5)
        self.assertGreater(probability, self.scorer["timingThreshold"])
        self.assertLess(scorer_logit({"x": 0.05}, self.scorer), self.scorer["timingThreshold"])

    def test_sigmoid_is_stable_for_large_predictors(self):
        self.assertGreater(sigmoid(1000.0), 0.999)
        self.assertLess(sigmoid(-1000.0), 0.001)

    def test_post_event_boundary_includes_trigger_and_uses_post_state(self):
        pre = CurveState(virtual_sol=100, virtual_token=1000, real_sol=100, real_token=1000)
        post = CurveState(virtual_sol=101, virtual_token=990, real_sol=101, real_token=990)
        event = Event(
            mint="mint", signature="sig", slot=1, transaction_index=0,
            instruction_index=0, observed_ms=1000, evidence_id="e",
            is_buy=True, user="wallet", creator="creator", quote_lamports=1,
            token_raw=1, pre_state=pre, post_state=post,
        )
        snapshot, prior = post_event_boundary(event, [])
        self.assertEqual(snapshot.pre_state, post)
        self.assertEqual(snapshot.post_state, post)
        self.assertEqual(prior, [event])
        self.assertFalse(snapshot.is_buy)

    def test_missing_lineage_is_fail_closed(self):
        metadata, reasons = metadata_for("mint", {}, {})
        self.assertEqual(metadata, {})
        self.assertIn("missing_exact_create_receive_clock", reasons)
        self.assertIn("missing_creator_identity", reasons)
        self.assertIn("missing_exact_launch_reserve_state", reasons)

    def test_enrichment_supplies_exact_lineage(self):
        metadata, reasons = metadata_for(
            "mint",
            {"mint": {"create_receive_ts_ms": 1000, "creator": "creator", "initial_state": {
                "virtual_sol": 1, "virtual_token": 2, "real_sol": 1, "real_token": 2,
            }}},
            {},
        )
        self.assertEqual(reasons, [])
        self.assertEqual(metadata["create_receive_ts_ms"], 1000)


if __name__ == "__main__":
    unittest.main()
