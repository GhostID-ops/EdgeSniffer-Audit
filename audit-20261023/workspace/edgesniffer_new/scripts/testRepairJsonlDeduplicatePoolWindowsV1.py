import json

from repairJsonlDeduplicatePoolWindowsV1 import repair


def test_deduplicates_identical_anchor_and_market_rows(tmp_path):
    source = tmp_path / "pool.jsonl"
    output = tmp_path / "pool-dedup.jsonl"
    report = tmp_path / "report.json"
    anchor = {"record_type": "ANCHOR_WINDOW", "anchor_signature": "a", "window_signatures": ["m"]}
    market = {"record_type": "MARKET_TRANSACTION", "signature": "m", "raw_transaction": {"slot": 1}}
    source.write_text("\n".join(json.dumps(row) for row in [anchor, anchor, market, market]) + "\n", encoding="utf-8")
    result = repair(source, output, report)
    assert result["input_rows"] == 4
    assert result["output_rows"] == 2
    assert result["duplicate_rows_removed"] == 2
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_conflicting_duplicate_fails_closed(tmp_path):
    source = tmp_path / "pool.jsonl"
    output = tmp_path / "pool-dedup.jsonl"
    report = tmp_path / "report.json"
    rows = [
        {"record_type": "MARKET_TRANSACTION", "signature": "m", "slot": 1},
        {"record_type": "MARKET_TRANSACTION", "signature": "m", "slot": 2},
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    try:
        repair(source, output, report)
    except RuntimeError as error:
        assert "CONFLICT" in str(error) or "conflicts=1" in str(error)
    else:
        raise AssertionError("expected conflicting duplicate to fail closed")


def test_market_envelope_metadata_differences_are_not_conflicts(tmp_path):
    source = tmp_path / "pool.jsonl"
    output = tmp_path / "pool-dedup.jsonl"
    report = tmp_path / "report.json"
    base = {
        "record_type": "MARKET_TRANSACTION", "signature": "m", "slot": 1,
        "block_time": 10, "transaction_sha256": "hash", "raw_transaction": {"slot": 1},
        "captured_at_utc": "first", "anchor_signature_first_seen": "a",
    }
    second = dict(base, captured_at_utc="second", anchor_signature_first_seen="b")
    source.write_text("\n".join(json.dumps(row) for row in [base, second]) + "\n", encoding="utf-8")
    result = repair(source, output, report)
    assert result["duplicate_rows_removed"] == 1


def test_anchor_window_signature_sets_are_merged(tmp_path):
    source = tmp_path / "pool.jsonl"
    output = tmp_path / "pool-dedup.jsonl"
    report = tmp_path / "report.json"
    base = {
        "record_type": "ANCHOR_WINDOW", "anchor_signature": "a", "anchor_slot": 1,
        "anchor_block_time": 10, "entry_delay_seconds": 1, "exit_horizon_seconds": 300,
        "pool": "p", "window_signatures": ["m1"], "captured_at_utc": "first",
    }
    second = dict(base, window_signatures=["m1", "m2"], captured_at_utc="second")
    source.write_text("\n".join(json.dumps(row) for row in [base, second]) + "\n", encoding="utf-8")
    result = repair(source, output, report)
    assert result["duplicate_rows_removed"] == 1
    merged = json.loads(output.read_text(encoding="utf-8"))
    assert merged["window_signatures"] == ["m1", "m2"]
