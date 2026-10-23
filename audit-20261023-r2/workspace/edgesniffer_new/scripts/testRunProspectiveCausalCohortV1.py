import json
import tempfile
import unittest
from pathlib import Path

from runProspectiveCausalCohortV1 import run_once


class ProspectiveCohortTests(unittest.TestCase):
    def test_fixed_cutoff_excludes_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = {"record_type": "INDEPENDENT_MARKET_WINDOW", "signal_receive_ts_ms": 99, "mint": "old"}
            new = {"record_type": "INDEPENDENT_MARKET_WINDOW", "signal_receive_ts_ms": 100, "mint": "new"}
            control = {"record_type": "INDEPENDENT_MARKET_CONTROL_WINDOW", "signal_receive_ts_ms": 100, "mint": "control"}
            (root / "source.jsonl").write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
            (root / "controls.jsonl").write_text(json.dumps(control) + "\n")
            report = run_once(root / "source.jsonl", root / "controls.jsonl", root / "windows.jsonl", root / "cohort-controls.jsonl", root / "report.json", root / "lab.json", "test", 100)
            self.assertEqual(report["prospective_windows"], 1)
            self.assertEqual(report["prospective_controls"], 1)
            self.assertEqual(json.loads((root / "windows.jsonl").read_text())["mint"], "new")
            self.assertTrue(json.loads((root / "lab.json").read_text())["prospective"])


if __name__ == "__main__":
    unittest.main()
