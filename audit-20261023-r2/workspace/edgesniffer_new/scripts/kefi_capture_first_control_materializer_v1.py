#!/usr/bin/env python3
"""Materialize sealed capture-first windows into a verified control-read surface.

This is deliberately outcome-blind. It never scores, fits, or evaluates the
fingerprint; it verifies and decodes only windows that the collector already
sealed with a COMPLETE promotion receipt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


SCHEMA = "KEFI_CAPTURE_FIRST_CONTROL_MATERIALIZER_V1"
MIN_CACHE_FREE_GIB = 40


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def publish_incremental_ingest_descriptor(out: Path, receipt_path: Path, receipt: dict) -> None:
    """Publish a tiny, immutable work item after the window receipt is durable.

    The canonical ingester consumes this queue rather than globbing and
    re-hashing every historical receipt on every timer run.  It is deliberately
    published *after* the materializer receipt so a descriptor can never point
    at a half-written scientific artifact.
    """
    descriptor = {
        "schema": SCHEMA + "_INGEST_DESCRIPTOR_V1",
        "window_id": receipt["window_id"],
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256(receipt_path),
        "market_rows_sha256": receipt["market_rows_sha256"],
    }
    atomic_json(out / "canonical-ingest" / "pending" / f"{receipt['window_id']}.json", descriptor)


def source_partition_refs(window: Path) -> list[dict[str, str]]:
    """Bind a derived window to the immutable partition manifests it decoded.

    The source payload is deliberately not copied again.  The receipt records
    the manifest identity and the sealed compressed-object identity so a later
    consumer can verify the exact raw evidence independently.
    """
    references: list[dict[str, str]] = []
    partition_root = window / "partitions"
    for manifest_path in sorted(partition_root.glob("**/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        compressed_sha256 = str(manifest.get("compressedSha256", ""))
        if not compressed_sha256:
            raise RuntimeError(f"FAIL_CLOSED: partition lacks compressed hash {manifest_path}")
        evidence = manifest_path.parent / "evidence.frames.zst"
        if not evidence.is_file():
            raise RuntimeError(f"FAIL_CLOSED: partition evidence missing {manifest_path.parent}")
        references.append({
            "relative_manifest_path": manifest_path.relative_to(window).as_posix(),
            "manifest_sha256": sha256(manifest_path),
            "compressed_sha256": compressed_sha256,
        })
    if not references:
        raise RuntimeError(f"FAIL_CLOSED: no source partition manifests {window}")
    return references


def decoder_identity(decoder: Path) -> dict[str, str]:
    if not decoder.is_file():
        raise RuntimeError(f"FAIL_CLOSED: decoder missing {decoder}")
    return {"decoder_path": str(decoder), "decoder_sha256": sha256(decoder)}


def validate_scientific_rows(rows_path: Path, registry_script: Path, registry_root: Path,
                             boundary_utc: str) -> dict[str, int]:
    """Prove every row is ordered and eligible for a future scientific cohort.

    A receipt timestamp is an availability clock, not a total order.  The
    source-writer sequence is therefore mandatory here.  A duplicate sequence
    in an epoch is permitted only for different events in *one* source frame;
    any other collision is an evidence-order ambiguity and fails closed.
    """
    spec = importlib.util.spec_from_file_location("kefi_epochs", registry_script)
    if not spec or not spec.loader:
        raise RuntimeError("FAIL_CLOSED: cannot load epoch registry")
    epochs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(epochs)
    frame_orders: dict[tuple[str, int, int], str] = {}
    event_orders: set[tuple[str, int, int, int]] = set()
    rows = 0
    for line_number, line in enumerate(rows_path.read_text().splitlines(), start=1):
        try:
            row = json.loads(line)
            received_ns = int(row["received_at_unix_ns"])
            epoch = int(row["continuity_epoch"])
            sequence = int(row["durable_output_sequence"])
            event_index = int(row["event_index"])
            frame = str(row["source_frame_sha256"])
            instance = str(row["source_instance_id"])
            if epoch < 0 or sequence <= 0 or event_index < 0 or not frame or not instance:
                raise ValueError("invalid deterministic source order")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"FAIL_CLOSED: invalid scientific row {line_number}") from error
        source_key = (instance, epoch, sequence)
        previous = frame_orders.setdefault(source_key, frame)
        if previous != frame:
            raise RuntimeError(f"FAIL_CLOSED: source-order collision at row {line_number}")
        event_key = (instance, epoch, sequence, event_index)
        if event_key in event_orders:
            raise RuntimeError(f"FAIL_CLOSED: duplicate deterministic event order at row {line_number}")
        event_orders.add(event_key)
        timestamp = dt.datetime.fromtimestamp(
            received_ns / 1_000_000_000, tz=dt.timezone.utc
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        epochs.eligible(registry_root, timestamp, boundary_utc)
        rows += 1
    if not rows:
        raise RuntimeError("FAIL_CLOSED: scientific materialization emitted zero rows")
    return {"rows": rows, "source_frames": len(frame_orders), "ordered_events": len(event_orders)}


def verified_processed_receipt(out: Path, window_id: str) -> bool:
    """Verify a remembered cursor item before trusting it after a restart."""
    receipt_path = out / "receipts" / f"{window_id}.json"
    if not receipt_path.is_file():
        return False
    try:
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("window_id") != window_id:
            return False
        rows_path = Path(str(receipt["market_rows_path"]))
        expected_parent = (out / "market-rows").resolve()
        if expected_parent not in rows_path.resolve().parents:
            return False
        return rows_path.is_file() and sha256(rows_path) == receipt.get("market_rows_sha256")
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def complete_signatures(root: Path) -> set[str]:
    result: set[str] = set()
    for receipt in (root / "promotion-results").glob("*.json"):
        payload = json.loads(receipt.read_text())
        if payload.get("status") == "COMPLETE":
            result.add(str(payload["signature"]))
    return result


def windows(root: Path, completed: set[str]) -> list[tuple[str, Path, dict]]:
    result: list[tuple[str, Path, dict]] = []
    for manifest_path in sorted((root / "shared-windows").glob("*/manifest.json")):
        payload = json.loads(manifest_path.read_text())
        if payload.get("schema") != "TRACKED_WALLET_SHARED_BURST_WINDOW_V1":
            continue
        candidates = payload.get("candidates", [])
        signatures = {str(candidate.get("signature", "")) for candidate in candidates}
        if not signatures or not signatures.issubset(completed):
            continue
        window = manifest_path.parent
        if not list(window.glob("partitions/**/manifest.json")):
            continue
        result.append((window.name, window, payload))
    return result


def direct_window(root: Path, window_id: str) -> tuple[str, Path, dict]:
    """Verify one immutable window without traversing the full NFS archive."""
    window = root / "shared-windows" / window_id
    manifest_path = window / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"FAIL_CLOSED: missing window manifest {window_id}")
    payload = json.loads(manifest_path.read_text())
    if payload.get("schema") != "TRACKED_WALLET_SHARED_BURST_WINDOW_V1":
        raise RuntimeError(f"FAIL_CLOSED: invalid window schema {window_id}")
    candidates = payload.get("candidates", [])
    signatures = {str(candidate.get("signature", "")) for candidate in candidates}
    if not signatures:
        raise RuntimeError(f"FAIL_CLOSED: empty candidate signatures {window_id}")
    for signature in signatures:
        receipt_path = root / "promotion-results" / f"{signature}.json"
        if not receipt_path.is_file():
            raise RuntimeError(f"FAIL_CLOSED: missing promotion receipt {signature}")
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("status") != "COMPLETE" or receipt.get("signature") != signature:
            raise RuntimeError(f"FAIL_CLOSED: incomplete promotion receipt {signature}")
    if not list(window.glob("partitions/**/manifest.json")):
        raise RuntimeError(f"FAIL_CLOSED: no sealed partitions {window_id}")
    return window_id, window, payload


def cached_partition_root(window: Path, cache_root: Path, temporary_root: Path) -> Path:
    """Return a verified local read-only view of a sealed window's partitions."""
    source_root = window / "partitions"
    cached_root = temporary_root / "data" / "source-spool" / "partitions"
    for manifest_path in sorted(source_root.glob("**/manifest.json")):
        source_partition = manifest_path.parent
        manifest = json.loads(manifest_path.read_text())
        expected_hash = str(manifest.get("compressedSha256", ""))
        evidence = source_partition / "evidence.frames.zst"
        if not expected_hash or not evidence.is_file():
            raise RuntimeError(f"FAIL_CLOSED: invalid sealed partition {source_partition}")
        relative = source_partition.relative_to(source_root)
        cache_partition = cache_root / expected_hash
        receipt_path = cache_partition / "cache-receipt.json"
        if not receipt_path.is_file():
            if shutil.disk_usage(cache_root.parent).free < MIN_CACHE_FREE_GIB * (1 << 30):
                raise RuntimeError("FAIL_CLOSED: insufficient local cache headroom")
            cache_root.mkdir(parents=True, exist_ok=True)
            staging = cache_root / f".{expected_hash}.staging-{os.getpid()}"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            source_before = sha256(evidence)
            if source_before != expected_hash:
                raise RuntimeError(f"FAIL_CLOSED: source hash mismatch {source_partition}")
            shutil.copyfile(evidence, staging / "evidence.frames.zst")
            source_after = sha256(evidence)
            cached_hash = sha256(staging / "evidence.frames.zst")
            if source_after != expected_hash or cached_hash != expected_hash:
                shutil.rmtree(staging, ignore_errors=True)
                raise RuntimeError(f"FAIL_CLOSED: cache copy mismatch {source_partition}")
            shutil.copyfile(manifest_path, staging / "manifest.json")
            atomic_json(staging / "cache-receipt.json", {
                "schema": SCHEMA + "_LOCAL_CACHE_V1",
                "source_manifest_sha256": sha256(manifest_path),
                "compressed_sha256": expected_hash,
                "verified": True,
            })
            try:
                os.replace(staging, cache_partition)
            except FileExistsError:
                shutil.rmtree(staging, ignore_errors=True)
        receipt = json.loads(receipt_path.read_text())
        cached_evidence = cache_partition / "evidence.frames.zst"
        if (receipt.get("compressed_sha256") != expected_hash
                or sha256(cached_evidence) != expected_hash):
            raise RuntimeError(f"FAIL_CLOSED: corrupt local cache {cache_partition}")
        destination = cached_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(cache_partition, destination)
    return cached_root


