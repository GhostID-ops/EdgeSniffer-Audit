from pathlib import Path

from captureWalletPoolWindowsV1 import state


def test_state(tmp_path: Path):
    value = state(tmp_path / "state.json", "c", "2026-10-19T00:00:00Z")
    assert value["schema"] == "WALLET_POST_ENTRY_POOL_WINDOW_STATE_V1"
