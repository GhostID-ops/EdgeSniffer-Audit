#!/usr/bin/env python3
import struct
import base64
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import raydiumCpmmExactEconomicsV1 as subject


class RaydiumCpmmExactEconomicsTest(unittest.TestCase):
    def test_decodes_official_swap_event_layout(self):
        pool = bytes(range(32))
        input_mint = bytes(range(32, 64))
        output_mint = bytes(range(64, 96))
        payload = (
            subject.SWAP_EVENT + pool
            + struct.pack("<6Q", 1000, 2000, 100, 80, 2, 3)
            + b"\x01" + input_mint + output_mint
            + struct.pack("<2Q", 4, 5) + b"\x00"
        )
        row = subject.decode_event(payload)
        self.assertEqual(100, row.input_amount)
        self.assertEqual(80, row.output_amount)
        self.assertTrue(row.base_input)

    def test_rejects_invalid_event_bool(self):
        payload = bytearray(
            subject.SWAP_EVENT + bytes(32) + struct.pack("<6Q", 1, 1, 1, 1, 0, 0)
            + b"\x02" + bytes(64) + struct.pack("<2Q", 0, 0) + b"\x00"
        )
        with self.assertRaises(subject.RaydiumCpmmExactError):
            subject.decode_event(bytes(payload))

    def test_program_and_discriminators_are_pinned(self):
        self.assertEqual(
            subject.PROGRAM_ID,
            "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
        )
        self.assertEqual(8, len(subject.SWAP_BASE_INPUT))
        self.assertEqual(8, len(subject.SWAP_BASE_OUTPUT))

    def test_same_named_foreign_swap_event_is_ignored(self):
        payload = subject.SWAP_EVENT + bytes(162)
        logs = [
            "Program Foreign1111111111111111111111111111111 invoke [1]",
            "Program data: " + base64.b64encode(payload).decode(),
            "Program Foreign1111111111111111111111111111111 success",
        ]
        self.assertEqual([], subject.decode_events(logs))


if __name__ == "__main__":
    unittest.main()
