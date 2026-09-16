#!/usr/bin/env python3

import unittest

from netFlowUnseenGateRefreshV1 import prefix_sha


class NetFlowUnseenGateRefreshV1Test(unittest.TestCase):
    def test_prefix_hash_changes_with_endpoint(self):
        rows = [{"ordinal": 1, "mint": "a"}, {"ordinal": 2, "mint": "b"}]
        self.assertNotEqual(prefix_sha(rows[:1]), prefix_sha(rows))


if __name__ == "__main__":
    unittest.main()
