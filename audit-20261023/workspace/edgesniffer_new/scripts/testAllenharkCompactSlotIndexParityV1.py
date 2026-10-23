#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import struct

import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
import twoCqProspectiveSymmetricChoiceMaterializerV3 as v3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", type=pathlib.Path, required=True)
    parser.add_argument("--index", type=pathlib.Path, required=True)
    args = parser.parse_args()
    raw = args.index.read_bytes()
    blocks = [
        v3.SLOT_INDEX_RECORD.unpack(raw[offset:offset + v3.SLOT_INDEX_RECORD.size])
        for offset in range(v3.SLOT_INDEX_HEADER.size, len(raw), v3.SLOT_INDEX_RECORD.size)
    ]
    selected = [blocks[int((len(blocks) - 1) * fraction)] for fraction in (.05, .25, .50, .75, .95)]
    results = []
    with args.compact.open("rb") as source:
        for start, count, min_slot, max_slot in selected:
            source.seek(len(legacy.COMPACT_MAGIC) + start * legacy.COMPACT_RECORD.size)
            sample = source.read(min(count, 250) * legacy.COMPACT_RECORD.size)
            targets = {row[0] for row in legacy.COMPACT_RECORD.iter_unpack(sample)}
            indexed, indexed_evidence = v3.indexed_compact_arrivals_by_slot(
                args.compact, targets, min_slot, max_slot, args.index
            )
            baseline, baseline_evidence = legacy.targeted_compact_arrivals_by_slot(
                args.compact, targets, min_slot, max_slot
            )
            if indexed != baseline:
                raise RuntimeError(
                    f"INDEX_PARITY_MISMATCH:{start}:indexed={len(indexed)}:baseline={len(baseline)}"
                )
            results.append({
                "blockStart": start, "minSlot": min_slot, "maxSlot": max_slot,
                "matches": len(indexed),
                "indexedRecordsScanned": indexed_evidence["compactRecordsScanned"],
                "baselineRecordsScanned": baseline_evidence["compactRecordsScanned"],
            })
    print(json.dumps({"verdict": "PASS", "ranges": results}, indent=2))


if __name__ == "__main__":
    main()
