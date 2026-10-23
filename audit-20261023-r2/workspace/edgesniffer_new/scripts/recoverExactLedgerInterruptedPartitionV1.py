#!/usr/bin/env python3
"""Fail-closed recovery for a ledger partition interrupted during compression."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import struct
import subprocess
from typing import Any, BinaryIO


MAGIC = b"PEL1FRM\0"
MAX_FRAME_BYTES = 128 * 1024 * 1024
BOUNDARY_TOLERANCE_NS = 5_000_000_000


def iso_ns(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1_000_000_000, dt.timezone.utc).isoformat()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_exact(stream: BinaryIO, size: int, description: str) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise RuntimeError(f"TRUNCATED_{description}:{len(value)}/{size}")
    return value


def empty_coverage() -> dict[str, int]:
    return {
        "records": 0,
        "relevantTransactionsObserved": 0,
        "provisionalTransactions": 0,
        "provisionalUnresolved": 0,
        "finalized": 0,
        "successful": 0,
        "failed": 0,
        "unresolved": 0,
        "missingFinality": 0,
        "missingRawTx": 0,
        "missingTradeEvent": 0,
        "missingReserveTransition": 0,
        "missingCanonicalOrdering": 0,
        "missingFeeEvidence": 0,
        "decodeFailures": 0,
        "accountUpdates": 0,
        "blockMetaUpdates": 0,
        "slotUpdates": 0,
        "rpcSnapshots": 0,
        "rpcFinalizedTransactions": 0,
        "rpcFinalizedBlocks": 0,
        "duplicatesSuppressed": 0,
    }


def observe(coverage: dict[str, int], metadata: dict[str, Any]) -> None:
    coverage["records"] += 1
    kind = str(metadata.get("kind") or "")
    kind_key = {
        "transaction": "relevantTransactionsObserved",
        "account": "accountUpdates",
        "block_meta": "blockMetaUpdates",
        "slot": "slotUpdates",
        "rpc_account_snapshot": "rpcSnapshots",
        "rpc_finalized_transaction": "rpcFinalizedTransactions",
        "rpc_finalized_block": "rpcFinalizedBlocks",
    }.get(kind)
    if kind_key:
        coverage[kind_key] += 1
    if kind == "transaction" and not bool(metadata.get("finalized")):
        coverage["provisionalTransactions"] += 1
        coverage["provisionalUnresolved"] += int(bool(metadata.get("unresolved")))
    authoritative = (
        (kind == "transaction" and bool(metadata.get("finalized")))
        or kind == "rpc_finalized_transaction"
    )
    if authoritative:
        coverage["finalized"] += int(bool(metadata.get("finalized")))
        coverage["successful"] += int(metadata.get("successful") is True)
        coverage["failed"] += int(metadata.get("failed") is True)
        coverage["unresolved"] += int(bool(metadata.get("unresolved")))
        coverage["missingFinality"] += int(not bool(metadata.get("finalized")))
        coverage["missingRawTx"] += int(bool(metadata.get("missingRawTx")))
        coverage["missingTradeEvent"] += int(bool(metadata.get("missingTradeEvent")))
        coverage["missingReserveTransition"] += int(
            bool(metadata.get("missingReserveTransition"))
        )
        coverage["missingCanonicalOrdering"] += int(
            bool(metadata.get("missingCanonicalOrdering"))
        )
        coverage["missingFeeEvidence"] += int(bool(metadata.get("missingFeeEvidence")))
    coverage["decodeFailures"] += int(bool(metadata.get("decodeFailed")))


def scan_frames(path: pathlib.Path) -> dict[str, Any]:
    coverage = empty_coverage()
    first_ns: int | None = None
    last_ns: int | None = None
    start_slot: int | None = None
    end_slot: int | None = None
    with path.open("rb") as stream:
        if read_exact(stream, len(MAGIC), "FRAME_MAGIC") != MAGIC:
            raise RuntimeError("FRAME_MAGIC_MISMATCH")
        while True:
            prefix = stream.read(8)
            if not prefix:
                break
            if prefix == MAGIC:
                continue
            if len(prefix) != 8:
                raise RuntimeError(f"TRUNCATED_FRAME_PREFIX:{len(prefix)}/8")
            metadata_len, raw_len = struct.unpack("<II", prefix)
            if metadata_len + raw_len > MAX_FRAME_BYTES:
                raise RuntimeError("UNSAFE_FRAME_LENGTH")
            body = read_exact(stream, metadata_len + raw_len, "FRAME_BODY")
            checksum = read_exact(stream, 32, "FRAME_CHECKSUM")
            if hashlib.sha256(body).digest() != checksum:
                raise RuntimeError("FRAME_CHECKSUM_MISMATCH")
            metadata = json.loads(body[:metadata_len])
            observe(coverage, metadata)
            received_ns = metadata.get("receivedAtUnixNs")
            if isinstance(received_ns, int):
                first_ns = received_ns if first_ns is None else min(first_ns, received_ns)
                last_ns = received_ns if last_ns is None else max(last_ns, received_ns)
            slot = metadata.get("slot")
            if isinstance(slot, int):
                start_slot = slot if start_slot is None else min(start_slot, slot)
                end_slot = slot if end_slot is None else max(end_slot, slot)
    return {
        "coverage": coverage,
        "firstReceivedAtUnixNs": first_ns,
        "lastReceivedAtUnixNs": last_ns,
        "firstReceivedAtUtc": iso_ns(first_ns) if first_ns is not None else None,
        "lastReceivedAtUtc": iso_ns(last_ns) if last_ns is not None else None,
        "startSlot": start_slot,
        "endSlot": end_slot,
    }


def partition_bounds(key: str) -> tuple[int, int]:
    start = dt.datetime.strptime(key, "%Y/%m/%d/%H").replace(tzinfo=dt.timezone.utc)
    start_ns = int(start.timestamp() * 1_000_000_000)
    return start_ns, start_ns + 3_600_000_000_000


def decompress_identity(path: pathlib.Path) -> tuple[int, str]:
    process = subprocess.Popen(["zstd", "-q", "-dc", str(path)], stdout=subprocess.PIPE)
    assert process.stdout is not None
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    process.stdout.close()
    if process.wait() != 0:
        raise RuntimeError("COMPRESSED_STREAM_VERIFICATION_FAILED")
    return size, digest.hexdigest()


def writer_pids(path: pathlib.Path) -> list[str]:
    result = subprocess.run(
        ["lsof", "-t", "--", str(path)], capture_output=True, text=True, check=False
    )
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})


def audit(root: pathlib.Path, key: str) -> dict[str, Any]:
    directory = root / "partitions" / key
    partial = directory / "evidence.frames.partial"
    interrupted = directory / "evidence.frames.zst.tmp"
    final = directory / "evidence.frames.zst"
    manifest = directory / "manifest.json"
    if not partial.is_file():
        raise RuntimeError("RAW_PARTIAL_MISSING")
    if final.exists() or manifest.exists():
        raise RuntimeError("PARTITION_ALREADY_FINALIZED")
    pids = writer_pids(partial)
    if pids:
        raise RuntimeError(f"ACTIVE_WRITER_PRESENT:{','.join(pids)}")
    scanned = scan_frames(partial)
    start_ns, end_ns = partition_bounds(key)
    first_ns = scanned["firstReceivedAtUnixNs"]
    last_ns = scanned["lastReceivedAtUnixNs"]
    start_covered = bool(
        first_ns is not None and start_ns <= first_ns <= start_ns + BOUNDARY_TOLERANCE_NS
    )
    end_covered = bool(
        last_ns is not None and end_ns - BOUNDARY_TOLERANCE_NS <= last_ns < end_ns
    )
    result = {
        "schema": "EXACT_LEDGER_INTERRUPTED_PARTITION_RECOVERY_V1_AUDIT",
        "partitionKey": key,
        "partialPath": str(partial),
        "partialBytes": partial.stat().st_size,
        "partialSha256": sha256_file(partial),
        "interruptedCompressedPresent": interrupted.is_file(),
        "interruptedCompressedBytes": interrupted.stat().st_size if interrupted.exists() else 0,
        "activeWriterPids": pids,
        "strictFrameScanPass": True,
        "startBoundaryCovered": start_covered,
        "endBoundaryCovered": end_covered,
        "recoverable": bool(interrupted.is_file() and start_covered and end_covered),
        **scanned,
    }
    return result


def apply_recovery(root: pathlib.Path, key: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("recoverable") is not True:
        raise RuntimeError("PARTITION_NOT_PROVEN_RECOVERABLE")
    directory = root / "partitions" / key
    partial = directory / "evidence.frames.partial"
    interrupted = directory / "evidence.frames.zst.tmp"
    compressed_tmp = directory / "evidence.frames.zst.recovery.tmp"
    compressed = directory / "evidence.frames.zst"
    manifest = directory / "manifest.json"
    subprocess.run(
        ["nice", "-n", "19", "ionice", "-c", "3", "zstd", "-q", "-9", "-f",
         str(partial), "-o", str(compressed_tmp)],
        check=True,
    )
    decoded_size, decoded_sha = decompress_identity(compressed_tmp)
    if decoded_size != result["partialBytes"] or decoded_sha != result["partialSha256"]:
        raise RuntimeError("RECOMPRESSED_IDENTITY_MISMATCH")
    compressed_sha = sha256_file(compressed_tmp)
    schema_path = root / "schema-v1.json"
    schema_sha = sha256_file(schema_path)
    raw_mtime = dt.datetime.fromtimestamp(
        partial.stat().st_mtime, dt.timezone.utc
    ).isoformat()
    document = {
        "schemaVersion": "PUMP_EXACT_EVIDENCE_LEDGER_V1",
        "schemaSha256": schema_sha,
        "partitionKey": key,
        "completeUtcHour": False,
        "temporalHourClosed": True,
        "exactReplayComplete": False,
        "reconciliationManifest": "reconciliation-manifest.json",
        "openedAtUtc": result["firstReceivedAtUtc"],
        "closedAtUtc": raw_mtime,
        "startSlot": result["startSlot"],
        "endSlot": result["endSlot"],
        "rawFrameBytes": result["partialBytes"],
        "compressedBytes": compressed_tmp.stat().st_size,
        "rawSha256": result["partialSha256"],
        "compressedSha256": compressed_sha,
        "coverage": result["coverage"],
    }
    manifest_tmp = directory / "manifest.json.recovery.tmp"
    manifest_tmp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(compressed_tmp, compressed)
    os.replace(manifest_tmp, manifest)
    interrupted.unlink(missing_ok=True)
    return {
        **result,
        "recovered": True,
        "compressedPath": str(compressed),
        "compressedBytes": compressed.stat().st_size,
        "compressedSha256": compressed_sha,
        "manifestPath": str(manifest),
        "manifestSha256": sha256_file(manifest),
        "decodedBytes": decoded_size,
        "decodedSha256": decoded_sha,
    }


def write_report(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = audit(args.root, args.partition)
    if args.apply:
        result = apply_recovery(args.root, args.partition, result)
    write_report(args.report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