def decode_window(decoder: Path, window_id: str, window: Path, output: Path, cache_root: Path | None) -> dict:
    partition_manifests = sorted(window.glob("partitions/**/manifest.json"))
    keys = [manifest.parent.relative_to(window / "partitions").as_posix() for manifest in partition_manifests]
    if not keys:
        raise RuntimeError(f"no sealed partitions in {window}")
    with tempfile.TemporaryDirectory(prefix="kefi-capture-first-control-") as temporary:
        temporary_root = Path(temporary)
        expected = temporary_root / "data" / "source-spool"
        expected.mkdir(parents=True)
        if cache_root:
            cached_partition_root(window, cache_root, temporary_root)
        else:
            os.symlink(window / "partitions", expected / "partitions")
        rows_tmp = output.with_suffix(".jsonl.tmp")
        summary_tmp = output.with_suffix(".summary.json.tmp")
        subprocess.run(
            [str(decoder), "--unified-market", str(temporary_root), str(rows_tmp), str(summary_tmp), *keys],
            check=True,
            capture_output=True,
            text=True,
        )
        summary = json.loads(summary_tmp.read_text())
        if summary.get("source_frames_scanned", 0) <= 0:
            raise RuntimeError(f"decoder emitted no source frames for {window_id}")
        os.replace(rows_tmp, output)
        os.replace(summary_tmp, output.with_suffix(".summary.json"))
    return summary


