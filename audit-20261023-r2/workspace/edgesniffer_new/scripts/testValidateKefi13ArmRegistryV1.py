#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "research" / "kefi-13-arm-program-v1" / "registry.json"
POLICIES = ROOT / "research" / "kefi-13-arm-program-v1" / "frozen-policies.json"
VALIDATOR = ROOT / "scripts" / "validateKefi13ArmRegistryV1.py"


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert registry["schema"] == "KEFI_CANONICAL_13_ARM_REGISTRY_V1"
    assert len(registry["arms"]) == 13
    result = subprocess.run([sys.executable, str(VALIDATOR), "--registry", str(REGISTRY), "--policies", str(POLICIES)], cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["arm_count"] == 13
    assert report["frozen_policy_count"] == 13
    assert report["exact_component_missing_paths"] == []
    assert report["jupiter_fallback_enabled"] is False
    print("PASS testValidateKefi13ArmRegistryV1")


if __name__ == "__main__":
    main()
