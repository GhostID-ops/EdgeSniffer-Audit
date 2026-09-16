#!/usr/bin/env python3
import unittest

from recoverTwoCqExactSellEvidenceV1 import PUMP_CURVE, PUMPSWAP, WALLET, fixed_actions, venue, wallet_token_deltas


MINT = "Mint111111111111111111111111111111111111111"


def transaction(signature: str, token_delta: int, native_delta: int, *, user: str = WALLET, mint: str = MINT, program: str = PUMP_CURVE, timestamp: int = 100) -> dict:
    return {
        "signature": signature, "timestamp": timestamp, "slot": timestamp,
        "source": "PUMP_FUN", "type": "SWAP", "fee": 5000,
        "transactionError": None, "instructions": [{"programId": program}],
        "accountData": [
            {"account": WALLET, "nativeBalanceChange": native_delta},
            {"account": "ata", "tokenBalanceChanges": [{"userAccount": user, "mint": mint, "rawTokenAmount": {"tokenAmount": str(token_delta), "decimals": 6}}]},
        ],
    }


class RecoveryTests(unittest.TestCase):
    def test_wallet_and_mint_attribution(self):
        self.assertEqual(wallet_token_deltas(transaction("a", -123, 10), WALLET), {MINT: -123})

    def test_unrelated_wallet_not_attributed(self):
        self.assertEqual(wallet_token_deltas(transaction("a", -123, 10, user="other"), WALLET), {})

    def test_sell_amount_timestamp_and_order(self):
        cohort = {MINT: {"buyUnixMs": 99_000}}
        rows = fixed_actions([transaction("buy", 500, -20, timestamp=100), transaction("sell", -200, 10, timestamp=101)], cohort)[MINT]
        self.assertEqual([(row["kind"], row["tokenDeltaRaw"], row["timestampUnixMs"]) for row in rows], [("BUY_OR_RECEIVE", 500, 100_000), ("SELL", -200, 101_000)])

    def test_duplicate_safe(self):
        tx = transaction("same", -123, 10)
        rows = fixed_actions([tx, tx], {MINT: {"buyUnixMs": 99_000}})[MINT]
        self.assertEqual(len(rows), 1)

    def test_venue(self):
        self.assertEqual(venue(transaction("a", -1, 1)), "PUMP_CURVE_SELL")
        self.assertEqual(venue(transaction("a", -1, 1, program=PUMPSWAP)), "PUMPSWAP_SELL")


if __name__ == "__main__":
    unittest.main()
