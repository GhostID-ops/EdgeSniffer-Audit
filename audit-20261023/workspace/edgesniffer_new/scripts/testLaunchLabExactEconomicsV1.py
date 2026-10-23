#!/usr/bin/env python3

import base64
import struct
import unittest

from launchLabExactEconomicsV1 import (
    BUY_EXACT_IN,
    PROGRAM_ID,
    SELL_EXACT_IN,
    TRADE_EVENT_DISCRIMINATOR,
    LaunchLabExactError,
    b58encode,
    decode_instruction,
    decode_trade_event,
    validate_constant_product_transition,
)


def event_bytes(*, buy: bool) -> bytes:
    pool = bytes(range(32))
    virtual_base = 1_000_000
    virtual_quote = 100_000
    if buy:
        real_base_before, real_quote_before, amount_in, fees = 100_000, 50_000, 10_000, 100
        amount_out = (virtual_base - real_base_before) * (amount_in - fees) // (
            virtual_quote + real_quote_before + amount_in - fees
        )
        real_base_after = real_base_before + amount_out
        real_quote_after = real_quote_before + amount_in - fees
        direction = 0
    else:
        real_base_before, real_quote_before, amount_in, fees = 200_000, 80_000, 10_000, 100
        gross = (virtual_quote + real_quote_before) * amount_in // (
            virtual_base - real_base_before + amount_in
        )
        amount_out = gross - fees
        real_base_after = real_base_before - amount_in
        real_quote_after = real_quote_before - gross
        direction = 1
    values = (
        800_000,
        virtual_base,
        virtual_quote,
        real_base_before,
        real_quote_before,
        real_base_after,
        real_quote_after,
        amount_in,
        amount_out,
        fees,
        0,
        0,
        0,
    )
    return TRADE_EVENT_DISCRIMINATOR + pool + struct.pack("<13Q", *values) + bytes([direction, 0, 1])


def accounts(pool: str) -> tuple[str, ...]:
    values = [f"account-{index}" for index in range(15)]
    values[4] = pool
    values[9] = "base-mint"
    values[10] = "quote-mint"
    return tuple(values)


class LaunchLabExactEconomicsTest(unittest.TestCase):
    def test_exact_buy_and_sell(self) -> None:
        for buy in (True, False):
            event = decode_trade_event(event_bytes(buy=buy))
            discriminator = BUY_EXACT_IN if buy else SELL_EXACT_IN
            instruction = decode_instruction(
                discriminator + struct.pack("<3Q", event.amount_in, 1, 0),
                accounts(event.pool_state),
            )
            result = validate_constant_product_transition(event, instruction)
            self.assertEqual(result["side"], "BUY" if buy else "SELL")

    def test_rejects_noncanonical_math(self) -> None:
        raw = bytearray(event_bytes(buy=True))
        raw[104] ^= 1
        event = decode_trade_event(bytes(raw))
        instruction = decode_instruction(
            BUY_EXACT_IN + struct.pack("<3Q", event.amount_in, 1, 0),
            accounts(event.pool_state),
        )
        with self.assertRaises(LaunchLabExactError):
            validate_constant_product_transition(event, instruction)

    def test_exact_in_preserves_input_transfer_fee(self) -> None:
        event = decode_trade_event(event_bytes(buy=True))
        instruction = decode_instruction(
            BUY_EXACT_IN + struct.pack("<3Q", event.amount_in + 50, 1, 0),
            accounts(event.pool_state),
        )
        result = validate_constant_product_transition(event, instruction)
        self.assertEqual(result["inputTransferFeeRaw"], 50)

    def test_program_is_pinned(self) -> None:
        self.assertEqual(PROGRAM_ID, "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")
        self.assertTrue(base64.b64encode(TRADE_EVENT_DISCRIMINATOR))


if __name__ == "__main__":
    unittest.main()
