import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arm08_discovery_artifact_is_independent_and_read_only():
    path = ROOT / "research/kefi-13-arm-program-v1/single-shot-arm08-discovery-immutable-20261019T150904Z.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_SINGLE_SHOT_ARM08_DISCOVERY_SURFACE_V1"
    assert report["arm_id"] == "ARM08_PUMPSWAP_CPI"
    assert report["read_only"] is True
    assert report["policy_selection"] == "DISABLED_DISCOVERY_ONLY"
    assert report["controls_available"] is False
    assert report["cohort"]["genuine_arm08_observations"] >= 50
    assert report["cohort"]["causal_entries"] > 0
