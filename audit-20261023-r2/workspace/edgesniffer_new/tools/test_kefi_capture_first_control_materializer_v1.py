import json
import importlib.util
import subprocess
import sys
from pathlib import Path


MATERIALIZER = Path(__file__).parents[1] / "scripts" / "kefi_capture_first_control_materializer_v1.py"


def materializer_module():
    spec = importlib.util.spec_from_file_location("kefi_materializer", MATERIALIZER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(candidate_signature: str, source_frame_sha256: str, pool_quote_raw: int) -> dict:
    return {
        "candidate_signature": candidate_signature,
        "event_index": 0,
        "source_frame_sha256": source_frame_sha256,
        "received_at_unix_ns": 10,
        "slot": 1,
        "pool_quote_raw": pool_quote_raw,
    }


def rebuild(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(MATERIALIZER),
            "--root",
            str(tmp_path / "source"),
            "--out",
            str(tmp_path / "out"),
            "--decoder",
            str(tmp_path / "unused-decoder"),
            "--rebuild-deduplicated-market",
        ],
        text=True,
        capture_output=True,
    )


def write_rows(tmp_path: Path, *rows: dict) -> None:
    path = tmp_path / "out" / "market-rows" / "rows.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def test_teacher_window_identity_does_not_collapse_distinct_source_frames(tmp_path):
    write_rows(tmp_path, row("teacher", "frame-a", 100), row("teacher", "frame-b", 200))
    result = rebuild(tmp_path)
    assert result.returncode == 0, result.stderr
    rows = (tmp_path / "out" / "market-deduplicated.jsonl").read_text().splitlines()
    assert len(rows) == 2


def test_true_source_frame_overlap_must_be_byte_identical(tmp_path):
    first = row("teacher-a", "frame-a", 100)
    write_rows(tmp_path, first, first)
    result = rebuild(tmp_path)
    assert result.returncode == 0, result.stderr
    rows = (tmp_path / "out" / "market-deduplicated.jsonl").read_text().splitlines()
    assert len(rows) == 1


def test_conflicting_source_frame_overlap_fails_closed(tmp_path):
    write_rows(tmp_path, row("teacher-a", "frame-a", 100), row("teacher-b", "frame-a", 200))
    result = rebuild(tmp_path)
    assert result.returncode != 0
    assert "FAIL_CLOSED: conflicting source-frame overlap" in result.stderr


def test_processed_window_requires_matching_receipt_and_rows_hash(tmp_path):
    module = materializer_module()
    out = tmp_path / "out"
    rows = out / "market-rows" / "window-a.jsonl"
    rows.parent.mkdir(parents=True)
    rows.write_text('{"source_frame_sha256":"frame-a","event_index":0}\n')
    receipt = out / "receipts" / "window-a.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "window_id": "window-a",
        "market_rows_path": str(rows),
        "market_rows_sha256": module.sha256(rows),
    }))
    assert module.verified_processed_receipt(out, "window-a")
    rows.write_text('{"source_frame_sha256":"frame-a","event_index":1}\n')
    assert not module.verified_processed_receipt(out, "window-a")


def test_source_partition_refs_bind_manifest_and_compressed_identity(tmp_path):
    module = materializer_module()
    window = tmp_path / "window"
    partition = window / "partitions" / "2026" / "10" / "12" / "00"
    partition.mkdir(parents=True)
    (partition / "evidence.frames.zst").write_bytes(b"evidence")
    (partition / "manifest.json").write_text(json.dumps({"compressedSha256": "abc"}))
    assert module.source_partition_refs(window) == [{
        "relative_manifest_path": "partitions/2026/10/12/00/manifest.json",
        "manifest_sha256": module.sha256(partition / "manifest.json"),
        "compressed_sha256": "abc",
    }]


def test_ingest_descriptor_is_published_only_after_receipt_is_durable(tmp_path):
    module = materializer_module()
    out = tmp_path / "out"
    receipt_path = out / "receipts" / "window-a.json"
    receipt = {"window_id": "window-a", "market_rows_sha256": "rows-hash"}
    module.atomic_json(receipt_path, receipt)
    module.publish_incremental_ingest_descriptor(out, receipt_path, receipt)
    descriptor = json.loads((out / "canonical-ingest" / "pending" / "window-a.json").read_text())
    assert descriptor["receipt_sha256"] == module.sha256(receipt_path)
    assert descriptor["market_rows_sha256"] == "rows-hash"


def test_cohort_row_validation_rejects_preboundary_evidence(tmp_path):
    module = materializer_module()
    registry_script = Path(__file__).parents[1] / "scripts" / "kefi_scientific_epoch_registry_v1.py"
    spec = importlib.util.spec_from_file_location("epochs", registry_script); epochs = importlib.util.module_from_spec(spec); spec.loader.exec_module(epochs)
    epochs.append(tmp_path / "registry", {"epoch_id":"future","kind":"FUTURE_UNOPENED","status":"UNOPENED","start_utc":"2026-10-10T00:00:00Z","end_utc":"","reason":"test"})
    rows = tmp_path / "rows.jsonl"; rows.write_text(json.dumps({
        "received_at_unix_ns": 1, "continuity_epoch": 1,
        "source_instance_id": "source", "durable_output_sequence": 1, "event_index": 0,
        "source_frame_sha256": "frame",
    }) + "\n")
    try:
        module.validate_scientific_rows(rows, registry_script, tmp_path / "registry", "2026-10-10T00:00:00Z")
    except RuntimeError as error:
        assert "PRE_BOUNDARY" in str(error)
    else:
        raise AssertionError("pre-boundary row accepted")


def test_scientific_validation_rejects_missing_or_ambiguous_source_order(tmp_path):
    module = materializer_module()
    registry_script = Path(__file__).parents[1] / "scripts" / "kefi_scientific_epoch_registry_v1.py"
    spec = importlib.util.spec_from_file_location("epochs", registry_script); epochs = importlib.util.module_from_spec(spec); spec.loader.exec_module(epochs)
    epochs.append(tmp_path / "registry", {"epoch_id":"future","kind":"FUTURE_UNOPENED","status":"UNOPENED","start_utc":"1970-01-01T00:00:00Z","end_utc":"","reason":"test"})
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"received_at_unix_ns": 1, "continuity_epoch": 1, "source_instance_id": "source", "durable_output_sequence": 5, "event_index": 0, "source_frame_sha256": "a"}) + "\n" + json.dumps({"received_at_unix_ns": 1, "continuity_epoch": 1, "source_instance_id": "source", "durable_output_sequence": 5, "event_index": 1, "source_frame_sha256": "b"}) + "\n")
    try:
        module.validate_scientific_rows(rows, registry_script, tmp_path / "registry", "1970-01-01T00:00:00Z")
    except RuntimeError as error:
        assert "source-order collision" in str(error)
    else:
        raise AssertionError("ambiguous source order accepted")
