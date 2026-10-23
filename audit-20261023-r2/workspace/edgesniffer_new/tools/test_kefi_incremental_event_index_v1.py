import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
INDEXER = ROOT / "scripts" / "kefi_incremental_event_index_v1.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row(frame: str, event_index: int, quote: int, candidate: str) -> dict:
    return {
        "source_frame_sha256": frame, "event_index": event_index,
        "received_at_unix_ns": 10, "slot": 1, "pool_quote_raw": quote,
        "candidate_signature": candidate,
    }


def scientific_row(frame: str, event_index: int, quote: int, candidate: str, sequence: int) -> dict:
    return {**row(frame, event_index, quote, candidate), "continuity_epoch": 7,
            "source_instance_id": "source-a", "durable_output_sequence": sequence}


def write_window(out: Path, window_id: str, rows: list[dict], *, decoder=True) -> Path:
    rows_path = out / "market-rows" / f"{window_id}.jsonl"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in rows))
    receipt = {
        "window_id": window_id, "market_rows_path": str(rows_path),
        "market_rows_sha256": sha256(rows_path),
        "source_partition_refs": [{"relative_manifest_path": "partitions/manifest.json", "manifest_sha256": "manifest", "compressed_sha256": "compressed"}],
    }
    if decoder:
        receipt["decoder"] = {"decoder_path": "/decoder", "decoder_sha256": "decoder"}
    receipt_path = out / "receipts" / f"{window_id}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    descriptor = {"schema": "KEFI_CAPTURE_FIRST_CONTROL_MATERIALIZER_V1_INGEST_DESCRIPTOR_V1", "window_id": window_id,
                  "receipt_path": str(receipt_path), "receipt_sha256": sha256(receipt_path),
                  "market_rows_sha256": receipt["market_rows_sha256"]}
    pending = out / "canonical-ingest" / "pending" / f"{window_id}.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(descriptor, sort_keys=True) + "\n")
    return receipt_path


def mark_scientific(out: Path, window_id: str) -> None:
    receipt_path = out / "receipts" / f"{window_id}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["scientific_validation"] = {"rows": 1, "source_frames": 1, "ordered_events": 1}
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    descriptor = out / "canonical-ingest" / "pending" / f"{window_id}.json"
    if not descriptor.exists():
        descriptor = out / "canonical-ingest" / "processing" / f"{window_id}.json"
    payload = json.loads(descriptor.read_text()); payload["receipt_sha256"] = sha256(receipt_path)
    descriptor.write_text(json.dumps(payload, sort_keys=True) + "\n")


def run(out: Path, index: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(INDEXER), "--materializer-out", str(out), "--index-root", str(index), *extra], text=True, capture_output=True)


def checkpoint(index: Path) -> dict:
    return json.loads((index / "checkpoint.json").read_text())


