import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrichNativePumpCurveStateV1 as bridge


class NativePumpStateBridgeTests(unittest.TestCase):
    def test_token2022_transfer_fee_config_is_decoded(self):
        payload = bytearray(108)
        payload[72:80] = (7).to_bytes(8, "little")
        payload[80:88] = (12345).to_bytes(8, "little")
        payload[88:90] = (250).to_bytes(2, "little")
        payload[90:98] = (8).to_bytes(8, "little")
        payload[98:106] = (23456).to_bytes(8, "little")
        payload[106:108] = (300).to_bytes(2, "little")
        raw = bytes(82) + (1).to_bytes(2, "little") + (108).to_bytes(2, "little") + payload
        state = bridge.decode_token2022_mint_state(base64.b64encode(raw).decode())
        self.assertTrue(state["complete"])
        self.assertTrue(state["transferFeeConfigPresent"])
        self.assertEqual(state["transferFeeConfig"]["olderTransferFee"]["transferFeeBasisPoints"], 250)
        self.assertEqual(state["transferFeeConfig"]["newerTransferFee"]["maximumFeeRaw"], 23456)

    def test_token2022_balance_deltas_are_retained(self):
        tx = {"meta": {
            "preTokenBalances": [{"accountIndex": 2, "mint": "m", "programId": bridge.TOKEN_2022,
                                   "owner": "owner", "uiTokenAmount": {"amount": "100"}}],
            "postTokenBalances": [{"accountIndex": 2, "mint": "m", "programId": bridge.TOKEN_2022,
                                    "owner": "owner", "uiTokenAmount": {"amount": "95"}}],
        }}
        self.assertEqual(bridge.token2022_balance_deltas(tx, "m")[0]["deltaRaw"], -5)

    def test_missing_exact_event_fails_closed(self):
        row = {"mint": "mint", "side": "buy", "local_receive_ts_ms": 1000}
        with self.assertRaises(RuntimeError):
            bridge.enrich_row(row, {"slot": 1, "blockTime": 1, "meta": {"logMessages": []}}, 2000)

    def test_output_contract_rejects_missing_native_clock(self):
        self.assertIsNone(bridge.timestamp_ms({"mint": "mint"}))


if __name__ == "__main__":
    unittest.main()
