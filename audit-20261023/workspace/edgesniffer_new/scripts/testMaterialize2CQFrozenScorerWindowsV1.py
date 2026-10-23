import json
import tempfile
import unittest
from pathlib import Path

from materialize2CQFrozenScorerWindowsV1 import materialize


class FrozenScorerWindowTests(unittest.TestCase):
    def test_joins_signal_and_control_to_native_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"virtual_sol": 1_000_000_000, "virtual_token": 1_000_000,
                     "real_sol": 1_000_000_000, "real_token": 1_000_000}
            rows = []
            for index, ts in enumerate((1000, 1100, 2200, 7000)):
                rows.append({"mint": "M", "side": "buy", "local_receive_ts_ms": ts,
                             "transaction_signature": f"s{index}", "observation_id": f"o{index}",
                             "source_slot": 1, "source_transaction_index": index,
                             "canonical_state": {"pre_state": state, "post_state": state},
                             "native_economics_state_complete": True,
                             "fee_basis_points": 100, "protocol_fee_basis_points": 70,
                             "creator_fee_basis_points": 30, "route": "PUMP_CURVE",
                             "quote_lamports": 100_000_000})
            source = root / "source.jsonl"
            source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            signal = root / "signals.jsonl"
            signal.write_text(json.dumps({"record_type": "INDEPENDENT_EVENT_DRIVEN_SCORE_EVENT",
                                          "mint": "M", "signal_receive_ts_ms": 1000,
                                          "signal_signature": "s0", "signal_wallet": "w"}) + "\n", encoding="utf-8")
            control = root / "controls.jsonl"
            control.write_text(json.dumps({"record_type": "INDEPENDENT_EVENT_DRIVEN_CONTROL_EVENT",
                                           "mint": "M", "signal_receive_ts_ms": 1100,
                                           "signal_signature": "s1", "signal_wallet": "w"}) + "\n", encoding="utf-8")
            out = root / "out.jsonl"
            cout = root / "cout.jsonl"
            report = root / "report.json"
            result = materialize(source, signal, control, out, cout, report)
            self.assertEqual(result["signal_windows"], 1)
            self.assertEqual(result["control_windows"], 1)
            self.assertEqual(json.loads(out.read_text())["record_type"], "INDEPENDENT_EVENT_DRIVEN_MARKET_WINDOW")
            self.assertEqual(json.loads(cout.read_text())["record_type"], "INDEPENDENT_MARKET_CONTROL_WINDOW")


if __name__ == "__main__":
    unittest.main()
