import json
from pathlib import Path

import normalizeKefiLegacyPumpSwapTapeLineageV1 as subject


def test_normalization_adds_deterministic_lineage_without_changing_economics(tmp_path):
    source = tmp_path / "legacy.jsonl"
    output = tmp_path / "normalized.jsonl"
    report = tmp_path / "report.json"
    row = {
        "lp_fee_bps": 2,
        "protocol_fee_bps": 93,
        "coin_creator_bps": 30,
        "virtual_quote_reserves": 1,
        "pool_base_raw": 2,
        "pool_quote_raw": 3,
        "pool_quote_delta": 1,
        "base_amount": 1,
        "received_at_unix_ns": 10,
        "exact_transition_validated": True,
        "quote_mint": subject.WSOL,
        "mint": "mint",
        "side": "BUY",
        "source_frame_sha256": "a" * 64,
        "event_index": 0,
        "pool": "pool",
    }
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = subject.normalize(source, output)
    normalized = json.loads(output.read_text(encoding="utf-8"))
    assert result["accepted_rows"] == 1
    assert result["lineage_is_synthetic"] is True
    assert normalized["pool_quote_delta"] == row["pool_quote_delta"]
    assert normalized["source_instance_id"].startswith("legacy-tape-")
    assert normalized["durable_output_sequence"] == 0
