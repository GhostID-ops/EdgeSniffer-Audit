#!/usr/bin/env python3
"""Outcome-blind temporal/actionability audit for sealed kEfi market rows.

This does not discover or score a strategy.  It proves whether a later paper
entry simulator has the ordering fields needed to avoid using an event before
it was visible to the source.  Any ambiguous identity, missing timing/fee
field, or conflicting duplicate is recorded as a fail-closed defect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path


SCHEMA = "KEFI_TEMPORAL_ACTIONABILITY_AUDIT_V1"
REQUIRED = {"source_frame_sha256", "event_index", "received_at_unix_ns", "slot", "event_timestamp", "side", "pool", "mint", "is_tracked_teacher"}
FEE_FIELDS = {"lp_fee_bps", "protocol_fee_bps", "buyback_bps", "cashback_bps", "coin_creator_bps"}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w") as handle:
        handle.write(canonical(payload) + "\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)


def inspect(paths: list[Path]) -> dict:
    identities: dict[tuple[str, int], str] = {}
    defects: Counter[str] = Counter()
    rows = teachers = 0
    source_frames: set[str] = set()
    pools: set[str] = set()
    slots: set[int] = set()
    earliest = latest = None
    fee_complete = event_time_after_receipt = 0
    for path in paths:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                defects["malformed_json"] += 1; continue
            rows += 1
            absent = REQUIRED - row.keys()
            if absent:
                defects["missing_required_fields"] += 1; continue
            try:
                frame, event = str(row["source_frame_sha256"]), int(row["event_index"])
                received, slot, event_seconds = int(row["received_at_unix_ns"]), int(row["slot"]), int(row["event_timestamp"])
                if not frame or event < 0 or received <= 0 or slot < 0 or event_seconds <= 0: raise ValueError()
            except (TypeError, ValueError):
                defects["invalid_identity_or_timing"] += 1; continue
            if event_seconds * 1_000_000_000 > received:
                event_time_after_receipt += 1
            key, value = (frame, event), canonical(row)
            if key in identities and identities[key] != value:
                defects["conflicting_event_identity"] += 1; continue
            identities[key] = value
            source_frames.add(frame); pools.add(str(row["pool"])); slots.add(slot)
            earliest = received if earliest is None else min(earliest, received)
            latest = received if latest is None else max(latest, received)
            if all(field in row and isinstance(row[field], int) and row[field] >= 0 for field in FEE_FIELDS):
                fee_complete += 1
            else:
                defects["missing_or_invalid_fee_schedule"] += 1
            if bool(row["is_tracked_teacher"]): teachers += 1
    return {
        "schema": SCHEMA, "rows_scanned": rows, "unique_source_events": len(identities),
        "source_frames": len(source_frames), "pools": len(pools), "slots": len(slots),
        "teacher_rows": teachers, "fee_complete_rows": fee_complete,
        "event_timestamp_after_receipt_rows": event_time_after_receipt,
        "actionability_clock": "received_at_unix_ns",
        "earliest_available_at_unix_ns": earliest, "latest_available_at_unix_ns": latest,
        "defects": dict(sorted(defects.items())),
        "actionability_ready": not defects and rows > 0,
        "outcome_blind": True, "pnl_accessed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.rows)
    report["input_sha256"] = {str(path): sha256(path) for path in args.rows}
    atomic_json(args.out, report)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
