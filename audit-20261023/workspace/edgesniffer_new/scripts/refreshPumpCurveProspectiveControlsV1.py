#!/usr/bin/env python3
"""Refresh controls for the isolated Pump top-level prospective cohort."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--control-seconds", type=int, default=5)
    args = parser.parse_args()
    if not args.windows.exists():
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            '{"schema":"WALLET_PUMP_CURVE_CONTROLS_V1","read_only":true,"windows_loaded":0,"controls":0,"records":[],"policy_frozen":true,"trading_enabled":false}\n',
            encoding="utf-8",
        )
        print('{"status":"PUMP_CURVE_PROSPECTIVE_CONTROLS_WAITING_FOR_WINDOWS"}', flush=True)
        return
    command = [
        sys.executable,
        str(Path(__file__).with_name("materializePumpCurveControlsV1.py")),
        "--windows", str(args.windows),
        "--out", str(args.out),
        "--control-seconds", str(args.control_seconds),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    value = json.loads(args.out.read_text(encoding="utf-8"))
    value["cohort_kind"] = "FRESH_PROSPECTIVE_VALIDATION"
    value["policy_frozen"] = True
    value["trading_enabled"] = False
    args.out.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
