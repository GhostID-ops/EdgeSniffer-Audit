#!/usr/bin/env python3
import hashlib
import pathlib
import sqlite3
import tempfile
import unittest

import launchLabEvidenceReconcilerV1 as reconciler


def base58_encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = reconciler.BASE58_ALPHABET[remainder] + encoded
    leading = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * leading + (encoded or "1")


class CompactArrivalIndexTest(unittest.TestCase):
    def test_indexes_only_forward_records_and_finds_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "arrivals.bin"
            path.write_bytes(reconciler.COMPACT_MAGIC)
            connection = sqlite3.connect(":memory:")
            reconciler.schema(connection)
            self.assertEqual(reconciler.index_compact_arrivals(connection, path)[0], 0)

            raw_signature = bytes(range(64))
            received_ns = 1_789_000_000_123_456_789
            slot = 446_000_000
            with path.open("ab") as stream:
                stream.write(reconciler.COMPACT_RECORD.pack(
                    hashlib.sha256(raw_signature).digest(), received_ns, slot
                ))

            self.assertEqual(reconciler.index_compact_arrivals(connection, path)[0], 1)
            signature = base58_encode(raw_signature)
            self.assertEqual(
                reconciler.compact_receipt_ns(connection, path, signature, slot), received_ns
            )


if __name__ == "__main__":
    unittest.main()
