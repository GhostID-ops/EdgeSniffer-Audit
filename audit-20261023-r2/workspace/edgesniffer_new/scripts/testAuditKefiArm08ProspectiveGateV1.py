import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arm08_gate_is_blocked_without_lineage_controls_and_fresh_validation():
    path = ROOT / "research/kefi-13-arm-program-v1/arm08-prospective-gate-audit-immutable-20261019T150904Z.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_ARM08_PROSPECTIVE_GATE_AUDIT_V1"
    assert report["status"] == "BLOCKED_NOT_READY"
    assert report["counts"]["strict_admitted"] == 265
    assert report["counts"]["materialized_arm08"] == 265
    assert report["counts"]["causal_entries"] == 261
    assert report["checks"]["lineage_complete"] is False
    assert report["checks"]["fresh_prospective_validation"] is False
    assert report["no_policy_frozen"] is True
