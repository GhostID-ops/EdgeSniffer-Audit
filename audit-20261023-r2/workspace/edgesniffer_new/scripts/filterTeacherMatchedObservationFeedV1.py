#!/usr/bin/env python3
"""Create a receive-only teacher-matched view without rewriting the raw feed.

The ShredStream source intentionally contains broad Pump flow context.  This
filter keeps only observations explicitly labeled as matching the configured
teacher wallet, preserving the broad source and its byte cursor unchanged.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def teacher_matched(row: dict[str, Any]) -> bool:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return bool(raw.get("teacherWalletMatched") or extra.get("teacherWalletMatched"))


def run(args: argparse.Namespace) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    state = {"schema": "TEACHER_MATCHED_FEED_STATE_V1", "input_offset": 0, "read": 0,
             "matched": 0, "malformed": 0}
    if args.state.exists():
        state.update(json.loads(args.state.read_text(encoding="utf-8")))
    offset = int(state.get("input_offset") or 0)
    with args.input.open("r", encoding="utf-8") as source, args.output.open("a", encoding="utf-8") as output:
        if args.start_at_end and not args.state.exists():
            source.seek(0, 2)
            offset = source.tell()
        source.seek(offset)
        while True:
            line = source.readline()
            if not line:
                state["input_offset"] = source.tell()
                args.state.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
                if args.once:
                    break
                time.sleep(args.poll_ms / 1000)
                continue
            state["read"] = int(state.get("read") or 0) + 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                state["malformed"] = int(state.get("malformed") or 0) + 1
                continue
            if isinstance(row, dict) and teacher_matched(row):
                output.write(json.dumps(row, sort_keys=True) + "\n")
                output.flush()
                state["matched"] = int(state.get("matched") or 0) + 1
            state["input_offset"] = source.tell()
            if int(state["read"]) % 100 == 0:
                args.state.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--once", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
