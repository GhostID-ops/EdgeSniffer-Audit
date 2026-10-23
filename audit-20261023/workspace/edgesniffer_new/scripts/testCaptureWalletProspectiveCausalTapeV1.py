import json
from pathlib import Path

import captureWalletProspectiveCausalTapeV1 as tape


def test_state_contract(tmp_path: Path):
    state = tape.load_state(tmp_path / "state.json", "c", "2026-10-19T00:00:00Z")
    assert state["schema"] == tape.STATE_SCHEMA
    assert state["cohort_id"] == "c"


def test_read_new_rows(tmp_path: Path):
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps({"schema": "x", "signature": "a"}) + "\n", encoding="utf-8")
    rows, offset = tape.read_new_rows(path, 0)
    assert rows[0]["signature"] == "a"
    assert offset == path.stat().st_size


def test_route_summary_fail_closed():
    result = tape.route_summary({})
    assert result["resolved"] is False
