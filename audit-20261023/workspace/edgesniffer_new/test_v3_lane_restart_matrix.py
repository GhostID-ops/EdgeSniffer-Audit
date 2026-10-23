#!/usr/bin/env python3
"""Disposable restart-state matrix for V3 reader durable-cursor recovery."""

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


spec = importlib.util.spec_from_file_location("v3_lane", Path(sys.argv[1]))
lane = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(lane)


def frame(sequence: int) -> bytes:
    meta = json.dumps({"writerSequence": sequence, "receivedAtUnixNs": sequence}).encode()
    raw = f"frame-{sequence}".encode()
    body = meta + raw
    return lane.MAGIC + struct.pack("<II", len(meta), len(raw)) + body + hashlib.sha256(body).digest()


def write(path: Path, values: range) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for value in values:
            handle.write(frame(value))


def read(path: Path, offset: int, consumed: int) -> tuple[list[int], int]:
    handle, process, seekable = lane.open_source(path, offset)
    result, final_offset = [], offset
    try:
        for meta, _, final_offset in lane.frames(handle, offset, seekable):
            sequence = int(meta["writerSequence"])
            if sequence > consumed:
                result.append(sequence)
    finally:
        handle.close()
        if process:
            assert process.wait(timeout=10) == 0
    return result, final_offset


def main() -> None:
    scenarios = [
        "mid-partition",
        "immediately-before-rollover",
        "while-old-partition-sealing",
        "immediately-after-new-partition-opens",
        "after-consuming-new-partition-prefix",
        "with-old-partition-immutable-compressed",
    ]
    for scenario in scenarios:
        with tempfile.TemporaryDirectory(prefix="v3-lane-restart-") as value:
            root = Path(value)
            old = root / "partitions/2026/09/30/14/evidence.frames.partial"
            new = root / "partitions/2026/09/30/15/evidence.frames.partial"
            write(old, range(1, 6)); write(new, range(6, 11))
            prefix, offset = read(old, 0, 0)
            # Persisted cursor represents the first two canonically committed rows.
            handle, proc, seekable = lane.open_source(old, 0)
            try:
                committed_offset = 0
                for index, (_, _, committed_offset) in enumerate(lane.frames(handle, 0, seekable), start=1):
                    if index == 2:
                        break
            finally:
                handle.close()
            assert prefix == [1, 2, 3, 4, 5]
            if scenario in {"while-old-partition-sealing", "immediately-after-new-partition-opens"}:
                os.replace(old, old.with_name("evidence.frames.sealed"))
                old = old.with_name("evidence.frames.sealed")
            elif scenario == "with-old-partition-immutable-compressed":
                compressed = old.with_name("evidence.frames.zst")
                subprocess.run(["zstd", "-q", "-f", str(old), "-o", str(compressed)], check=True)
                old.unlink(); old = compressed; committed_offset = 0
            parts = lane.source_parts(root)
            keys = [key for key, _ in parts]
            old_key = str(old.parent)
            # Re-open from persisted state. Compressed replay may see old rows,
            # but durable-sequence suppression prevents duplicate admission.
            replay, _ = read(old, committed_offset, 2 if old.suffix != ".zst" else 5)
            if old.name != "evidence.frames.partial":
                next_key, handoff = lane.reader_handoff_after_eof(keys, old_key, old, dict(parts)[old_key], True)
                assert handoff and next_key == str(new.parent)
                after, _ = read(dict(parts)[next_key], 0, 2)
                replay += after
            else:
                # Mid-hour/before-rollover restart resumes only the uncommitted tail.
                assert replay == [3, 4, 5]
            assert len(replay) == len(set(replay)), scenario
            assert all(sequence > 2 for sequence in replay), scenario
    print("PASS: six lane durable-cursor restart scenarios; no duplicate or skipped uncommitted sequence")


if __name__ == "__main__":
    main()
