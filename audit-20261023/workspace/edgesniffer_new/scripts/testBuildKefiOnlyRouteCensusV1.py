#!/usr/bin/env python3
"""Regression tests for the KEFI-only route census."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buildKefiOnlyRouteCensusV1 import build_census, load_jsonl


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "research/kefi-13-arm-program-v1/prospective-multivenue-20261019T041500Z/raw-transactions-immutable-20261019T150904Z.jsonl"


class KefiOnlyRouteCensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RAW.exists():
            raise unittest.SkipTest(f"missing sealed KEFI raw source: {RAW}")
        cls.census, cls.events = build_census(load_jsonl(RAW))
        cls.by_route = {row["route"]: row for row in cls.census["routes"]}

    def test_legacy_labels_are_not_route_truth(self) -> None:
        self.assertTrue(self.census["legacy_candidate_labels_ignored"])
        self.assertFalse(self.census["strategy_selection_performed"])

    def test_direct_pumpswap_counts(self) -> None:
        row = self.by_route["PUMPSWAP_DIRECT"]
        self.assertEqual(row["genuine_buys"], 938)
        self.assertEqual(row["genuine_sells"], 931)
        self.assertTrue(row["available_pre_entry_state"])
        self.assertTrue(row["liquidity_and_fee_fields"])

    def test_cpi_pumpswap_counts(self) -> None:
        row = self.by_route["PUMPSWAP_CPI"]
        self.assertEqual(row["genuine_buys"], 265)
        self.assertEqual(row["genuine_sells"], 256)
        self.assertTrue(row["available_pre_entry_state"])

    def test_router_route_is_separate(self) -> None:
        row = self.by_route["ROUTER_JUPITER_V6_PUMPSWAP_CPI"]
        self.assertEqual(row["genuine_buys"], 0)
        self.assertEqual(row["genuine_sells"], 9)

    def test_non_trade_pump_rows_are_not_admitted(self) -> None:
        row = self.by_route["PUMP_BONDING_CURVE"]
        self.assertEqual(row["genuine_exact_events"], 0)
        self.assertGreater(row["unresolved_or_incomplete_rows"], 0)


if __name__ == "__main__":
    unittest.main()
