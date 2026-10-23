import json
import io
import base64
import tempfile
import unittest
from pathlib import Path

import materializeNativePumpCurveStateV1 as replay


class NativeReplayTests(unittest.TestCase):
    def test_execution_status_is_fail_closed(self):
        self.assertEqual(replay.execution_status({}), "UNKNOWN")
        self.assertEqual(replay.execution_status({"execution": {"success": True}}), "SUCCESS")
        self.assertEqual(replay.execution_status({"execution": {"success": False}}), "FAILED")

    def test_buy_replay_matches_pump_fee_math(self):
        post, out = replay.apply_flow({"virtual_sol": replay.INITIAL_SOL, "virtual_token": replay.INITIAL_TOKEN}, "buy", 100_000_000, None)
        self.assertGreater(out, 0)
        self.assertEqual(post["virtual_sol"], replay.INITIAL_SOL + 98_750_000)
        self.assertLess(post["virtual_token"], replay.INITIAL_TOKEN)

    def test_max_cost_intent_is_not_replayed_as_spent(self):
        row = {
            "mint": "m",
            "side": "buy",
            "quote_lamports": 100_000_000,
            "source_ix_data_base64": base64.b64encode(replay.BUY_DISCRIMINATOR + bytes(16)).decode(),
        }
        amount, variant, semantics, source = replay.buy_quote_for_replay(row, {}, "UNKNOWN")
        self.assertIsNone(amount)
        self.assertEqual(variant, "buy")
        self.assertEqual(semantics, "MAX_SOL_COST_INTENT")
        self.assertEqual(source, "max_cost_or_unknown_intent")

    def test_exact_input_intent_can_remain_conditional(self):
        row = {
            "mint": "m",
            "side": "buy",
            "quote_lamports": 100_000_000,
            "source_ix_data_base64": base64.b64encode(replay.BUY_EXACT_SOL_IN_DISCRIMINATOR + bytes(16)).decode(),
        }
        amount, variant, semantics, source = replay.buy_quote_for_replay(row, {}, "UNKNOWN")
        self.assertEqual(amount, 100_000_000)
        self.assertEqual(variant, "buy_exact_sol_in")
        self.assertEqual(semantics, "EXACT_SOL_IN_INTENT")
        self.assertEqual(source, "exact_input_intent")

    def test_replay_preserves_real_reserves_when_native_seed_has_them(self):
        post, out = replay.apply_flow({"virtual_sol": replay.INITIAL_SOL, "virtual_token": replay.INITIAL_TOKEN,
                                       "real_sol": 0, "real_token": replay.INITIAL_TOKEN}, "buy", 100_000_000, None)
        self.assertEqual(post["real_sol"], 98_750_000)
        self.assertEqual(post["real_token"], replay.INITIAL_TOKEN - out)

    def test_cutoff_offset_starts_on_or_after_receive_time(self):
        source = io.StringIO("\n".join([
            json.dumps({"local_receive_ts_ms": 100}),
            json.dumps({"local_receive_ts_ms": 200}),
        ]) + "\n")
        offset = replay.offset_at_receive_cutoff(source, 150)
        source.seek(offset)
        self.assertEqual(json.loads(source.readline())["local_receive_ts_ms"], 200)

    def test_stream_requires_creation_seed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jsonl"
            output = root / "out.jsonl"
            state = root / "state.json"
            source.write_text(json.dumps({"mint": "m", "side": "buy", "quote_lamports": 100_000_000,
                                          "source_slot": 2, "source_transaction_index": 0,
                                          "local_receive_ts_ms": 2_000,
                                          "raw_record": {"bondingCurve": "c", "nativeKind": "flow"}}) + "\n")
            replay.run(type("Args", (), {"input": source, "output": output, "state": state,
                                          "poll_ms": 1, "once": True, "start_at_end": False})())
            self.assertFalse(output.exists() and output.read_text().strip())

    def test_existing_curve_can_be_seeded_from_native_account_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jsonl"
            output = root / "out.jsonl"
            state = root / "state.json"
            seeds = root / "seeds.jsonl"
            seeds.write_text(json.dumps({
                "bondingCurve": "c",
                "virtualSolReserves": 30_000_000_000,
                "virtualTokenReserves": 1_073_000_000_000_000,
                "realSolReserves": 0,
                "realTokenReserves": 1_073_000_000_000_000,
                "observedAtMs": 1_000,
                "slot": 1,
                "source": "native-account-state",
            }) + "\n")
            source.write_text(json.dumps({
                "mint": "m", "side": "buy", "quote_lamports": 100_000_000,
                "source_ix_data_base64": base64.b64encode(replay.BUY_EXACT_SOL_IN_DISCRIMINATOR + bytes(16)).decode(),
                "source_slot": 2, "source_transaction_index": 0,
                "local_receive_ts_ms": 2_000,
                "raw_record": {"bondingCurve": "c", "nativeKind": "flow"},
            }) + "\n")
            replay.run(type("Args", (), {
                "input": source, "output": output, "state": state,
                "account_seed_input": seeds, "poll_ms": 1, "once": True,
                "start_at_end": False,
            })())
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["canonical_state"]["replay_seed_source"], "native-account-state")
            self.assertEqual(rows[0]["canonical_state"]["replay_seed_slot"], 1)
            self.assertEqual(rows[0]["execution_status"], "UNKNOWN")
            self.assertEqual(rows[0]["state_transition_authority"], "CONDITIONAL_INTENT_REPLAY")
            self.assertFalse(rows[0]["native_economics_state_complete"])
            self.assertIn("real_sol", rows[0]["canonical_state"]["pre_state"])


if __name__ == "__main__":
    unittest.main()
