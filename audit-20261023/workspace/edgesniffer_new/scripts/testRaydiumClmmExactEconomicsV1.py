#!/usr/bin/env python3
import base64
import hashlib
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import raydiumClmmExactEconomicsV1 as subject


class RaydiumClmmExactEconomicsTest(unittest.TestCase):
    def test_decodes_current_swap_event(self):
        addresses = [bytes([value]) * 32 for value in range(1, 5)]
        payload = (
            subject.SWAP_EVENT + b"".join(addresses)
            + struct.pack("<4Q", 100, 2, 80, 1) + b"\x01"
            + (123).to_bytes(16, "little") + (456).to_bytes(16, "little")
            + struct.pack("<i2Q", -7, 3, 0)
        )
        event = subject.decode_event(payload)
        self.assertEqual(221, len(payload))
        self.assertEqual(100, event.amount_0_raw)
        self.assertEqual(3, event.trade_fee_0_raw)
        self.assertTrue(event.zero_for_one)

    def test_decodes_legacy_event_without_trade_fee_fields(self):
        payload = (
            subject.SWAP_EVENT + bytes(128)
            + struct.pack("<4Q", 1, 0, 2, 0) + b"\x00"
            + (3).to_bytes(16, "little") + (4).to_bytes(16, "little")
            + struct.pack("<i", 5)
        )
        event = subject.decode_event(payload)
        self.assertEqual(205, len(payload))
        self.assertIsNone(event.trade_fee_0_raw)

    def test_ignores_same_event_discriminator_from_other_program(self):
        payload = (
            subject.SWAP_EVENT + bytes(128)
            + struct.pack("<4Q", 1, 0, 2, 0) + b"\x00"
            + (3).to_bytes(16, "little") + (4).to_bytes(16, "little")
            + struct.pack("<i", 5)
        )
        encoded = base64.b64encode(payload).decode()
        logs = [
            "Program Other111111111111111111111111111111111 invoke [1]",
            "Program data: " + encoded,
            "Program Other111111111111111111111111111111111 success",
        ]
        self.assertEqual([], subject.decode_program_events(logs))

    def test_discriminators_are_anchor_canonical(self):
        self.assertEqual(hashlib.sha256(b"global:swap_v2").digest()[:8], subject.SWAP_V2)
        self.assertEqual(hashlib.sha256(b"event:SwapEvent").digest()[:8], subject.SWAP_EVENT)

    def test_instruction_accounts_accept_normalized_string_metas(self):
        keys = ["account-0", "account-1"]
        self.assertEqual(
            ["account-0", "account-1"],
            subject.instruction_accounts({"accounts": ["account-0", "account-1"]}, keys),
        )
        self.assertEqual(
            ["account-1", "account-0"],
            subject.instruction_accounts({"accounts": [1, 0]}, keys),
        )


if __name__ == "__main__":
    unittest.main()
