import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "kefi_preh3_live_proof_runner_v1.py"
spec = importlib.util.spec_from_file_location("proof", SCRIPT); assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_candidate_window_requires_complete_post_warmup_promotion(tmp_path):
    root = tmp_path / "promotions"; window = root / "shared-windows" / "window-a"; window.mkdir(parents=True)
    (window / "manifest.json").write_text(json.dumps({"requestedCaptureStartNs": 200, "candidates": [{"signature": "sig"}]}))
    receipts = root / "promotion-results"; receipts.mkdir()
    (receipts / "sig.json").write_text(json.dumps({"status": "COMPLETE"}))
    assert m.candidate_window(root, 200) == "window-a"
    assert m.candidate_window(root, 201) is None


def test_epoch_ns_preserves_fractional_nanoseconds():
    assert m.epoch_ns("1970-01-01T00:00:00.123456789Z") == 123_456_789
