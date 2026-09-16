#!/usr/bin/env python3

import unittest

import twoCqTeacherValidationV1 as study


def fact(signature, timestamp, token_delta, quote_delta, kind="PUMP_BONDING_CURVE", successful=True):
    return {
        "signature": signature,
        "timestamp": timestamp,
        "utc": study.utc_iso(timestamp),
        "slot": timestamp,
        "successful": successful,
        "tokenDeltas": {"mint": token_delta},
        "tokenDecimals": {"mint": 6},
        "category": kind,
        "source": "PUMP_FUN",
        "type": "SWAP" if kind.startswith("PUMP_") else "TRANSFER",
        "walletQuoteDeltaLamports": quote_delta,
        "walletNativeDeltaLamports": quote_delta,
        "wsolDeltaLamports": 0,
        "feeLamports": 5000,
        "explicitNativeSentLamports": 0,
        "nativeCounterparties": [],
    }


class TeacherValidationTests(unittest.TestCase):
    def test_partial_sells_form_one_episode(self):
        rows = [
            fact("buy", 100, 1000, -100_000_000),
            fact("sell1", 105, -400, 55_000_000),
            fact("sell2", 110, -600, 70_000_000),
        ]
        actions, unsupported = study.build_actions(rows, 90, 120)
        episodes = study.reconstruct_episodes(actions, 90, 120)
        self.assertEqual(unsupported, [])
        self.assertEqual(len(episodes), 1)
        self.assertTrue(episodes[0]["fullySelfContained"])
        self.assertTrue(episodes[0]["stagedExit"])
        self.assertAlmostEqual(episodes[0]["netPnlSol"], 0.025)

    def test_transfer_in_never_gets_cost_basis(self):
        rows = [
            fact("receive", 100, 1000, 0, "PLAIN_SPL_TRANSFER"),
            fact("sell", 110, -1000, 50_000_000),
        ]
        actions, _ = study.build_actions(rows, 90, 120)
        episode = study.reconstruct_episodes(actions, 90, 120)[0]
        self.assertFalse(episode["fullySelfContained"])
        self.assertEqual(episode["deployedSol"], 0)
        self.assertIn("UNEXPLAINED_TOKEN_TRANSFER", episode["reconciliationReasons"])

    def test_failed_transaction_mutates_zero_inventory(self):
        rows = [
            fact("failed", 100, 1000, -250_000_000, successful=False),
            fact("buy", 105, 1000, -100_000_000),
            fact("sell", 110, -1000, 110_000_000),
        ]
        actions, _ = study.build_actions(rows, 90, 120)
        self.assertEqual([row["signature"] for row in actions], ["buy", "sell"])

    def test_failed_transaction_facts_keep_only_wallet_paid_fee(self):
        tx = {
            "signature": "failed",
            "timestamp": 100,
            "slot": 100,
            "transactionError": {"InstructionError": [1, {"Custom": 6042}]},
            "fee": 10_000,
            "feePayer": study.WALLET,
            "type": "SWAP",
            "source": "PUMP_FUN",
            "instructions": [{"programId": study.PUMP_CURVE, "innerInstructions": []}],
            "accountData": [
                {
                    "account": study.WALLET,
                    "nativeBalanceChange": -1_000_010_000,
                    "tokenBalanceChanges": [
                        {
                            "mint": "mint",
                            "userAccount": study.WALLET,
                            "rawTokenAmount": {"tokenAmount": "1000", "decimals": 6},
                        }
                    ],
                }
            ],
            "nativeTransfers": [],
            "tokenTransfers": [],
        }
        result = study.transaction_facts(tx)
        self.assertEqual(result["tokenDeltas"], {})
        self.assertEqual(result["walletQuoteDeltaLamports"], -10_000)
        self.assertEqual(result["attemptedRolledBackDeltas"]["tokenDeltas"], {"mint": 1000})

    def test_activity_fees_only_include_wallet_paid_fees(self):
        paid = fact("paid", 100, 0, 0)
        paid.update({"chainFailed": True, "evidenceAvailable": True, "feePayer": study.WALLET})
        sponsored = fact("sponsored", 101, 0, 0)
        sponsored.update({"chainFailed": True, "evidenceAvailable": True, "feePayer": "someone-else"})
        inventory = study.activity_inventory([paid, sponsored], 90, 120)
        self.assertEqual(len(inventory), 1)
        self.assertAlmostEqual(inventory[0]["feesSol"], 0.000005)

    def test_failed_fee_drag_is_separate_period_overhead(self):
        result = study.apply_failed_fee_drag({"episodes": 2, "deployedSol": 1.0, "netPnlSol": 0.2}, 0.05)
        self.assertAlmostEqual(result["netPnlAfterFailedFeesSol"], 0.15)
        self.assertAlmostEqual(result["expectancyAfterFailedFeesPerEpisodeSol"], 0.075)

    def test_multi_mint_transaction_fails_closed(self):
        row = fact("multi", 100, 1, -100)
        row["tokenDeltas"] = {"mint-a": 1, "mint-b": 2}
        actions, unsupported = study.build_actions([row], 90, 120)
        self.assertEqual(actions, [])
        self.assertEqual(unsupported[0]["reason"], "MULTI_MINT_WALLET_DELTA")


if __name__ == "__main__":
    unittest.main()
