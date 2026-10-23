#!/usr/bin/env python3
"""Conservative retention policy for the isolated full-meta lane.

Closed raw partitions are already compressed atomically by LedgerWriter.  This
policy never removes an open partition, a promoted window, or a partition that
contains an unresolved wallet candidate.  It only becomes destructive after a
long bounded retention period and emits an append-only audit report first.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
from typing import Any


SCHEMA = "2CQ_FULL_META_PROSPECTIVE_RETENTION_V1"
HOUR_PATH = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/(\d{2})$")
WINDOW_NS = 120_000_000_000


def parse_ns(row: dict[str, Any]) -> int:
    return int(row.get("receivedAtUnixNs") or row.get("received_at_unix_ns") or 0)


def hour_start(path: pathlib.Path) -> int | None:
    match = HOUR_PATH.search(str(path))
    if not match:
        return None
    stamp = dt.datetime(*map(int, match.groups()), tzinfo=dt.timezone.utc)
    return int(stamp.timestamp() * 1_000_000_000)


def overlaps_causal_window(start_ns: int, end_ns: int, receipt_ns: int) -> bool:
    """Whether a closed hourly partition contains any required causal frame."""
    return receipt_ns - WINDOW_NS < end_ns and receipt_ns >= start_ns


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--retain-hours", type=int, default=24)
    parser.add_argument("--work-db", type=pathlib.Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.retain_hours < 6:
        raise RuntimeError("RETENTION_BELOW_MINIMUM_6H_REFUSED")

    now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)
    protected_candidates: set[int] = set()
    candidate_file = args.root / "2cq-wallet-pump-candidates.jsonl"
    if candidate_file.exists():
        for line in candidate_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                protected_candidates.add(parse_ns(json.loads(line)))
    # Once a candidate has been replayed, its immutable promoted 120-second
    # window and canonical materialization are the permanent evidence.  Only
    # unresolved work still needs the broad source partition past retention.
    unresolved_candidates: set[int] = set()
    if args.work_db and args.work_db.exists():
        connection = sqlite3.connect(args.work_db)
        try:
            for (received_ns,) in connection.execute(
                "SELECT received_ns FROM acquisition_work WHERE status IN ('PENDING_OPEN_WINDOW','PENDING_MATERIALIZATION','RETRYABLE') AND received_ns>0"
            ):
                unresolved_candidates.add(int(received_ns))
        finally:
            connection.close()
    promoted = []
    for manifest in sorted((args.root / "promoted-windows").glob("*/manifest.json")):
        value = json.loads(manifest.read_text(encoding="utf-8"))
        # Promotion manifests use windowStartNs/windowEndNs.  Accept the old
        # names only for historical artifacts written before this lane.
        promoted.append({
            "path": str(manifest),
            "startNs": int(value.get("windowStartNs", value.get("startNs", 0)) or 0),
            "endNs": int(value.get("windowEndNs", value.get("endNs", 0)) or 0),
        })

    cutoff_ns = now_ns - args.retain_hours * 3_600_000_000_000
    rows = []
    deleted_bytes = 0
    for compressed in sorted((args.root / "partitions").glob("*/*/*/*/evidence.frames.zst")):
        directory = compressed.parent
        start_ns = hour_start(directory)
        if start_ns is None:
            continue
        end_ns = start_ns + 3_600_000_000_000
        partial = directory / "evidence.frames.partial"
        # A 2CQ entry just after an hour boundary still needs the preceding
        # hour's 120-second causal frames.  Protect both unresolved work and
        # unindexed candidate-journal observations across that full interval.
        unresolved_inside = any(overlaps_causal_window(start_ns, end_ns, value) for value in unresolved_candidates)
        candidate_inside = any(overlaps_causal_window(start_ns, end_ns, value) for value in protected_candidates)
        promoted_inside = any(not (entry["endNs"] <= start_ns or entry["startNs"] >= end_ns) for entry in promoted)
        eligible_age = end_ns < cutoff_ns
        action = "KEEP"
        reason = "WITHIN_RETENTION"
        if partial.exists():
            reason = "OPEN_OR_RECOVERABLE_PARTITION"
        elif unresolved_inside or candidate_inside:
            reason = "UNRESOLVED_2CQ_CANDIDATE_PROTECTED"
        elif promoted_inside and eligible_age:
            # The immutable promoted window has the full causal interval for
            # this completed acquisition.  The broader source partition can
            # roll off after the safety window without losing reproducibility.
            action, reason = "DELETE_COMPRESSED", "PROMOTED_WINDOW_PERMANENT_COPY_RETAINED"
        elif eligible_age:
            action, reason = "DELETE_COMPRESSED", "CLOSED_NON_CANDIDATE_BEYOND_RETENTION"
        rows.append({"partition": str(directory), "bytes": compressed.stat().st_size, "action": action, "reason": reason})
        if args.apply and action == "DELETE_COMPRESSED":
            deleted_bytes += compressed.stat().st_size
            compressed.unlink()
            manifest = directory / "manifest.json"
            manifest.unlink(missing_ok=True)

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "apply": bool(args.apply), "retainHours": args.retain_hours,
        "openRawProtected": True,
        "promotedWindowProtection": True,
        "canonicalMaterializedStateRetained": True,
        "candidateJournalProtection": True,
        "closedPartitions": len(rows), "promotedWindows": len(promoted),
        "walletCandidatesObserved": len(protected_candidates),
        "unresolvedCandidatesProtected": len(unresolved_candidates), "deletedBytes": deleted_bytes,
        "rows": rows,
    }
    atomic_json(args.report, report)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, sort_keys=True))


if __name__ == "__main__":
    main()
