#!/usr/bin/env python3
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pumpQuoteNativeStatePlaneV1 as subject


ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(value: bytes) -> str:
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = ALPHABET[remainder] + encoded
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + (encoded or "")


class QuoteNativeStatePlaneTests(unittest.TestCase):
    def transaction(self, quote_mint: str):
        keys = ["mint", *[f"account-{index}" for index in range(1, 16)], quote_mint,
                "quote-curve-ata", "quote-token-program", subject.PUMP_PROGRAM_ID]
        return {
            "transaction": {"message": {
                "accountKeys": keys,
                "instructions": [{
                    "programIdIndex": 19,
                    "accounts": list(range(19)),
                    "data": b58encode(subject.CREATE_V2 + b"payload"),
                }],
            }},
            "meta": {"loadedAddresses": {"writable": [], "readonly": []}},
        }

    def test_non_sol_create_v2_is_recorded(self):
        self.assertEqual(
            [("mint", "USDC")], subject.quote_create_records(self.transaction("USDC"))
        )

    def test_sol_create_v2_is_excluded(self):
        quote = next(iter(subject.SOL_QUOTE_MINTS))
        self.assertEqual([], subject.quote_create_records(self.transaction(quote)))

    def test_partition_key_supports_frozen_cohort_boundary(self):
        root = pathlib.Path("/evidence")
        path = root / "partitions/2026/09/11/07/evidence.frames.zst"
        self.assertEqual("2026/09/11/07", subject.evidence_partition_key(path, root))

    def test_decoder_change_invalidates_only_derived_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(pathlib.Path(temporary) / "state.sqlite3")
            self.assertTrue(subject.schema(connection))
            connection.execute(
                "INSERT INTO evidence_files VALUES(?,?,?,?,?,?,?,?)",
                ("evidence", 1, 1, "sha", "now", 1, 0, 1),
            )
            connection.execute(
                "INSERT INTO exclusions VALUES(?,?,?,?,?)",
                ("signature", "mint", "reason", "detail", "evidence"),
            )
            connection.execute(
                "INSERT INTO compact_slots VALUES(?,?,?,?,?)", (1, 2, 3, 4, 5)
            )
            connection.execute(
                "UPDATE metadata SET value='stale' WHERE key='decoder_dependency_sha256'"
            )
            connection.commit()

            self.assertTrue(subject.schema(connection))
            self.assertEqual(0, connection.execute(
                "SELECT COUNT(*) FROM evidence_files"
            ).fetchone()[0])
            self.assertEqual(0, connection.execute(
                "SELECT COUNT(*) FROM exclusions"
            ).fetchone()[0])
            self.assertEqual(1, connection.execute(
                "SELECT COUNT(*) FROM compact_slots"
            ).fetchone()[0])
            self.assertFalse(subject.schema(connection))
            connection.close()


if __name__ == "__main__":
    unittest.main()
