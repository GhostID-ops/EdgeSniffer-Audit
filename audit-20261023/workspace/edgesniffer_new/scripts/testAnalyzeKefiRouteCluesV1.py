#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyzeKefiRouteCluesV1 as subject


def make_row(route: str, side: str, timestamp: int, *, pool: str = "pool") -> dict:
    return {
        "route": route,
        "side": side,
        "block_time": timestamp,
        "event_index": 0,
        "pool": pool,
        "mint": "mint",
        "user_quote_amount_raw": 500_000_000,
        "pre_state": {"quote": 100_000_000_000, "virtual_quote": 50_000_000_000},
        "fees": {"lp_bps": 20, "protocol_bps": 5, "creator_bps": 95},
    }


class KefiRouteClueTests(unittest.TestCase):
    def test_report_is_exploratory_and_does_not_freeze(self) -> None:
        report = subject.build_report([make_row("PUMPSWAP_CPI", "BUY", 1000)], Path("missing.jsonl"))
        self.assertTrue(report["exploratory_only"])
        self.assertTrue(report["outcome_blind"])
        self.assertFalse(report["policy_selection_performed"])
        self.assertFalse(report["policy_frozen"])

    def test_describes_holding_time_and_liquidity(self) -> None:
        report = subject.build_report([
            make_row("PUMPSWAP_DIRECT", "BUY", 1000),
            make_row("PUMPSWAP_DIRECT", "SELL", 1010),
        ], Path("missing.jsonl"))
        stats = report["route_stats"]["PUMPSWAP_DIRECT"]
        self.assertEqual(stats["buy_to_first_sell"]["buys_with_later_sell"], 1)
        self.assertEqual(stats["buy_to_first_sell"]["holding_seconds"]["median"], 10)
        self.assertEqual(stats["buy_pre_pool_quote_liquidity_sol"]["median"], 100)


if __name__ == "__main__":
    unittest.main()
