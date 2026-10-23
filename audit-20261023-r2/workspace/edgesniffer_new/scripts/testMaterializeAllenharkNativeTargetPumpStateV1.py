import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("native_target_state", ROOT / "materializeAllenharkNativeTargetPumpStateV1.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class NativeTargetStateTests(unittest.TestCase):
    def test_causal_seed_rejects_evaluated_transaction_post_state(self):
        MODULE._SEED_OBSERVED_INDEX.clear()
        rows = [
            {"bondingCurve": "curve", "observedAtMs": 100, "slot": 7,
             "account_txn_signature": "prior", "virtualSolReserves": 30,
             "virtualTokenReserves": 100},
            {"bondingCurve": "curve", "observedAtMs": 110, "slot": 7,
             "account_txn_signature": "target", "virtualSolReserves": 31,
             "virtualTokenReserves": 99},
        ]
        MODULE._SEED_OBSERVED_INDEX["curve"] = [100, 110]
        seed = MODULE.latest_causal_seed(rows, 110, 7, "target")
        self.assertEqual(seed["account_txn_signature"], "prior")

    def test_strict_lineage_rejects_legacy_seed_without_receive_metadata(self):
        MODULE._SEED_OBSERVED_INDEX.clear()
        rows = [
            {"bondingCurve": "curve", "observedAtMs": 100, "slot": 7,
             "virtualSolReserves": 30, "virtualTokenReserves": 100},
            {"bondingCurve": "curve", "observedAtMs": 101, "slot": 7,
             "native_receive_unix_ns": 101_000_000_000,
             "native_receive_monotonic_ns": 55,
             "native_receive_source": "yellowstone-account-update-receive",
             "account_txn_signature": "prior",
             "write_version": 9,
             "virtualSolReserves": 31, "virtualTokenReserves": 99},
        ]
        MODULE._SEED_OBSERVED_INDEX["curve"] = [100, 101]
        seed = MODULE.latest_causal_seed(rows, 101, 7, "target", True, True)
        self.assertEqual(seed["account_txn_signature"], "prior")
        legacy_only = MODULE.latest_causal_seed(rows[:1], 101, 7, "target", True, True)
        self.assertIsNone(legacy_only)

    def test_native_nanosecond_clock_rejects_later_same_millisecond_seed(self):
        MODULE._SEED_OBSERVED_INDEX.clear()
        rows = [
            {"bondingCurve": "curve", "observedAtMs": 100, "slot": 7,
             "native_receive_unix_ns": 100_000_000_100,
             "account_txn_signature": "before", "virtualSolReserves": 30,
             "virtualTokenReserves": 100},
            {"bondingCurve": "curve", "observedAtMs": 100, "slot": 7,
             "native_receive_unix_ns": 100_000_000_900,
             "account_txn_signature": "after", "virtualSolReserves": 31,
             "virtualTokenReserves": 99},
        ]
        MODULE._SEED_OBSERVED_INDEX["curve"] = [100, 100]
        seed = MODULE.latest_causal_seed(
            rows, 100, 7, "target", True, False, 100_000_000_500
        )
        self.assertEqual(seed["account_txn_signature"], "before")

    def test_exact_quote_buy_requires_complete_causal_seed_and_emits_state(self):
        curve_id = "Curve111111111111111111111111111111111111111"
        mint = "Mint111111111111111111111111111111111111111"
        pump = MODULE.PUMP_PROGRAM_ID
        discriminator = bytes([194, 171, 28, 70, 104, 77, 91, 47])
        payload = discriminator + (1_000_000).to_bytes(8, "little") + (1).to_bytes(8, "little")
        tx = {
            "schema": "ALLENHARK_CURRENT_CODEC_TARGET_TRANSACTION_V1",
            "signature": "native-sig",
            "slot": 20,
            "wallet": "wallet",
            "native_receive_unix_ns": 20_000_000_000,
            "account_keys": [pump, "fee", mint, curve_id],
            "instructions": [{"program_id": pump, "accounts": [0, 1, 2, 3], "data_hex": payload.hex()}],
        }
        seed = {
            "bondingCurve": curve_id,
            "observedAtMs": 19_999,
            "slot": 19,
            "virtualSolReserves": 30_000_000_000,
            "virtualTokenReserves": 1_073_000_000_000_000,
            "realSolReserves": 0,
            "realTokenReserves": 793_100_000_000_000,
            "protocolFeeBps": 95,
            "creatorFeeBps": 30,
            "lpFeeBps": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tx_path = root / "tx.jsonl"
            seed_path = root / "seed.jsonl"
            out = root / "out.jsonl"
            report = root / "report.json"
            tx_path.write_text(json.dumps(tx) + "\n", encoding="utf-8")
            seed_path.write_text(json.dumps(seed) + "\n", encoding="utf-8")
            result = MODULE.materialize(tx_path, seed_path, out, report)
            self.assertEqual(result["stats"]["emitted_rows"], 1)
            row = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(row["native_state"]["state_source"], "NATIVE_ACCOUNT_STATE_PLUS_NATIVE_TRANSACTION")
            self.assertEqual(row["native_state"]["seed_slot"], 19)

    def test_existing_virtual_only_seed_is_blocked(self):
        curve_id = "Curve222222222222222222222222222222222222222"
        tx = {"signature": "sig", "slot": 2, "native_receive_unix_ns": 2_000_000_000,
              "account_keys": [MODULE.PUMP_PROGRAM_ID, "fee", "Mint", curve_id],
              "instructions": [{"program_id": MODULE.PUMP_PROGRAM_ID, "accounts": [0, 1, 2, 3],
                                  "data_hex": (bytes([194, 171, 28, 70, 104, 77, 91, 47]) + (1_000_000).to_bytes(8, "little") + (1).to_bytes(8, "little")).hex()}]}
        seed = {"bondingCurve": curve_id, "observedAtMs": 1_000, "slot": 1,
                "virtualSolReserves": 30_000_000_000, "virtualTokenReserves": 1_073_000_000_000_000}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tx.jsonl").write_text(json.dumps(tx) + "\n", encoding="utf-8")
            (root / "seed.jsonl").write_text(json.dumps(seed) + "\n", encoding="utf-8")
            result = MODULE.materialize(root / "tx.jsonl", root / "seed.jsonl", root / "out.jsonl", root / "report.json")
            self.assertEqual(result["stats"]["emitted_rows"], 0)
            self.assertEqual(result["stats"]["skipped_incomplete_reserves"], 1)

    def test_same_slot_seed_before_receive_is_causal(self):
        curve_id = "CurveSameSlot111111111111111111111111111111111"
        tx = {"signature": "same-slot", "slot": 20, "native_receive_unix_ns": 20_000_000_000,
              "account_keys": [MODULE.PUMP_PROGRAM_ID, "fee", "Mint", curve_id],
              "instructions": [{"program_id": MODULE.PUMP_PROGRAM_ID, "accounts": [0, 1, 2, 3],
                                  "data_hex": (bytes([194, 171, 28, 70, 104, 77, 91, 47]) + (1_000_000).to_bytes(8, "little") + (1).to_bytes(8, "little")).hex()}]}
        seed = {"bondingCurve": curve_id, "observedAtMs": 19_999, "slot": 20,
                "virtualSolReserves": 30_000_000_000, "virtualTokenReserves": 1_073_000_000_000_000,
                "realSolReserves": 0, "realTokenReserves": 793_100_000_000_000,
                "protocolFeeBps": 95, "creatorFeeBps": 30}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tx.jsonl").write_text(json.dumps(tx) + "\n", encoding="utf-8")
            (root / "seed.jsonl").write_text(json.dumps(seed) + "\n", encoding="utf-8")
            result = MODULE.materialize(root / "tx.jsonl", root / "seed.jsonl", root / "out.jsonl", root / "report.json")
            self.assertEqual(result["stats"]["emitted_rows"], 1)
            row = json.loads((root / "out.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["native_state"]["seed_slot"], 20)

    def test_verified_executor_cpi_is_recorded_without_fabricating_state(self):
        curve_id = "Curve333333333333333333333333333333333333333"
        tx = {
            "signature": "cpi-sig",
            "slot": 30,
            "wallet": "wallet",
            "native_receive_unix_ns": 30_000_000_000,
            "account_keys": [MODULE.PUMP_EXECUTOR_PROGRAM_ID, curve_id, MODULE.PUMP_PROGRAM_ID, "Mint"],
            "instructions": [{
                "program_id": MODULE.PUMP_EXECUTOR_PROGRAM_ID,
                "accounts": [1, 2, 3],
                "data_hex": "07" + "00" * 16,
            }],
        }
        seed = {"bondingCurve": curve_id, "observedAtMs": 29_000, "slot": 29,
                "virtualSolReserves": 30_000_000_000, "virtualTokenReserves": 1_073_000_000_000_000}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tx.jsonl").write_text(json.dumps(tx) + "\n", encoding="utf-8")
            (root / "seed.jsonl").write_text(json.dumps(seed) + "\n", encoding="utf-8")
            candidates = root / "candidates.jsonl"
            result = MODULE.materialize(
                root / "tx.jsonl", root / "seed.jsonl", root / "out.jsonl", root / "report.json", candidates
            )
            self.assertEqual(result["stats"]["executor_cpi_candidates"], 1)
            self.assertEqual(result["stats"]["executor_cpi_unique_curve_candidates"], 1)
            self.assertEqual(result["output_rows"], 0)
            row = json.loads(candidates.read_text(encoding="utf-8"))
            self.assertEqual(row["route"], "PUMP_CPI_EXECUTOR")
            self.assertFalse(row["state_available"])


if __name__ == "__main__":
    unittest.main()