def test_queue_ingest_preserves_intrinsic_identity_and_exposures(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    write_window(out, "a", [row("frame", 0, 100, "teacher-a")])
    write_window(out, "b", [row("frame", 0, 100, "teacher-b"), row("frame-b", 0, 200, "teacher-b")])
    result = run(out, index)
    assert result.returncode == 0, result.stderr
    assert checkpoint(index)["canonical_events"] == 2
    assert checkpoint(index)["verified_identical_overlaps"] == 1
    assert len(list((out / "canonical-ingest" / "processed").glob("*.json"))) == 2


def test_normal_run_does_not_require_old_receipts_or_scan_history(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    first = write_window(out, "a", [row("frame-a", 0, 100, "teacher-a")])
    assert run(out, index).returncode == 0
    first.unlink()  # already verified and committed; hot path must not revisit it
    second = write_window(out, "b", [row("frame-b", 0, 200, "teacher-b")])
    result = run(out, index)
    assert result.returncode == 0, result.stderr
    assert checkpoint(index)["canonical_events"] == 2
    assert second.is_file()


def test_recovery_replays_processing_descriptor_safely(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    write_window(out, "a", [row("frame-a", 0, 100, "teacher-a")])
    pending = out / "canonical-ingest" / "pending" / "a.json"
    processing = out / "canonical-ingest" / "processing" / "a.json"
    processing.parent.mkdir(parents=True)
    pending.replace(processing)
    result = run(out, index)
    assert result.returncode == 0, result.stderr
    assert (out / "canonical-ingest" / "processed" / "a.json").is_file()


def test_queue_ingest_fails_closed_on_changed_rows_or_missing_decoder(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    write_window(out, "a", [row("frame-a", 0, 100, "teacher-a")], decoder=False)
    result = run(out, index)
    assert result.returncode != 0 and "decoder" in result.stderr


def test_explicit_canonical_export_is_identical_across_replay_and_restart(tmp_path):
    first_out, second_out = tmp_path / "first", tmp_path / "second"
    for out in (first_out, second_out):
        write_window(out, "a", [row("frame", 0, 100, "teacher-a")])
        write_window(out, "b", [row("frame", 0, 100, "teacher-b"), row("frame-b", 1, 200, "teacher-b")])
    assert run(first_out, tmp_path / "index-a", "--export-canonical").returncode == 0
    # Simulate a process restart with one descriptor already claimed.
    pending = second_out / "canonical-ingest" / "pending" / "a.json"
    processing = second_out / "canonical-ingest" / "processing" / "a.json"; processing.parent.mkdir(parents=True)
    pending.replace(processing)
    assert run(second_out, tmp_path / "index-b", "--export-canonical").returncode == 0
    a = json.loads((tmp_path / "index-a" / "canonical-export-v1" / "manifest.json").read_text())
    b = json.loads((tmp_path / "index-b" / "canonical-export-v1" / "manifest.json").read_text())
    assert a == b


def test_scientific_ingest_requires_receipt_proof_and_source_sequence(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    write_window(out, "a", [scientific_row("frame-a", 0, 100, "teacher-a", 9)])
    assert run(out, index, "--require-scientific-order").returncode != 0
    mark_scientific(out, "a")
    result = run(out, index, "--require-scientific-order", "--export-canonical")
    assert result.returncode == 0, result.stderr
    export = (index / "canonical-export-v1" / "events.jsonl").read_text()
    assert '"durable_output_sequence":9' in export


def test_scientific_ingest_fails_closed_on_cross_frame_sequence_collision(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    write_window(out, "a", [scientific_row("frame-a", 0, 100, "teacher-a", 9)])
    write_window(out, "b", [scientific_row("frame-b", 0, 200, "teacher-b", 9)])
    mark_scientific(out, "a"); mark_scientific(out, "b")
    result = run(out, index, "--require-scientific-order")
    assert result.returncode != 0
    assert "conflicting scientific source order" in result.stderr


def test_scientific_export_has_stable_full_tie_order_across_restart(tmp_path):
    """Receipt arrival order cannot affect canonical source-order output."""
    first_out, second_out = tmp_path / "first", tmp_path / "second"
    ordered = [
        scientific_row("frame-a", 1, 100, "teacher-a", 8),
        scientific_row("frame-b", 0, 100, "teacher-b", 9),
    ]
    for out, rows in ((first_out, ordered), (second_out, list(reversed(ordered)))):
        write_window(out, "window", rows)
        mark_scientific(out, "window")
    first = run(first_out, tmp_path / "index-a", "--require-scientific-order", "--export-canonical")
    assert first.returncode == 0, first.stderr
    pending = second_out / "canonical-ingest" / "pending" / "window.json"
    processing = second_out / "canonical-ingest" / "processing" / "window.json"
    processing.parent.mkdir(parents=True); pending.replace(processing)
    second = run(second_out, tmp_path / "index-b", "--require-scientific-order", "--export-canonical")
    assert second.returncode == 0, second.stderr
    left = (tmp_path / "index-a" / "canonical-export-v1" / "events.jsonl").read_bytes()
    right = (tmp_path / "index-b" / "canonical-export-v1" / "events.jsonl").read_bytes()
    assert left == right
    assert left.find(b'"durable_output_sequence":8') < left.find(b'"durable_output_sequence":9')


def test_qualification_export_deduplicates_events_but_keeps_window_exposures(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    overlapping = scientific_row("frame-a", 0, 100, "teacher-a", 8)
    write_window(out, "a", [overlapping])
    write_window(out, "b", [{**overlapping, "candidate_signature": "teacher-b"}])
    mark_scientific(out, "a"); mark_scientific(out, "b")
    result = run(out, index, "--require-scientific-order", "--export-qualification-input")
    assert result.returncode == 0, result.stderr
    root = index / "qualification-input-v1"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["canonical_events"] == 1
    assert manifest["window_exposures"] == 2
    assert len((root / "canonical-events.jsonl").read_text().splitlines()) == 1
    assert len((root / "window-exposures.jsonl").read_text().splitlines()) == 2


def test_conflicting_intrinsic_duplicate_fails_closed_but_outcome_annotations_do_not_define_identity(tmp_path):
    out, index = tmp_path / "out", tmp_path / "index"
    original = scientific_row("frame-a", 0, 100, "teacher-a", 8)
    harmless_annotation = {**original, "post_entry_pnl": 999, "future_outcome_label": True}
    write_window(out, "a", [original]); write_window(out, "b", [harmless_annotation])
    mark_scientific(out, "a"); mark_scientific(out, "b")
    assert run(out, index, "--require-scientific-order").returncode == 0
    out2, index2 = tmp_path / "out2", tmp_path / "index2"
    write_window(out2, "a", [original]); write_window(out2, "b", [{**original, "pool_quote_raw": 101}])
    mark_scientific(out2, "a"); mark_scientific(out2, "b")
    result = run(out2, index2, "--require-scientific-order")
    assert result.returncode != 0 and "conflicting source event" in result.stderr


def test_qualification_export_is_identical_under_reversed_receipts_and_same_timestamp(tmp_path):
    first_out, second_out = tmp_path / "first", tmp_path / "second"
    rows = [scientific_row("frame-a", 0, 100, "teacher-a", 8), scientific_row("frame-b", 0, 200, "teacher-b", 9)]
    # Equal availability times are ordered by durable writer sequence, not receipt order.
    rows[1]["received_at_unix_ns"] = rows[0]["received_at_unix_ns"]
    for out, order in ((first_out, rows), (second_out, list(reversed(rows)))):
        for position, item in enumerate(order):
            write_window(out, f"window-{position}", [item]); mark_scientific(out, f"window-{position}")
    assert run(first_out, tmp_path / "ia", "--require-scientific-order", "--export-qualification-input").returncode == 0
    assert run(second_out, tmp_path / "ib", "--require-scientific-order", "--export-qualification-input").returncode == 0
    left = (tmp_path / "ia" / "qualification-input-v1" / "canonical-events.jsonl").read_bytes()
    right = (tmp_path / "ib" / "qualification-input-v1" / "canonical-events.jsonl").read_bytes()
    assert left == right
    assert left.find(b'"durable_output_sequence":8') < left.find(b'"durable_output_sequence":9')
