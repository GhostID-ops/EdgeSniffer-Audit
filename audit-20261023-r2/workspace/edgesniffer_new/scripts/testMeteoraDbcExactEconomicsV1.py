#!/usr/bin/env python3
import hashlib
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import meteoraDbcExactEconomicsV1 as subject
from launchLabExactEconomicsV1 import b58encode


ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode_bytes(raw: bytes) -> str:
    value = int.from_bytes(raw, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, 58)
        encoded = ALPHABET[remainder] + encoded
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + (encoded or "")


def event_payload(pool: bytes, config: bytes) -> bytes:
    return (
        subject.EVT_SWAP2 + pool + config + bytes([1, 0])
        + struct.pack("<2Q", 1_000, 900) + bytes([0])
        + struct.pack("<4Q", 1_000, 970, 0, 2_000)
        + (1234).to_bytes(16, "little")
        + struct.pack("<6Q", 20, 10, 0, 50_000, 100_000, 123456)
    )


class MeteoraDbcExactEconomicsTest(unittest.TestCase):
    def test_discriminators_match_published_anchor_names(self):
        self.assertEqual(hashlib.sha256(b"global:swap2").digest()[:8], subject.SWAP2)
        self.assertEqual(hashlib.sha256(b"event:EvtSwap2").digest()[:8], subject.EVT_SWAP2)

    def test_decodes_evt_swap2(self):
        event = subject.decode_event(event_payload(bytes([1]) * 32, bytes([2]) * 32))
        self.assertEqual(1_000, event.included_fee_input_amount_raw)
        self.assertEqual(2_000, event.output_amount_raw)
        self.assertEqual("4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi", event.pool)
        self.assertEqual(1, event.trade_direction)

    def test_validates_current_swap2_and_wallet_settlement(self):
        keys = [
            "authority", "config", "pool", "input", "output", "baseVault",
            "quoteVault", "BASE", "QUOTE", "wallet", "baseProgram",
            "quoteProgram", subject.PROGRAM_ID, "eventAuthority", subject.PROGRAM_ID,
        ]
        pool = bytes([3]) * 32
        config = bytes([4]) * 32
        keys[1], keys[2] = b58encode(config), b58encode(pool)
        instruction = subject.SWAP2 + struct.pack("<2Q", 1_000, 900) + bytes([0])
        event = subject.EVENT_CPI + event_payload(pool, config)
        tx = {
            "transaction": {"message": {
                "accountKeys": keys,
                "instructions": [{
                    "programIdIndex": 12, "accounts": list(range(15)),
                    "data": b58encode_bytes(instruction),
                }],
            }},
            "meta": {
                "err": None,
                "loadedAddresses": {"writable": [], "readonly": []},
                "innerInstructions": [{"index": 0, "instructions": [{
                    "programIdIndex": 12, "accounts": [13],
                    "data": b58encode_bytes(event),
                }]}],
                "preTokenBalances": [
                    {"accountIndex": 3, "mint": "QUOTE", "owner": "wallet", "uiTokenAmount": {"amount": "5000"}},
                    {"accountIndex": 4, "mint": "BASE", "owner": "wallet", "uiTokenAmount": {"amount": "0"}},
                ],
                "postTokenBalances": [
                    {"accountIndex": 3, "mint": "QUOTE", "owner": "wallet", "uiTokenAmount": {"amount": "4000"}},
                    {"accountIndex": 4, "mint": "BASE", "owner": "wallet", "uiTokenAmount": {"amount": "2000"}},
                ],
            },
        }
        rows = subject.validate_transaction(tx)
        self.assertEqual(1, len(rows))
        self.assertEqual("QUOTE", rows[0]["inputMint"])
        selected = subject.selected_wallet_acquisition(tx, "BASE", "wallet")
        self.assertEqual(2_000, selected["acquiredAmountRaw"])
        self.assertEqual(1_000, selected["spentAmountRaw"])

    def test_rejects_transfer_mismatch(self):
        payload = bytearray(event_payload(bytes([1]) * 32, bytes([2]) * 32))
        payload[8 + 64 + 2 + 17:8 + 64 + 2 + 17 + 8] = struct.pack("<Q", 0)
        with self.assertRaises(subject.MeteoraDbcExactError):
            subject.decode_event(bytes(payload))

    def test_shared_account_effects_are_reconciled_in_aggregate(self):
        rows = [
            (0, 100, 200),
            (1, 300, 400),
        ]
        effects = {}
        for index, input_amount, output_amount in rows:
            effects.setdefault(("input", "QUOTE"), []).append((index, -input_amount, "input"))
            effects.setdefault(("output", "BASE"), []).append((index, output_amount, "output"))
        self.assertEqual(-400, sum(row[1] for row in effects[("input", "QUOTE")]))
        self.assertEqual(600, sum(row[1] for row in effects[("output", "BASE")]))


if __name__ == "__main__":
    unittest.main()
