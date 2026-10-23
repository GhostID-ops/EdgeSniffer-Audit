#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auditNativeStateLineageV1 import audit


class NativeStateLineageTests(unittest.TestCase):
    def test_causal_lineage_passes_only_for_prior_same_slot_seed(self):
        rows = []
        for i in range(20):
            rows.append({
                "signature": f"tx-{i}",
                "slot": 100,
                "same_source_slot": 100,
                "same_source_receive_unix_ns": 1_000 + i * 100,
                "native_state": {
                    "seed_slot": 100,
                    "seed_native_receive_unix_ns": 900 + i * 100,
                    "seed_native_receive_source": "yellowstone-account-update-receive",
                    "seed_account_txn_signature": f"seed-{i}",
                    "seed_write_version": i + 1,
                },
            })
        result = audit(rows)
        self.assertEqual(result["status"], "PASS_CAUSAL_STATE_LINEAGE")
        self.assertEqual(result["causally_lineaged_rows"], 20)

    def test_future_seed_is_rejected(self):
        row = {
            "signature": "tx",
            "slot": 100,
            "same_source_slot": 100,
            "same_source_receive_unix_ns": 1_000,
            "native_state": {
                "seed_slot": 100,
                "seed_native_receive_unix_ns": 1_001,
                "seed_native_receive_source": "yellowstone-account-update-receive",
                "seed_account_txn_signature": "seed",
                "seed_write_version": 1,
            },
        }
        result = audit([row], min_rows=1)
        self.assertEqual(result["status"], "BLOCKED_CAUSAL_STATE_LINEAGE")
        self.assertEqual(result["invalid_rows"], 1)


if __name__ == "__main__":
    unittest.main()
