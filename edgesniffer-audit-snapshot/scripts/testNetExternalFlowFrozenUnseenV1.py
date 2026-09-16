#!/usr/bin/env python3

import unittest

from netExternalFlowFrozenUnseenV1 import percentile, prefix_sha


class NetExternalFlowFrozenUnseenV1Test(unittest.TestCase):
    def test_linear_q90(self):
        self.assertEqual(9.1, percentile(list(range(1, 11)), 0.9))

    def test_prefix_hash_is_order_sensitive(self):
        left = [{"ordinal": 1, "mint": "a"}, {"ordinal": 2, "mint": "b"}]
        right = list(reversed(left))
        self.assertNotEqual(prefix_sha(left), prefix_sha(right))


if __name__ == "__main__":
    unittest.main()
