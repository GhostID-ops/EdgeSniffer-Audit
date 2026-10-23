#!/usr/bin/env python3
"""Focused offline checks for the read-only Pump parity-v2 diagnostic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from evaluatePumpCurveParityV2V1 import summary  # noqa: E402


ROOT = SCRIPT_DIR.parent
ARTIFACT = ROOT / "research/kefi-13-arm-program-v1/pump-curve-live-parity-v2-bounded-replay-v1.json"


def test_summary() -> None:
    result = summary([0.5, -0.25, 0.0])
    assert result["actionable"] == 3
    assert result["pnl_sol"] == 0.25
    assert result["profit_factor"] == 2.0
    assert result["win_rate"] == 1 / 3
    assert result["max_drawdown_sol"] == 0.25
    assert result["gross_loss_sol"] == 0.25


def test_sealed_artifact_invariants() -> None:
    result = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert result["schema"] == "PUMP_CURVE_LIVE_PARITY_V2_BOUNDED_REPLAY_V1"
    assert result["read_only"] is True
    assert result["policy_unchanged"] is True
    assert result["trading_enabled"] is False
    timing = result["timing_classes"]
    assert sum(timing.values()) == result["windows_loaded"]
    assert result["bounded_low"]["pnl_sol"] <= result["bounded_high"]["pnl_sol"]
    assert result["bounded_low"]["actionable"] == result["bounded_high"]["actionable"]


if __name__ == "__main__":
    test_summary()
    test_sealed_artifact_invariants()
    print("parity-v2 focused checks: PASS")
