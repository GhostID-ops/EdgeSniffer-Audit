import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("kefi_selection_shadow_closeout_v1.py")


def test_closeout_requires_complete_clean_audit():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "start-receipt.json").write_text(json.dumps({"started_at_utc": "x"}))
        (root / "protocol-amendment.json").write_text("{}")
        (root / "checkpoint.json").write_text(json.dumps({"elapsed_hours": 24, "model_sha256": "m",
            "match_rate_on_complete": .1, "outcomes_accessed": False, "pnl_accessed": False, "execution_enabled": False}))
        (root / "completion-receipt.json").write_text(json.dumps({"status": "COMPLETE", "completed_at_utc": "y"}))
        (root / "audit-latest.json").write_text(json.dumps({"status": "PASS_READY", "ready_for_final_closeout": True,
            "totals": {"complete_windows": 1}, "rejects": {}}))
        run = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], text=True, capture_output=True)
        assert run.returncode == 0, run.stderr
        assert json.loads((root / "selection-shadow-closeout.json").read_text())["status"] == "CLOSED_OUTCOME_BLIND_SELECTION_SHADOW"
