import mergeKefi13ArmPumpSwapTapeV1 as subject


def row(frame, sequence, quote=1):
    return {
        "schema": "KEFI_13_ARM_PUMPSWAP_EXACT_TAPE_V1",
        "source_frame_sha256": frame,
        "event_index": 0,
        "received_at_unix_ns": sequence,
        "source_instance_id": "source-" + frame,
        "continuity_epoch": 1,
        "durable_output_sequence": sequence,
        "quote_mint": "So11111111111111111111111111111111111111112",
        "side": "BUY",
        "quote_amount": quote,
    }


def test_merge_deduplicates_identical_segments(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    import json
    first.write_text(json.dumps(row("a", 2)) + "\n" + json.dumps(row("b", 1)) + "\n", encoding="utf-8")
    second.write_text(json.dumps(row("a", 2)) + "\n", encoding="utf-8")
    rows, report = subject.merge([first, second])
    assert [item["source_frame_sha256"] for item in rows] == ["b", "a"]
    assert report["identical_duplicate_rows_dropped"] == 1
    assert report["errors"] == []


def test_merge_rejects_conflicting_identity(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    import json
    first.write_text(json.dumps(row("a", 2, 1)) + "\n", encoding="utf-8")
    second.write_text(json.dumps(row("a", 2, 9)) + "\n", encoding="utf-8")
    _, report = subject.merge([first, second])
    assert report["status"] == "FAIL_CUMULATIVE_TAPE_MERGE"
    assert report["errors"][0]["reason"] == "CONFLICTING_DUPLICATE"
