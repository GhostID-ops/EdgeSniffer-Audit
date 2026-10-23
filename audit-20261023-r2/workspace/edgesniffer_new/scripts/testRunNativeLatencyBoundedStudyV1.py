#!/usr/bin/env python3
import unittest

from runNativeLatencyBoundedStudyV1 import checkpoint_for


class NativeLatencyStudyTests(unittest.TestCase):
    def test_checkpoint_selection_is_horizon_specific(self):
        window = {
            "native_checkpoint_marks": [
                {"horizon_seconds": 15, "checkpoint_receive_ts_ms": 20_000},
                {"horizon_seconds": 30, "checkpoint_receive_ts_ms": 35_000},
                {"horizon_seconds": 60, "checkpoint_receive_ts_ms": 65_000},
            ]
        }
        self.assertEqual(checkpoint_for(window, 15)["checkpoint_receive_ts_ms"], 20_000)
        self.assertEqual(checkpoint_for(window, 30)["checkpoint_receive_ts_ms"], 35_000)
        self.assertIsNone(checkpoint_for(window, 120))

    def test_duplicate_horizon_uses_earliest_native_checkpoint(self):
        window = {"native_checkpoint_marks": [
            {"horizon_seconds": 15, "checkpoint_receive_ts_ms": 25_000},
            {"horizon_seconds": 15, "checkpoint_receive_ts_ms": 20_000},
        ]}
        self.assertEqual(checkpoint_for(window, 15)["checkpoint_receive_ts_ms"], 20_000)


if __name__ == "__main__":
    unittest.main()
