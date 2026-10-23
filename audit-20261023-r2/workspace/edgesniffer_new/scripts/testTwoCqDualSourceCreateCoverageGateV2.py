#!/usr/bin/env python3
"""Regression tests for exact-evidence partition attribution in gate V2."""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import twoCqDualSourceCreateCoverageGateV2 as subject


class CoverageGateV2Tests(unittest.TestCase):
    def test_duplicate_signature_is_counted_once_across_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = pathlib.Path(temporary) / "registry.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE create_registry(signature TEXT PRIMARY KEY);
                CREATE TABLE source_arrival_candidates(signature TEXT, source TEXT);
                CREATE TABLE yellowstone_processed_partitions(partition_key TEXT PRIMARY KEY);
            """)
            connection.execute("INSERT INTO create_registry VALUES ('same-signature')")
            connection.execute(
                "INSERT INTO source_arrival_candidates VALUES (?,?)",
                ("same-signature", subject.YELLOWSTONE_SOURCE),
            )
            connection.executemany(
                "INSERT INTO yellowstone_processed_partitions VALUES (?)",
                [("first",), ("second",)],
            )
            frame = ({
                "kind": "rpc_finalized_transaction",
                "finalized": True,
                "successful": True,
                "pumpInstructionKinds": ["CREATE_V2"],
                "signature": "same-signature",
                "slot": 100,
            }, b"ignored")
            with mock.patch.object(subject.registry, "iter_frames", side_effect=lambda *_args: iter([frame])):
                counted: set[str] = set()
                first = subject.coverage_for_partition(
                    connection, "first", pathlib.Path("first.zst"), 0, counted,
                )
                second = subject.coverage_for_partition(
                    connection, "second", pathlib.Path("second.zst"), 0, counted,
                )
            self.assertEqual(first["finalizedCreateDenominator"], 1)
            self.assertEqual(first["unionMatches"], 1)
            self.assertEqual(second["finalizedCreateDenominator"], 0)
            connection.close()
            self.assertEqual(second["duplicateSignaturesExcluded"], 1)


if __name__ == "__main__":
    unittest.main()
