import json
from pathlib import Path

from captureWalletPostEntryWindowsV1 import load_state, read_rows


def test_state_contract(tmp_path: Path):
    state = load_state(tmp_path / "state.json", "c", "2026-10-19T00:00:00Z")
    assert state["schema"] == "WALLET_POST_ENTRY_MARKET_WINDOW_STATE_V1"


def test_reads_append_only_rows(tmp_path: Path):
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps({"role": "ANCHOR", "anchor_signature": "a"}) + "\n", encoding="utf-8")
    rows, offset = read_rows(path, 0)
    assert rows[0]["anchor_signature"] == "a"
    assert offset == path.stat().st_size
