from evaluatePumpCurveProspectivePolicyV1 import pre_state, summary


def test_pre_state_reconstructs_reserves():
    result = pre_state({
        "sol_amount": 20,
        "token_amount": 7,
        "post_state": {
            "virtual_sol": 120,
            "virtual_token": 93,
            "real_sol": 30,
            "real_token": 43,
        },
    })
    assert result.virtual_sol == 100
    assert result.virtual_token == 100
    assert result.real_sol == 10
    assert result.real_token == 50


def test_summary_counts_non_actionable_without_pnl():
    result = summary([
        {"status": "ACTIONABLE", "pnl_sol": 0.2},
        {"status": "ACTIONABLE", "pnl_sol": -0.1},
        {"status": "MISSED_EXIT_MARK", "pnl_sol": None},
    ])
    assert result["attempted"] == 3
    assert result["actionable"] == 2
    assert result["missed_or_failed"] == 1
    assert result["wins"] == 1
    assert result["pnl_sol"] == 0.1
    assert result["profit_factor"] == 2.0
