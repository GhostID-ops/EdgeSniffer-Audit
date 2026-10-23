from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arm08_admission_audit_is_fail_closed_and_separate() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm08-admission-audit-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_ARM08_GENUINE_CPI_WALLET_PURCHASE_ADMISSION_AUDIT_V1"
    assert report["read_only"] is True
    assert report["cohort_created"] is False
    assert report["policy_changed"] is False
    assert report["arm07_partition_excluded"] is True
    assert report["admitted_arm08_count"] == 0


def test_current_arm08_candidates_are_rejected_as_direct_pumpswap() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm08-admission-audit-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["inputs"]["arm08_candidate_rows"] == 478
    assert report["counts"]["REJECT_ARM07_DIRECT_OR_NON_CPI_ROUTE"] == 478
