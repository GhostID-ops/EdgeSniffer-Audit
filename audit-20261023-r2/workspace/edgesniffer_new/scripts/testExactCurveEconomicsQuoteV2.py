#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from exactCurveEconomicsQuoteV2 import QuoteTradeEvent, validate_quote_transition
from exactCurveEconomicsV1 import (
    CurveState,
    ExactCurveError,
    Fees,
    PumpInstruction,
    fee_ceil,
    quote_exact_quote_in_buy,
)


class QuoteV2Tests(unittest.TestCase):
    def fixture(self):
        pre = CurveState(4_000_000_000, 1_100_000_000_000, 1_000_000_000, 800_000_000_000)
        fees = Fees(95, 30)
        gross = 10_000_000
        quote = quote_exact_quote_in_buy(pre, gross, fees)
        event = QuoteTradeEvent(
            mint="mint",
            token_amount=quote.token_out,
            is_buy=True,
            user="user",
            timestamp=1,
            virtual_token_reserves=quote.post_state.virtual_token,
            real_token_reserves=quote.post_state.real_token,
            fee_recipient="fee",
            fee_basis_points=fees.protocol_bps,
            fee=fee_ceil(quote.curve_quote_in, fees.protocol_bps),
            creator="creator",
            creator_fee_basis_points=fees.creator_bps,
            creator_fee=fee_ceil(quote.curve_quote_in, fees.creator_bps),
            ix_name="buy_exact_quote_in_v2",
            mayhem_mode=False,
            cashback_fee_basis_points=0,
            cashback=0,
            buyback_fee_basis_points=0,
            buyback_fee=0,
            quote_mint="USDC",
            quote_amount=quote.curve_quote_in,
            virtual_quote_reserves=quote.post_state.virtual_sol,
            real_quote_reserves=quote.post_state.real_sol,
        )
        instruction = PumpInstruction("buy_exact_quote_in_v2", gross, quote.token_out, True)
        return instruction, event

    def test_quote_native_transition_reproduces(self):
        instruction, event = self.fixture()
        result = validate_quote_transition(instruction, event)
        self.assertEqual("EXACT_QUOTE_NATIVE_TRANSITION_VALIDATED", result["classification"])
        self.assertIn("BLOCKED", result["solNormalization"])

    def test_failed_transaction_is_zero_state(self):
        instruction, event = self.fixture()
        result = validate_quote_transition(instruction, event, transaction_successful=False)
        self.assertEqual("FAILED_ZERO_STATE_MUTATION", result["classification"])

    def test_sol_quote_is_not_admitted(self):
        instruction, event = self.fixture()
        object.__setattr__(event, "quote_mint", "11111111111111111111111111111111")
        with self.assertRaises(ExactCurveError):
            validate_quote_transition(instruction, event)


if __name__ == "__main__":
    unittest.main()
