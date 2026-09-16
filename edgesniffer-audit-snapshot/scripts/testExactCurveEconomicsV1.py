#!/usr/bin/env python3
import base64
import unittest
from types import SimpleNamespace

from exactCurveEconomicsV1 import (
    BUY,
    BUY_EXACT_QUOTE_IN_V2,
    BUY_EXACT_SOL_IN,
    BUY_V2,
    SELL,
    SELL_V2,
    CurveState,
    ExactCurveError,
    Fees,
    PumpInstruction,
    SourceObservation,
    apply_instruction,
    decode_pump_instruction,
    decode_trade_event,
    infer_fee_config_address,
    order_successful_rows_by_reserve_continuity,
    quote_exact_quote_in_buy,
    quote_exact_token_buy,
    quote_exact_token_sell,
    validate_actual_trade_transition,
)


BUY_EVENT = (
    "vdt/007mYe7sSazlSNI6BYf5AfTY0aqd8LRRuMjH+GNm5A+WCBt8nz60lgAAAAAAVu98RkwAAAABG1LAJKhrxkavTHBcnJsGDPCOr8j2io00mIfVfqiVEJS9mpxqAAAAAMiYHkEHAAAAhBpbyaOrAwDI7PpEAAAAAISCSH0SrQIAjRgaDISfqTem80re0wge+VcAqssMm7PZCaS5FHUnpOtfAAAAAAAAAIRuAQAAAAAAA81XFipLQbGWKULe9grQP60OmtTt0gJMthEjAvEuXEoeAAAAAAAAAL5zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEgAAAGJ1eV9leGFjdF9xdW90ZV9pbgAAAAAAAAAAAAAAAAAAAAAAiBMAAAAAAABCtwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD60lgAAAAAAyJgeQQcAAADI7PpEAAAAAA=="
)
SELL_EVENT = (
    "vdt/007mYe7sSazlSNI6BYf5AfTY0aqd8LRRuMjH+GNm5A+WCBt8n4S7lQAAAAAAVu98RkwAAAAAG1LAJKhrxkavTHBcnJsGDPCOr8j2io00mIfVfqiVEJQ2m5xqAAAAAH2qiToHAAAAGEREVvuuAwB9/mU+AAAAABisMQpqsAIArRHmpPwpRKT6glG++BVCbhv7KMa2ZGZ3YHxq2fVmpkZfAAAAAAAAACdsAQAAAAAAA81XFipLQbGWKULe9grQP60OmtTt0gJMthEjAvEuXEoeAAAAAAAAAP9yAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAHNlbGwAAAAAAAAAAAAAAAAAAAAAAIgTAAAAAAAAE7YAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACEu5UAAAAAAH2qiToHAAAAff5lPgAAAAA="
)
BUY_V2_EVENT = (
    "vdt/007mYe7sSazlSNI6BYf5AfTY0aqd8LRRuMjH+GNm5A+WCBt8n+9YlwAAAAAAi3VjjUwAAAABtgstbJ7tHjfIdfQDA+wX1mSW1GdqvFux7UcQMnhPR5G+mpxqAAAAALfxtUEHAAAA+aT3O1erAwC3RZJFAAAAAPkM5e/FrAIASsL40N1cvJfjKJwZfLUGKlTz2Va5zm5RFfllZ6pcs+ZfAAAAAAAAABRwAQAAAAAAA81XFipLQbGWKULe9grQP60OmtTt0gJMthEjAvEuXEoeAAAAAAAAAD10AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAwAAAGJ1eQAAAAAAAAAAAAAAAAAAAAAAiBMAAAAAAAAKuAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAO9YlwAAAAAAt/G1QQcAAAC3RZJFAAAAAA=="
)


