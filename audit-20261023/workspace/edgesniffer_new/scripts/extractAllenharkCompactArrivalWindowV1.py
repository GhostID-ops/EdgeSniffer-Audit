#!/usr/bin/env python3
"""Extract an immutable slot window from the fixed-record AllenHark journal."""

from __future__ import annotations

import argparse
import json
import pathlib
import struct


MAGIC = b"AHCDV1\n\0"
RECORD = struct.Struct("<32sqQ")


def slot_at(stream, index: int) -> int:
    stream.seek(len(MAGIC) + index * RECORD.size)
    raw = stream.read(RECORD.size)
    if len(raw) != RECORD.size:
        raise RuntimeError("COMPACT_ARRIVAL_SHORT_RANDOM_READ")
    return int(RECORD.unpack(raw)[2])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--start-slot", type=int, required=True)
    parser.add_argument("--end-slot", type=int, required=True)
    parser.add_argument("--lookback-records", type=int, default=5_000_000)
    parser.add_argument("--stop-slot-margin", type=int, default=512)
    args = parser.parse_args()

    size = args.input.stat().st_size
    payload_size = size - len(MAGIC)
    if payload_size < 0 or payload_size % RECORD.size:
        raise RuntimeError("COMPACT_ARRIVAL_INVALID_SIZE")
    total_records = payload_size // RECORD.size
    args.output.parent.mkdir(parents=True, exist_ok=True)

    selected = 0
    scanned = 0
    inversions = 0
    min_selected_slot = None
    max_selected_slot = None
    with args.input.open("rb") as source:
        if source.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        low, high = 0, total_records
        while low < high:
            middle = (low + high) // 2
            if slot_at(source, middle) < args.start_slot:
                low = middle + 1
            else:
                high = middle
        estimated_first = low
        scan_start = max(0, estimated_first - args.lookback_records)
        source.seek(len(MAGIC) + scan_start * RECORD.size)
        previous_slot = None
        with args.output.open("wb") as target:
            target.write(MAGIC)
            while True:
                raw = source.read(RECORD.size)
                if not raw:
                    break
                if len(raw) != RECORD.size:
                    raise RuntimeError("COMPACT_ARRIVAL_TRUNCATED_RECORD")
                slot = int(RECORD.unpack(raw)[2])
                scanned += 1
                if previous_slot is not None and slot < previous_slot:
                    inversions += 1
                previous_slot = slot
                if args.start_slot <= slot <= args.end_slot:
                    target.write(raw)
                    selected += 1
                    min_selected_slot = slot if min_selected_slot is None else min(min_selected_slot, slot)
                    max_selected_slot = slot if max_selected_slot is None else max(max_selected_slot, slot)
                if slot > args.end_slot + args.stop_slot_margin:
                    break

    report = {
        "schema": "ALLENHARK_COMPACT_ARRIVAL_WINDOW_V1",
        "input": str(args.input),
        "inputSizeAtOpen": size,
        "totalCompleteRecordsAtOpen": total_records,
        "startSlot": args.start_slot,
        "endSlot": args.end_slot,
        "estimatedFirstRecord": estimated_first,
        "scanStartRecord": scan_start,
        "lookbackRecords": args.lookback_records,
        "recordsScanned": scanned,
        "recordsSelected": selected,
        "minSelectedSlot": min_selected_slot,
        "maxSelectedSlot": max_selected_slot,
        "slotInversionsObserved": inversions,
        "output": str(args.output),
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
