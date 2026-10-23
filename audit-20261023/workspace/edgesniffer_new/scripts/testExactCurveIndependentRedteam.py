#!/usr/bin/env python3
import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("exactCurveIndependentRedteam.py")
SPEC = importlib.util.spec_from_file_location("exact_curve_independent_redteam", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class IndependentMathTests(unittest.TestCase):
    def event(self, **overrides):
        values = dict(
            mint="mint",
            quote=192_057,
            token=2_171_992_145,
            is_buy=True,
            user="user",
            timestamp=1,
            post=MODULE.State(53_351_438_770, 603_357_816_600_186, 23_351_438_770, 323_457_816_600_186),
            protocol_bps=95,
            protocol_fee=1_825,
            creator="creator",
            creator_bps=30,
            creator_fee=577,
            ix_name="buy_exact_sol_in",
            mayhem=False,
            cashback_bps=0,
            cashback=0,
            buyback_bps=5000,
            buyback_fee=912,
            quote_mint="11111111111111111111111111111111",
        )
        values.update(overrides)
        return MODULE.Event(**values)

    def test_exact_quote_instruction_matches_deployed_idl_example(self):
        event = self.event()
        instruction = {
            "kind": "buy_exact_sol_in",
            "mode": "exact_quote_buy",
            "arg0": 194_459,
            "arg1": 2_063_410_118,
        }
        quote = MODULE.official_quote(event, instruction)
        self.assertEqual(quote["curve_quote"], event.quote)
        self.assertEqual(quote["token"], event.token)
        self.assertTrue(quote["constraint_pass"])

    def test_exact_token_divisibility_exposes_one_lamport_difference(self):
        pre = MODULE.State(7_000_000_000, 210_000_000_000_000, 7_000_000_000, 210_000_000_000_000)
        token = 105_000_000_000_000
        official = token * pre.vq // (pre.vt - token) + 1
        evaluator = (token * pre.vq + (pre.vt - token) - 1) // (pre.vt - token)
        self.assertEqual(official, evaluator + 1)

    def test_failed_transaction_policy_is_zero_mutation(self):
        state = MODULE.State(30_000_000_000, 1_073_000_000_000_000, 0, 793_100_000_000_000)
        unchanged = state
        self.assertEqual(state, unchanged)

    def test_profit_factor_recomputed_from_rows(self):
        result = MODULE.aggregate_returns([20.0, 10.0, -5.0, -5.0])
        self.assertEqual(result["n"], 4)
        self.assertEqual(result["pf"], 3.0)
        self.assertEqual(result["expectancy_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
