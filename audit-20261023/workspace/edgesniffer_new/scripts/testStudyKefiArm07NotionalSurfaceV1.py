from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_notional_surface_is_read_only_and_covers_target_sizes() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm07-notional-surface-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == "KEFI_ARM07_NOTIONAL_SURFACE_V1"
    assert report["read_only"] is True
    assert report["notionals_sol"] == [0.1, 0.5, 1.0]
    for cohort in ("old", "new"):
        surface = report["cohorts"][cohort]["size_surface"]
        assert set(surface) == {"0.1", "0.5", "1.0"}
        assert all(len(surface[size]["fixed30_slippage_surface"]) == 3 for size in surface)


def test_fixed30_surface_shows_size_and_stress_degradation() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm07-notional-surface-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    new = report["cohorts"]["new"]["size_surface"]

    def fixed(size: str, bps: int) -> dict:
        return next(row for row in new[size]["fixed30_slippage_surface"] if row["slippage_bps"] == bps)

    assert fixed("0.1", 0)["pnl_sol"] < 0
    assert fixed("0.5", 0)["pnl_sol"] < fixed("0.1", 0)["pnl_sol"]
    assert fixed("1.0", 0)["pnl_sol"] < fixed("0.5", 0)["pnl_sol"]
    assert fixed("0.1", 50)["pnl_sol"] < fixed("0.1", 0)["pnl_sol"]
    assert fixed("0.5", 0)["mean_entry_amm_price_impact_bps"] > fixed("0.1", 0)["mean_entry_amm_price_impact_bps"]
    assert fixed("1.0", 0)["mean_entry_amm_price_impact_bps"] > fixed("0.5", 0)["mean_entry_amm_price_impact_bps"]


def test_notional_surface_keeps_exit_changes_as_hypotheses() -> None:
    path = ROOT / "research/kefi-13-arm-program-v1/arm07-notional-surface-current-v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert "hypotheses_only" in report["policy_status"]
    new = report["cohorts"]["new"]["size_surface"]["0.1"]["exit_policy_surface"]
    tp = next(row for row in new if row["policy"] == "tp_100" and row["slippage_bps"] == 0)
    assert tp["failed_or_unavailable"] > 0


def test_notional_surface_matches_causal_study_at_frozen_size() -> None:
    surface = json.loads(
        (ROOT / "research/kefi-13-arm-program-v1/arm07-notional-surface-current-v1.json").read_text(encoding="utf-8")
    )
    causal = json.loads(
        (ROOT / "research/kefi-13-arm-program-v1/arm07-causal-quote-parity-study-current-v1.json").read_text(encoding="utf-8")
    )
    for cohort in ("old", "new"):
        fixed = next(
            row for row in surface["cohorts"][cohort]["size_surface"]["0.1"]["fixed30_slippage_surface"]
            if row["slippage_bps"] == 0
        )
        causal_fixed = next(
            row for row in causal["cohorts"][cohort]["slippage_policy_surface"]["0"]
            if row["policy"] == "fixed30"
        )
        assert fixed["n"] == causal_fixed["n"]
        assert fixed["pnl_sol"] == causal_fixed["pnl_sol"]
