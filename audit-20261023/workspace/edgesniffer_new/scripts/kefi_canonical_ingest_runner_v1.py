#!/usr/bin/env python3
"""Low-impact runner for queue-driven kEfi canonical ingestion.

It has no scientific selection logic.  It runs only while capture is fresh,
and delegates all work to the descriptor-driven ingester so it never scans
the historical receipt tree during a normal cycle.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path


def source_is_fresh(root: Path, collector_unit: str) -> tuple[bool, str]:
    try:
        if subprocess.run(["systemctl", "is-active", "--quiet", collector_unit]).returncode:
            return False, "collector inactive"
        health = json.loads((root / "health.json").read_text())
        latest = dt.datetime.fromisoformat(health["latestReceivedAtUtc"].replace("Z", "+00:00"))
        if (dt.datetime.now(dt.timezone.utc) - latest).total_seconds() > 45:
            return False, "source stale"
        for name in ("continuityGaps", "receiveErrors", "reconnects"):
            if int(health.get(name, 0)):
                return False, f"source {name}"
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        return False, str(error)
    return True, "healthy"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--collector-unit", required=True)
    parser.add_argument("--materializer-out", type=Path, required=True)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--indexer", type=Path, required=True)
    args = parser.parse_args()
    fresh, reason = source_is_fresh(args.source_root, args.collector_unit)
    if not fresh:
        print(json.dumps({"status": "SKIP", "reason": reason}, sort_keys=True)); return
    if not args.indexer.is_file():
        raise RuntimeError("FAIL_CLOSED: canonical indexer missing")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    completed = subprocess.run(
        ["nice", "-n", "19", "python3", str(args.indexer), "--materializer-out", str(args.materializer_out), "--index-root", str(args.index_root)],
        capture_output=True, text=True, env=env,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "FAIL_CLOSED: canonical ingest failed")
    print(completed.stdout.strip())


if __name__ == "__main__":
    main()
