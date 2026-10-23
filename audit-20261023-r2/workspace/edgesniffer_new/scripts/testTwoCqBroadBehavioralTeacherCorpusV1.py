#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import twoCqBroadBehavioralTeacherCorpusV1 as subject


class BroadBehavioralCorpusTests(unittest.TestCase):
    def test_outcome_keys_are_removed_recursively(self):
        value = {"feature": 1, "futurePnl": 2, "nested": {"returnPct": 3, "state": 4}}
        self.assertEqual(subject.strip_outcomes(value), {"feature": 1, "nested": {"state": 4}})

    def test_tier_does_not_promote_incomplete_exact_evidence(self):
        row = {"status": "ONLINE_ELIGIBLE"}
        tier, flags = subject.evidence(row, ["EXACT_RECONCILIATION_NOT_READY"], {"states": [{}], "controlN": 1})
        self.assertEqual(tier, 1)
        self.assertFalse(flags["exactCausalPartitionComplete"])
        self.assertTrue(flags["strictSymmetricEligible"])

    def test_tier_four_requires_strict_eligibility(self):
        row = {"status": "ONLINE_ELIGIBLE"}
        tier, flags = subject.evidence(row, [], {"states": [{}], "controlN": 1})
        self.assertEqual(tier, 4)
        self.assertTrue(flags["strictSymmetricEligible"])


if __name__ == "__main__":
    unittest.main()
