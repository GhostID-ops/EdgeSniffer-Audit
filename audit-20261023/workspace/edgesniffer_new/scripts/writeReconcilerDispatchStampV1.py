#!/usr/bin/env python3
"""Write a same-boot monotonic dispatch marker before systemd lock acquisition."""

import argparse
import datetime as dt
import json
import os
import pathlib
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=pathlib.Path)
    args = parser.parse_args()
    value = {
        "schema": "PUMP_EXACT_RECONCILER_DISPATCH_STAMP_V1",
        "utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "monotonicNs": time.monotonic_ns(),
    }
    temporary = args.path.with_suffix(args.path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
