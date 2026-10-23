import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refreshIndependentStrategyLabV1 import refresh


class RefreshLabTests(unittest.TestCase):
    def test_refresh_writes_report_when_source_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            windows = root / "windows.jsonl"
            report = root / "report.json"
            source.write_text("", encoding="utf-8")
            result = refresh(type("Args", (), {"input": source, "windows": windows, "report": report})())
            self.assertEqual(result["windows"], 0)
            self.assertTrue(report.exists())
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["policy_frozen"])

            second = refresh(type("Args", (), {"input": source, "windows": windows, "report": report})())
            self.assertTrue(second["skipped"])
            self.assertEqual(second["refresh_mode"], "unchanged_input_checkpoint")

            source.write_text("{}\n", encoding="utf-8")
            third = refresh(type("Args", (), {"input": source, "windows": windows, "report": report})())
            self.assertTrue(third["skipped"])
            self.assertEqual(third["refresh_mode"], "delta_no_relevant_change")

            with source.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                "mint": "MintForRefreshTest",
                "route": "PUMP_CURVE",
                "receive_ts_ms": 1,
                "side": "buy",
                "state": {
                    "virtual_sol": 30_000_000_000,
                    "virtual_token": 1_073_000_000_000_000,
                    "real_sol": 0,
                    "real_token": 793_100_000_000_000,
                },
                }) + "\n")
            fourth = refresh(type("Args", (), {"input": source, "windows": windows, "report": report})())
            self.assertFalse(fourth["skipped"])
            self.assertEqual(fourth["refresh_mode"], "source_changed_full_rebuild")


if __name__ == "__main__":
    unittest.main()
