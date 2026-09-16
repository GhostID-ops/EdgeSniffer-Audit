import unittest
from types import SimpleNamespace

from twoCqCausalContributionFixed26V1 import include_market_event


WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"


def row(order, user, is_buy=True):
    return SimpleNamespace(
        arrival_ms=1000 + order,
        slot=10,
        tx_index=order,
        signature=f"sig{order}",
        instruction_index=0,
        event=SimpleNamespace(user=user, is_buy=is_buy),
    )


class ScenarioFilterTest(unittest.TestCase):
    def setUp(self):
        self.before = row(0, "other")
        self.buy = row(1, WALLET)
        self.after_other = row(2, "other")
        self.sell = row(3, WALLET, False)

    def test_a_keeps_complete_future(self):
        self.assertTrue(include_market_event(self.buy, self.buy, "A"))
        self.assertTrue(include_market_event(self.sell, self.buy, "A"))

    def test_b_removes_only_initial_buy(self):
        self.assertFalse(include_market_event(self.buy, self.buy, "B"))
        self.assertTrue(include_market_event(self.sell, self.buy, "B"))
        self.assertTrue(include_market_event(self.after_other, self.buy, "B"))

    def test_c_removes_all_wallet_activity(self):
        self.assertFalse(include_market_event(self.buy, self.buy, "C"))
        self.assertFalse(include_market_event(self.sell, self.buy, "C"))
        self.assertTrue(include_market_event(self.after_other, self.buy, "C"))

    def test_d_starts_strictly_after_buy(self):
        self.assertFalse(include_market_event(self.buy, self.buy, "D"))
        self.assertTrue(include_market_event(self.after_other, self.buy, "D"))

    def test_all_scenarios_exclude_prior_events(self):
        for scenario in ("A", "B", "C", "D"):
            self.assertFalse(include_market_event(self.before, self.buy, scenario))


if __name__ == "__main__":
    unittest.main()