class ExactCurveMathTest(unittest.TestCase):
    def test_all_frozen_trade_discriminators_decode(self):
        cases = (
            (BUY, "buy", False),
            (BUY_EXACT_SOL_IN, "buy_exact_sol_in", True),
            (BUY_V2, "buy_v2", False),
            (BUY_EXACT_QUOTE_IN_V2, "buy_exact_quote_in_v2", True),
            (SELL, "sell", False),
            (SELL_V2, "sell_v2", False),
        )
        for discriminator, kind, exact_quote_in in cases:
            instruction = decode_pump_instruction(
                discriminator + (11).to_bytes(8, "little") + (22).to_bytes(8, "little")
            )
            self.assertEqual(instruction.kind, kind)
            self.assertEqual(instruction.arg0, 11)
            self.assertEqual(instruction.arg1, 22)
            self.assertEqual(instruction.exact_quote_in, exact_quote_in)

    def test_fee_config_uses_preferred_instruction_layout(self):
        legacy_accounts = tuple(f"legacy-{index}" for index in range(18))
        v2_accounts = tuple(f"v2-{index}" for index in range(27))
        observations = [
            SourceObservation(
                mint="mint",
                signature="later-v2",
                side="buy",
                slot=2,
                transaction_index=1,
                instruction_index=0,
                observed_ms=2,
                instruction=PumpInstruction("buy_v2", 1, 2, False),
                source_accounts=v2_accounts,
                timing_source="test",
            ),
            SourceObservation(
                mint="mint",
                signature="live-buy",
                side="buy",
                slot=1,
                transaction_index=1,
                instruction_index=0,
                observed_ms=1,
                instruction=PumpInstruction("buy_exact_sol_in", 1, 2, True),
                source_accounts=legacy_accounts,
                timing_source="test",
            ),
        ]

        self.assertEqual(
            infer_fee_config_address(observations, "live-buy"), legacy_accounts[14]
        )

    def test_trade_event_and_exact_input_buy_match_chain(self):
        event = decode_trade_event(base64.b64decode(BUY_EVENT))
        self.assertTrue(event.is_buy)
        self.assertEqual(event.sol_amount, 9_876_542)
        self.assertEqual(event.token_amount, 327_600_107_350)
        self.assertEqual(event.fee_basis_points, 95)
        self.assertEqual(event.creator_fee_basis_points, 30)
        quote = quote_exact_quote_in_buy(event.pre_state, 10_000_000, Fees(95, 30))
        self.assertEqual(quote.curve_quote_in, event.sol_amount)
        self.assertEqual(quote.token_out, event.token_amount)
        self.assertEqual(quote.protocol_fee, event.fee)
        self.assertEqual(quote.creator_fee, event.creator_fee)
        self.assertEqual(quote.post_state, event.post_state)
        source = SourceObservation(
            mint=event.mint,
            signature="chain-vector",
            side="buy",
            slot=1,
            transaction_index=1,
            instruction_index=0,
            observed_ms=1,
            instruction=PumpInstruction(
                "buy_exact_quote_in_v2", 10_000_000, event.token_amount, True
            ),
            source_accounts=(),
            timing_source="test",
        )
        validated = validate_actual_trade_transition(source, event, Fees(95, 30))
        self.assertEqual(validated.post_state, event.post_state)

    def test_holder_rewards_trailing_fields_are_backward_compatible(self):
        legacy_payload = base64.b64decode(BUY_EVENT)
        legacy = decode_trade_event(legacy_payload)
        current = decode_trade_event(
            legacy_payload
            + (300).to_bytes(8, "little")
            + (16_410_343_097).to_bytes(8, "little")
        )
        self.assertEqual(0, legacy.holder_rewards_basis_points)
        self.assertEqual(0, legacy.holder_rewards)
        self.assertEqual(300, current.holder_rewards_basis_points)
        self.assertEqual(16_410_343_097, current.holder_rewards)
        self.assertEqual(legacy.immediate_quote_amount, current.immediate_quote_amount)

    def test_current_v2_sol_event_uses_quote_amount_and_reserves(self):
        legacy_payload = bytearray(base64.b64decode(BUY_EVENT))
        legacy = decode_trade_event(bytes(legacy_payload))
        legacy_payload[40:48] = bytes(8)  # legacy sol_amount
        legacy_payload[97:105] = bytes(8)  # legacy virtual_sol_reserves
        legacy_payload[113:121] = bytes(8)  # legacy real_sol_reserves
        current = decode_trade_event(
            bytes(legacy_payload)
            + (300).to_bytes(8, "little")
            + (123).to_bytes(8, "little")
        )
        self.assertEqual(legacy.sol_amount, current.sol_amount)
        self.assertEqual(legacy.post_state, current.post_state)
        self.assertEqual(legacy.pre_state, current.pre_state)

    def test_unknown_trade_event_tail_remains_fail_closed(self):
        with self.assertRaisesRegex(ExactCurveError, "TradeEvent schema mismatch"):
            decode_trade_event(base64.b64decode(BUY_EVENT) + bytes(8))

    def test_exact_token_buy_reverse_rounding(self):
        event = decode_trade_event(base64.b64decode(BUY_V2_EVENT))
        quote = quote_exact_token_buy(
            event.pre_state, event.token_amount, 12_500_000, Fees(95, 30)
        )
        self.assertEqual(quote.curve_quote_in, event.sol_amount)
        self.assertEqual(quote.gross_quote_in, event.immediate_quote_amount)
        self.assertEqual(quote.post_state, event.post_state)

    def test_sell_event_matches_chain_and_fees(self):
        event = decode_trade_event(base64.b64decode(SELL_EVENT))
        self.assertFalse(event.is_buy)
        quote = quote_exact_token_sell(
            event.pre_state, event.token_amount, 0, Fees(95, 30)
        )
        self.assertEqual(quote.gross_quote_out, event.sol_amount)
        self.assertEqual(quote.protocol_fee, event.fee)
        self.assertEqual(quote.creator_fee, event.creator_fee)
        self.assertEqual(quote.immediate_quote_out, 9_690_206)
        self.assertEqual(quote.post_state, event.post_state)

    def test_failed_constraints_do_not_offer_a_mutation(self):
        event = decode_trade_event(base64.b64decode(BUY_EVENT))
        payload = bytes([194, 171, 28, 70, 104, 77, 91, 47])
        payload += (10_000_000).to_bytes(8, "little")
        payload += (event.token_amount + 1).to_bytes(8, "little")
        instruction = decode_pump_instruction(payload)
        with self.assertRaises(ExactCurveError):
            apply_instruction(event.pre_state, instruction, Fees(95, 30))

    def test_sell_minout_is_after_fees(self):
        event = decode_trade_event(base64.b64decode(SELL_EVENT))
        with self.assertRaises(ExactCurveError):
            quote_exact_token_sell(
                event.pre_state,
                event.token_amount,
                event.immediate_quote_amount + 1,
                Fees(95, 30),
            )

    def test_cashback_and_buyback_are_allocations_not_extra_curve_fees(self):
        event = decode_trade_event(base64.b64decode(SELL_EVENT))
        quote = quote_exact_token_sell(
            event.pre_state,
            event.token_amount,
            0,
            Fees(95, 30),
            cashback_bps=30,
            buyback_share_bps=1000,
        )
        self.assertEqual(quote.immediate_quote_out, 9_690_206)
        self.assertEqual(quote.cashback_accrual, quote.creator_fee)
        self.assertEqual(quote.buyback_fee_allocation, quote.protocol_fee // 10)

    def test_reserve_order_includes_predecessors_before_live_buy(self):
        states = [
            CurveState(100 + index, 1000 - index, 50 + index, 500 - index)
            for index in range(4)
        ]

        def row(signature, before, after, slot):
            return {
                "source": SourceObservation(
                    mint="mint",
                    signature=signature,
                    side="buy",
                    slot=slot,
                    transaction_index=slot,
                    instruction_index=0,
                    observed_ms=slot,
                    instruction=PumpInstruction("buy", 1, 2, False),
                    source_accounts=(),
                    timing_source="test",
                ),
                "event": SimpleNamespace(pre_state=before, post_state=after),
            }

        ordered = order_successful_rows_by_reserve_continuity(
            [
                row("terminal", states[2], states[3], 3),
                row("anchor", states[1], states[2], 2),
                row("predecessor", states[0], states[1], 1),
            ],
            anchor_signature="anchor",
            terminal_signature="terminal",
        )
        self.assertEqual(
            [item["source"].signature for item in ordered],
            ["predecessor", "anchor", "terminal"],
        )


if __name__ == "__main__":
    unittest.main()
