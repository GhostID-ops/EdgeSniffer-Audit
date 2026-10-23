from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_single_shot_contract_is_pre_registered_and_research_only() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/single-shot-strategy-discovery-contract-current-v1.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert contract["schema"] == "KEFI_SINGLE_SHOT_STRATEGY_DISCOVERY_CONTRACT_V1"
    assert contract["research_only"] is True
    assert contract["live_enabled"] is False
    assert contract["frozen_policies_changed"] is False
    assert len(contract["arm_scope"]["arm_ids"]) == 13


def test_contract_has_realistic_size_exit_and_oos_gates() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/single-shot-strategy-discovery-contract-current-v1.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert contract["discovery_grid"]["notionals_sol"] == [0.1, 0.5, 1.0]
    assert contract["discovery_grid"]["fixed_exit_seconds"] == [5, 10, 15, 30, 60, 120, 300]
    assert contract["discovery_selection_gate"]["minimum_actionable_trades"] == 50
    assert contract["prospective_gate"]["no_same_data_validation"] is True
    assert "genuine venue admission certificate on every row" in contract["prospective_gate"]["requirements"]
