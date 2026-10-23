import json
from pathlib import Path

from captureWalletPumpCurveWindowsV1 import load_state, read_rows


def test_state_contract(tmp_path: Path):
    value = load_state(tmp_path / "state.json", "c", "2026-10-19T00:00:00Z")
    assert value["schema"] == "WALLET_POST_ENTRY_PUMP_CURVE_WINDOW_STATE_V1"


def test_reads_append_only_rows(tmp_path: Path):
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps({"schema": "WALLET_FINALIZED_ROUTE_RAW_TX_V1", "signature": "a"}) + "\n", encoding="utf-8")
    rows = list(read_rows(path, 0))
    assert rows[0][0]["signature"] == "a"
    assert rows[0][1] == path.stat().st_size
