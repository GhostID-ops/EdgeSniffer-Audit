#!/usr/bin/env python3

import unittest

from netFlowCovariateDriftV1 import classify, ks_distance, quantile, wasserstein_distance


class NetFlowCovariateDriftV1Test(unittest.TestCase):
    def test_quantile_linear(self):
        self.assertEqual(9.1, quantile(list(range(1, 11)), 0.9))

    def test_identical_distances_are_zero(self):
        values = [1.0, 2.0, 3.0]
        self.assertEqual(0.0, ks_distance(values, values))
        self.assertEqual(0.0, wasserstein_distance(values, values))

    def test_wasserstein_simple_shift(self):
        self.assertEqual(1.0, wasserstein_distance([0.0, 1.0], [1.0, 2.0]))

    def test_classification_is_frozen(self):
        self.assertEqual("NET_FLOW_DISTRIBUTION_STABLE", classify(0.09, 92.9, 0.1, 1.0))
        self.assertEqual("NET_FLOW_DISTRIBUTION_MODEST_DRIFT", classify(0.10, 90.0, 0.0, 1.0))
        self.assertEqual("NET_FLOW_DISTRIBUTION_MATERIAL_DRIFT", classify(0.20, 90.0, 0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
