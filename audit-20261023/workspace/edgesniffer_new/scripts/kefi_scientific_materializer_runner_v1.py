#!/usr/bin/env python3
"""Fail-closed launcher for a future kEfi scientific materialization cohort.

This is intentionally not enabled and cannot create a cohort.  When an
independently reviewed H3 boundary eventually exists, this runner is the only
supported materialization entrypoint: it supplies the epoch registry/boundary
and asks the materializer to require deterministic source order.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materializer", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--registry-script", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--window-id")
    parser.add_argument("--max-windows", type=int, default=1)
    args = parser.parse_args()
    required_paths = (args.materializer, args.decoder, args.registry_script)
    if not all(path.is_file() for path in required_paths):
        raise SystemExit("FAIL_CLOSED: scientific materializer dependency missing")
    command = [
        "python3", str(args.materializer), "--root", str(args.root), "--out", str(args.out),
        "--decoder", str(args.decoder), "--scientific", "--registry-script", str(args.registry_script),
        "--registry-root", str(args.registry_root), "--boundary-utc", args.boundary_utc,
        "--max-windows", str(args.max_windows),
    ]
    if args.window_id:
        command.extend(("--window-id", args.window_id))
    completed = subprocess.run(command, text=True)
    if completed.returncode:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
