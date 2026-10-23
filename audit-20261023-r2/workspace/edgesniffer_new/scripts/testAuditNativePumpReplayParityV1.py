import json
import tempfile
import unittest
from pathlib import Path

from auditNativePumpReplayParityV1 import audit


class NativeReplayParityTests(unittest.TestCase):
    def test_matching_states_pass_only_after_minimum_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native, finalized = root / "native.jsonl", root / "finalized.jsonl"
            rows = []
            for i in range(20):
                rows.append({"signature": f"s{i}", "canonical_state": {"pre_state": {"virtual_sol": 30 + i, "virtual_token": 1000 - i}, "post_state": {"virtual_sol": 31 + i, "virtual_token": 999 - i}}})
            native.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            finalized.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            result = audit(native, finalized)
            self.assertEqual(result["gateStatus"], "PASS_NATIVE_REPLAY_PARITY")

    def test_empty_overlap_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native, finalized = root / "native.jsonl", root / "finalized.jsonl"
            native.write_text(json.dumps({"signature": "a"}) + "\n")
            finalized.write_text(json.dumps({"signature": "b"}) + "\n")
            self.assertFalse(audit(native, finalized)["economicsInputAllowed"])


if __name__ == "__main__":
    unittest.main()
