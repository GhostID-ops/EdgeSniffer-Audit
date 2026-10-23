#!/usr/bin/env python3
"""Join native reserve horizon checkpoints onto a separate window artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


HORIZONS = (5, 10, 15, 30, 60, 120, 300)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def canonical_window_id(row: dict[str, Any]) -> str:
    identity = {
        "fingerprint_id": row.get("fingerprint_id"),
        "mint": row.get("mint"),
        "signal_receive_ts_ms": row.get("signal_receive_ts_ms"),
        "signal_signature": row.get("signal_signature"),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def materialize(windows_path: Path, checkpoints_path: Path, output_path: Path) -> dict[str, Any]:
    checkpoints: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(checkpoints_path):
        window_id = row.get("window_id")
        if window_id:
            checkpoints[str(window_id)].append(row)
    for values in checkpoints.values():
        values.sort(key=lambda row: (int(row.get("horizon_seconds") or 0), int(row.get("checkpoint_receive_ts_ms") or 0)))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    windows = read_jsonl(windows_path)
    joined = 0
    checkpoint_rows = 0
    temp = output_path.with_name(output_path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for window in windows:
            value = dict(window)
            window_id = canonical_window_id(window)
            marks = checkpoints.get(window_id, [])
            value["native_checkpoint_marks"] = marks
            marked = sorted({int(row["horizon_seconds"]) for row in marks})
            value["native_checkpoint_coverage"] = {
                "requested_horizons_seconds": list(HORIZONS),
                "marked_horizons_seconds": marked,
                "missing_horizons_seconds": [h for h in HORIZONS if h not in marked],
                "source": "native_account_state_stream",
                "strict_marks_unchanged": True,
                "carry_forward_marks_unchanged": True,
            }
            if marks:
                joined += 1
                checkpoint_rows += len(marks)
            handle.write(json.dumps(value, sort_keys=True) + "\n")
    os.replace(temp, output_path)
    return {
        "schema": "EDGESNIFFER_NATIVE_CHECKPOINT_WINDOWS_V1",
        "windows": len(windows),
        "windows_with_native_checkpoints": joined,
        "native_checkpoint_rows": checkpoint_rows,
        "read_only": True,
        "trading_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        print(json.dumps(materialize(args.windows, args.checkpoints, args.output), sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(max(1.0, args.poll_sec))


if __name__ == "__main__":
    main()
