#!/usr/bin/env python3
"""Future-only, append-only realtime provenance registry for SOL-Pump CREATEs.

This observer deliberately has no dependency on the 2CQ symmetric-choice
materializer.  It preserves a small, auditable CREATE-only arrival record and
uses the existing finalized exact ledger solely to prove canonical mint/order.
It reads neither outcomes nor PnL and cannot admit a row to any cohort.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import struct
import sys
from typing import Any, Iterable

from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqChoiceSetCoverageRepairV1 import canonical_create_mints
from twoCqAllenharkArrivalClassifierV1 import MAGIC as ARRIVAL_MAGIC, RECORD as ARRIVAL_RECORD


SCHEMA = "2CQ_SOL_PUMP_CREATE_PROVENANCE_REGISTRY_V1"
SOURCE = "allenhark-jito-shreds-grpc-v1"
YELLOWSTONE_SOURCE = "yellowstone-pump-transactions-v1"
YELLOWSTONE_RAW_FORMAT = "yellowstone_subscribe_update_protobuf"
YELLOWSTONE_PROJECTION_VERSION = "CREATE_PROVENANCE_YELLOWSTONE_V3_CROSS_PARTITION_ATTACH"
RECOVERY_ADAPTER_VERSION = "CANONICAL_RECOVERY_TO_CREATE_REGISTRY_ADAPTER_V1"
RECOVERY_ADAPTER_PROJECTION_VERSION = "V1_EXISTING_EXACT_SOURCE_RECEIPT_PRESERVED"
CREATE_KINDS = frozenset({"CREATE", "CREATE_V2"})


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_utc_ns(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000_000)


def canonical_boundary(all_transaction_root: pathlib.Path, capture_start_ns: int) -> dict[str, Any]:
    """Anchor the registry to the actual realtime all-transaction clock.

    Finalized-RPC records carry the time they were fetched, which is not an
    observation timestamp.  Canonical slot is monotonic and the first raw
    AllenHark record at/after the contract timestamp gives an exact local
    boundary without inferring any transaction's arrival time.
    """
    instant = dt.datetime.fromtimestamp(capture_start_ns / 1_000_000_000, tz=dt.timezone.utc)
    path = all_transaction_root / f"{instant:%Y}" / f"{instant:%m}" / f"{instant:%d}" / f"{instant:%H}" / "arrivals.bin.partial"
    if not path.exists():
        raise RuntimeError(f"BOUNDARY_ALL_TRANSACTION_JOURNAL_MISSING:{path}")
    with path.open("rb") as stream:
        if stream.read(len(ARRIVAL_MAGIC)) != ARRIVAL_MAGIC:
            raise RuntimeError(f"BOUNDARY_ALL_TRANSACTION_JOURNAL_MAGIC_MISMATCH:{path}")
        while data := stream.read(ARRIVAL_RECORD.size):
            if len(data) != ARRIVAL_RECORD.size:
                break
            values = ARRIVAL_RECORD.unpack(data)
            if values[1] >= capture_start_ns:
                return {
                    "captureStartUnixNs": capture_start_ns,
                    "firstRealtimeReceiptUnixNs": int(values[1]),
                    "firstCanonicalSlot": int(values[2]),
                    "source": SOURCE,
                    "journal": str(path),
                }
    raise RuntimeError(f"BOUNDARY_RECORDED_RECEIPT_NOT_YET_AVAILABLE:{path}")


def open_db(path: pathlib.Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS journal_cursor(
          path TEXT PRIMARY KEY, byte_offset INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS arrival_candidates(
          signature TEXT PRIMARY KEY,
          slot INTEGER NOT NULL,
          observed_order INTEGER,
          realtime_receipt_ns INTEGER NOT NULL,
          source TEXT NOT NULL,
          continuity_epoch TEXT,
          instruction_kinds_json TEXT NOT NULL,
          arrival_evidence_hash TEXT NOT NULL,
          source_file TEXT NOT NULL,
          first_seen_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS arrival_epoch_attestations(
          signature TEXT NOT NULL,
          continuity_epoch TEXT NOT NULL,
          attested_utc TEXT NOT NULL,
          PRIMARY KEY(signature, continuity_epoch)
        );
        -- One immutable receipt record per signature *and source*.  This is
        -- deliberately separate from the legacy AllenHark-only candidate
        -- table so a future proof can inspect both clocks without selecting,
        -- averaging, or replacing either timestamp.
        CREATE TABLE IF NOT EXISTS source_arrival_candidates(
          signature TEXT NOT NULL,
          source TEXT NOT NULL,
          slot INTEGER NOT NULL,
          observed_order INTEGER,
          realtime_receipt_ns INTEGER NOT NULL,
          continuity_epoch TEXT,
          instruction_kinds_json TEXT NOT NULL,
          arrival_evidence_hash TEXT NOT NULL,
          source_file TEXT NOT NULL,
          first_seen_utc TEXT NOT NULL,
          PRIMARY KEY(signature, source)
        );
        CREATE TABLE IF NOT EXISTS yellowstone_processed_partitions(
          partition_key TEXT PRIMARY KEY,
          report_hash TEXT NOT NULL,
          processed_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS processed_partitions(
          partition_key TEXT PRIMARY KEY,
          report_hash TEXT NOT NULL,
          processed_utc TEXT NOT NULL
        );
        -- This is intentionally independent from the normal finalized-frame
        -- cursor.  It records a bounded recovery projection only after an
        -- immutable raw realtime CREATE frame is bound to canonical recovery
        -- artifacts for the same signature.
        CREATE TABLE IF NOT EXISTS recovery_adapter_processed_partitions(
          partition_key TEXT PRIMARY KEY,
          report_hash TEXT NOT NULL,
          processed_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS create_registry(
          signature TEXT NOT NULL,
          mint TEXT NOT NULL,
          slot INTEGER NOT NULL,
          canonical_tx_index INTEGER,
          canonical_order_status TEXT NOT NULL,
          realtime_receipt_ns INTEGER,
          source TEXT,
          continuity_epoch TEXT,
          finalized_status TEXT NOT NULL,
          realtime_status TEXT NOT NULL,
          decoder_status TEXT NOT NULL,
          canonical_evidence_hash TEXT NOT NULL,
          first_seen_utc TEXT NOT NULL,
          PRIMARY KEY(signature, mint)
        );
        CREATE TABLE IF NOT EXISTS unsupported_creates(
          signature TEXT PRIMARY KEY,
          slot INTEGER NOT NULL,
          canonical_tx_index INTEGER,
          realtime_receipt_ns INTEGER,
          source TEXT,
          continuity_epoch TEXT,
          reason TEXT NOT NULL,
          canonical_evidence_hash TEXT NOT NULL,
          first_seen_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conflicts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          category TEXT NOT NULL,
          signature TEXT NOT NULL,
          detail_json TEXT NOT NULL,
          observed_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS window_status(
          entry_signature TEXT PRIMARY KEY,
          mint TEXT NOT NULL,
          entry_realtime_receipt_ns INTEGER NOT NULL,
          create_signature TEXT,
          status TEXT NOT NULL,
          evaluated_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS create_registry_mint ON create_registry(mint);
        CREATE INDEX IF NOT EXISTS source_arrival_candidates_source ON source_arrival_candidates(source);
    """)
    return connection


