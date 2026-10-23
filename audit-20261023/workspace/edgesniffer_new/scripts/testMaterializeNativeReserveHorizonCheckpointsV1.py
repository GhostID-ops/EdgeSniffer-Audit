#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

import materializeNativeCheckpointWindowsV1 as joiner
import materializeNativeReserveHorizonCheckpointsV1 as checkpoints


def window(curve: str = "curve-a") -> dict:
    return {
        "record_type": "INDEPENDENT_MARKET_WINDOW",
        "fingerprint_id": "fp",
        "mint": "mint-a",
        "signal_receive_ts_ms": 1_000,
        "signal_signature": "sig-a",
        "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
        "marks": [{"local_receive_ts_ms": 2_000, "extra": {"bondingCurve": curve}}],
    }


def seed(ts: int, curve: str = "curve-a", complete: bool = True) -> dict:
    row = {
        "bondingCurve": curve,
        "observedAtMs": ts,
        "slot": 10,
        "write_version": 99,
        "account_txn_signature": "account-sig",
        "virtualSolReserves": 30_000_000_000,
        "virtualTokenReserves": 1_000_000_000_000_000,
        "realSolReserves": 0,
        "realTokenReserves": 1_000_000_000_000_000,
        "complete": False,
    }
    if not complete:
        row.pop("realSolReserves")
    return row


class NativeCheckpointTests(unittest.TestCase):
    def test_emits_first_complete_native_update_at_or_after_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            windows = root / "windows.jsonl"
            seeds = root / "seeds.jsonl"
            output = root / "checkpoints.jsonl"
            state = root / "state.json"
            windows.write_text(json.dumps(window()) + "\n")
            seeds.write_text("\n".join(json.dumps(row) for row in [seed(15_900), seed(17_100)]) + "\n")
            args = type("Args", (), {
                "windows": windows, "seeds": seeds, "output": output, "state": state,
                "start_at_receive_ms": None, "horizons": (15,), "poll_sec": 1, "once": True,
            })()
            result = checkpoints.process_once(args, checkpoints.load_state(state), set())
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(result["checkpoints_written"], 1)
            self.assertEqual(rows[0]["horizon_seconds"], 15)
            self.assertEqual(rows[0]["checkpoint_receive_ts_ms"], 17_100)
            self.assertEqual(rows[0]["delay_after_deadline_ms"], 100)
            self.assertFalse(rows[0]["strict_mark"])
            self.assertFalse(rows[0]["carry_forward"])

    def test_incomplete_native_state_does_not_create_a_mark(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            windows = root / "windows.jsonl"
            seeds = root / "seeds.jsonl"
            output = root / "checkpoints.jsonl"
            state = root / "state.json"
            windows.write_text(json.dumps(window()) + "\n")
            seeds.write_text(json.dumps(seed(20_000, complete=False)) + "\n")
            args = type("Args", (), {
                "windows": windows, "seeds": seeds, "output": output, "state": state,
                "start_at_receive_ms": None, "horizons": (15,), "poll_sec": 1, "once": True,
            })()
            result = checkpoints.process_once(args, checkpoints.load_state(state), set())
            self.assertEqual(result["checkpoints_written"], 0)
            self.assertFalse(output.exists() and output.read_text().strip())

    def test_join_is_separate_and_preserves_original_marks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            windows_path = root / "windows.jsonl"
            checkpoints_path = root / "checkpoints.jsonl"
            output = root / "joined.jsonl"
            original = window()
            windows_path.write_text(json.dumps(original) + "\n")
            wid = joiner.canonical_window_id(original)
            checkpoints_path.write_text(json.dumps({
                "window_id": wid, "horizon_seconds": 15, "checkpoint_receive_ts_ms": 17_100,
                "state": {"virtual_sol": 30_000_000_000, "virtual_token": 1_000_000_000_000_000,
                           "real_sol": 0, "real_token": 1_000_000_000_000_000},
            }) + "\n")
            result = joiner.materialize(windows_path, checkpoints_path, output)
            joined = json.loads(output.read_text())
            self.assertEqual(result["native_checkpoint_rows"], 1)
            self.assertEqual(len(joined["marks"]), 1)
            self.assertEqual(len(joined["native_checkpoint_marks"]), 1)
            self.assertTrue(joined["native_checkpoint_coverage"]["strict_marks_unchanged"])


if __name__ == "__main__":
    unittest.main()
