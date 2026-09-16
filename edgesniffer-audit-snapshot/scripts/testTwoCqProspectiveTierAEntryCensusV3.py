#!/usr/bin/env python3

import json
import pathlib
import tempfile
import unittest

import twoCqProspectiveTierAEntryCensusV3 as census


class CompactContinuityTest(unittest.TestCase):
    def segments(self):
        rows = [
            {"event": "CONNECTED", "processInstance": "p1", "timestampUtc": "2026-01-01T00:00:00Z"},
            {"event": "DISCONNECTED", "processInstance": "p1", "timestampUtc": "2026-01-01T00:01:00Z"},
            {"event": "CONNECTED", "processInstance": "p1", "timestampUtc": "2026-01-01T00:01:01Z"},
            {"event": "CONNECTED", "processInstance": "p2", "timestampUtc": "2026-01-01T00:02:00Z"},
        ]
        health = {
            "processInstance": "p2",
            "connected": True,
            "lastReceiptAtUtc": "2026-01-01T00:03:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "continuity.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            return census.compact_continuity_segments(path, health)

    @staticmethod
    def ns(value):
        return int(census.tier_a.parse_utc(value).timestamp() * 1_000_000_000)

    def covered(self, start, end):
        return census.covering_continuity_segment(
            self.ns(start), self.ns(end), self.segments()
        )

    def test_before_disconnect_is_covered(self):
        self.assertIsNotNone(self.covered("2026-01-01T00:00:10Z", "2026-01-01T00:00:50Z"))

    def test_crossing_disconnect_is_rejected(self):
        self.assertIsNone(self.covered("2026-01-01T00:00:50Z", "2026-01-01T00:01:10Z"))

    def test_after_reconnect_is_covered(self):
        self.assertIsNotNone(self.covered("2026-01-01T00:01:10Z", "2026-01-01T00:01:50Z"))

    def test_crossing_process_handoff_is_rejected(self):
        self.assertIsNone(self.covered("2026-01-01T00:01:50Z", "2026-01-01T00:02:10Z"))

    def test_current_process_is_covered_to_health_boundary(self):
        segment = self.covered("2026-01-01T00:02:10Z", "2026-01-01T00:02:50Z")
        self.assertEqual(segment["processInstance"], "p2")

    def test_invalid_or_future_interval_is_rejected(self):
        self.assertIsNone(self.covered("2026-01-01T00:02:50Z", "2026-01-01T00:03:10Z"))


if __name__ == "__main__":
    unittest.main()
