import json
from pathlib import Path

from runWalletStrategyMatrixV1 import entry_impact_bps, exit_simulation, metrics, route_matches


def test_entry_impact_increases_with_size():
    state = {"base": 1_000_000, "quote": 5_000_000_000, "virtual_quote": 0}
    small = entry_impact_bps(0.1, state, 100, 0)
    large = entry_impact_bps(1.0, state, 100, 0)
    assert small is not None and large is not None
    assert large > small


def test_route_filter_is_exact():
    assert route_matches({"research_route": "PUMPSWAP_DIRECT"}, "ANY_EXACT_PUMPSWAP")
    assert route_matches({"research_route": "PUMPSWAP_CPI"}, "PUMPSWAP_CPI")
    assert not route_matches({"research_route": "PUMPSWAP_UNRESOLVED_ROUTE"}, "ANY_EXACT_PUMPSWAP")


def test_fixed_exit_records_missing_mark_without_imputation():
    result = exit_simulation(
        [{"block_time": 1001, "postState": {"base": 1_000_000, "quote": 1_000_000_000, "virtual_quote": 0}, "lpFeeBps": 100}],
        1000,
        0.1,
        0,
        {"kind": "FIXED", "horizon_seconds": 5},
        1000,
    )
    assert result["status"] == "MISSED_EXIT_MARK"
    assert result["pnl_sol"] is None


def test_partial_take_profit_requires_both_marks():
    marks = [
        {"block_time": 1002, "postState": {"base": 1_000_000, "quote": 2_000_000_000, "virtual_quote": 0}, "lpFeeBps": 100},
        {"block_time": 1031, "postState": {"base": 1_000_000, "quote": 2_100_000_000, "virtual_quote": 0}, "lpFeeBps": 100},
    ]
    result = exit_simulation(marks, 100_000, 0.1, 0, {"kind": "PARTIAL_TP", "horizon_seconds": 30, "take_profit_pct": 0.01, "partial_fraction": 0.5}, 1000)
    assert result["status"] == "ACTIONABLE"
    assert result["exit_reason"] == "PARTIAL_TAKE_PROFIT"


def test_metrics_exposes_misses_and_drawdown():
    result = metrics([
        {"status": "ACTIONABLE", "pnl_sol": 1.0, "notional_sol": 1.0, "mfe_sol": 1.2, "mae_sol": -0.1},
        {"status": "ACTIONABLE", "pnl_sol": -0.5, "notional_sol": 1.0, "mfe_sol": 0.1, "mae_sol": -0.6},
        {"status": "MISSED_EXIT_MARK", "pnl_sol": None, "notional_sol": 1.0, "mfe_sol": None, "mae_sol": None},
    ])
    assert result["attempted"] == 3
    assert result["missed_or_failed"] == 1
    assert result["max_drawdown_sol"] == 0.5
