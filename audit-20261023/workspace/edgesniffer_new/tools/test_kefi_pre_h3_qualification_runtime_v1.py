#!/usr/bin/env python3
"""Regression checks for the pre-H3 boundary and outcome-sealed runner."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def module(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec); spec.loader.exec_module(loaded)
    return loaded


RUNNER = module("kefi_pre_h3_qualification_runner_v1.py")


def health(**overrides):
    value = {
        "latestReceivedAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceInstanceId": "source-a", "currentEpoch": 1,
        "reconnects": 0, "continuityGaps": 0, "receiveErrors": 0,
        "rollingWindowPromotionFailures": 0, "rollingWindowQueueFull": 0, "sharedBurstGroupsFailed": 0,
    }
    value.update(overrides); return value


def boundary(root: Path) -> dict:
    return {"sourceRoot": str(root), "sourceBaseline": {**{field: 0 for field in RUNNER.ZERO_COUNTERS}, "sourceInstanceId": "source-a", "currentEpoch": 1}}


def test_source_hard_walls() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); (root / "health.json").write_text(json.dumps(health()))
        assert RUNNER.source_gate(boundary(root))[0] == "OK"
        (root / "health.json").write_text(json.dumps(health(reconnects=1)))
        assert RUNNER.source_gate(boundary(root))[0] == "HARD"
        (root / "health.json").write_text(json.dumps(health(sourceInstanceId="source-b")))
        assert RUNNER.source_gate(boundary(root))[0] == "HARD"
        (root / "health.json").write_text(json.dumps(health(currentEpoch=2)))
        assert RUNNER.source_gate(boundary(root))[0] == "HARD"


def test_representation_never_reads_outcomes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "assignments.jsonl"
        rows = []
        for index in range(300):
            rows.append({"target_id": str(index), "pool": f"pool-{index % 40}", "chronological_block": f"block-{index % 8}", "controls": [{"control_id": f"c-{index}"}], "target_complete_actionable": "poison", "target_outcome_reason": "poison"})
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        result = RUNNER.representation(path)
        assert result["ready"] is True


if __name__ == "__main__":
    test_source_hard_walls(); test_representation_never_reads_outcomes(); print("2 passed")
