import unittest

from materialize2CQEventDrivenIndependentWindowsV1 import build_features, core_gate, flow, quote_sol


class EventDrivenIndependentWindowTests(unittest.TestCase):
    def row(self, ts, side, quote, wallet, price):
        return {
            "local_receive_ts_ms": ts,
            "side": side,
            "quote_lamports": int(quote * 1_000_000_000),
            "wallet": wallet,
            "canonical_state": {"post_state": {
                "virtual_sol": int(price * 1_000_000_000),
                "virtual_token": 1_000_000_000_000,
                "real_sol": 10_000_000_000,
                "real_token": 1_000_000_000_000,
            }},
        }

    def test_core_gate_uses_pre_signal_burst(self):
        rows = [
            self.row(9_800, "buy", 0.6, "a", 1.00),
            self.row(9_900, "buy", 0.7, "b", 1.10),
            self.row(10_000, "buy", 0.8, "c", 1.20),
        ]
        features = build_features(rows, rows[-1])
        self.assertGreaterEqual(features["buy_count_250ms"], 2)
        self.assertGreater(features["net_flow_sol_250ms"], 0)
        self.assertTrue(core_gate(features))

    def test_single_buy_does_not_pass(self):
        rows = [self.row(10_000, "buy", 1.0, "a", 1.0)]
        self.assertFalse(core_gate(build_features(rows, rows[-1])))

    def test_sell_quote_fallback_is_used_and_unknown_is_not_zero(self):
        sell = self.row(10_000, "sell", 1.0, "seller", 1.0)
        sell.pop("quote_lamports")
        sell["sell_quote_lamports"] = 277_000_000
        self.assertAlmostEqual(quote_sol(sell), 0.277, places=9)
        self.assertAlmostEqual(flow(sell), -0.277, places=9)

        unknown = self.row(10_000, "sell", 1.0, "seller", 1.0)
        unknown.pop("quote_lamports")
        self.assertIsNone(quote_sol(unknown))
        self.assertIsNone(flow(unknown))
        features = build_features([unknown], unknown)
        self.assertIsNone(features["net_flow_sol_250ms"])
        self.assertEqual(features["unknown_quote_count_250ms"], 1)
        self.assertFalse(core_gate(features))

    def test_signed_flow_counterexample_is_negative_not_positive(self):
        sell = self.row(9_800, "sell", 1.0, "seller", 1.0)
        sell.pop("quote_lamports")
        sell["sell_quote_lamports"] = 277_000_000
        buy_a = self.row(9_900, "buy", 0.1, "a", 1.01)
        buy_b = self.row(10_000, "buy", 0.1, "b", 1.02)
        features = build_features([sell, buy_a, buy_b], buy_b)
        self.assertAlmostEqual(features["net_flow_sol_250ms"], -0.077, places=9)
        self.assertFalse(core_gate(features))


if __name__ == "__main__":
    unittest.main()
