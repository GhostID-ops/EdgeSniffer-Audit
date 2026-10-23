#!/usr/bin/env python3
"""Bounded, outcome-blind continuation worker for full-meta 2CQ N production.

It never changes the frozen choice definition.  It only turns already-retained
prospective evidence into the exact state-plane input expected by that
definition, checkpointing each acquisition so a restart cannot duplicate work.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import sqlite3
import struct
import subprocess
import sys
import time
from collections import Counter
from typing import Any

from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_FULL_META_PROSPECTIVE_N_WORKER_V1"
WINDOW_NS = 120_000_000_000
# A single 120-second causal window is the smallest safe work unit.  Keeping
# batches to one acquisition prevents a high-traffic interval from combining
# multiple windows into a memory-heavy state-plane replay.
MAX_BATCH_SPAN_NS = 0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    os.replace(temporary, path)


def open_work(path: pathlib.Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS acquisition_work (
          signature TEXT NOT NULL,
          mint TEXT NOT NULL,
          received_ns INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          status TEXT NOT NULL,
          symmetric_eligible INTEGER NOT NULL DEFAULT 0,
          reason_json TEXT NOT NULL,
          batch_id TEXT,
          updated_utc TEXT NOT NULL,
          PRIMARY KEY(signature,mint)
        );
        CREATE INDEX IF NOT EXISTS acquisition_work_pending
          ON acquisition_work(status,received_ns,signature);
        CREATE TABLE IF NOT EXISTS batches (
          batch_id TEXT PRIMARY KEY,
          started_utc TEXT NOT NULL,
          completed_utc TEXT,
          evidence_path TEXT NOT NULL,
          report_path TEXT NOT NULL,
          status TEXT NOT NULL,
          detail_json TEXT NOT NULL,
          backfill_complete INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS valid_observations (
          signature TEXT NOT NULL,
          mint TEXT NOT NULL,
          received_ns INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          chronological_ordinal INTEGER NOT NULL UNIQUE,
          bucket TEXT NOT NULL CHECK(bucket IN ('CONTINUATION_N20','N150_PLUS_RESERVE')),
          batch_id TEXT NOT NULL,
          evidence_path TEXT NOT NULL,
          report_path TEXT NOT NULL,
          recorded_utc TEXT NOT NULL,
          PRIMARY KEY(signature,mint)
        );
        CREATE TABLE IF NOT EXISTS ledger_metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(acquisition_work)")}
    if "symmetric_eligible" not in columns:
        connection.execute("ALTER TABLE acquisition_work ADD COLUMN symmetric_eligible INTEGER NOT NULL DEFAULT 0")
    batch_columns = {row[1] for row in connection.execute("PRAGMA table_info(batches)")}
    if "backfill_complete" not in batch_columns:
        connection.execute("ALTER TABLE batches ADD COLUMN backfill_complete INTEGER NOT NULL DEFAULT 0")
        # This migration is reached only after the prior worker has repeatedly
        # reconciled every completed immutable report into acquisition_work.
        # Marking that verified historical baseline prevents a full report scan
        # on every future run; newly completed batches are marked below only
        # after their durable row updates commit.
        connection.execute("UPDATE batches SET backfill_complete=1 WHERE status='COMPLETED'")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS batches_pending_backfill ON batches(status,backfill_complete,started_utc)"
    )
    connection.commit()
    return connection


def read_funnel(path: pathlib.Path, mature_before_ns: int) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for row in document.get("rows") or []:
        if row.get("stage") != "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION_PENDING_FULL_WINDOW":
            continue
        received_ns = int(row.get("receivedAtUnixNs") or 0)
        if not received_ns:
            continue
        for mint in row.get("acquiredMints") or []:
            rows.append({
                "signature": str(row["signature"]), "mint": str(mint),
                "receivedAtUnixNs": received_ns, "receivedAtUtc": str(row["receivedAtUtc"]),
                "canonicalSlot": int(row.get("canonicalSlot") or 0),
                "mature": received_ns <= mature_before_ns,
            })
    return sorted(rows, key=lambda row: (row["receivedAtUnixNs"], row["signature"], row["mint"]))


def seed_prior(connection: sqlite3.Connection, prior_replay: pathlib.Path) -> None:
    if not prior_replay.exists():
        return
    document = json.loads(prior_replay.read_text(encoding="utf-8"))
    promoted_root = prior_replay.parent.parent / "promoted-windows"
    manifests = sorted(promoted_root.glob("*first-successful-acquisitions/manifest.json"))
    if len(manifests) != 1:
        raise RuntimeError("FIRST_VALID_N_BASELINE_MANIFEST_UNRESOLVED")
    evidence_path = manifests[0].parent / "evidence.frames"
    connection.execute(
        """INSERT OR IGNORE INTO batches
           (batch_id,started_utc,completed_utc,evidence_path,report_path,status,detail_json)
           VALUES (?,?,?,?,?,?,?)""",
        ("FIRST_VALID_N_BASELINE", utc_now(), utc_now(), str(evidence_path), str(prior_replay),
         "COMPLETED", canonical({"baseline": True, "outcomeBlind": True}).decode()),
    )
    for row in document.get("replayRows") or []:
        eligible = int(bool(row.get("symmetricEligible")))
        connection.execute(
            """INSERT INTO acquisition_work
               (signature,mint,received_ns,slot,status,symmetric_eligible,reason_json,batch_id,updated_utc)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(signature,mint) DO UPDATE SET
                 status=excluded.status,symmetric_eligible=excluded.symmetric_eligible,
                 reason_json=excluded.reason_json,updated_utc=excluded.updated_utc
               WHERE acquisition_work.batch_id='FIRST_VALID_N_BASELINE'""",
            (str(row["signature"]), str(row["mint"]), 0, 0,
             "PROCESSED" if eligible else "TERMINAL_REJECTED", eligible,
             canonical(row.get("failureReasons") or []).decode(), "FIRST_VALID_N_BASELINE", utc_now()),
        )
    connection.commit()
    connection.execute(
        """UPDATE valid_observations SET evidence_path=?,report_path=?
           WHERE batch_id='FIRST_VALID_N_BASELINE' AND evidence_path=''""",
        (str(evidence_path), str(prior_replay)),
    )
    connection.commit()


def recover_interrupted_batches(connection: sqlite3.Connection) -> int:
    """Make a killed service run auditable and retryable without changing rows."""
    cursor = connection.execute(
        "UPDATE batches SET status='ABORTED_RETRYABLE',completed_utc=?,detail_json=? WHERE status='RUNNING'",
        (utc_now(), canonical({"reason": "WORKER_INTERRUPTED_BEFORE_DURABLE_REPLAY"}).decode()),
    )
    connection.commit()
    return int(cursor.rowcount)


def upsert_work(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        connection.execute(
            """INSERT INTO acquisition_work
               (signature,mint,received_ns,slot,status,symmetric_eligible,reason_json,batch_id,updated_utc)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(signature,mint) DO UPDATE SET
                 received_ns=excluded.received_ns,slot=excluded.slot,updated_utc=excluded.updated_utc,
                 status=CASE WHEN acquisition_work.status IN ('PENDING_OPEN_WINDOW','PENDING_MATERIALIZATION','RETRYABLE')
                             THEN excluded.status ELSE acquisition_work.status END
               WHERE excluded.received_ns>0""",
            (row["signature"], row["mint"], row["receivedAtUnixNs"], row["canonicalSlot"],
             "PENDING_MATERIALIZATION" if row["mature"] else "PENDING_OPEN_WINDOW", 0, "[]", None, utc_now()),
        )
    connection.commit()


def pending_batch(connection: sqlite3.Connection, limit: int, max_batch_span_ns: int) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM acquisition_work WHERE status IN ('PENDING_MATERIALIZATION','RETRYABLE') AND received_ns>0 ORDER BY received_ns,signature,mint"
    ).fetchall()
    connection.row_factory = None
    if not rows:
        return []
    first = int(rows[0]["received_ns"])
    return [row for row in rows if int(row["received_ns"]) <= first + max_batch_span_ns][:limit]


def encode_frame(metadata: dict[str, Any], raw: bytes) -> bytes:
    metadata_bytes = canonical(metadata)
    body = metadata_bytes + raw
    return struct.pack("<II", len(metadata_bytes), len(raw)) + body + hashlib.sha256(body).digest()


def promote(root: pathlib.Path, batch: list[sqlite3.Row], destination: pathlib.Path) -> dict[str, Any]:
    start_ns = min(int(row["received_ns"]) for row in batch) - WINDOW_NS
    end_ns = max(int(row["received_ns"]) for row in batch)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "evidence.frames"
    copied = 0
    seen: set[str] = set()
    source_bytes_scanned = 0
    digest = hashlib.sha256()
    first_hour = dt.datetime.fromtimestamp(start_ns / 1_000_000_000, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    last_hour = dt.datetime.fromtimestamp(end_ns / 1_000_000_000, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    directories: list[pathlib.Path] = []
    hour = first_hour
    while hour <= last_hour:
        directory = root / "partitions" / hour.strftime("%Y/%m/%d/%H")
        if directory.exists():
            directories.append(directory)
        hour += dt.timedelta(hours=1)
    with output.open("wb") as stream:
        header = b"PEL1FRM\0"
        stream.write(header)
        digest.update(header)
        for directory in directories:
            # The active hour may atomically rotate from .partial to .zst
            # while a promotion is starting.  Retry that directory under its
            # current representation; deduplication makes a partial first read
            # safe, and absence of both forms remains a retryable failure.
            read_error: FileNotFoundError | None = None
            for _ in range(2):
                path = directory / "evidence.frames.zst"
                if not path.exists():
                    path = directory / "evidence.frames.partial"
                if not path.exists():
                    read_error = FileNotFoundError(str(directory))
                    continue
                try:
                    source_bytes_scanned += path.stat().st_size
                    for metadata, raw in iter_frames(path, path.name.endswith(".partial")):
                        receipt_ns = int(metadata.get("receivedAtUnixNs") or 0)
                        evidence_id = str(metadata.get("evidenceId") or "")
                        if start_ns <= receipt_ns <= end_ns and evidence_id and evidence_id not in seen:
                            encoded = encode_frame(metadata, raw)
                            stream.write(encoded)
                            digest.update(encoded)
                            seen.add(evidence_id)
                            copied += 1
                    read_error = None
                    break
                except FileNotFoundError as error:
                    read_error = error
            if read_error is not None:
                output.unlink(missing_ok=True)
                raise RuntimeError(f"PROMOTION_PARTITION_ROTATION_RETRYABLE:{directory}") from read_error
    if copied == 0:
        raise RuntimeError("PROMOTION_EMPTY_WINDOW")
    result = {
        "schema": "2CQ_FULL_META_WINDOW_PROMOTION_V2", "outcomeBlind": True,
        "windowStartNs": start_ns, "windowEndNs": end_ns, "framesCopied": copied,
        "sourceEvidenceBytesScanned": source_bytes_scanned,
        "promotedEvidenceBytes": output.stat().st_size,
        "acquisitions": [{"signature": str(row["signature"]), "mint": str(row["mint"]), "receivedAtUnixNs": int(row["received_ns"]), "canonicalSlot": int(row["slot"])} for row in batch],
        "evidenceSha256": digest.hexdigest(),
    }
    atomic_json(destination / "manifest.json", result)
    return result


def batch_funnel(batch: list[sqlite3.Row], destination: pathlib.Path) -> pathlib.Path:
    rows = []
    for row in batch:
        rows.append({
            "signature": str(row["signature"]), "receivedAtUnixNs": int(row["received_ns"]),
            "receivedAtUtc": dt.datetime.fromtimestamp(int(row["received_ns"]) / 1_000_000_000, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "canonicalSlot": int(row["slot"]), "stage": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION_PENDING_FULL_WINDOW",
            "acquiredMints": [str(row["mint"])],
        })
    output = destination / "batch-funnel.json"
    atomic_json(output, {"schema": "2CQ_FULL_META_PROSPECTIVE_FUNNEL_V1", "rows": rows, "outcomeBlind": True, "submissions": 0})
    return output


def invoke_adapter(
    args: argparse.Namespace, evidence: pathlib.Path, funnel: pathlib.Path, report: pathlib.Path,
    evidence_sha256: str,
) -> dict[str, Any]:
    command = [sys.executable, str(args.adapter), "--evidence", str(evidence), "--funnel", str(funnel),
               "--state-db", str(args.state_db), "--normalizer", str(args.normalizer), "--report", str(report)]
    command.extend(["--canonical-fetch-workers", str(args.canonical_fetch_workers)])
    command.extend(["--expected-evidence-sha256", evidence_sha256])
    if args.proof_cache:
        command.extend(["--proof-cache", str(args.proof_cache)])
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=args.adapter_timeout_s)
    if completed.returncode:
        raise RuntimeError(f"ADAPTER_FAILED:{completed.stderr[-1500:]}")
    return json.loads(report.read_text(encoding="utf-8"))


def finish_batch(connection: sqlite3.Connection, batch_id: str, report: dict[str, Any]) -> None:
    by_key = {(str(row["signature"]), str(row["mint"])): row for row in report.get("replayRows") or []}
    for key, row in by_key.items():
        status = "PROCESSED" if row.get("symmetricEligible") else "TERMINAL_REJECTED"
        connection.execute(
            "UPDATE acquisition_work SET status=?,symmetric_eligible=?,reason_json=?,batch_id=?,updated_utc=? WHERE signature=? AND mint=?",
            (status, int(bool(row.get("symmetricEligible"))), canonical(row.get("failureReasons") or []), batch_id, utc_now(), key[0], key[1]),
        )
    expected = {(str(row["signature"]), str(row["mint"])) for row in pending_batch_for_id(connection, batch_id)}
    # Every input must result in a durable status; missing replay output is a retryable operational error.
    for signature, mint in expected - set(by_key):
        connection.execute("UPDATE acquisition_work SET status='RETRYABLE',symmetric_eligible=0,reason_json=?,batch_id=?,updated_utc=? WHERE signature=? AND mint=?", (canonical(["ADAPTER_REPLAY_ROW_MISSING"]), batch_id, utc_now(), signature, mint))
    connection.execute(
        "UPDATE batches SET status='COMPLETED',completed_utc=?,detail_json=?,backfill_complete=1 WHERE batch_id=?",
        (utc_now(), canonical({"replayRows": len(by_key), "symmetricEligible": int(report.get("symmetricEligible") or 0)}), batch_id),
    )
    connection.commit()


def pending_batch_for_id(connection: sqlite3.Connection, batch_id: str) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM acquisition_work WHERE batch_id=?", (batch_id,)).fetchall()
    connection.row_factory = None
    return rows


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = dict(connection.execute("SELECT status,COUNT(*) FROM acquisition_work GROUP BY status").fetchall())
    reasons: Counter[str] = Counter()
    for (value,) in connection.execute("SELECT reason_json FROM acquisition_work WHERE status='TERMINAL_REJECTED'"):
        for reason in json.loads(value):
            reasons[str(reason)] += 1
    return {"statuses": counts, "failureReasons": dict(sorted(reasons.items()))}


def backfill_completed_replays(connection: sqlite3.Connection) -> int:
    """Populate only not-yet-durable legacy batch bookkeeping once."""
    updated = 0
    for batch_id, report_path in connection.execute(
        "SELECT batch_id,report_path FROM batches WHERE status='COMPLETED' AND backfill_complete=0 ORDER BY started_utc"
    ):
        path = pathlib.Path(str(report_path))
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")).get("replayRows") or []:
            eligible = int(bool(row.get("symmetricEligible")))
            status = "PROCESSED" if eligible else "TERMINAL_REJECTED"
            cursor = connection.execute(
                """UPDATE acquisition_work SET status=?,symmetric_eligible=?,reason_json=?,updated_utc=?
                   WHERE signature=? AND mint=?""",
                (status, eligible, canonical(row.get("failureReasons") or []).decode(), utc_now(),
                 str(row["signature"]), str(row["mint"])),
            )
            updated += int(cursor.rowcount)
        connection.execute("UPDATE batches SET backfill_complete=1 WHERE batch_id=?", (batch_id,))
    connection.commit()
    return updated


def sync_valid_ledger(connection: sqlite3.Connection) -> dict[str, Any]:
    """Append immutable continuation/reserve labels in original receipt order."""
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT work.signature,work.mint,work.received_ns,work.slot,work.batch_id,
                  COALESCE(batches.evidence_path,'') AS evidence_path,
                  COALESCE(batches.report_path,'') AS report_path
           FROM acquisition_work AS work
           LEFT JOIN batches ON batches.batch_id=work.batch_id
           WHERE work.symmetric_eligible=1 AND work.received_ns>0
           ORDER BY work.received_ns,work.signature,work.mint"""
    ).fetchall()
    existing = {(str(row["signature"]), str(row["mint"])): row for row in connection.execute(
        "SELECT * FROM valid_observations"
    ).fetchall()}
    initialized = connection.execute("SELECT value FROM ledger_metadata WHERE key='initialized'").fetchone()
    rebuilt_initial_ledger = False
    if not initialized:
        # The first deployment may encounter reports written before the new
        # eligibility column existed.  Reconstruct once from those immutable
        # reports before declaring the chronological ledger initialized.
        if existing:
            connection.execute("DELETE FROM valid_observations")
            connection.commit()
            existing = {}
        rebuilt_initial_ledger = True
    if existing:
        latest = max(existing.values(), key=lambda row: int(row["chronological_ordinal"]))
        next_ordinal = int(latest["chronological_ordinal"]) + 1
        last_received_ns = int(latest["received_ns"])
    else:
        next_ordinal = 1
        last_received_ns = -1
    added = 0
    for row in rows:
        key = (str(row["signature"]), str(row["mint"]))
        if key in existing:
            continue
        if int(row["received_ns"]) < last_received_ns:
            raise RuntimeError("CHRONOLOGICAL_LEDGER_BACKFILL_REFUSED")
        bucket = "CONTINUATION_N20" if next_ordinal <= 20 else "N150_PLUS_RESERVE"
        connection.execute(
            "INSERT INTO valid_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key[0], key[1], int(row["received_ns"]), int(row["slot"]), next_ordinal, bucket,
             str(row["batch_id"]), str(row["evidence_path"]), str(row["report_path"]), utc_now()),
        )
        next_ordinal += 1
        last_received_ns = int(row["received_ns"])
        added += 1
    if not initialized:
        connection.execute("INSERT OR REPLACE INTO ledger_metadata VALUES ('initialized',?)", (utc_now(),))
    connection.commit()
    all_rows = connection.execute(
        "SELECT signature,mint,received_ns,slot,chronological_ordinal,bucket,batch_id,evidence_path,report_path FROM valid_observations ORDER BY chronological_ordinal"
    ).fetchall()
    connection.row_factory = None
    values = [dict(row) for row in all_rows]
    return {
        "schema": "2CQ_FULL_META_PROSPECTIVE_VALID_OBSERVATION_LEDGER_V1",
        "outcomeBlind": True,
        "submissions": 0,
        "added": added,
        "validRowsTotal": len(values),
        "continuationN20": sum(row["bucket"] == "CONTINUATION_N20" for row in values),
        "n150PlusReserve": sum(row["bucket"] == "N150_PLUS_RESERVE" for row in values),
        "initializationRebuilt": rebuilt_initial_ledger,
        "rows": values,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    lock_path = pathlib.Path(args.lock)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"schema": SCHEMA, "status": "LOCK_BUSY", "outcomeBlind": True}
        now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)
        stage_timings: dict[str, float] = {}
        connection = open_work(args.work_db)
        try:
            if args.refresh_funnel:
                started = time.perf_counter()
                completed = subprocess.run(
                    [sys.executable, str(args.funnel_builder), "--root", str(args.root), "--report", str(args.funnel), "--incremental"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=args.funnel_timeout_s,
                )
                if completed.returncode:
                    raise RuntimeError(f"FUNNEL_REFRESH_FAILED:{completed.stderr[-1500:]}")
                stage_timings["funnelRefreshSeconds"] = round(time.perf_counter() - started, 6)
            started = time.perf_counter()
            seed_prior(connection, args.prior_replay)
            interrupted_batches = recover_interrupted_batches(connection)
            rows = read_funnel(args.funnel, now_ns - WINDOW_NS)
            upsert_work(connection, rows)
            replay_bookkeeping_backfilled = backfill_completed_replays(connection)
            stage_timings["workBookkeepingSeconds"] = round(time.perf_counter() - started, 6)
            before = summary(connection)
            batches = []
            for offset in range(args.max_batches):
                batch = pending_batch(connection, args.batch_size, args.max_batch_span_s * 1_000_000_000)
                if not batch:
                    break
                batch_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{offset:02d}"
                destination = args.root / "promoted-windows" / f"auto-{batch_id}"
                connection.executemany(
                    "UPDATE acquisition_work SET batch_id=?,updated_utc=? WHERE signature=? AND mint=?",
                    [(batch_id, utc_now(), str(row["signature"]), str(row["mint"])) for row in batch],
                )
                connection.execute(
                    "INSERT INTO batches (batch_id,started_utc,completed_utc,evidence_path,report_path,status,detail_json) VALUES (?,?,?,?,?,?,?)",
                    (batch_id, utc_now(), None, str(destination / "evidence.frames"), str(destination / "adapter-report.json"), "RUNNING", "{}"),
                )
                connection.commit()
                started = time.perf_counter()
                manifest = promote(args.root, batch, destination)
                promotion_seconds = time.perf_counter() - started
                funnel_path = batch_funnel(batch, destination)
                started = time.perf_counter()
                report = invoke_adapter(
                    args, destination / "evidence.frames", funnel_path,
                    destination / "adapter-report.json", str(manifest["evidenceSha256"]),
                )
                adapter_seconds = time.perf_counter() - started
                started = time.perf_counter()
                finish_batch(connection, batch_id, report)
                finish_seconds = time.perf_counter() - started
                batches.append({"batchId": batch_id, "acquisitions": len(batch), "frames": manifest["framesCopied"], "sourceEvidenceBytesScanned": manifest["sourceEvidenceBytesScanned"], "promotedEvidenceBytes": manifest["promotedEvidenceBytes"], "symmetricEligible": int(report.get("symmetricEligible") or 0), "timingsSeconds": {"promotion": round(promotion_seconds, 6), "adapter": round(adapter_seconds, 6), "finish": round(finish_seconds, 6)}})
            after = summary(connection)
            ledger = sync_valid_ledger(connection)
            atomic_json(args.ledger, ledger)
            assignment = None
            if args.assignment_script:
                started = time.perf_counter()
                command = [
                    sys.executable, str(args.assignment_script),
                    "--root", str(args.root), "--ledger", str(args.ledger),
                    "--allocation-root", str(args.assignment_root),
                    "--policy", str(args.assignment_policy),
                    "--state-adapter", str(args.adapter),
                    "--normalizer", str(args.normalizer),
                ]
                completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=args.assignment_timeout_s)
                if completed.returncode:
                    raise RuntimeError(f"PROSPECTIVE_ASSIGNMENT_FAILED:{completed.stderr[-1500:]}")
                assignment = json.loads(completed.stdout)
                stage_timings["assignmentSeconds"] = round(time.perf_counter() - started, 6)
            document = {"schema": SCHEMA, "status": "OK", "outcomeBlind": True, "submissions": 0, "runAtUtc": utc_now(), "funnelRows": len(rows), "interruptedBatchesRecovered": interrupted_batches, "replayBookkeepingBackfilled": replay_bookkeeping_backfilled, "stageTimingsSeconds": stage_timings, "before": before, "batches": batches, "after": after, "validLedger": {key: ledger[key] for key in ("added","validRowsTotal","continuationN20","n150PlusReserve","initializationRebuilt")}, "maturePending": int(after["statuses"].get("PENDING_MATERIALIZATION", 0)) + int(after["statuses"].get("RETRYABLE", 0)), "openWindows": int(after["statuses"].get("PENDING_OPEN_WINDOW", 0))}
            if assignment is not None:
                document["prospectiveAssignment"] = {
                    "assignedRows": assignment["assignedRows"],
                    "bucketCounts": assignment["bucketCounts"],
                    "allocationContractSha256": assignment["allocationContractSha256"],
                    "whatEvaluationContractSha256": assignment["whatEvaluationContractSha256"],
                }
        finally:
            connection.close()
        atomic_json(args.report, document)
        return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--funnel", type=pathlib.Path, required=True)
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--work-db", type=pathlib.Path, required=True)
    parser.add_argument("--prior-replay", type=pathlib.Path, required=True)
    parser.add_argument("--adapter", type=pathlib.Path, required=True)
    parser.add_argument("--normalizer", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-batch-span-s", type=int, default=0)
    parser.add_argument("--max-batches", type=int, default=1)
    parser.add_argument("--adapter-timeout-s", type=int, default=1800)
    parser.add_argument("--canonical-fetch-workers", type=int, default=4)
    parser.add_argument("--proof-cache", type=pathlib.Path)
    parser.add_argument("--refresh-funnel", action="store_true")
    parser.add_argument("--funnel-builder", type=pathlib.Path, default=pathlib.Path("/root/bot/EdgeSniffer/scripts/twoCqFullMetaProspectiveFunnelV1.py"))
    parser.add_argument("--funnel-timeout-s", type=int, default=1800)
    parser.add_argument("--assignment-script", type=pathlib.Path)
    parser.add_argument("--assignment-root", type=pathlib.Path)
    parser.add_argument("--assignment-policy", type=pathlib.Path)
    parser.add_argument("--assignment-timeout-s", type=int, default=120)
    args = parser.parse_args()
    if args.max_batch_span_s < 0:
        parser.error("--max-batch-span-s must be non-negative")
    if args.canonical_fetch_workers < 1 or args.canonical_fetch_workers > 16:
        parser.error("--canonical-fetch-workers must be between 1 and 16")
    if bool(args.assignment_script) != bool(args.assignment_root) or bool(args.assignment_script) != bool(args.assignment_policy):
        parser.error("--assignment-script, --assignment-root, and --assignment-policy must be supplied together")
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
