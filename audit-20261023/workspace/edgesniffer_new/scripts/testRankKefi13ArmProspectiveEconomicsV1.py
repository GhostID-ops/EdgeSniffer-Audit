#!/usr/bin/env python3
import json
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rankKefi13ArmProspectiveEconomicsV1.py"


def test_sampled_control_failure_kills_research_only():
    spec = importlib.util.spec_from_file_location("ranker", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    arm = {"id": "A", "venue": "v", "evaluator": {"status": "EXACT"}}
    report = {
        "cohort": "FRESH_PROSPECTIVE",
        "outcome_source": "POST_FREEZE",
        "metrics": {"actionable_n": 50, "profit_factor": 2, "pnl_quote": 1, "controls_outperformed": False},
    }
    result = module.evaluate_row(arm, report, 50)
    assert result["status"] == "FAIL_PROMOTION_GATE_CONTROLS"
    assert result["kill_decision"] == "KILL_RESEARCH_ONLY"


def main() -> None:
    # The repository contains real prospective artifacts.  Use an empty
    # reports directory so this regression test verifies missing-report
    # handling rather than depending on the current research checkpoint.
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "ranking.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--reports-dir", str(Path(temp_dir) / "reports"), "--output", str(output)],
            cwd=ROOT, text=True, capture_output=True,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        report = json.loads(output.read_text(encoding="utf-8"))
    assert len(report["arms"]) == 13
    assert report["fresh_only"] is True
    assert report["winner"] is None
    assert report["status"] == "NO_WINNER_DATA_OR_GATE_PASS"
    assert all(row["promotion_eligible"] is False for row in report["arms"])
    print("PASS testRankKefi13ArmProspectiveEconomicsV1")


if __name__ == "__main__":
    main()
