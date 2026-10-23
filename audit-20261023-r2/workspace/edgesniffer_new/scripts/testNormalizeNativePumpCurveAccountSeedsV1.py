import base64
import json
import tempfile
import unittest
from pathlib import Path

import normalizeNativePumpCurveAccountSeedsV1 as normalizer


class NativeSeedTests(unittest.TestCase):
    def test_decodes_pump_curve_account_bytes(self):
        data = bytearray(81)
        data[8:16] = (1_073_000_000_000_000).to_bytes(8, "little")
        data[16:24] = (30_000_000_000).to_bytes(8, "little")
        data[24:32] = (793_100_000_000_000).to_bytes(8, "little")
        data[32:40] = (0).to_bytes(8, "little")
        seed = normalizer.extract({"accountPubkey": "curve", "data": base64.b64encode(data).decode(), "slot": 7,
                                   "nativeReceiveUnixNs": 1_000_000_123_456,
                                   "nativeReceiveMonotonicNs": 456,
                                   "nativeReceiveSource": "fixture",
                                   "accountTxnSignature": "sig",
                                   "writeVersion": 9})
        self.assertEqual(seed["virtualSolReserves"], 30_000_000_000)
        self.assertEqual(seed["virtualTokenReserves"], 1_073_000_000_000_000)
        self.assertEqual(seed["realTokenReserves"], 793_100_000_000_000)
        self.assertEqual(seed["realSolReserves"], 0)
        self.assertEqual(seed["source"], "native-account-state")
        self.assertEqual(seed["observedAtMs"], 1_000_000)
        self.assertEqual(seed["native_receive_unix_ns"], 1_000_000_123_456)
        self.assertEqual(seed["native_receive_monotonic_ns"], 456)
        self.assertEqual(seed["account_txn_signature"], "sig")

    def test_rejects_non_pump_total_supply_state(self):
        self.assertIsNone(normalizer.extract({"bondingCurve": "other", "virtualSolReserves": 30,
                                              "virtualTokenReserves": 100, "tokenTotalSupply": 99,
                                              "observedAtMs": 10}))

    def test_rejects_completed_curve_state(self):
        self.assertIsNone(normalizer.extract({"bondingCurve": "done", "virtualSolReserves": 30,
                                              "virtualTokenReserves": 100, "tokenTotalSupply": 1_000_000_000_000_000,
                                              "complete": True, "observedAtMs": 10}))

    def test_keeps_latest_seed_per_curve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / "in.jsonl", root / "out.jsonl"
            rows = [{"bondingCurve": "curve", "virtualSolReserves": 30, "virtualTokenReserves": 100, "observedAtMs": 10},
                    {"bondingCurve": "curve", "virtualSolReserves": 31, "virtualTokenReserves": 99, "observedAtMs": 20}]
            source.write_text("".join(json.dumps(row) + "\n" for row in rows))
            stats = normalizer.run(source, output)
            self.assertEqual(stats["accepted"], 2)
            self.assertEqual(len(output.read_text().splitlines()), 2)
            self.assertIn('"virtualSolReserves": 31', output.read_text())


if __name__ == "__main__":
    unittest.main()
