import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pumpSwapEvidenceReconcilerV1 as selected
import pumpSwapGlobalEvidenceReconcilerV1 as subject


class FakeRpc:
    def __init__(self, pages):
        self.pages = iter(pages)

    def call(self, method, params):
        self.assert_method = method
        return next(self.pages)


class ProgramSignatureCursorTests(unittest.TestCase):
    def connection(self):
        connection = sqlite3.connect(":memory:")
        selected.schema(connection)
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES('program_head_signature','old-head')"
        )
        return connection

    @mock.patch.object(subject.common, "compact_receipt_ns", return_value=None)
    def test_small_capped_page_preserves_durable_cursor(self, _receipt):
        connection = self.connection()
        rpc = FakeRpc([[{"signature": "new-head", "slot": 42}]])
        report = subject.discover_program_signatures(
            connection, rpc, pathlib.Path("unused"), max_signatures=1
        )

        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        self.assertTrue(report["truncated"])
        self.assertFalse(report["scanCommitted"])
        self.assertEqual(metadata["program_head_signature"], "old-head")
        self.assertEqual(metadata["scan_new_head"], "new-head")
        self.assertEqual(metadata["scan_cursor"], "new-head")

    @mock.patch.object(subject.common, "compact_receipt_ns", return_value=None)
    def test_short_page_commits_new_head(self, _receipt):
        connection = self.connection()
        rpc = FakeRpc([[{"signature": "new-head", "slot": 42}]])
        report = subject.discover_program_signatures(
            connection, rpc, pathlib.Path("unused"), max_signatures=2
        )

        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        self.assertFalse(report["truncated"])
        self.assertTrue(report["scanCommitted"])
        self.assertEqual(metadata["program_head_signature"], "new-head")
        self.assertNotIn("scan_cursor", metadata)


class CompactSlotIndexTests(unittest.TestCase):
    def test_aggregated_index_matches_reference_implementation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "arrivals.bin"
            records = [
                (bytes([1]) * 32, 100, 7),
                (bytes([2]) * 32, 110, 7),
                (bytes([3]) * 32, 120, 8),
                (bytes([4]) * 32, 130, 7),
            ]
            with path.open("wb") as stream:
                stream.write(subject.common.COMPACT_MAGIC)
                for record in records:
                    stream.write(subject.common.COMPACT_RECORD.pack(*record))

            reference = sqlite3.connect(":memory:")
            optimized = sqlite3.connect(":memory:")
            selected.schema(reference)
            selected.schema(optimized)
            start = len(subject.common.COMPACT_MAGIC)
            subject.common.metadata_set(reference, "compact_offset", str(start))
            subject.common.metadata_set(optimized, "compact_offset", str(start))

            expected = subject.common.index_compact_arrivals(reference, path)
            actual = subject.index_compact_arrivals_by_slot(optimized, path)
            self.assertEqual(actual, expected)
            query = "SELECT * FROM compact_slots ORDER BY slot"
            self.assertEqual(reference.execute(query).fetchall(), optimized.execute(query).fetchall())


if __name__ == "__main__":
    unittest.main()