def build_deduplicated_market(out: Path) -> tuple[int, int, str]:
    """Merge overlapping sealed windows by canonical transaction/event identity.

    Overlap is expected because every teacher window includes pre-entry history.
    ``candidate_signature`` names the teacher window that exposed an event; it
    is not an identity for every event in that window.  The immutable source
    frame digest plus the frame-local event index is the actual event identity.
    A conflicting duplicate under that identity is evidence corruption, not an
    excuse to choose one.
    """
    database = out / ".market-dedup.sqlite.tmp"
    database.unlink(missing_ok=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute(
        "CREATE TABLE events ("
        "source_frame_sha256 TEXT NOT NULL, event_index INTEGER NOT NULL, canonical TEXT NOT NULL, "
        "received_at_unix_ns INTEGER NOT NULL, slot INTEGER NOT NULL, "
        "PRIMARY KEY(source_frame_sha256, event_index))"
    )
    duplicates = 0
    try:
        for rows_path in sorted((out / "market-rows").glob("*.jsonl")):
            with rows_path.open() as handle, connection:
                for line in handle:
                    row = json.loads(line)
                    source_frame_sha256 = str(row.get("source_frame_sha256", ""))
                    if not source_frame_sha256:
                        raise RuntimeError("FAIL_CLOSED: market row lacks source_frame_sha256")
                    event_index = int(row["event_index"])
                    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?)",
                        (source_frame_sha256, event_index, canonical, int(row["received_at_unix_ns"]), int(row["slot"])),
                    )
                    if cursor.rowcount == 0:
                        previous = connection.execute(
                            "SELECT canonical FROM events WHERE source_frame_sha256 = ? AND event_index = ?",
                            (source_frame_sha256, event_index),
                        ).fetchone()[0]
                        if previous != canonical:
                            raise RuntimeError(
                                f"FAIL_CLOSED: conflicting source-frame overlap for "
                                f"{source_frame_sha256}:{event_index}"
                            )
                        duplicates += 1
        count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        cursor = connection.execute(
            "SELECT canonical FROM events "
            "ORDER BY received_at_unix_ns, slot, event_index, source_frame_sha256"
        )
        path = out / "market-deduplicated.jsonl"
        temporary = path.with_suffix(".jsonl.tmp")
        with temporary.open("w") as handle:
            for (canonical,) in cursor:
                handle.write(canonical + "\n")
        os.replace(temporary, path)
    finally:
        connection.close()
        database.unlink(missing_ok=True)
    return count, duplicates, sha256(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--max-windows", type=int, default=0)
    parser.add_argument("--not-before-window-id", default="")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--skip-deduplicated-market", action="store_true")
    parser.add_argument("--rebuild-deduplicated-market", action="store_true")
    parser.add_argument("--window-id")
    parser.add_argument("--registry-script", type=Path)
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--boundary-utc")
    parser.add_argument("--scientific", action="store_true",
                        help="require registered epoch eligibility and durable source order")
    parser.add_argument("--list-eligible", action="store_true")
    args = parser.parse_args()
    cohort_args = (args.registry_script, args.registry_root, args.boundary_utc)
    if any(cohort_args) and not all(cohort_args):
        raise SystemExit("FAIL_CLOSED: registry-script, registry-root, and boundary-utc are required together")
    if args.scientific and not all(cohort_args):
        raise SystemExit("FAIL_CLOSED: scientific materialization requires registry-script, registry-root, and boundary-utc")

    if not args.rebuild_deduplicated_market and not args.decoder.is_file():
        raise SystemExit("FAIL_CLOSED: decoder missing")
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"processed": []}
    processed = set(state["processed"])
    if args.rebuild_deduplicated_market:
        unique_rows, duplicate_rows, deduplicated_sha256 = build_deduplicated_market(args.out)
        atomic_json(args.out / "checkpoint.json", {
            "schema": SCHEMA + "_CHECKPOINT_V1",
            "eligible_windows": None,
            "processed_windows": len(processed),
            "new_windows": 0,
            "deduplicated_market_rows": unique_rows,
            "overlap_duplicates_verified_identical": duplicate_rows,
            "deduplicated_market_sha256": deduplicated_sha256,
            "outcome_blind": True,
            "pnl_accessed": False,
        })
        print(json.dumps({"processed": len(processed), "new": 0, "rebuilt": True}, sort_keys=True))
        return
    if args.window_id:
        eligible = [direct_window(args.root, args.window_id)]
    else:
        completed = complete_signatures(args.root)
        eligible = windows(args.root, completed)
        if args.not_before_window_id:
            eligible = [entry for entry in eligible if entry[0] >= args.not_before_window_id]
    # A remembered ID is not proof that its output remains intact.  Revalidate
    # the durable receipt and content hash before reusing it after a restart.
    corrupt_processed = [entry[0] for entry in eligible if entry[0] in processed
                         and not verified_processed_receipt(args.out, entry[0])]
    if corrupt_processed:
        raise RuntimeError(
            "FAIL_CLOSED: processed window receipt/content verification failed: "
            + ",".join(corrupt_processed)
        )
    # Filter the durable cursor before applying the bounded-work limit.  The
    # previous ordering repeatedly selected the oldest already-processed
    # window, then performed zero useful work forever when --max-windows=1.
    eligible = [entry for entry in eligible if entry[0] not in processed]
    if args.list_eligible:
        print(json.dumps({"window_ids": [entry[0] for entry in eligible]}, sort_keys=True))
        return
    if args.max_windows:
        eligible = eligible[: args.max_windows]
    results: list[dict] = []
    for window_id, window, manifest in eligible:
        destination = args.out / "market-rows" / f"{window_id}.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            summary = decode_window(args.decoder, window_id, window, destination, args.cache_root)
            scientific_validation = None
            if args.scientific:
                scientific_validation = validate_scientific_rows(
                    destination, args.registry_script, args.registry_root, args.boundary_utc
                )
        except Exception:
            destination.unlink(missing_ok=True)
            destination.with_suffix(".summary.json").unlink(missing_ok=True)
            raise
        receipt = {
            "schema": SCHEMA + "_WINDOW_RECEIPT_V1",
            "window_id": window_id,
            "window_manifest_sha256": sha256(window / "manifest.json"),
            "candidate_signatures": sorted(str(c["signature"]) for c in manifest["candidates"]),
            "market_rows_path": str(destination),
            "market_rows_sha256": sha256(destination),
            "source_partition_refs": source_partition_refs(window),
            "decoder": decoder_identity(args.decoder),
            "decoder_summary": summary,
            "scientific_validation": scientific_validation,
            "outcome_blind": True,
            "pnl_accessed": False,
        }
        receipt_path = args.out / "receipts" / f"{window_id}.json"
        atomic_json(receipt_path, receipt)
        publish_incremental_ingest_descriptor(args.out, receipt_path, receipt)
        processed.add(window_id)
        state = {"schema": SCHEMA + "_STATE_V1", "processed": sorted(processed), "outcome_blind": True}
        atomic_json(state_path, state)
        results.append(receipt)
    if not args.skip_deduplicated_market:
        unique_rows, duplicate_rows, deduplicated_sha256 = build_deduplicated_market(args.out)
        atomic_json(args.out / "checkpoint.json", {
            "schema": SCHEMA + "_CHECKPOINT_V1",
            "eligible_windows": len(eligible),
            "processed_windows": len(processed),
            "new_windows": len(results),
            "deduplicated_market_rows": unique_rows,
            "overlap_duplicates_verified_identical": duplicate_rows,
            "deduplicated_market_sha256": deduplicated_sha256,
            "outcome_blind": True,
            "pnl_accessed": False,
        })
    print(json.dumps({"processed": len(processed), "new": len(results)}, sort_keys=True))


if __name__ == "__main__":
    main()
