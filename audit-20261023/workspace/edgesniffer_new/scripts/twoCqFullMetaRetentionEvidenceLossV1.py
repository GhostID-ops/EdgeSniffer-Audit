#!/usr/bin/env python3
"""Fail-close only rows whose required raw causal partition was evicted."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_FULL_META_RETENTION_EVIDENCE_LOSS_V1"
REASON = "RAW_CAUSAL_PARTITION_UNRECOVERABLE_AFTER_RETENTION"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-db", type=pathlib.Path, required=True)
    parser.add_argument("--partition", type=pathlib.Path, required=True)
    parser.add_argument("--target", action="append", required=True,
                        help="signature,mint,received_ns")
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()

    if args.report.exists():
        raise RuntimeError("RETENTION_EVIDENCE_LOSS_REPORT_ALREADY_EXISTS")
    evidence_files = [args.partition / "evidence.frames.zst", args.partition / "evidence.frames.partial"]
    if any(path.exists() for path in evidence_files):
        raise RuntimeError("SOURCE_PARTITION_STILL_HAS_RAW_EVIDENCE")

    targets = []
    for value in args.target:
        signature, mint, received_ns = value.split(",", 2)
        targets.append((signature, mint, int(received_ns)))
    if len(set(targets)) != len(targets):
        raise RuntimeError("DUPLICATE_TARGET")

    connection = sqlite3.connect(args.work_db, timeout=60)
    try:
        rows = []
        for signature, mint, received_ns in targets:
            row = connection.execute(
                "SELECT signature,mint,received_ns,slot,status,symmetric_eligible,batch_id "
                "FROM acquisition_work WHERE signature=? AND mint=?",
                (signature, mint),
            ).fetchone()
            if row is None:
                raise RuntimeError("TARGET_NOT_IN_WORK_QUEUE")
            if int(row[2]) != received_ns:
                raise RuntimeError("TARGET_RECEIPT_TIME_MISMATCH")
            if str(row[4]) not in {"PENDING_MATERIALIZATION", "RETRYABLE"}:
                raise RuntimeError(f"TARGET_NOT_FAIL_CLOSED_PENDING:{row[4]}")
            if int(row[5]) != 0:
                raise RuntimeError("TARGET_ALREADY_ELIGIBLE")
            rows.append({
                "signature": str(row[0]), "mint": str(row[1]), "receivedNs": int(row[2]),
                "slot": int(row[3]), "priorStatus": str(row[4]), "priorBatchId": row[6],
            })

        report = {
            "schema": SCHEMA,
            "createdAtUtc": utc_now(),
            "partition": str(args.partition),
            "partitionRawEvidencePresent": False,
            "reason": REASON,
            "semanticRecoveryAttempted": False,
            "targets": rows,
            "outcomeBlind": True,
            "submissions": 0,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_bytes(json.dumps(report, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        os.replace(temporary, args.report)
        os.chmod(args.report, 0o444)

        now = utc_now()
        for row in rows:
            connection.execute(
                "UPDATE acquisition_work SET status='TERMINAL_REJECTED', symmetric_eligible=0, "
                "reason_json=?, batch_id='RETENTION_EVIDENCE_LOSS_V1', updated_utc=? "
                "WHERE signature=? AND mint=?",
                (canonical([REASON]).decode(), now, row["signature"], row["mint"]),
            )
        connection.commit()
    finally:
        connection.close()

    print(json.dumps({
        "schema": SCHEMA, "targetsTerminalized": len(targets),
        "report": str(args.report), "reportSha256": sha256(args.report),
        "outcomeBlind": True, "submissions": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
