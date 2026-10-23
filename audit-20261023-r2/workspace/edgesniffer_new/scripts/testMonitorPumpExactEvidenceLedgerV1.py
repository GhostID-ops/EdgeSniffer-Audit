#!/usr/bin/env python3
import datetime as dt
import importlib.util
import pathlib
import unittest


PATH = pathlib.Path(__file__).with_name("monitorPumpExactEvidenceLedgerV1.py")
SPEC = importlib.util.spec_from_file_location("watchdog", PATH)
WATCHDOG = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(WATCHDOG)


class WatchdogTests(unittest.TestCase):
    def test_dead_open_stream_is_unhealthy_even_when_process_runs(self):
        now = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.timezone.utc)
        healthy, reasons = WATCHDOG.evaluate(
            now,
            {"ActiveState": "active", "SubState": "running", "MainPID": "7", "InvocationID": "abc", "Environment": ""},
            {
                "atUtc": "2026-09-06T11:59:55Z",
                "pid": 7,
                "processInstanceUuid": "abc",
                "startupTimeUtc": "2026-09-06T11:00:00Z",
                "evidenceFlowHealthy": False,
                "lastRawStreamUpdateUtc": "2026-09-06T11:50:00Z",
                "lastPartitionAppendUtc": "2026-09-06T11:50:00Z",
            },
            180,
        )
        self.assertFalse(healthy)
        self.assertIn("EVIDENCE_FLOW_UNHEALTHY", reasons)
        self.assertIn("RAW_STREAM_STALE", reasons)
        self.assertIn("PARTITION_APPEND_STALE", reasons)

    def test_fresh_flow_is_healthy(self):
        now = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.timezone.utc)
        healthy, reasons = WATCHDOG.evaluate(
            now,
            {"ActiveState": "active", "SubState": "running", "MainPID": "7", "InvocationID": "abc", "Environment": ""},
            {
                "atUtc": "2026-09-06T11:59:58Z",
                "pid": 7,
                "processInstanceUuid": "abc",
                "startupTimeUtc": "2026-09-06T11:00:00Z",
                "evidenceFlowHealthy": True,
                "lastRawStreamUpdateUtc": "2026-09-06T11:59:59Z",
                "lastPartitionAppendUtc": "2026-09-06T11:59:59Z",
            },
            180,
        )
        self.assertTrue(healthy)
        self.assertEqual([], reasons)

    def test_foreign_health_writer_is_rejected(self):
        now = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.timezone.utc)
        healthy, reasons = WATCHDOG.evaluate(
            now,
            {"ActiveState": "active", "SubState": "running", "MainPID": "7", "InvocationID": "expected", "Environment": ""},
            {
                "atUtc": "2026-09-06T11:59:58Z",
                "pid": 8,
                "processInstanceUuid": "foreign",
                "startupTimeUtc": "2026-09-06T11:00:00Z",
                "evidenceFlowHealthy": True,
                "lastRawStreamUpdateUtc": "2026-09-06T11:59:59Z",
                "lastPartitionAppendUtc": "2026-09-06T11:59:59Z",
            },
            180,
        )
        self.assertFalse(healthy)
        self.assertIn("HEALTH_WRITER_PID_MISMATCH", reasons)
        self.assertIn("HEALTH_WRITER_INSTANCE_MISMATCH", reasons)


if __name__ == "__main__":
    unittest.main()
