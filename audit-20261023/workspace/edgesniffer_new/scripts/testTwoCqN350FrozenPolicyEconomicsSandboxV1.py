#!/usr/bin/env python3
"""Deterministic unit checks for the N350 economics sandbox adapter."""

from __future__ import annotations

import hashlib

import twoCqN350FrozenPolicyEconomicsSandboxV1 as subject


def main() -> None:
    model = {
        "featureNames": ["x"],
        "means": {"x": 1.0},
        "scales": {"x": 2.0},
        "coefficients": {"x": 3.0},
        "intercept": -1.0,
    }
    assert subject.score_state({"features": {"x": 5.0}}, model) == 5.0
    row = {
        "eligibleOrdinal": 1,
        "episodeId": "episode-1",
        "states": [
            {"mint": "a", "stateSignature": "1", "label": True, "features": {"x": 1.0}},
            {"mint": "b", "stateSignature": "2", "label": False, "features": {"x": 5.0}},
        ],
    }
    seed = "fixed"
    chosen = subject.choose_states(row, {"crossMintModel": model}, seed)
    assert chosen["TEACHER_SELECTED"]["mint"] == "a"
    assert chosen["FROZEN_MODEL_TOP1"]["mint"] == "b"
    ordered = sorted(row["states"], key=lambda state: (state["mint"], state["stateSignature"]))
    expected = ordered[int.from_bytes(hashlib.sha256(f"{seed}:episode-1".encode()).digest()[:8], "big") % 2]
    assert chosen["DETERMINISTIC_BLIND_ONE_PER_CHOICE_SET"] == expected

    rows = [
        {"status": "EXACT", "pnlSol": 0.1, "returnPct": 10.0, "outcome": "TP", "counterfactualDivergenceRate": 0.0},
        {"status": "EXACT", "pnlSol": -0.05, "returnPct": -5.0, "outcome": "MAX_HOLD", "counterfactualDivergenceRate": 0.1},
        {"status": "EXCLUDED", "reason": "test"},
    ]
    metric = subject.metrics(rows)
    assert metric["exactEvaluableN"] == 2
    assert metric["excludedN"] == 1
    assert metric["wins"] == 1 and metric["losses"] == 1
    assert abs(metric["profitFactor"] - 2.0) < 1e-12
    assert abs(metric["totalExactPnlSol"] - 0.05) < 1e-12
    print("PASS: N350 sandbox selection and aggregation are deterministic")


if __name__ == "__main__":
    main()
