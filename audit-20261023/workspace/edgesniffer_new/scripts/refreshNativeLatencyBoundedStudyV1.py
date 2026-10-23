#!/usr/bin/env python3
"""Continuously refresh the isolated native latency-bounded report."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from runNativeLatencyBoundedStudyV1 import evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-sec", type=float, default=60.0)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_name(args.output.name + ".tmp")
    while True:
        result = evaluate(args.input, args.contract)
        temp_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temp_path, args.output)
        print(json.dumps({"admitted_windows": result["admitted_windows"], "outcomes": len(result["outcomes"]),
                          "surface": len(result["surface"]), "policy_frozen": False,
                          "promotion_blocked": True, "read_only": True, "trading_enabled": False}, sort_keys=True), flush=True)
        time.sleep(max(1.0, args.poll_sec))


if __name__ == "__main__":
    main()
