import json
import tempfile
import unittest
from pathlib import Path

from normalizeNativePumpCurveAccountSeedsIncrementalV1 import run


class IncrementalSeedNormalizerTests(unittest.TestCase):
    def test_processes_only_from_offset_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            output = root / "seeds.jsonl"
            state = root / "state.json"
            rows = [
                {"bondingCurve": "curve-a", "virtualSolReserves": 30_000_000_000,
                 "virtualTokenReserves": 1_073_000_000_000_000, "realSolReserves": 1,
                 "realTokenReserves": 2, "observedAtMs": 1, "slot": 1},
                {"bondingCurve": "curve-b", "virtualSolReserves": 30_000_000_001,
                 "virtualTokenReserves": 1_072_999_999_999_999, "realSolReserves": 3,
                 "realTokenReserves": 4, "observedAtMs": 2, "slot": 2},
            ]
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            first_offset = len((json.dumps(rows[0]) + "\n").encode("utf-8"))
            run(source, output, state, 1, first_offset, True)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 1)
            run(source, output, state, 1, None, True)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 1)
            with source.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "bondingCurve": "curve-c", "virtualSolReserves": 30_000_000_002,
                    "virtualTokenReserves": 1_072_999_999_999_998, "realSolReserves": 5,
                    "realTokenReserves": 6, "observedAtMs": 3, "slot": 3,
                }) + "\n")
            run(source, output, state, 1, None, True)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
