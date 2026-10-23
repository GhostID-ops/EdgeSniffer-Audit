import json
import tempfile
import unittest
from pathlib import Path

from validateNativeDecoderMatrixV1 import load_matrix, validate


ROOT = Path(__file__).resolve().parents[1]


class NativeDecoderMatrixTests(unittest.TestCase):
    def test_current_source_covers_pinned_variants(self):
        matrix = load_matrix(ROOT / "research/native-decoder-matrix-v1/decoder-matrix.json")
        report = validate(matrix, ROOT / "rust-sidecar/src/main.rs")
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["variant_count"], 9)

    def test_changed_discriminator_is_rejected(self):
        matrix = load_matrix(ROOT / "research/native-decoder-matrix-v1/decoder-matrix.json")
        matrix["variants"][0]["discriminator"][0] ^= 1
        report = validate(matrix, ROOT / "rust-sidecar/src/main.rs")
        self.assertFalse(report["valid"])
        self.assertIn("BUY_DISCRIMINATOR", report["mismatched_constants"])


if __name__ == "__main__":
    unittest.main()
