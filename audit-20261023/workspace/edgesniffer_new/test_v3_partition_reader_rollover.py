#!/usr/bin/env python3
"""Deterministic regression tests for the V3 source-partition reader."""

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


MODULE = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("v3_lane_candidate", MODULE)
lane = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(lane)


def frame(sequence: int) -> bytes:
    metadata = json.dumps({"writerSequence": sequence, "receivedAtUnixNs": sequence}).encode()
    raw = f"raw-{sequence}".encode()
    body = metadata + raw
    return lane.MAGIC + struct.pack("<II", len(metadata), len(raw)) + body + hashlib.sha256(body).digest()


def write_frames(path: Path, sequences: range) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for sequence in sequences:
            handle.write(frame(sequence))


def test_one_transition(root: Path, index: int) -> None:
    old = root / "partitions/2026/09/30" / f"{index:02d}"
    new = root / "partitions/2026/09/30" / f"{index + 1:02d}"
    old_partial = old / "evidence.frames.partial"
    new_partial = new / "evidence.frames.partial"
    write_frames(old_partial, range(index * 10 + 1, index * 10 + 6))
    write_frames(new_partial, range(index * 10 + 6, index * 10 + 11))

    # The reader has the old writable inode open before source detaches it.
    fd, proc, seekable = lane.open_source(old_partial, 0)
    assert proc is None and seekable
    os.replace(old_partial, old / "evidence.frames.sealed")
    parts = lane.source_parts(root)
    keys = [key for key, _ in parts]
    selected = dict(parts)[str(old)]
    assert selected.name == "evidence.frames.sealed"
    consumed = [meta["writerSequence"] for meta, _, _ in lane.frames(fd, 0, seekable)]
    fd.close()
    assert consumed == list(range(index * 10 + 1, index * 10 + 6))
    next_key, handoff = lane.reader_handoff_after_eof(keys, str(old), old_partial, selected, True)
    assert handoff and next_key == str(new)

    # The next partition may already contain records before the reader switches.
    fd, proc, seekable = lane.open_source(dict(parts)[next_key], 0)
    next_records = [meta["writerSequence"] for meta, _, _ in lane.frames(fd, 0, seekable)]
    fd.close()
    assert next_records == list(range(index * 10 + 6, index * 10 + 11))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="v3-reader-rollover-") as value:
        root = Path(value)
        for index in range(20):
            test_one_transition(root / f"case-{index}", index)

    # Restart after compression replays from zero and must suppress all already
    # committed writer sequences before any new sequence can be admitted.
    with tempfile.TemporaryDirectory(prefix="v3-reader-restart-") as value:
        root = Path(value)
        partial = root / "partitions/2026/09/30/14/evidence.frames.partial"
        write_frames(partial, range(1, 6))
        compressed = partial.with_name("evidence.frames.zst")
        subprocess.run(["zstd", "-q", "-f", str(partial), "-o", str(compressed)], check=True)
        partial.unlink()
        path = lane.source_parts(root)[0][1]
        fd, proc, seekable = lane.open_source(path, 0)
        replay = [meta["writerSequence"] for meta, _, _ in lane.frames(fd, 0, seekable)]
        fd.close()
        if proc:
            assert proc.wait(timeout=10) == 0
        assert [sequence for sequence in replay if sequence > 5] == []

    print("PASS: 20 forced reader transitions plus compressed restart cursor suppression")


if __name__ == "__main__":
    main()
