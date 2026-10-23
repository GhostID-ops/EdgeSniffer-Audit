#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auditKefi13ArmExactCoverageV1.py"
OUTPUT = ROOT / "research" / "kefi-13-arm-program-v1" / "exact-coverage-audit.json"


def main() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    report = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert report["summary"]["arm_count"] == 13
    assert report["summary"]["module_coverage_pass"] >= 8
    assert report["summary"]["fixture_parity_verified"] == 0
    assert report["summary"]["economic_promotion_ready"] == 0
    assert any(row["arm_id"] == "ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED" and row["status"] == "FAIL_CLOSED_NO_ADAPTER" for row in report["arms"])
    assert any(row["arm_id"] == "ARM10_ROUTER_ORCA_WHIRLPOOL" and row["status"] == "MODULE_COVERAGE_PASS" for row in report["arms"])
    print("PASS testAuditKefi13ArmExactCoverageV1")


if __name__ == "__main__":
    main()
