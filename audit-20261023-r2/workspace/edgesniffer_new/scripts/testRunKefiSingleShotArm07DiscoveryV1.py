from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arm07_discovery_surface_is_read_only_and_complete() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/single-shot-arm07-discovery-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_SINGLE_SHOT_ARM07_DISCOVERY_SURFACE_V1"
    assert report["read_only"] is True
    assert report["policy_selection"] == "DISABLED_DISCOVERY_ONLY"
    assert report["prospective_validation"] == "REQUIRED"
    assert report["cohorts"]["old"]["causal_entries"] == 175
    assert report["cohorts"]["new"]["causal_entries"] == 46


def test_arm07_discovery_surface_cannot_promote_from_same_data() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/single-shot-arm07-discovery-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report["cohorts"]["new"]["surface"]
    assert len(rows) == 3 * 2 * (7 + 5)
    assert all(row["selection_gate"]["status"] == "HOLD_NO_SELECTION" for row in rows)
