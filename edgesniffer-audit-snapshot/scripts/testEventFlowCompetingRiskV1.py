#!/usr/bin/env python3

import unittest

from eventFlowCompetingRiskV1Build import external_group, safe_ratio


class EventFlowCompetingRiskV1Test(unittest.TestCase):
    def test_safe_ratio(self):
        self.assertEqual(2.0, safe_ratio(4, 2))
        self.assertIsNone(safe_ratio(4, 0))

    def test_feature_count_is_frozen(self):
        import json
        from pathlib import Path
        contract = json.loads((Path(__file__).parent.parent / "research" / "event-flow-competing-risk-v1" / "contract.json").read_text())
        self.assertEqual(20, len(contract["features"]))


if __name__ == "__main__":
    unittest.main()
