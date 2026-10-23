#!/usr/bin/env python3
import base64
import dataclasses
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pumpSwapExactEconomicsV1 as subject
from exactCurveEconomicsV1 import base58_encode


def pubkey(seed: int) -> bytes:
    return bytes([seed]) * 32


def string(value: str) -> bytes:
    raw = value.encode()
    return struct.pack("<I", len(raw)) + raw


def buy_event() -> bytes:
    fixed = struct.pack(
        "<14Q", 1, 100, 200, 1_100, 900, 900, 1_100, 102, 20, 1, 5, 1, 101, 100
    )
    return (
        subject.BUY_EVENT + fixed + b"".join(pubkey(i) for i in range(1, 8))
        + struct.pack("<2Q", 0, 0)
        + bytes([0]) + struct.pack("<3QqQ", 0, 0, 0, 0, 100)
        + string("buy") + struct.pack("<4Q", 0, 0, 0, 0)
        + int(0).to_bytes(16, "little", signed=True) + bytes([0]) + struct.pack("<Q", 1_000)
    )


class PumpSwapExactTests(unittest.TestCase):
    def test_current_buy_event_decodes_exact_reserves(self):
        event = subject.decode_event(buy_event())
        self.assertEqual("BUY", event.side)
        self.assertEqual(1_000, event.pre_state.base)
        self.assertEqual(999, event.pre_state.quote)
        self.assertEqual(900, event.post_state.base)
        self.assertEqual(1_100, event.post_state.quote)

    def test_log_decoder_ignores_unrelated_data(self):
        lines = ["Program data: " + base64.b64encode(b"other").decode(),
                 "Program data: " + base64.b64encode(buy_event()).decode()]
        self.assertEqual(1, len(subject.decode_events(lines)))

    def test_direct_instruction_uses_canonical_account_positions(self):
        keys = [base58_encode(pubkey(i)) for i in range(1, 6)] + [subject.PROGRAM_ID]
        data = subject.BUY + struct.pack("<2Q", 100, 200)
        tx = {"transaction": {"message": {"accountKeys": keys, "instructions": [{
            "programIdIndex": 5, "accounts": [0, 1, 2, 3, 4],
            "data": base58_encode(data),
        }]}}, "meta": {"loadedAddresses": {"writable": [], "readonly": []}}}
        decoded = subject.decode_instructions(tx)
        self.assertEqual(1, len(decoded))
        self.assertEqual("EXACT_BASE", decoded[0].amount_mode)
        self.assertEqual(keys[3], decoded[0].base_mint)
        self.assertEqual(keys[4], decoded[0].quote_mint)

    def test_truncated_current_event_fails_closed(self):
        with self.assertRaises(Exception):
            subject.decode_event(buy_event()[:-1])

    def test_zero_holder_rewards_extension_is_inert(self):
        event = subject.decode_event(buy_event() + bytes(16))
        self.assertEqual("BUY", event.side)
        self.assertEqual(0, event.holder_rewards_basis_points)
        self.assertEqual(0, event.holder_rewards)

    def test_nonzero_holder_rewards_extension_decodes(self):
        event = subject.decode_event(
            buy_event() + struct.pack("<2Q", 300, 16_410_343_097)
        )
        self.assertEqual(300, event.holder_rewards_basis_points)
        self.assertEqual(16_410_343_097, event.holder_rewards)

    def test_current_creator_fee_unclaimed_extension_decodes(self):
        event = subject.decode_event(
            buy_event() + struct.pack("<3Q", 300, 16_410_343_097, 8_925_136)
        )
        self.assertEqual(300, event.holder_rewards_basis_points)
        self.assertEqual(16_410_343_097, event.holder_rewards)
        self.assertEqual(8_925_136, event.creator_fee_unclaimed)

    def test_unknown_extension_length_fails_closed(self):
        with self.assertRaises(subject.PumpSwapExactError):
            subject.decode_event(buy_event() + bytes(8))

    def test_sell_protocol_fee_uses_pre_fee_curve_quote(self):
        event = dataclasses.replace(
            subject.decode_event(buy_event()),
            side="SELL",
            curve_quote_amount=80_740_950,
            pool_quote_delta=80_724_801,
            user_quote_amount=79_731_687,
            lp_fee_basis_points=2,
            lp_fee=16_149,
            protocol_fee_basis_points=93,
            protocol_fee=750_891,
            coin_creator_fee=242_223,
        )
        fee_base = event.curve_quote_amount
        subject.validate_event_fees(event, "EXACT_BASE")
        self.assertEqual(event.lp_fee, (fee_base * event.lp_fee_basis_points + 9_999) // 10_000)
        self.assertEqual(
            event.protocol_fee,
            (fee_base * event.protocol_fee_basis_points + 9_999) // 10_000,
        )
        self.assertNotEqual(
            event.lp_fee,
            (event.pool_quote_delta * event.lp_fee_basis_points + 9_999) // 10_000,
        )
        with self.assertRaises(subject.PumpSwapExactError):
            subject.validate_event_fees(
                dataclasses.replace(event, protocol_fee=750_890), "EXACT_BASE"
            )

    def test_exact_quote_buy_uses_user_quote_fee_base(self):
        event = dataclasses.replace(
            subject.decode_event(buy_event()),
            curve_quote_amount=2_000_000_000,
            user_quote_amount=1_975_308_641,
            lp_fee_basis_points=2,
            lp_fee=395_062,
            protocol_fee_basis_points=93,
            protocol_fee=18_370_371,
        )
        subject.validate_event_fees(event, "EXACT_QUOTE_IN")
        with self.assertRaises(subject.PumpSwapExactError):
            subject.validate_event_fees(event, "EXACT_BASE")
        subject.validate_event_fees(
            dataclasses.replace(event, protocol_fee=18_370_372), "EXACT_QUOTE_IN"
        )
        with self.assertRaises(subject.PumpSwapExactError):
            subject.validate_event_fees(
                dataclasses.replace(event, protocol_fee=18_370_373), "EXACT_QUOTE_IN"
            )


if __name__ == "__main__":
    unittest.main()
