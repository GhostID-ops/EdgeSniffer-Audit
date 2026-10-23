#!/usr/bin/env python3
"""Continuously refresh a separate prospective 2CQ fingerprint cohort.

The cutoff is fixed before this process starts.  Every refresh replays only
rows at or after that cutoff, writes a complete replacement file atomically,
and never uses outcomes to alter the fingerprint contract.  This is a
read-only prospective materializer; policy selection and trading are outside
its scope.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from materialize2CQFingerprintMarketWindowsV1 import materialize, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-at-receive-ms", type=int, required=True)
    parser.add_argument("--poll-sec", type=float, default=60.0)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_name(args.output.name + ".tmp")
    while True:
        result = materialize(
            args.input,
            args.fingerprint,
            temp_path,
            args.start_at_receive_ms,
        )
        os.replace(temp_path, args.output)
        print(
            json.dumps(
                {
                    **result,
                    "output_sha256": sha256_file(args.output),
                    "prospective_cutoff_receive_ms": args.start_at_receive_ms,
                    "read_only": True,
                    "trading_enabled": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        time.sleep(max(1.0, args.poll_sec))


if __name__ == "__main__":
    main()