def metadata(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", (key, value))


def continuity_epochs(path: pathlib.Path, capture_start_ns: int) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    if not path.exists():
        return rows
    with path.open("rb") as stream:
        for line in stream:
            try:
                row = json.loads(line)
                timestamp = int(row.get("timestampUnixNs") or 0)
                # Keep the most recent connection that began before this
                # registry. A healthy long-lived observer should not lose its
                # provenance epoch merely because it did not reconnect.
                if row.get("event") == "CONNECTED":
                    rows.append((timestamp, str(row.get("processInstance") or "UNKNOWN")))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    return sorted(rows)


def epoch_for(receipt_ns: int, epochs: list[tuple[int, str]]) -> str | None:
    current: str | None = None
    for started_ns, epoch in epochs:
        if started_ns > receipt_ns:
            break
        current = epoch
    return current


def record_source_arrival(connection: sqlite3.Connection, payload: dict[str, Any]) -> str:
    """Append one independently auditable source receipt.

    A signature can have one AllenHark and one Yellowstone row.  A repeated
    identical source record is a duplicate; a conflicting one is preserved in
    the conflict ledger instead of replacing the original receipt.
    """
    signature = str(payload["signature"])
    source = str(payload["source"])
    evidence_hash = sha256(payload)
    existing = connection.execute(
        "SELECT arrival_evidence_hash FROM source_arrival_candidates WHERE signature = ? AND source = ?",
        (signature, source),
    ).fetchone()
    if existing is not None:
        if existing["arrival_evidence_hash"] != evidence_hash:
            connection.execute(
                "INSERT INTO conflicts(category,signature,detail_json,observed_utc) VALUES(?,?,?,?)",
                (
                    "SOURCE_ARRIVAL_CONFLICT",
                    signature,
                    json.dumps({"source": source, "existing": existing["arrival_evidence_hash"], "observed": evidence_hash}),
                    utc_now(),
                ),
            )
            return "conflict"
        return "duplicate"
    connection.execute(
        """
        INSERT INTO source_arrival_candidates(
          signature,source,slot,observed_order,realtime_receipt_ns,continuity_epoch,
          instruction_kinds_json,arrival_evidence_hash,source_file,first_seen_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            signature,
            source,
            int(payload["slot"]),
            payload.get("observedOrder"),
            int(payload["realtimeReceiptNs"]),
            payload.get("continuityEpoch"),
            json.dumps(sorted({str(item) for item in payload.get("instructionKinds") or []})),
            evidence_hash,
            str(payload["sourceFile"]),
            utc_now(),
        ),
    )
    return "inserted"


def backfill_allenhark_source_arrivals(connection: sqlite3.Connection) -> int:
    """Append source rows for legacy AllenHark candidates without rewriting them."""
    inserted = 0
    for row in connection.execute("SELECT * FROM arrival_candidates"):
        payload = {
            "signature": row["signature"],
            "source": row["source"],
            "slot": row["slot"],
            "observedOrder": row["observed_order"],
            "realtimeReceiptNs": row["realtime_receipt_ns"],
            "continuityEpoch": row["continuity_epoch"],
            "instructionKinds": json.loads(row["instruction_kinds_json"]),
            "sourceFile": row["source_file"],
        }
        inserted += int(record_source_arrival(connection, payload) == "inserted")
    return inserted


def ingest_arrivals(
    connection: sqlite3.Connection,
    arrival_journal: pathlib.Path,
    capture_start_ns: int,
    epochs: list[tuple[int, str]],
) -> dict[str, int]:
    counters = {"lines": 0, "createCandidates": 0, "duplicates": 0, "malformed": 0}
    if not arrival_journal.exists():
        return counters
    row = connection.execute("SELECT byte_offset FROM journal_cursor WHERE path = ?", (str(arrival_journal),)).fetchone()
    offset = int(row[0]) if row else 0
    size = arrival_journal.stat().st_size
    if offset > size:
        # Journal rotation/truncation never overwrites a provenance record; start
        # reading the new file from zero and retain the old DB entries.
        offset = 0
    with arrival_journal.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line:
                break
            counters["lines"] += 1
            try:
                event = json.loads(line)
                receipt_ns = int(event["receivedAtUnixNs"])
                kinds = {str(item) for item in event.get("pumpInstructionKinds") or []}
                signature = str(event["signature"])
                if receipt_ns < capture_start_ns or not kinds.intersection(CREATE_KINDS):
                    continue
                payload = {
                    "signature": signature,
                    "slot": int(event["slot"]),
                    "observedOrder": event.get("observedSlotTransactionOrdinal"),
                    "realtimeReceiptNs": receipt_ns,
                    "source": str(event.get("source") or SOURCE),
                    "continuityEpoch": epoch_for(receipt_ns, epochs),
                    "instructionKinds": sorted(kinds),
                }
                inserted = connection.execute("""
                    INSERT OR IGNORE INTO arrival_candidates(
                      signature,slot,observed_order,realtime_receipt_ns,source,continuity_epoch,
                      instruction_kinds_json,arrival_evidence_hash,source_file,first_seen_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    signature, payload["slot"], payload["observedOrder"], receipt_ns,
                    payload["source"], payload["continuityEpoch"],
                    json.dumps(payload["instructionKinds"]), sha256(payload), str(arrival_journal), utc_now(),
                ))
                if inserted.rowcount:
                    counters["createCandidates"] += 1
                else:
                    counters["duplicates"] += 1
                source_result = record_source_arrival(connection, {**payload, "sourceFile": str(arrival_journal)})
                if source_result == "conflict":
                    counters["conflicts"] += 1
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                counters["malformed"] += 1
        connection.execute("INSERT OR REPLACE INTO journal_cursor(path,byte_offset) VALUES(?,?)", (str(arrival_journal), stream.tell()))
    return counters


def attest_existing_epochs(connection: sqlite3.Connection, epochs: list[tuple[int, str]]) -> int:
    """Append a correction for records captured before an epoch was known.

    The original arrival record stays immutable. This side table is an explicit
    provenance addendum, rather than an in-place metadata rewrite.
    """
    attested = 0
    for row in connection.execute("SELECT signature,realtime_receipt_ns FROM arrival_candidates WHERE continuity_epoch IS NULL"):
        epoch = epoch_for(int(row["realtime_receipt_ns"]), epochs)
        if epoch is not None:
            result = connection.execute(
                "INSERT OR IGNORE INTO arrival_epoch_attestations(signature,continuity_epoch,attested_utc) VALUES(?,?,?)",
                (row["signature"], epoch, utc_now()),
            )
            attested += int(result.rowcount)
    return attested


def partition_inputs(exact_root: pathlib.Path, capture_start_ns: int, boundary_slot: int, connection: sqlite3.Connection) -> Iterable[tuple[str, str, list[pathlib.Path]]]:
    """Yield closed finalized-evidence partitions only.

    This registry purposely reads the direct closed-partition evidence rather
    than the alpha-readiness reports: those reports can refer to historical
    partitions whose evidence was intentionally not migrated to this host.
    """
    maturity_cutoff_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000) - 300_000_000_000
    for status_path in sorted((exact_root / "partitions").glob("*/*/*/*/reconciliation-status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("temporalHourClosed") is not True:
            continue
        # Avoid decompressing historical partitions that cannot possibly have
        # a post-boundary canonical transaction.
        if int(status.get("endSlot") or 0) < boundary_slot:
            continue
        key = str(status.get("partitionKey") or "/".join(status_path.parts[-5:-1]))
        if connection.execute("SELECT 1 FROM processed_partitions WHERE partition_key = ?", (key,)).fetchone():
            continue
        try:
            year, month, day, hour = (int(item) for item in key.split("/"))
            partition_end_ns = int(dt.datetime(year, month, day, hour, tzinfo=dt.timezone.utc).timestamp() * 1_000_000_000) + 3_600_000_000_000
        except ValueError:
            continue
        if partition_end_ns > maturity_cutoff_ns:
            continue
        evidence_path = status_path.parent / "evidence.frames.zst"
        if not evidence_path.exists():
            continue
        # Every stored row below still requires its own finalized/successful
        # proof. An incomplete partition is never used to invent a CREATE or
        # declare an absent realtime record a delivery loss.
        yield key, sha256(status), [evidence_path]


def yellowstone_partition_inputs(
    exact_root: pathlib.Path,
    boundary_slot: int,
    connection: sqlite3.Connection,
) -> Iterable[tuple[str, str, pathlib.Path]]:
    """Yield each relevant closed partition once for the retained Yellowstone input.

    This reads existing Pump-specific realtime frames only.  It does not call
    RPC, create a market archive, or use finalized fetch time as a receipt.
    """
    for status_path in sorted((exact_root / "partitions").glob("*/*/*/*/reconciliation-status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("temporalHourClosed") is not True:
            continue
        if int(status.get("endSlot") or 0) < boundary_slot:
            continue
        key = str(status.get("partitionKey") or "/".join(status_path.parts[-5:-1]))
        input_hash = sha256({"status": status, "projectionVersion": YELLOWSTONE_PROJECTION_VERSION})
        recorded = connection.execute(
            "SELECT report_hash FROM yellowstone_processed_partitions WHERE partition_key = ?", (key,)
        ).fetchone()
        if recorded is not None and recorded["report_hash"] == input_hash:
            continue
        evidence_path = status_path.parent / "evidence.frames.zst"
        if evidence_path.exists():
            yield key, input_hash, evidence_path


def prior_realtime_evidence_paths(
    exact_root: pathlib.Path,
    partition_key: str,
    lookback_hours: int = 6,
) -> list[pathlib.Path]:
    """Return the finalized partition and its bounded receipt lookback.

    Finalized RPC evidence can materialize in a later hourly partition than
    the original Yellowstone receipt.  The receipt remains authoritative only
    when the same signature is found in these retained source frames.
    """
    year, month, day, hour = (int(part) for part in partition_key.split("/"))
    anchor = dt.datetime(year, month, day, hour, tzinfo=dt.timezone.utc)
    paths: list[pathlib.Path] = []
    for offset in range(lookback_hours + 1):
        cursor = anchor - dt.timedelta(hours=offset)
        root = exact_root / "partitions" / cursor.strftime("%Y/%m/%d/%H")
        for name in ("evidence.frames.zst", "evidence.frames.partial"):
            path = root / name
            if path.exists():
                paths.append(path)
                break
    return paths


def backfill_pending_yellowstone_arrivals(
    connection: sqlite3.Connection,
    exact_root: pathlib.Path,
    attachment_start_ns: int,
    attachment_start_slot: int,
) -> dict[str, int]:
    """One bounded recovery pass over retained post-boundary receipts.

    It is deliberately exact-signature-only: the finalized CREATE registry
    supplies candidates, while raw Yellowstone frames supply the original
    receipt clock.  Nothing is synthesized from finalized RPC evidence.
    """
    counters = {"partitions": 0, "records": 0, "recovered": 0, "duplicates": 0, "conflicts": 0, "malformed": 0}
    pending = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT r.signature FROM create_registry r
            WHERE r.slot >= ?
              AND NOT EXISTS (
                SELECT 1 FROM source_arrival_candidates a
                WHERE a.signature = r.signature AND a.source = ?
              )
            """,
            (attachment_start_slot, YELLOWSTONE_SOURCE),
        )
    }
    if not pending:
        return counters
    start_hour = dt.datetime.fromtimestamp(
        attachment_start_ns / 1_000_000_000,
        tz=dt.timezone.utc,
    ).replace(minute=0, second=0, microsecond=0)
    for path in sorted((exact_root / "partitions").glob("*/*/*/*/evidence.frames.zst")):
        try:
            position = path.parts.index("partitions")
            partition_hour = dt.datetime(
                *(int(value) for value in path.parts[position + 1:position + 5]),
                tzinfo=dt.timezone.utc,
            )
        except (ValueError, IndexError):
            continue
        if partition_hour < start_hour:
            continue
        for metadata, _raw in iter_frames(path, False):
            if metadata.get("kind") != "transaction" or metadata.get("rawFormat") != YELLOWSTONE_RAW_FORMAT:
                continue
            counters["records"] += 1
            try:
                signature = str(metadata["signature"])
                if signature not in pending:
                    continue
                receipt_ns = int(metadata["receivedAtUnixNs"])
                slot = int(metadata["slot"])
                if receipt_ns < attachment_start_ns or slot < attachment_start_slot:
                    continue
                result = record_source_arrival(connection, {
                    "signature": signature,
                    "source": YELLOWSTONE_SOURCE,
                    "slot": slot,
                    "observedOrder": metadata.get("transactionIndex"),
                    "realtimeReceiptNs": receipt_ns,
                    "continuityEpoch": None,
                    "instructionKinds": sorted({str(item) for item in metadata.get("pumpInstructionKinds") or []}),
                    "sourceFile": str(path),
                })
                counters["recovered"] += int(result == "inserted")
                counters["duplicates"] += int(result == "duplicate")
                counters["conflicts"] += int(result == "conflict")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                counters["malformed"] += 1
        counters["partitions"] += 1
    return counters


def mark_yellowstone_projection_complete(
    connection: sqlite3.Connection,
    exact_root: pathlib.Path,
    attachment_start_slot: int,
) -> int:
    """Advance only the V3 source-attachment frontier after an exact backfill."""
    marked = 0
    for key, input_hash, _path in yellowstone_partition_inputs(exact_root, attachment_start_slot, connection):
        connection.execute(
            "INSERT OR REPLACE INTO yellowstone_processed_partitions(partition_key,report_hash,processed_utc) VALUES(?,?,?)",
            (key, input_hash, utc_now()),
        )
        marked += 1
    return marked


def finalized_create_signatures(
    connection: sqlite3.Connection,
    evidence_path: pathlib.Path,
    attachment_start_slot: int,
) -> set[str]:
    signatures: set[str] = set()
    for metadata, _raw in iter_frames(evidence_path, evidence_path.suffix == ".partial"):
        if not (
            metadata.get("kind") == "rpc_finalized_transaction"
            and metadata.get("finalized") is True
            and metadata.get("successful") is True
            and int(metadata.get("slot") or 0) >= attachment_start_slot
            and {str(item) for item in metadata.get("pumpInstructionKinds") or []}.intersection(CREATE_KINDS)
        ):
            continue
        signature = str(metadata.get("signature") or "")
        if not signature:
            continue
        if connection.execute("SELECT 1 FROM create_registry WHERE signature = ?", (signature,)).fetchone():
            signatures.add(signature)
    return signatures


def ingest_yellowstone_arrivals(
    connection: sqlite3.Connection,
    exact_root: pathlib.Path,
    capture_start_ns: int,
    attachment_start_slot: int,
) -> dict[str, int]:
    """Append original Yellowstone receipts for canonical finalized CREATEs.

    The live source's first-pass Pump classifier can be incomplete for a V0
    transaction.  Receipt capture therefore comes from the raw retained
    Yellowstone transaction frame, while CREATE meaning still comes only from
    the finalized canonical record already in ``create_registry``.
    """
    counters = {"partitions": 0, "records": 0, "createCandidates": 0, "duplicates": 0, "conflicts": 0, "malformed": 0}
    for key, input_hash, path in yellowstone_partition_inputs(exact_root, attachment_start_slot, connection):
        signatures = finalized_create_signatures(connection, path, attachment_start_slot)
        # Receipt frames may be in one of the preceding physical hour files
        # even when their final canonical rows were reconciled later.
        for receipt_path in prior_realtime_evidence_paths(exact_root, key):
            for frame_metadata, _raw in iter_frames(receipt_path, receipt_path.suffix == ".partial"):
                if frame_metadata.get("kind") != "transaction" or frame_metadata.get("rawFormat") != YELLOWSTONE_RAW_FORMAT:
                    continue
                counters["records"] += 1
                try:
                    signature = str(frame_metadata["signature"])
                    if signature not in signatures:
                        continue
                    receipt_ns = int(frame_metadata["receivedAtUnixNs"])
                    slot = int(frame_metadata["slot"])
                    if receipt_ns < capture_start_ns or slot < attachment_start_slot:
                        continue
                    kinds = {str(item) for item in frame_metadata.get("pumpInstructionKinds") or []}
                    result = record_source_arrival(connection, {
                        "signature": signature,
                        "source": YELLOWSTONE_SOURCE,
                        "slot": slot,
                        "observedOrder": frame_metadata.get("transactionIndex"),
                        "realtimeReceiptNs": receipt_ns,
                        # Retained Yellowstone records have no per-record
                        # reconnect epoch; never infer one from AllenHark.
                        "continuityEpoch": None,
                        "instructionKinds": sorted(kinds),
                        "sourceFile": str(receipt_path),
                    })
                    counters["createCandidates"] += int(result == "inserted")
                    counters["duplicates"] += int(result == "duplicate")
                    counters["conflicts"] += int(result == "conflict")
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    counters["malformed"] += 1
        connection.execute(
            "INSERT OR REPLACE INTO yellowstone_processed_partitions(partition_key,report_hash,processed_utc) VALUES(?,?,?)",
            (key, input_hash, utc_now()),
        )
        counters["partitions"] += 1
    return counters


def arrival(connection: sqlite3.Connection, signature: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM arrival_candidates WHERE signature = ?", (signature,)).fetchone()


def source_arrivals(connection: sqlite3.Connection, signature: str) -> dict[str, sqlite3.Row]:
    return {
        str(row["source"]): row
        for row in connection.execute(
            "SELECT * FROM source_arrival_candidates WHERE signature = ?", (signature,)
        )
    }


def add_finalized_create(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    raw: bytes | str,
    boundary_slot: int,
) -> None:
    signature = str(metadata.get("signature") or "")
    if not signature:
        return
    kinds = {str(item) for item in metadata.get("pumpInstructionKinds") or []}
    if not kinds.intersection(CREATE_KINDS):
        return
    slot = int(metadata.get("slot") or 0)
    if slot < boundary_slot:
        return
    tx_index = metadata.get("transactionIndex")
    tx_index = int(tx_index) if tx_index is not None else None
    candidates = source_arrivals(connection, signature)
    allenhark_candidate = candidates.get(SOURCE)
    yellowstone_candidate = candidates.get(YELLOWSTONE_SOURCE)
    candidate = allenhark_candidate
    canonical_hash = sha256(raw)
    try:
        tx = json.loads(raw).get("result")
        mints = canonical_create_mints(tx) if tx else set()
    except (ValueError, TypeError, json.JSONDecodeError):
        mints = set()
    if not mints:
        reason = "FINALIZED_CREATE_DECODER_UNSUPPORTED"
        connection.execute("""
            INSERT OR IGNORE INTO unsupported_creates(
              signature,slot,canonical_tx_index,realtime_receipt_ns,source,continuity_epoch,
              reason,canonical_evidence_hash,first_seen_utc
            ) VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            signature, slot, tx_index,
            candidate["realtime_receipt_ns"] if candidate else None,
            candidate["source"] if candidate else None,
            candidate["continuity_epoch"] if candidate else None,
            reason, canonical_hash, utc_now(),
        ))
        return
    order_status = "VALID" if tx_index is not None and tx_index >= 0 else "INVALID"
    if allenhark_candidate and yellowstone_candidate:
        realtime_status = "CAPTURED_BOTH_SOURCES"
    elif allenhark_candidate:
        realtime_status = "CAPTURED_ALLENHARK"
    elif yellowstone_candidate:
        realtime_status = "CAPTURED_YELLOWSTONE"
    else:
        realtime_status = "NOT_SEEN_REALTIME"
    for mint in sorted(mints):
        row = connection.execute("SELECT * FROM create_registry WHERE signature = ? AND mint = ?", (signature, mint)).fetchone()
        values = (
            signature, mint, slot, tx_index, order_status,
            candidate["realtime_receipt_ns"] if candidate else None,
            candidate["source"] if candidate else None,
            candidate["continuity_epoch"] if candidate else None,
            "FINALIZED_SUCCESS", realtime_status, "SUPPORTED", canonical_hash, utc_now(),
        )
        if row is None:
            connection.execute("""
                INSERT INTO create_registry(
                  signature,mint,slot,canonical_tx_index,canonical_order_status,realtime_receipt_ns,
                  source,continuity_epoch,finalized_status,realtime_status,decoder_status,
                  canonical_evidence_hash,first_seen_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, values)
        elif row["canonical_evidence_hash"] != canonical_hash:
            connection.execute("INSERT INTO conflicts(category,signature,detail_json,observed_utc) VALUES(?,?,?,?)", (
                "FINALIZED_CREATE_CONFLICT", signature,
                json.dumps({"mint": mint, "existing": row["canonical_evidence_hash"], "observed": canonical_hash}), utc_now(),
            ))


def reconcile_partitions(connection: sqlite3.Connection, exact_root: pathlib.Path, capture_start_ns: int, boundary_slot: int) -> dict[str, int]:
    counters = {"reports": 0, "finalizedCreateTransactions": 0}
    for key, input_hash, paths in partition_inputs(exact_root, capture_start_ns, boundary_slot, connection):
        for path in paths:
            for frame_metadata, raw in iter_frames(path, path.suffix == ".partial"):
                if not (
                    frame_metadata.get("kind") == "rpc_finalized_transaction"
                    and frame_metadata.get("finalized") is True
                    and frame_metadata.get("successful") is True
                ):
                    continue
                if {str(item) for item in frame_metadata.get("pumpInstructionKinds") or []}.intersection(CREATE_KINDS):
                    counters["finalizedCreateTransactions"] += 1
                    add_finalized_create(connection, frame_metadata, raw, boundary_slot)
        connection.execute("INSERT INTO processed_partitions(partition_key,report_hash,processed_utc) VALUES(?,?,?)", (key, input_hash, utc_now()))
        counters["reports"] += 1
    return counters


def recovery_adapter_partition_inputs(
    exact_root: pathlib.Path,
    boundary_slot: int,
    connection: sqlite3.Connection,
) -> Iterable[tuple[str, str, pathlib.Path, pathlib.Path, pathlib.Path]]:
    """Yield closed partitions with the three immutable recovery inputs.

    The normal registry cursor intentionally consumes only finalized-RPC
    frames.  This separate cursor is for the narrow case where the exact
    transaction was retained in realtime evidence, while finality and
    canonical ordering were recovered in their own immutable artifacts.
    """
    for status_path in sorted((exact_root / "partitions").glob("*/*/*/*/reconciliation-status.json")):
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if status.get("temporalHourClosed") is not True or int(status.get("endSlot") or 0) < boundary_slot:
            continue
        key = str(status.get("partitionKey") or "/".join(status_path.parts[-5:-1]))
        directory = status_path.parent
        evidence_path = directory / "evidence.frames.zst"
        finality_path = directory / "canonical-finality-recovery-v1.json"
        block_path = directory / "canonical-block-recovery-v1.json"
        if not (evidence_path.exists() and finality_path.exists() and block_path.exists()):
            continue
        # Recovery artifacts are append-only per partition.  Size and mtime are
        # sufficient to notice an updated immutable artifact without hashing
        # multi-gigabyte files on every five-minute registry cycle.
        artifact_state = {
            "version": RECOVERY_ADAPTER_PROJECTION_VERSION,
            "status": status,
            "files": {
                path.name: {"size": path.stat().st_size, "mtimeNs": path.stat().st_mtime_ns}
                for path in (evidence_path, finality_path, block_path)
            },
        }
        report_hash = sha256(artifact_state)
        previous = connection.execute(
            "SELECT report_hash FROM recovery_adapter_processed_partitions WHERE partition_key = ?", (key,)
        ).fetchone()
        if previous is not None and previous["report_hash"] == report_hash:
            continue
        yield key, report_hash, evidence_path, finality_path, block_path


def recovery_promotion_decision(
    frame_metadata: dict[str, Any],
    finality: dict[str, Any] | None,
    block_signatures: list[Any] | None,
    mint: str,
) -> dict[str, Any]:
    """Validate the exact proof chain for a recovery-backed CREATE.

    The raw Yellowstone frame supplies the original receipt clock and decoded
    CREATE/mint.  Finality and block recovery independently prove that exact
    signature was successful and canonically ordered at that slot.  No clock
    value from recovery artifacts participates in this decision.
    """
    signature = str(frame_metadata.get("signature") or "")
    kinds = {str(item) for item in frame_metadata.get("pumpInstructionKinds") or []}
    try:
        slot = int(frame_metadata["slot"])
        receipt_ns = int(frame_metadata["receivedAtUnixNs"])
    except (KeyError, TypeError, ValueError):
        return {"promotionPass": False, "reason": "RAW_REALTIME_CREATE_METADATA_INVALID"}
    if not signature:
        return {"promotionPass": False, "reason": "RAW_REALTIME_SIGNATURE_MISSING"}
    if frame_metadata.get("kind") != "transaction" or frame_metadata.get("rawFormat") != YELLOWSTONE_RAW_FORMAT:
        return {"promotionPass": False, "reason": "RAW_REALTIME_SOURCE_NOT_ACCEPTED"}
    if not kinds.intersection(CREATE_KINDS):
        return {"promotionPass": False, "reason": "RAW_REALTIME_CREATE_KIND_MISSING"}
    if not mint or mint not in {str(item) for item in frame_metadata.get("discoveredMints") or []}:
        return {"promotionPass": False, "reason": "RAW_REALTIME_MINT_MISMATCH"}
    if not isinstance(finality, dict):
        return {"promotionPass": False, "reason": "FINALIZED_RECOVERY_PROOF_MISSING"}
    if finality.get("signature") not in (None, signature):
        return {"promotionPass": False, "reason": "FINALIZED_RECOVERY_SIGNATURE_MISMATCH"}
    if (
        finality.get("confirmationStatus") != "finalized"
        or finality.get("successful") is not True
        or int(finality.get("slot") or -1) != slot
    ):
        return {"promotionPass": False, "reason": "FINALIZED_RECOVERY_PROOF_INVALID"}
    if not isinstance(block_signatures, list):
        return {"promotionPass": False, "reason": "CANONICAL_BLOCK_PROOF_MISSING"}
    try:
        # Yellowstone's observed transaction ordinal is source-local.  The
        # finalized block list is the authority for canonical order; requiring
        # the two distinct ordinals to be numerically equal would reject valid
        # canonical transactions.
        canonical_tx_index = next(
            index for index, value in enumerate(block_signatures) if str(value) == signature
        )
    except StopIteration:
        return {"promotionPass": False, "reason": "CANONICAL_BLOCK_ORDER_MISMATCH"}
    return {
        "promotionPass": True,
        "reason": "PROMOTABLE",
        "signature": signature,
        "mint": mint,
        "slot": slot,
        "canonicalTxIndex": canonical_tx_index,
        "realtimeReceiptNs": receipt_ns,
        "canonicalEvidenceHash": sha256({
            "adapter": RECOVERY_ADAPTER_VERSION,
            "rawEvidenceId": frame_metadata.get("evidenceId"),
            "rawSha256": frame_metadata.get("rawSha256"),
            "signature": signature,
            "mint": mint,
            "slot": slot,
            "canonicalTxIndex": canonical_tx_index,
            "finality": finality,
            "blockSignature": signature,
        }),
    }


def promote_recovered_create(
    connection: sqlite3.Connection,
    decision: dict[str, Any],
    frame_metadata: dict[str, Any],
    source_file: pathlib.Path,
) -> str:
    """Append one validated recovery-backed CREATE without rewriting history."""
    if decision.get("promotionPass") is not True:
        return "rejected"
    signature = str(decision["signature"])
    mint = str(decision["mint"])
    existing = connection.execute(
        "SELECT 1 FROM create_registry WHERE signature = ? AND mint = ?", (signature, mint)
    ).fetchone()
    if existing is not None:
        return "existing"
    observed_order = frame_metadata.get("transactionIndex")
    try:
        observed_order = int(observed_order) if observed_order is not None else None
    except (TypeError, ValueError):
        observed_order = None
    preexisting_source = source_arrivals(connection, signature).get(YELLOWSTONE_SOURCE)
    if (
        preexisting_source is not None
        and int(preexisting_source["slot"]) == int(decision["slot"])
        and int(preexisting_source["realtime_receipt_ns"]) == int(decision["realtimeReceiptNs"])
        and set(json.loads(preexisting_source["instruction_kinds_json"])).intersection(CREATE_KINDS)
    ):
        # The original source receipt is already preserved.  Fields such as
        # its physical source-file path or source-local ordinal are not
        # canonical facts and must not be rewritten merely because a migrated
        # copy of the same raw evidence is being reconciled.
        source_result = "verified_existing"
    else:
        source_result = record_source_arrival(connection, {
            "signature": signature,
            "source": YELLOWSTONE_SOURCE,
            "slot": int(decision["slot"]),
            # This table is an immutable record of what Yellowstone observed.
            # The recovered canonical index belongs only in create_registry.
            "observedOrder": observed_order,
            "realtimeReceiptNs": int(decision["realtimeReceiptNs"]),
            # Yellowstone source epochs are deliberately not inferred from
            # another source.  The raw frame remains the epoch authority.
            "continuityEpoch": None,
            "instructionKinds": sorted({str(item) for item in frame_metadata.get("pumpInstructionKinds") or []}),
            "sourceFile": str(source_file),
        })
    if source_result == "conflict":
        return "source_conflict"
    candidates = source_arrivals(connection, signature)
    allenhark_candidate = candidates.get(SOURCE)
    yellowstone_candidate = candidates.get(YELLOWSTONE_SOURCE)
    candidate = allenhark_candidate or yellowstone_candidate
    if candidate is None:
        return "source_missing_after_append"
    if allenhark_candidate and yellowstone_candidate:
        realtime_status = "CAPTURED_BOTH_SOURCES"
    elif allenhark_candidate:
        realtime_status = "CAPTURED_ALLENHARK"
    else:
        realtime_status = "CAPTURED_YELLOWSTONE"
    connection.execute(
        """
        INSERT INTO create_registry(
          signature,mint,slot,canonical_tx_index,canonical_order_status,realtime_receipt_ns,
          source,continuity_epoch,finalized_status,realtime_status,decoder_status,
          canonical_evidence_hash,first_seen_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            signature, mint, int(decision["slot"]), int(decision["canonicalTxIndex"]), "VALID",
            int(candidate["realtime_receipt_ns"]), candidate["source"], candidate["continuity_epoch"],
            "FINALIZED_SUCCESS", realtime_status, "SUPPORTED_RECOVERY_BOUND_CREATE",
            str(decision["canonicalEvidenceHash"]), utc_now(),
        ),
    )
    return "promoted"


def reconcile_recovery_creates(
    connection: sqlite3.Connection,
    exact_root: pathlib.Path,
    capture_start_ns: int,
    boundary_slot: int,
    *,
    dry_run: bool,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Bind retained realtime CREATEs to canonical recovery proof, fail-closed."""
    counters = {
        "partitions": 0,
        "rawCreateFrames": 0,
        "candidatesFound": 0,
        "promoted": 0,
        "existing": 0,
        "rejected": 0,
        "sourceConflicts": 0,
    }
    report_rows: list[dict[str, Any]] = []
    for key, report_hash, evidence_path, finality_path, block_path in recovery_adapter_partition_inputs(
        exact_root, boundary_slot, connection
    ):
        # Source frames are scanned before loading the sizable recovery maps so
        # only actual retained CREATE transactions are evaluated.
        frames: list[dict[str, Any]] = []
        for frame_metadata, _raw in iter_frames(evidence_path, False):
            if (
                frame_metadata.get("kind") != "transaction"
                or frame_metadata.get("rawFormat") != YELLOWSTONE_RAW_FORMAT
                or int(frame_metadata.get("receivedAtUnixNs") or 0) < capture_start_ns
                or int(frame_metadata.get("slot") or 0) < boundary_slot
                or not {str(item) for item in frame_metadata.get("pumpInstructionKinds") or []}.intersection(CREATE_KINDS)
            ):
                continue
            frames.append(frame_metadata)
        if not frames:
            if not dry_run:
                connection.execute(
                    "INSERT OR REPLACE INTO recovery_adapter_processed_partitions(partition_key,report_hash,processed_utc) VALUES(?,?,?)",
                    (key, report_hash, utc_now()),
                )
            counters["partitions"] += 1
            continue
        try:
            finality_rows = dict(json.loads(finality_path.read_text(encoding="utf-8")).get("signatures") or {})
            block_rows = dict(json.loads(block_path.read_text(encoding="utf-8")).get("blocks") or {})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            for frame_metadata in frames:
                for mint in sorted({str(item) for item in frame_metadata.get("discoveredMints") or []}):
                    report_rows.append({
                        "partitionKey": key,
                        "canonicalSignature": frame_metadata.get("signature"),
                        "mint": mint,
                        "realtimeSource": YELLOWSTONE_SOURCE,
                        "originalRealtimeReceiptNs": frame_metadata.get("receivedAtUnixNs"),
                        "finalizedEvidenceSource": "canonical recovery artifacts",
                        "registryRowAlreadyExists": False,
                        "promotionPass": False,
                        "reason": "RECOVERY_ARTIFACT_UNREADABLE",
                    })
                    counters["rejected"] += 1
            continue
        for frame_metadata in frames:
            counters["rawCreateFrames"] += 1
            signature = str(frame_metadata.get("signature") or "")
            for mint in sorted({str(item) for item in frame_metadata.get("discoveredMints") or []}):
                decision = recovery_promotion_decision(
                    frame_metadata,
                    finality_rows.get(signature),
                    block_rows.get(str(frame_metadata.get("slot"))),
                    mint,
                )
                existing = connection.execute(
                    "SELECT 1 FROM create_registry WHERE signature = ? AND mint = ?", (signature, mint)
                ).fetchone() is not None
                report_rows.append({
                    "partitionKey": key,
                    "canonicalSignature": signature,
                    "mint": mint,
                    "slot": frame_metadata.get("slot"),
                    "canonicalTxIndex": frame_metadata.get("transactionIndex"),
                    "realtimeSource": YELLOWSTONE_SOURCE,
                    "originalRealtimeReceiptNs": frame_metadata.get("receivedAtUnixNs"),
                    "finalizedEvidenceSource": f"{finality_path.name}+{block_path.name}",
                    "registryRowAlreadyExists": existing,
                    "promotionPass": bool(decision.get("promotionPass")),
                    "reason": decision.get("reason"),
                })
                if decision.get("promotionPass") is not True:
                    counters["rejected"] += 1
                    continue
                counters["candidatesFound"] += 1
                if dry_run:
                    continue
                result = promote_recovered_create(connection, decision, frame_metadata, evidence_path)
                if result == "promoted":
                    counters["promoted"] += 1
                elif result == "existing":
                    counters["existing"] += 1
                elif result == "source_conflict":
                    counters["sourceConflicts"] += 1
                else:
                    counters["rejected"] += 1
        if not dry_run:
            connection.execute(
                "INSERT OR REPLACE INTO recovery_adapter_processed_partitions(partition_key,report_hash,processed_utc) VALUES(?,?,?)",
                (key, report_hash, utc_now()),
            )
        counters["partitions"] += 1
    return counters, report_rows


def assess_window(
    connection: sqlite3.Connection,
    teacher_journal: pathlib.Path | None,
    capture_start_ns: int,
) -> int:
    """Diagnostic-only selected-mint status; never sent to the official cohort."""
    if teacher_journal is None or not teacher_journal.exists():
        return 0
    count = 0
    with teacher_journal.open("rb") as stream:
        for line in stream:
            try:
                row = json.loads(line)
                signature = str(row["signature"])
                mint = str(row["outputMint"])
                receipt_ns = int(row.get("detectedAtUnixNs") or int(row["detectedAtMs"]) * 1_000_000)
                if receipt_ns < capture_start_ns:
                    continue
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            existing = connection.execute("SELECT 1 FROM window_status WHERE entry_signature = ?", (signature,)).fetchone()
            if existing:
                continue
            create = connection.execute("SELECT * FROM create_registry WHERE mint = ? ORDER BY slot DESC LIMIT 1", (mint,)).fetchone()
            if create is None:
                continue  # Exact finality may not have reached this entry yet.
            if create["canonical_order_status"] != "VALID":
                status = "CREATE_KNOWN_CANONICAL_ORDER_INVALID"
            elif create["realtime_status"] != "CAPTURED":
                status = "CREATE_NOT_SEEN_REALTIME"
            elif receipt_ns - int(create["realtime_receipt_ns"]) > 120_000_000_000 or receipt_ns < int(create["realtime_receipt_ns"]):
                status = "CREATE_KNOWN_OUTSIDE_120S"
            else:
                status = "CREATE_USABLE_IN_120S"
            connection.execute("INSERT INTO window_status(entry_signature,mint,entry_realtime_receipt_ns,create_signature,status,evaluated_utc) VALUES(?,?,?,?,?,?)", (
                signature, mint, receipt_ns, create["signature"], status, utc_now(),
            ))
            count += 1
    return count


def status(connection: sqlite3.Connection, output_root: pathlib.Path, counters: dict[str, int]) -> dict[str, Any]:
    def scalar(sql: str) -> int:
        return int(connection.execute(sql).fetchone()[0])
    finalized = scalar("SELECT COUNT(*) FROM create_registry") + scalar("SELECT COUNT(*) FROM unsupported_creates")
    allenhark_captured = scalar("""
        SELECT COUNT(*) FROM create_registry r WHERE EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature = r.signature AND a.source = 'allenhark-jito-shreds-grpc-v1'
        )
    """)
    yellowstone_captured = scalar("""
        SELECT COUNT(*) FROM create_registry r WHERE EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature = r.signature AND a.source = 'yellowstone-pump-transactions-v1'
        )
    """)
    both_captured = scalar("""
        SELECT COUNT(*) FROM create_registry r WHERE EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature = r.signature AND a.source = 'allenhark-jito-shreds-grpc-v1'
        ) AND EXISTS (
          SELECT 1 FROM source_arrival_candidates a
          WHERE a.signature = r.signature AND a.source = 'yellowstone-pump-transactions-v1'
        )
    """)
    union_captured = scalar("""
        SELECT COUNT(*) FROM create_registry r WHERE EXISTS (
          SELECT 1 FROM source_arrival_candidates a WHERE a.signature = r.signature
        )
    """)
    missing = finalized - union_captured
    unsupported = scalar("SELECT COUNT(*) FROM unsupported_creates")
    invalid_order = scalar("SELECT COUNT(*) FROM create_registry WHERE canonical_order_status = 'INVALID'")
    started = parse_utc_ns(metadata(connection, "captureStartUtc") or utc_now())
    elapsed_hours = max((int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000) - started) / 3_600_000_000_000, 1 / 3600)
    db = output_root / "registry.sqlite3"
    db_size = sum(path.stat().st_size for path in output_root.glob("registry.sqlite3*"))
    return {
        "schema": SCHEMA + "_STATUS",
        "updatedAtUtc": utc_now(),
        "prospectiveStartUtc": metadata(connection, "captureStartUtc"),
        "arrivalCreateCandidates": scalar("SELECT COUNT(*) FROM arrival_candidates"),
        "finalizedSolPumpCreateRows": finalized,
        "allenharkCaptured": allenhark_captured,
        "yellowstoneCaptured": yellowstone_captured,
        "bothSourceCaptured": both_captured,
        "unionCaptured": union_captured,
        "createNotSeenRealtime": missing,
        "decoderUnsupported": unsupported,
        "canonicalOrderingInvalid": invalid_order,
        "allenharkCoveragePercent": round((100 * allenhark_captured / finalized), 4) if finalized else None,
        "yellowstoneCoveragePercent": round((100 * yellowstone_captured / finalized), 4) if finalized else None,
        "bothSourceCoveragePercent": round((100 * both_captured / finalized), 4) if finalized else None,
        "unionCoveragePercent": round((100 * union_captured / finalized), 4) if finalized else None,
        "sourceClocksIndependentlyAuditable": True,
        "continuityEpochs": scalar("""
            SELECT COUNT(DISTINCT COALESCE(c.continuity_epoch,a.continuity_epoch))
            FROM arrival_candidates c LEFT JOIN arrival_epoch_attestations a USING(signature)
            WHERE COALESCE(c.continuity_epoch,a.continuity_epoch) IS NOT NULL
        """),
        "duplicateCandidatesIgnored": counters.get("duplicates", 0),
        "conflictingRows": scalar("SELECT COUNT(*) FROM conflicts"),
        "windowStatus": {row[0]: int(row[1]) for row in connection.execute("SELECT status,COUNT(*) FROM window_status GROUP BY status")},
        "storageBytes": db_size,
        "storageGrowthBytesPerHour": round(db_size / elapsed_hours, 2),
        "outcomesRead": 0,
        "submissions": 0,
        "cycle": counters,
    }


def initialize(connection: sqlite3.Connection, output_root: pathlib.Path) -> None:
    if metadata(connection, "captureStartUtc"):
        return
    contract_path = output_root / "contract.json"
    if contract_path.exists():
        # A projection rebuild retains the immutable original prospective
        # boundary; never silently turn a recovery run into a new cohort.
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    else:
        contract = {
            "schema": SCHEMA + "_CONTRACT",
            "createdAtUtc": utc_now(),
            "purpose": "prospective CREATE-only realtime provenance coverage",
            "arrivalAuthorities": [SOURCE, YELLOWSTONE_SOURCE],
            "canonicalAuthority": "PUMP_EXACT_EVIDENCE_LEDGER_V1 finalized successful transaction",
            "appendOnly": True,
            "timestampInferencePermitted": False,
            "clockMergingPermitted": False,
            "officialCohortWriteAccess": False,
            "outcomesRead": 0,
            "submissions": 0,
        }
        atomic_json(contract_path, contract)
    set_metadata(connection, "captureStartUtc", contract["createdAtUtc"])
    set_metadata(connection, "schema", SCHEMA)


def run(args: argparse.Namespace) -> dict[str, Any]:
    connection = open_db(args.output_root / "registry.sqlite3")
    try:
        initialize(connection, args.output_root)
        capture_start_ns = parse_utc_ns(metadata(connection, "captureStartUtc") or utc_now())
        boundary_path = args.output_root / "canonical-boundary.json"
        if boundary_path.exists():
            boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
        else:
            boundary = canonical_boundary(args.all_transaction_root, capture_start_ns)
            atomic_json(boundary_path, boundary)
        boundary_slot = int(boundary["firstCanonicalSlot"])
        if args.recovery_adapter_dry_run or args.recovery_adapter_only:
            recovery_counts, recovery_rows = reconcile_recovery_creates(
                connection,
                args.exact_root,
                capture_start_ns,
                boundary_slot,
                dry_run=args.recovery_adapter_dry_run,
            )
            report = {
                "schema": RECOVERY_ADAPTER_VERSION + ("_DRY_RUN" if args.recovery_adapter_dry_run else "_RUN"),
                "generatedAtUtc": utc_now(),
                "dryRun": bool(args.recovery_adapter_dry_run),
                "writesToRegistry": bool(args.recovery_adapter_only),
                "boundarySlot": boundary_slot,
                "counters": recovery_counts,
                # The complete dry-run remains immutable and contains every
                # candidate.  The apply report stays compact because it is a
                # post-proof operational receipt, not a second evidence copy.
                "rows": recovery_rows if args.recovery_adapter_dry_run else [],
                "outcomesRead": 0,
                "submissions": 0,
            }
            report_name = "recovery-adapter-v1-dry-run.json" if args.recovery_adapter_dry_run else "recovery-adapter-v1-last-run.json"
            if args.recovery_adapter_only:
                connection.commit()
            atomic_json(args.output_root / report_name, report)
            return report
        epochs = continuity_epochs(args.continuity_journal, capture_start_ns)
        arrival_counts = ingest_arrivals(connection, args.arrival_journal, capture_start_ns, epochs)
        arrival_counts["epochAttestations"] = attest_existing_epochs(connection, epochs)
        reconcile_counts = reconcile_partitions(connection, args.exact_root, capture_start_ns, boundary_slot)
        recovery_counts, _recovery_rows = reconcile_recovery_creates(
            connection,
            args.exact_root,
            capture_start_ns,
            boundary_slot,
            dry_run=False,
        )
        arrival_counts["legacyAllenharkBackfill"] = backfill_allenhark_source_arrivals(connection)
        attachment_start_slot = max(boundary_slot, int(args.yellowstone_attachment_start_slot or 0))
        if args.yellowstone_backfill_only:
            yellowstone_counts = {}
        else:
            yellowstone_counts = ingest_yellowstone_arrivals(
                connection,
                args.exact_root,
                capture_start_ns,
                attachment_start_slot,
            )
        if args.yellowstone_backfill_start_utc:
            yellowstone_counts["backfill"] = backfill_pending_yellowstone_arrivals(
                connection,
                args.exact_root,
                parse_utc_ns(args.yellowstone_backfill_start_utc),
                attachment_start_slot,
            )
            yellowstone_counts["backfillProjectionPartitionsMarked"] = mark_yellowstone_projection_complete(
                connection,
                args.exact_root,
                attachment_start_slot,
            )
        assessed = assess_window(connection, args.teacher_journal, capture_start_ns)
        connection.commit()
        counters = {
            **arrival_counts,
            **{f"yellowstone{key[:1].upper()}{key[1:]}": value for key, value in yellowstone_counts.items()},
            **reconcile_counts,
            **{f"recoveryAdapter{key[:1].upper()}{key[1:]}": value for key, value in recovery_counts.items()},
            "windowAssessments": assessed,
        }
        result = status(connection, args.output_root, counters)
        atomic_json(args.output_root / "status.json", result)
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--arrival-journal", type=pathlib.Path, required=True)
    parser.add_argument("--continuity-journal", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--all-transaction-root", type=pathlib.Path, required=True)
    parser.add_argument("--teacher-journal", type=pathlib.Path)
    parser.add_argument("--yellowstone-attachment-start-slot", type=int)
    parser.add_argument("--yellowstone-backfill-start-utc")
    parser.add_argument("--yellowstone-backfill-only", action="store_true")
    parser.add_argument("--recovery-adapter-dry-run", action="store_true")
    parser.add_argument("--recovery-adapter-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
