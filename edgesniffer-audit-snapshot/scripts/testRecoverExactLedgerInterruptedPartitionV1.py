#!/usr/bin/env python3

import datetime as dt
import hashlib
import json
import pathlib
import struct
import tempfile
import unittest

import recoverExactLedgerInterruptedPartitionV1 as subject


def frame(metadata):
    encoded = json.dumps(metadata, separators=(",", ":")).encode()
    return struct.pack("<II", len(encoded), 0) + encoded + hashlib.sha256(encoded).digest()


class InterruptedPartitionRecoveryTests(unittest.TestCase):
    def test_strict_scan_and_boundary_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "evidence.frames.partial"
            start = dt.datetime(2026, 9, 12, 19, tzinfo=dt.timezone.utc)
            first = int((start + dt.timedelta(seconds=1)).timestamp() * 1e9)
            last = int((start + dt.timedelta(minutes=59, seconds=59)).timestamp() * 1e9)
            metadata = lambda ns, slot: {
                "kind": "transaction", "receivedAtUnixNs": ns, "slot": slot,
                "finalized": False, "unresolved": True,
            }
            path.write_bytes(subject.MAGIC + frame(metadata(first, 1)) + frame(metadata(last, 2)))
            scanned = subject.scan_frames(path)
            self.assertEqual(2, scanned["coverage"]["records"])
            self.assertEqual(2, scanned["coverage"]["provisionalTransactions"])
            self.assertEqual((1, 2), (scanned["startSlot"], scanned["endSlot"]))

    def test_strict_scan_rejects_truncated_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "evidence.frames.partial"
            path.write_bytes(subject.MAGIC + b"short")
            with self.assertRaisesRegex(RuntimeError, "TRUNCATED_FRAME_PREFIX"):
                subject.scan_frames(path)


if __name__ == "__main__":
    unittest.main()
