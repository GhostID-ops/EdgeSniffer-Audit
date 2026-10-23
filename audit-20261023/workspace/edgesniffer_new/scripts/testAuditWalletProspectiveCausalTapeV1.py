import json
from pathlib import Path

from auditWalletProspectiveCausalTapeV1 import audit


def test_audit_blocks_without_marks(tmp_path: Path):
    path = tmp_path / "tape.jsonl"
    rows = [
        {"schema": "WALLET_PROSPECTIVE_CAUSAL_TAPE_V1", "role": "ANCHOR", "anchor_signature": "a", "anchor_slot": 1, "slot": 1, "route": {"resolved": True, "route_resolution": {"venue": "PUMPSWAP"}}},
        {"schema": "WALLET_PROSPECTIVE_CAUSAL_TAPE_V1", "role": "CONTROL_SAME_SLOT", "anchor_signature": "a", "anchor_slot": 1, "slot": 1, "successful": True, "route": {"resolved": True}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = audit(path)
    assert result["anchors"] == 1
    assert result["same_slot_controls"] == 1
    assert result["economics_ready"] is False
    assert "NO_POST_ENTRY_MARKS_IN_TAPE" in result["blocking_reasons"]
