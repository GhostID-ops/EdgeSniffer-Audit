#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import twoCqRouteEvidenceAdaptersV1 as subject


def tx(top, inner=()):
    keys = list(dict.fromkeys([*top, *inner]))
    return {
        "transaction": {"message": {"accountKeys": keys, "instructions": [
            {"programIdIndex": keys.index(value), "accounts": [], "data": ""} for value in top
        ]}},
        "meta": {"loadedAddresses": {"writable": [], "readonly": []}, "innerInstructions": [{
            "index": 0,
            "instructions": [
                {"programIdIndex": keys.index(value), "accounts": [], "data": ""} for value in inner
            ],
        }]},
    }


class RouteAdaptersTests(unittest.TestCase):
    def test_exact_wallet_settlement(self):
        row = subject.exact_wallet_settlement({
            "transaction": {"message": {"accountKeys": [subject.WALLET, "ata"]}},
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [1_000_000, 0],
                "postBalances": [900_000, 0],
                "preTokenBalances": [{
                    "accountIndex": 1, "mint": "mint", "owner": subject.WALLET,
                    "uiTokenAmount": {"amount": "0"},
                }],
                "postTokenBalances": [{
                    "accountIndex": 1, "mint": "mint", "owner": subject.WALLET,
                    "uiTokenAmount": {"amount": "123"},
                }],
            },
        }, "mint")
        self.assertEqual(123, row["baseReceivedRaw"])
        self.assertEqual(-100_000, row["walletLamportDeltaRaw"])
        self.assertFalse(row["exactCurveEconomics"])

    def test_router_resolves_underlying_venue(self):
        row = subject.resolve_route(tx([next(iter(subject.ROUTERS))], [subject.PUMPSWAP]))
        self.assertTrue(row["resolved"])
        self.assertEqual("PUMPSWAP", row["resolution"]["venue"])
        self.assertEqual("CPI", row["resolution"]["invocation"])

    def test_multiple_economic_venues_fail_closed(self):
        programs = list(subject.PROGRAMS)[1:3]
        row = subject.resolve_route(tx(programs))
        self.assertFalse(row["resolved"])
        self.assertIsNone(row["resolution"])

    def test_unknown_program_is_retained(self):
        unknown = "Unknown1111111111111111111111111111111111"
        row = subject.resolve_route(tx([unknown]))
        self.assertIn(unknown, row["unknownPrograms"])

    def test_acquired_asset_buy_perspective(self):
        row = subject.acquired_asset_transition({
            "side": "BUY", "baseMint": "selected", "quoteMint": "spent",
            "walletBaseDeltaRaw": 120, "walletQuoteDeltaRaw": -50,
        }, "selected")
        self.assertEqual("selected", row["acquiredMint"])
        self.assertEqual("spent", row["spentMint"])
        self.assertEqual(120, row["acquiredAmountRaw"])
        self.assertEqual(50, row["spentAmountRaw"])

    def test_acquired_asset_sell_perspective(self):
        row = subject.acquired_asset_transition({
            "side": "SELL", "baseMint": "spent", "quoteMint": "selected",
            "walletBaseDeltaRaw": -120, "walletQuoteDeltaRaw": 50,
        }, "selected")
        self.assertEqual("selected", row["acquiredMint"])
        self.assertEqual("spent", row["spentMint"])
        self.assertEqual(50, row["acquiredAmountRaw"])
        self.assertEqual(120, row["spentAmountRaw"])

    def test_acquired_asset_rejects_wrong_mint(self):
        with self.assertRaisesRegex(ValueError, "not the acquired asset"):
            subject.acquired_asset_transition({
                "side": "BUY", "baseMint": "other", "quoteMint": "spent",
                "amountRaw": 10, "userQuoteAmountRaw": 5,
            }, "selected")


if __name__ == "__main__":
    unittest.main()
