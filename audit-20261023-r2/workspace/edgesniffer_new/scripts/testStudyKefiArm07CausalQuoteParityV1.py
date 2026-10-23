from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_causal_study_artifact_is_read_only_and_has_two_evidence_tiers() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm07-causal-quote-parity-study-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_ARM07_CAUSAL_QUOTE_LIVE_PARITY_STUDY_V1"
    assert report["read_only"] is True
    assert report["entry_delay_seconds"] == 1
    assert report["cohorts"]["old"]["input_counts"]["strict_signal_signature_joins"] == 5
    assert report["cohorts"]["new"]["input_counts"]["strict_signal_signature_joins"] == 4
    assert report["cohorts"]["old"]["input_counts"]["timestamp_pool_causal_trades"] == 117
    assert report["cohorts"]["new"]["input_counts"]["timestamp_pool_causal_trades"] == 45
    assert report["execution_model"]["network_and_priority_fees"].startswith("not present")


def test_causal_study_does_not_claim_a_selected_policy() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm07-causal-quote-parity-study-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert "requires a new frozen prospective policy" in report["hypotheses_only"]["promotion_rule"]
