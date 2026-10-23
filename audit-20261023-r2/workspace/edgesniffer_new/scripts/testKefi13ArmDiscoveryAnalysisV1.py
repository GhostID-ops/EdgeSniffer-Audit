#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "runKefi13ArmDiscoveryAnalysisV1.py"
OUTPUT = ROOT / "research" / "kefi-13-arm-program-v1" / "discovery-analysis.json"


def main() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    report = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert report["summary"]["arm_count"] == 13
    assert report["outcome_blind"] is True
    assert len(report["arms"]) == 13
    assert all(row["discovery_result"] == "COVERAGE_RECORDED_NOT_ECONOMIC_VALIDATION" for row in report["arms"])
    assert report["summary"]["economic_validation_performed"] is False
    print("PASS testKefi13ArmDiscoveryAnalysisV1")


if __name__ == "__main__":
    main()
