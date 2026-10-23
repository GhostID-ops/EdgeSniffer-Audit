from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_coverage_priority_audit_is_complete_and_read_only() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm-coverage-priority-audit-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_13_ARM_COVERAGE_PRIORITY_AUDIT_V1"
    assert report["read_only"] is True
    assert report["no_collector_or_policy_change"] is True
    assert len(report["arms"]) == 13
    assert report["priority_order"][0] == "ARM08_PUMPSWAP_CPI"


def test_zero_admitted_is_not_classified_as_negative_pnl() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm-coverage-priority-audit-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert "not a negative PnL result" in report["interpretation"]["zero_admitted_means"]
    assert any(
        row["arm_id"] == "ARM08_PUMPSWAP_CPI"
        and row["raw_candidate_references"] == 95
        and row["exact_admitted_observations"] == 0
        for row in report["arms"]
    )
