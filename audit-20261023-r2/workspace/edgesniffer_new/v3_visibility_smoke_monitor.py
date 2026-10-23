#!/usr/bin/env python3
"""Read-only sampler for an isolated V3 source-plane smoke test."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> int:
    root = Path(sys.argv[1])
    seconds = float(sys.argv[2])
    output = root / "visibility-smoke-samples.jsonl"
    deadline = time.monotonic() + seconds
    with output.open("a", encoding="utf-8") as stream:
        while time.monotonic() < deadline:
            global_state = read_json(root / "state" / "global.json")
            a = read_json(root / "state" / "observer-A.json")
            b = read_json(root / "state" / "observer-B.json")
            if global_state and a and b:
                stream.write(json.dumps({
                    "sampledUnixNs": time.time_ns(),
                    "global": global_state,
                    "a": {"state": a.get("state"), "epoch": a.get("epoch")},
                    "b": {"state": b.get("state"), "epoch": b.get("epoch")},
                }, sort_keys=True) + "\n")
                stream.flush()
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
