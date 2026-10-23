#!/usr/bin/env python3
"""Promote a bounded, already-retained full-meta pre-entry window verbatim."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

MAGIC = b"PEL1FRM\0"
WINDOW_NS = 120_000_000_000


def frames(path: Path):
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        return
    cursor = len(MAGIC)
    while cursor + 8 <= len(data):
        metadata_len, raw_len = struct.unpack_from("<II", data, cursor)
        end = cursor + 8 + metadata_len + raw_len + 32
        if end > len(data):
            break
        body_start = cursor + 8
        metadata = json.loads(data[body_start : body_start + metadata_len])
        digest = data[body_start + metadata_len + raw_len : end]
        if hashlib.sha256(data[body_start : body_start + metadata_len + raw_len]).digest() != digest:
            break
        yield metadata, data[cursor:end]
        cursor = end


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--funnel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    funnel = json.loads(args.funnel.read_text(encoding="utf-8"))
    selected = [
        row for row in funnel["rows"]
        if row["stage"] == "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION_PENDING_FULL_WINDOW"
    ]
    if not selected:
        raise SystemExit("no canonical successful Pump acquisitions to promote")
    start_ns = min(int(row["receivedAtUnixNs"]) for row in selected) - WINDOW_NS
    end_ns = max(int(row["receivedAtUnixNs"]) for row in selected)
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / "evidence.frames"
    seen: set[str] = set()
    copied = 0
    with destination.open("wb") as stream:
        stream.write(MAGIC)
        for source in sorted(args.root.glob("partitions/*/*/*/*/evidence.frames.partial")):
            for metadata, encoded in frames(source):
                received_ns = int(metadata.get("receivedAtUnixNs") or 0)
                evidence_id = str(metadata.get("evidenceId") or "")
                if start_ns <= received_ns <= end_ns and evidence_id and evidence_id not in seen:
                    stream.write(encoded)
                    seen.add(evidence_id)
                    copied += 1
    payload = {
        "schema": "2CQ_FULL_META_WINDOW_PROMOTION_V1",
        "outcomeBlind": True,
        "windowStartNs": start_ns,
        "windowEndNs": end_ns,
        "windowSeconds": (end_ns - start_ns) / 1_000_000_000,
        "canonicalSuccessfulAcquisitions": selected,
        "framesCopied": copied,
        "evidenceSha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
    (args.output / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
