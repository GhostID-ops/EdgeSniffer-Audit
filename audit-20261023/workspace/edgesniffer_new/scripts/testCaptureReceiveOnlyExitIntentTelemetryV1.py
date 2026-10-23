import json
from pathlib import Path

from captureReceiveOnlyExitIntentTelemetryV1 import capture


def test_capture_is_explicitly_non_submitting(tmp_path: Path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "out.jsonl"
    source.write_text(
        json.dumps({
            "record_type": "ANCHOR_WINDOW",
            "anchor_id": "a:0",
            "anchor_signature": "sig",
            "anchor_slot": 10,
            "anchor_block_time": 100,
            "mint": "mint",
            "wallet": "wallet",
            "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
        }) + "\n" + json.dumps({"record_type": "MARKET_TRANSACTION"}) + "\n",
        encoding="utf-8",
    )
    result = capture(source, output, policy_id="P", entry_delay=1, horizon=5)
    assert result == {"anchors_scanned": 2, "rows_emitted": 1, "rows_skipped": 1}
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["telemetrySchema"] == "SHADOW_EXIT_INTENT_TELEMETRY_V1"
    assert row["exit_target_block_time"] == 106
    assert row["exit_result"] == "NOT_SUBMITTED_RECEIVE_ONLY"
    assert row["actual_exit_submission_observed"] is False
    assert row["actual_exit_inclusion_observed"] is False
    result2 = capture(source, output, policy_id="P", entry_delay=1, horizon=5)
    assert result2["rows_emitted"] == 0
