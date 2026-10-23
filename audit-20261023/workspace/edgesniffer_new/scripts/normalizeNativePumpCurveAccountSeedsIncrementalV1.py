#!/usr/bin/env python3
"""Incrementally normalize native Pump account observations.

The older normalizer rebuilt the entire multi-million-row seed file on every
loop.  This append-only follower preserves the existing seed artifact and
processes only new account-update bytes after a durable input offset.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from normalizeNativePumpCurveAccountSeedsV1 import extract


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"input_offset": 0, "read": 0, "accepted": 0, "rejected": 0}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"input_offset": 0}
    except (OSError, json.JSONDecodeError):
        return {"input_offset": 0}


def save_state(path: Path, state: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def run(input_path: Path, output_path: Path, state_path: Path, poll_ms: int,
        start_offset: int | None, once: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_state(state_path)
    if not state_path.exists() and start_offset is not None:
        state["input_offset"] = max(0, int(start_offset))
    offset = int(state.get("input_offset") or 0)
    stats = {key: int(state.get(key) or 0) for key in ("read", "accepted", "rejected", "duplicates")}
    rows_since_checkpoint = 0
    with input_path.open("r", encoding="utf-8") as source, output_path.open("a", encoding="utf-8") as output:
        source.seek(offset)
        while True:
            line = source.readline()
            if not line:
                state.update(stats, input_offset=source.tell())
                save_state(state_path, state)
                if once:
                    return
                time.sleep(max(1, poll_ms) / 1000)
                continue
            offset = source.tell()
            stats["read"] += 1
            rows_since_checkpoint += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["rejected"] += 1
                continue
            seed = extract(row)
            if not seed:
                stats["rejected"] += 1
                continue
            output.write(json.dumps(seed, sort_keys=True) + "\n")
            output.flush()
            stats["accepted"] += 1
            if rows_since_checkpoint >= 1000:
                state.update(stats, input_offset=offset)
                save_state(state_path, state)
                rows_since_checkpoint = 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--start-offset", type=int)
    parser.add_argument("--poll-ms", type=int, default=100)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(args.input, args.output, args.state, args.poll_ms, args.start_offset, args.once)


if __name__ == "__main__":
    main()
