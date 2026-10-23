#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from exactCurveEconomicsMayhemV2 import validate_mayhem_transition
from exactCurveEconomicsV1 import CurveState, PumpInstruction, SourceObservation, TradeEvent


class MayhemV2Tests(unittest.TestCase):
    def fixture(self):
        event = TradeEvent(
            "HPoF25X6ohpnzxHc811rxG1ui5BoERhRfcmZhPicpump",
            69_135_801,
            2_467_071_716_458,
            True,
            "11111111111111111111111111111111",
            1_788_687_000,
            CurveState(30_069_135_801, 1_070_532_928_283_542, 69_135_801, 790_632_928_283_542),
            "11111111111111111111111111111111",
            95,
            656_791,
            "11111111111111111111111111111111",
            30,
            207_408,
            False,
            "buy_v2",
            True,
            0,
            0,
            0,
            0,
            (),
            "11111111111111111111111111111111",
            69_135_801,
            30_069_135_801,
            69_135_801,
        )
        source = SourceObservation(
            mint=event.mint,
            signature="36QVfn6YByCxv9Ha7CFRuEFnfRJUCQSuRpUjQrHfQ4ttYutUz2oupm6dhNdWieEyv8gzs2h454Akrqqi6bM3edj5",
            side="buy",
            slot=1,
            transaction_index=0,
            instruction_index=4,
            observed_ms=0,
            instruction=PumpInstruction("buy_v2", event.token_amount, 70_700_000, False),
            source_accounts=(),
        )
        return source, event

    def test_finalized_chain_fixture_reproduces_exactly(self):
        source, event = self.fixture()
        result = validate_mayhem_transition(source, event)
        self.assertEqual("EXACT_MAYHEM_TRANSITION_VALIDATED", result["classification"])
        self.assertEqual(event.post_state.virtual_sol, result["postState"]["virtualSol"])

    def test_failed_transaction_mutates_zero_state(self):
        source, event = self.fixture()
        result = validate_mayhem_transition(source, event, transaction_successful=False)
        self.assertEqual("FAILED_ZERO_STATE_MUTATION", result["classification"])

    def test_inconsistent_quote_view_fails_closed(self):
        source, event = self.fixture()
        event = TradeEvent(*event.__dict__.values())
        object.__setattr__(event, "quote_amount", event.quote_amount + 1)
        with self.assertRaisesRegex(ValueError, "quote amount"):
            validate_mayhem_transition(source, event)


if __name__ == "__main__":
    unittest.main()
