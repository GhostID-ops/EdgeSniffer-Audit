#!/usr/bin/env python3
"""Crash-recoverable, queue-driven canonical kEfi event ingestion.

Normal operation consumes only descriptors published by the materializer after
its receipt is durable.  Historical receipts are never globbed or re-hashed on
the hot path.  A descriptor is claimed atomically, its receipt and rows are
verified once, and SQLite commits the event plus exposure mapping together.
After a crash, an item left in ``processing`` is safely replayed: the existing
window binding must match byte-for-byte or ingestion fails closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path


SCHEMA = "KEFI_INCREMENTAL_EVENT_INDEX_V2"
WINDOW_SCOPED_FIELDS = {
    "candidate_signature", "candidate_signatures", "candidate_index",
    "window_id", "windowId", "window_path", "windowPath",
}
# These are derived/outcome annotations, never source evidence.  They cannot
# affect canonical identity, frozen fingerprint membership, or control choice.
OUTCOME_DERIVED_FIELDS = {
    "post_entry_pnl", "future_outcome_label", "later_mark", "hypothetical_exit",
    "actionability", "target_complete_actionable", "target_outcome_reason",
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    # Windows does not permit opening a directory this way.  Production is
    # Linux and fsyncs directory metadata; local regression tests retain the
    # same atomic rename semantics without pretending Windows supports it.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w") as handle:
        handle.write(canonical(payload) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def connection_for(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS ingested_windows ("
        "window_id TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL, rows_sha256 TEXT NOT NULL, "
        "source_refs_canonical TEXT NOT NULL, decoder_sha256 TEXT NOT NULL, ingested_at_utc TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "source_frame_sha256 TEXT NOT NULL, event_index INTEGER NOT NULL, intrinsic_canonical TEXT NOT NULL, "
        "received_at_unix_ns INTEGER NOT NULL, slot INTEGER NOT NULL, "
        "continuity_epoch INTEGER, durable_output_sequence INTEGER, source_instance_id TEXT, "
        "PRIMARY KEY(source_frame_sha256, event_index))"
    )
    # Existing operational indexes remain readable.  A future scientific index
    # is created with these columns from day one, while an interrupted upgrade
    # gets a deterministic, idempotent schema migration.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    if "continuity_epoch" not in columns:
        connection.execute("ALTER TABLE events ADD COLUMN continuity_epoch INTEGER")
    if "durable_output_sequence" not in columns:
        connection.execute("ALTER TABLE events ADD COLUMN durable_output_sequence INTEGER")
    if "source_instance_id" not in columns:
        connection.execute("ALTER TABLE events ADD COLUMN source_instance_id TEXT")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_order ("
        "continuity_epoch INTEGER NOT NULL, durable_output_sequence INTEGER NOT NULL, "
        "source_frame_sha256 TEXT NOT NULL, "
        "PRIMARY KEY(continuity_epoch, durable_output_sequence))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_order_v2 ("
        "source_instance_id TEXT NOT NULL, continuity_epoch INTEGER NOT NULL, durable_output_sequence INTEGER NOT NULL, "
        "source_frame_sha256 TEXT NOT NULL, "
        "PRIMARY KEY(source_instance_id, continuity_epoch, durable_output_sequence))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS window_events ("
        "window_id TEXT NOT NULL, source_frame_sha256 TEXT NOT NULL, event_index INTEGER NOT NULL, "
        "exposure_canonical TEXT NOT NULL, PRIMARY KEY(window_id, source_frame_sha256, event_index), "
        "FOREIGN KEY(window_id) REFERENCES ingested_windows(window_id))"
    )
    return connection


def queue_paths(queue_root: Path) -> tuple[Path, Path, Path]:
    return queue_root / "pending", queue_root / "processing", queue_root / "processed"


def claim_descriptors(queue_root: Path) -> list[Path]:
    pending, processing, _ = queue_paths(queue_root)
    pending.mkdir(parents=True, exist_ok=True)
    processing.mkdir(parents=True, exist_ok=True)
    claimed = sorted(processing.glob("*.json"))  # recover a prior interrupted claim first
    for path in sorted(pending.glob("*.json")):
        claimed_path = processing / path.name
        try:
            os.replace(path, claimed_path)
            claimed.append(claimed_path)
        except FileNotFoundError:
            continue
    fsync_directory(pending)
    fsync_directory(processing)
    return claimed


def validate_descriptor(out: Path, descriptor_path: Path) -> tuple[dict, dict, Path, str, str]:
    descriptor = json.loads(descriptor_path.read_text())
    if descriptor.get("schema") != "KEFI_CAPTURE_FIRST_CONTROL_MATERIALIZER_V1_INGEST_DESCRIPTOR_V1":
        raise RuntimeError(f"FAIL_CLOSED: invalid ingest descriptor {descriptor_path}")
    window_id = str(descriptor.get("window_id", ""))
    receipt_path = Path(str(descriptor.get("receipt_path", "")))
    receipt_root = (out / "receipts").resolve()
    if not window_id or receipt_path.resolve().parent != receipt_root:
        raise RuntimeError(f"FAIL_CLOSED: descriptor receipt path escapes output root {descriptor_path}")
    if receipt_path.name != f"{window_id}.json" or not receipt_path.is_file():
        raise RuntimeError(f"FAIL_CLOSED: descriptor receipt absent {window_id}")
    receipt_sha256 = sha256(receipt_path)
    if receipt_sha256 != descriptor.get("receipt_sha256"):
        raise RuntimeError(f"FAIL_CLOSED: descriptor receipt hash mismatch {window_id}")
    receipt = json.loads(receipt_path.read_text())
    source_refs = receipt.get("source_partition_refs")
    decoder = receipt.get("decoder")
    if receipt.get("window_id") != window_id or not isinstance(source_refs, list) or not source_refs:
        raise RuntimeError(f"FAIL_CLOSED: invalid source receipt {window_id}")
    if not isinstance(decoder, dict) or not decoder.get("decoder_sha256"):
        raise RuntimeError(f"FAIL_CLOSED: source receipt lacks decoder identity {window_id}")
    rows_path = Path(str(receipt.get("market_rows_path", "")))
    rows_root = (out / "market-rows").resolve()
    if rows_path.resolve().parent != rows_root or not rows_path.is_file():
        raise RuntimeError(f"FAIL_CLOSED: source rows missing or outside output root {window_id}")
    rows_sha256 = sha256(rows_path)
    if rows_sha256 != receipt.get("market_rows_sha256") or rows_sha256 != descriptor.get("market_rows_sha256"):
        raise RuntimeError(f"FAIL_CLOSED: source rows hash mismatch {window_id}")
    return descriptor, receipt, rows_path, receipt_sha256, rows_sha256


def intrinsic_event(row: dict) -> dict:
    return {key: value for key, value in row.items()
            if key not in WINDOW_SCOPED_FIELDS and key not in OUTCOME_DERIVED_FIELDS}


def scientific_order(row: dict, window_id: str, line_number: int) -> tuple[str, int, int, int, str]:
    try:
        epoch = int(row["continuity_epoch"])
        sequence = int(row["durable_output_sequence"])
        event_index = int(row["event_index"])
        frame = str(row["source_frame_sha256"])
        instance = str(row["source_instance_id"])
        if epoch < 0 or sequence <= 0 or event_index < 0 or not frame or not instance:
            raise ValueError("invalid source order")
        return instance, epoch, sequence, event_index, frame
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"FAIL_CLOSED: invalid scientific source order {window_id}:{line_number}") from error


def ingest(connection: sqlite3.Connection, receipt: dict, rows_path: Path,
           receipt_sha256: str, rows_sha256: str, *, require_scientific_order: bool) -> tuple[int, int]:
    window_id = receipt["window_id"]
    if require_scientific_order:
        validation = receipt.get("scientific_validation")
        if not isinstance(validation, dict) or int(validation.get("rows", 0)) <= 0:
            raise RuntimeError(f"FAIL_CLOSED: receipt lacks scientific eligibility proof {window_id}")
    source_refs = canonical(receipt["source_partition_refs"])
    decoder_sha256 = str(receipt["decoder"]["decoder_sha256"])
    existing = connection.execute(
        "SELECT receipt_sha256, rows_sha256, source_refs_canonical, decoder_sha256 "
        "FROM ingested_windows WHERE window_id = ?", (window_id,)
    ).fetchone()
    expected = (receipt_sha256, rows_sha256, source_refs, decoder_sha256)
    if existing:
        if tuple(existing) != expected:
            raise RuntimeError(f"FAIL_CLOSED: previously ingested window changed {window_id}")
        return 0, 0
    inserted = duplicates = 0
    connection.execute(
        "INSERT INTO ingested_windows VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (window_id, *expected),
    )
    with rows_path.open() as handle:
        for line_number, line in enumerate(handle, start=1):
            row = json.loads(line)
            frame = str(row.get("source_frame_sha256", ""))
            if not frame:
                raise RuntimeError(f"FAIL_CLOSED: missing source frame identity {window_id}:{line_number}")
            event_index = int(row["event_index"])
            instance = None
            epoch = sequence = None
            if require_scientific_order:
                instance, epoch, sequence, ordered_event_index, ordered_frame = scientific_order(row, window_id, line_number)
                if ordered_event_index != event_index or ordered_frame != frame:
                    raise RuntimeError(f"FAIL_CLOSED: inconsistent event identity {window_id}:{line_number}")
                known = connection.execute(
                    "SELECT source_frame_sha256 FROM source_order_v2 WHERE source_instance_id = ? AND continuity_epoch = ? AND durable_output_sequence = ?",
                    (instance, epoch, sequence),
                ).fetchone()
                if known and known[0] != frame:
                    raise RuntimeError(f"FAIL_CLOSED: conflicting scientific source order {window_id}:{line_number}")
                connection.execute("INSERT OR IGNORE INTO source_order_v2 VALUES (?, ?, ?, ?)",
                                   (instance, epoch, sequence, frame))
            intrinsic = canonical(intrinsic_event(row))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO events (source_frame_sha256,event_index,intrinsic_canonical,received_at_unix_ns,slot,continuity_epoch,durable_output_sequence,source_instance_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (frame, event_index, intrinsic, int(row["received_at_unix_ns"]), int(row["slot"]), epoch, sequence, instance if require_scientific_order else None),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                previous = connection.execute(
                    "SELECT intrinsic_canonical FROM events WHERE source_frame_sha256 = ? AND event_index = ?",
                    (frame, event_index),
                ).fetchone()[0]
                if previous != intrinsic:
                    raise RuntimeError(f"FAIL_CLOSED: conflicting source event {frame}:{event_index}")
                duplicates += 1
            exposure = canonical({key: row[key] for key in sorted(WINDOW_SCOPED_FIELDS & row.keys())})
            connection.execute(
                "INSERT OR IGNORE INTO window_events VALUES (?, ?, ?, ?)",
                (window_id, frame, event_index, exposure),
            )
    return inserted, duplicates


def checkpoint(connection: sqlite3.Connection, database: Path, index_root: Path, new: int, duplicates: int) -> tuple[int, int]:
    events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    windows = connection.execute("SELECT COUNT(*) FROM ingested_windows").fetchone()[0]
    atomic_json(index_root / "checkpoint.json", {
        "schema": SCHEMA + "_CHECKPOINT_V1",
        "index_database_sha256": sha256(database),
        "ingested_windows": windows,
        "canonical_events": events,
        "new_events": new,
        "verified_identical_overlaps": duplicates,
        "outcome_blind": True,
        "pnl_accessed": False,
    })
    return events, windows


def export_canonical(connection: sqlite3.Connection, index_root: Path) -> dict:
    """Explicit deterministic export for equivalence/restart certification.

    This is intentionally not part of the timer path: it walks the index by
    design and is therefore a bounded certification operation, not ingestion.
    """
    export_root = index_root / "canonical-export-v1"
    export_root.mkdir(parents=True, exist_ok=True)
    events_path = export_root / "events.jsonl"
    exposures_path = export_root / "window-exposures.jsonl"
    for path, query in [
        (events_path, "SELECT source_frame_sha256,event_index,intrinsic_canonical,received_at_unix_ns,slot,continuity_epoch,durable_output_sequence,source_instance_id FROM events ORDER BY CASE WHEN source_instance_id IS NULL THEN 1 ELSE 0 END,source_instance_id,continuity_epoch,durable_output_sequence,event_index,source_frame_sha256"),
        (exposures_path, "SELECT window_id,source_frame_sha256,event_index,exposure_canonical FROM window_events ORDER BY window_id,source_frame_sha256,event_index"),
    ]:
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w") as handle:
            for row in connection.execute(query):
                keys = (("source_frame_sha256", "event_index", "intrinsic_canonical", "received_at_unix_ns", "slot", "continuity_epoch", "durable_output_sequence", "source_instance_id")
                        if path == events_path else ("window_id", "source_frame_sha256", "event_index", "exposure_canonical"))
                handle.write(canonical(dict(zip(keys, row))) + "\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    manifest = {
        "schema": SCHEMA + "_CANONICAL_EXPORT_V1",
        "events_sha256": sha256(events_path), "window_exposures_sha256": sha256(exposures_path),
        "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "window_exposures": connection.execute("SELECT COUNT(*) FROM window_events").fetchone()[0],
    }
    atomic_json(export_root / "manifest.json", manifest)
    return manifest


def export_qualification_input(connection: sqlite3.Connection, index_root: Path) -> dict:
    """Publish the sole permitted multi-window qualification input surface.

    ``events`` is the intrinsic-event authority: one event per frame/index.
    ``window_events`` is deliberately exported separately as multiplicity
    provenance.  A downstream scientific builder must never concatenate
    window JSONL files and decide for itself which duplicate is legitimate.
    """
    root = index_root / "qualification-input-v1"
    root.mkdir(parents=True, exist_ok=True)
    events_path, provenance_path = root / "canonical-events.jsonl", root / "window-exposures.jsonl"
    temporary = events_path.with_name(f".{events_path.name}.tmp")
    event_count = 0
    with temporary.open("w") as handle:
        query = (
            "SELECT source_frame_sha256,event_index,intrinsic_canonical,received_at_unix_ns,slot,"
            "continuity_epoch,durable_output_sequence,source_instance_id FROM events "
            "ORDER BY source_instance_id,continuity_epoch,durable_output_sequence,event_index,source_frame_sha256"
        )
        for frame, event_index, intrinsic, received, slot, epoch, sequence, instance in connection.execute(query):
            row = json.loads(intrinsic)
            # The intrinsic payload remains the scientific row, but exported
            # index columns must agree with it exactly rather than silently
            # winning a conflict.
            expected = {"source_frame_sha256": frame, "event_index": event_index,
                        "received_at_unix_ns": received, "slot": slot,
                        "continuity_epoch": epoch, "durable_output_sequence": sequence,
                        "source_instance_id": instance}
            if any(row.get(key) != value for key, value in expected.items()):
                raise RuntimeError("FAIL_CLOSED: intrinsic/index provenance mismatch")
            handle.write(canonical(row) + "\n"); event_count += 1
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, events_path)
    temporary = provenance_path.with_name(f".{provenance_path.name}.tmp")
    exposure_count = 0
    with temporary.open("w") as handle:
        query = (
            "SELECT w.source_frame_sha256,w.event_index,w.window_id,w.exposure_canonical,"
            "i.receipt_sha256,i.rows_sha256,i.decoder_sha256,i.source_refs_canonical "
            "FROM window_events w JOIN ingested_windows i ON i.window_id=w.window_id "
            "ORDER BY w.source_frame_sha256,w.event_index,w.window_id"
        )
        for frame, event_index, window_id, exposure, receipt_hash, rows_hash, decoder_hash, refs in connection.execute(query):
            handle.write(canonical({"source_frame_sha256": frame, "event_index": event_index,
                                    "window_id": window_id, "exposure": json.loads(exposure),
                                    "receipt_sha256": receipt_hash, "market_rows_sha256": rows_hash,
                                    "decoder_sha256": decoder_hash, "source_partition_refs": json.loads(refs)}) + "\n")
            exposure_count += 1
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, provenance_path)
    manifest = {
        "schema": SCHEMA + "_QUALIFICATION_INPUT_V1",
        "canonical_events_path": str(events_path), "canonical_events_sha256": sha256(events_path),
        "window_exposures_path": str(provenance_path), "window_exposures_sha256": sha256(provenance_path),
        "canonical_events": event_count, "window_exposures": exposure_count,
        "index_database_sha256": sha256(index_root / "market-events-v2.sqlite"),
        "input_policy": "canonical intrinsic events exactly once; window multiplicity is provenance only",
        "outcome_blind": True, "pnl_accessed": False,
    }
    atomic_json(root / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materializer-out", type=Path, required=True)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--queue-root", type=Path)
    parser.add_argument("--export-canonical", action="store_true")
    parser.add_argument("--export-qualification-input", action="store_true")
    parser.add_argument("--require-scientific-order", action="store_true")
    args = parser.parse_args()
    queue_root = args.queue_root or args.materializer_out / "canonical-ingest"
    database = args.index_root / "market-events-v2.sqlite"
    connection = connection_for(database)
    new = duplicates = 0
    try:
        for descriptor_path in claim_descriptors(queue_root):
            _, receipt, rows, receipt_hash, rows_hash = validate_descriptor(args.materializer_out, descriptor_path)
            with connection:
                added, overlap = ingest(connection, receipt, rows, receipt_hash, rows_hash,
                                        require_scientific_order=args.require_scientific_order)
            new += added; duplicates += overlap
            _, processing, processed = queue_paths(queue_root)
            processed.mkdir(parents=True, exist_ok=True)
            os.replace(descriptor_path, processed / descriptor_path.name)
            fsync_directory(processing); fsync_directory(processed)
        events, _ = checkpoint(connection, database, args.index_root, new, duplicates)
        exported = export_canonical(connection, args.index_root) if args.export_canonical else None
        qualification_export = export_qualification_input(connection, args.index_root) if args.export_qualification_input else None
    finally:
        connection.close()
    print(json.dumps({"canonical_events": events, "new_events": new, "verified_identical_overlaps": duplicates,
                      "canonical_export": exported, "qualification_input": qualification_export}, sort_keys=True))


if __name__ == "__main__":
    main()
